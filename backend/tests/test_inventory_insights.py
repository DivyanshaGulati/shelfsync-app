import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from app.core import claude_agent
from app.core.config import settings
from app.core.security import create_access_token
from app.db.models import InventoryItem, OrderItem, PurchaseOrder, Transaction, User
from app.db.seed import HISTORY_PREFIX
from test_groq_provider import fake_groq
from test_quotation_collection import create_request, setup


def test_owner_dashboard_insight_uses_groq_with_claude_blank(setup, monkeypatch):
    client, factory, owner, employee = setup
    order_id = create_request(setup)
    with factory.begin() as db:
        item = db.get(InventoryItem, 1)
        item.on_hand = 1
        item.reorder_point = 3
        sku, name = item.sku, item.name
    monkeypatch.setattr(settings, "ai_provider", "claude")
    monkeypatch.setattr(settings, "ai_fallback_provider", "")
    monkeypatch.setattr(settings, "claude_api_key", " ")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    def responder(request, payload, _):
        assert request.url.host == "api.groq.com"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert "tools" not in payload
        content = payload["messages"][-1]["content"]
        snapshot = json.loads(content.split("Snapshot: ", maxsplit=1)[1])
        # A request still waiting for quotes is not an order, so nothing is on order.
        assert snapshot["low_stock_candidates"][0] == {
            "sku": sku, "name": name, "on_hand": 1, "reserved_qty": 0, "available_stock": 1,
            "reorder_point": "3.0000", "on_order": 0, "order_status": None,
        }
        answer = json.dumps({"summary": "Review the item below its reorder point.", "priority_sku": sku})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": answer}}]})

    requests = fake_groq(monkeypatch, responder)
    assert client.post("/api/inventory/ai-insights", headers=employee).status_code == 403
    assert not requests
    insight = client.post("/api/inventory/ai-insights", headers=owner)
    assert insight.status_code == 200, insight.text
    saved = insight.json()
    assert saved.pop("id") > 0
    assert datetime.fromisoformat(saved.pop("created_at")).tzinfo is not None
    assert saved.pop("priority_items") == [saved["priority_item"]]
    assert saved == {
        "summary": "Review the item below its reorder point.",
        "priority_sku": sku,
        "inventory_items": 1,
        "low_stock_items": 1,
        "priority_item": {
            "sku": sku, "name": name, "on_hand": 1, "reserved_qty": 0, "available_stock": 1,
            "reorder_point": "3.0000", "on_order": 0, "order_status": None,
        },
    }
    assert len(requests) == 1
    with factory() as db:
        assert db.get(PurchaseOrder, order_id).status == "WAITING_FOR_QUOTES"
        assert db.get(InventoryItem, 1).on_hand == 1


def test_dashboard_insight_rejects_unverified_priority_and_handles_missing_key(setup, monkeypatch):
    client, _, owner, _ = setup
    monkeypatch.setattr(settings, "ai_fallback_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "")
    missing = client.post("/api/inventory/ai-insights", headers=owner)
    assert missing.status_code == 503
    assert missing.json()["detail"]["message"] == "GROQ_API_KEY not configured"

    monkeypatch.setattr(claude_agent, "call_provider_messages", lambda _: {
        "stop_reason": "end_turn", "content": [{"type": "text", "text": (
            '```json\n{"summary":"A fabricated candidate was returned.","priority_sku":"UNKNOWN"}\n```'
        )}],
    })
    invalid = client.post("/api/inventory/ai-insights", headers=owner)
    assert invalid.status_code == 409
    assert invalid.json()["detail"]["ai_status"] == "INVALID_INSIGHT"
    assert client.get("/api/inventory/ai-insights", headers=owner).json()["items"] == []


def test_saved_history_is_owner_scoped_paginated_and_keeps_original_stock(setup, monkeypatch):
    client, factory, owner, employee = setup
    url = "/api/inventory/ai-insights"
    monkeypatch.setattr(claude_agent, "call_provider_messages", lambda _: {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"summary":"Review the stock levels.","priority_sku":null}'}],
    })
    with factory.begin() as db:
        db.get(InventoryItem, 1).reorder_point = 6
        second_owner = User(name="Second Owner", email="second@quotes.local", password_hash="unused", role="OWNER")
        db.add(second_owner)
        db.flush()
        other_owner = {"Authorization": f"Bearer {create_access_token(second_owner.id)}"}

    assert client.get(url, headers=employee).status_code == 403
    assert client.get(f"{url}/snapshot", headers=employee).status_code == 403
    snapshot = client.get(f"{url}/snapshot", headers=owner).json()
    assert snapshot["low_stock_items"] == len(snapshot["priority_items"]) == 1
    first = client.post(url, headers=owner).json()
    assert first["priority_item"]["on_hand"] == 4
    with factory.begin() as db:
        db.get(InventoryItem, 1).on_hand = 10

    assert client.get(f"{url}/snapshot", headers=owner).json() == {"inventory_items": 1, "low_stock_items": 0, "priority_items": []}
    second = client.post(url, headers=owner).json()
    assert second["priority_item"] is None
    assert second["id"] > first["id"]
    assert client.post(url, headers=other_owner).status_code == 200
    latest = client.get(f"{url}?limit=1", headers=owner).json()
    assert latest == {"items": [second], "next_before_id": second["id"]}
    assert client.get(f"{url}?before_id={second['id']}", headers=owner).json() == {
        "items": [first], "next_before_id": None,
    }
    assert len(client.get(url, headers=other_owner).json()["items"]) == 1


# --- Low Stock must not be hidden by On Order quantity -----------------------------------

@pytest.fixture
def insight(setup, monkeypatch):
    """Call the owner insight endpoint through a fake provider; return what the AI was given."""
    client, _, owner, _ = setup
    monkeypatch.setattr(settings, "ai_provider", "claude")
    monkeypatch.setattr(settings, "ai_fallback_provider", "")
    monkeypatch.setattr(settings, "claude_api_key", " ")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    seen = {}

    def responder(request, payload, _):
        content = payload["messages"][-1]["content"]
        seen["system"] = payload["messages"][0]["content"]
        seen["snapshot"] = snapshot = json.loads(content.split("Snapshot: ", maxsplit=1)[1])
        candidates = snapshot["low_stock_candidates"]
        answer = json.dumps({"summary": "Stock summary for the owner.",
                             "priority_sku": candidates[0]["sku"] if candidates else None})
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": answer}}]})

    fake_groq(monkeypatch, responder)

    def run():
        response = client.post("/api/inventory/ai-insights", headers=owner)
        assert response.status_code == 200, response.text
        seen["result"] = response.json()
        return seen["snapshot"]

    run.seen = seen
    return run


def set_stock_and_order(factory, *, on_hand, rop, reserved=0, status="PLACED", ordered=20,
                        received=0, in_transit=False, is_open=True):
    """Put the single fixture item and its one purchase order into the requested state."""
    with factory.begin() as db:
        item = db.get(InventoryItem, 1)
        item.on_hand, item.reserved_qty, item.reorder_point = on_hand, reserved, rop
        order = db.scalar(select(PurchaseOrder))
        order.status = status
        order.mock_order = in_transit
        order.mock_in_transit_at = datetime.now(timezone.utc) if in_transit else None
        line = db.scalar(select(OrderItem).where(OrderItem.purchase_order_id == order.id))
        line.ordered_quantity, line.received_quantity, line.is_open = ordered, received, is_open
        return item.sku, item.name


@pytest.mark.parametrize("status, in_transit, ordered, received, on_hand, shown_status, on_order", [
    ("PLACED", False, 20, 0, 1, "PLACED", 20),
    ("PLACED", True, 20, 0, 1, "IN_TRANSIT", 20),
    ("PARTIALLY_RECEIVED", False, 20, 5, 2, "PARTIALLY_RECEIVED", 15),
])
def test_item_at_or_below_rop_stays_low_stock_while_an_order_is_on_the_way(
        setup, insight, status, in_transit, ordered, received, on_hand, shown_status, on_order):
    _, factory, _, _ = setup
    create_request(setup)
    sku, name = set_stock_and_order(factory, on_hand=on_hand, rop=3, status=status, ordered=ordered,
                                    received=received, in_transit=in_transit)
    snapshot = insight()
    # Counting On Order as stock (21 > 3) used to make this item look healthy.
    assert snapshot["low_stock_items"] == 1
    assert snapshot["low_stock_candidates"] == [{
        "sku": sku, "name": name, "on_hand": on_hand, "reserved_qty": 0, "available_stock": on_hand,
        "reorder_point": "3.0000", "on_order": on_order, "order_status": shown_status,
    }]
    assert insight.seen["result"]["priority_item"] == snapshot["low_stock_candidates"][0]


def test_item_equal_to_rop_is_low_and_delivery_above_rop_makes_it_healthy(setup, insight):
    _, factory, _, _ = setup
    create_request(setup)
    set_stock_and_order(factory, on_hand=3, rop=3)
    assert insight()["low_stock_items"] == 1                       # at ROP, order placed: still low

    # Delivered, but the received stock only brings On Hand up to the ROP: still low, nothing on order.
    set_stock_and_order(factory, on_hand=3, rop=3, status="RECEIVED", received=20, is_open=False)
    snapshot = insight()
    assert snapshot["low_stock_items"] == 1
    assert snapshot["low_stock_candidates"][0]["on_order"] == 0
    assert snapshot["low_stock_candidates"][0]["order_status"] is None

    # Delivered and On Hand updated above the ROP: healthy.
    set_stock_and_order(factory, on_hand=4, rop=3, status="RECEIVED", received=20, is_open=False)
    snapshot = insight()
    assert snapshot["low_stock_items"] == 0
    assert snapshot["low_stock_candidates"] == []


def test_healthy_item_with_an_open_order_is_not_reported_low(setup, insight):
    _, factory, _, _ = setup
    create_request(setup)
    set_stock_and_order(factory, on_hand=10, rop=3)
    assert insight()["low_stock_items"] == 0
    assert insight.seen["result"]["priority_item"] is None


def test_reserved_units_are_not_available_stock(setup, insight):
    _, factory, _, _ = setup
    create_request(setup)
    set_stock_and_order(factory, on_hand=5, rop=3, reserved=3)
    candidate = insight()["low_stock_candidates"][0]
    assert (candidate["on_hand"], candidate["reserved_qty"], candidate["available_stock"]) == (5, 3, 2)


def test_insight_prompt_tells_the_ai_that_on_order_stock_is_not_available(setup, insight):
    create_request(setup)
    insight()
    system = insight.seen["system"]
    assert "never makes an item healthy" in system
    assert "product name, on hand, ROP, on order" in system


def test_generate_rechecks_stock_changed_by_checkout_during_ai_call(setup, monkeypatch):
    client, _, owner, employee = setup
    calls = []

    def explain(snapshot):
        calls.append(snapshot["low_stock_items"])
        if len(calls) == 1:
            checkout = client.post("/api/checkout", headers=employee, json={
                "inventory_item_id": 1, "quantity": 4, "idempotency_key": "checkout-during-insight",
            })
            assert checkout.status_code == 201, checkout.text
        return claude_agent.InventoryInsight(summary="Review current stock levels.")

    monkeypatch.setattr("app.api.inventory.explain_inventory", explain)
    url = "/api/inventory/ai-insights"
    response = client.post(url, headers=owner)
    assert response.status_code == 200, response.text
    assert calls == [0, 1]
    assert response.json()["priority_items"][0]["on_hand"] == 0
    assert len(client.get(url, headers=owner).json()["items"]) == 1


def test_growth_tip_counts_paired_checkouts_and_excludes_untracked_lines(setup, monkeypatch):
    client, factory, owner, employee = setup
    url = "/api/inventory/ai-insights/growth-tip"
    assert client.get(url, headers=employee).status_code == 403
    assert client.get(url, headers=owner).json()["tip"] == {
        "product": "Teflon Tape", "available": 4, "units_checked_out": 0,
        "window_days": 30, "action": "promote_item",
    }

    with factory.begin() as db:
        other = InventoryItem(name="Pipe", sku="TEST-PIPE", category="Plumbing", unit="pcs")
        unrelated = InventoryItem(name="Hinges", sku="TEST-HINGE", category="Hardware", unit="pcs")
        db.add_all([other, unrelated])
        db.flush()
        for index in range(15):
            group = uuid4()
            item_ids = [1, 1, other.id] if index < 3 else [1] if index < 10 else [unrelated.id]
            for item_id in item_ids:
                db.add(Transaction(user_id=2, inventory_item_id=item_id, transaction_type="CHECKOUT",
                                   quantity=1, idempotency_key=f"checkout-cart-{group}-{uuid4()}"))
        db.add(Transaction(user_id=2, inventory_item_id=other.id, transaction_type="CHECKOUT",
                           quantity=1, idempotency_key="legacy-checkout"))
        db.add(Transaction(user_id=2, inventory_item_id=other.id, transaction_type="CHECKOUT",
                           quantity=1, idempotency_key=f"checkout-cart-{uuid4()}-{uuid4()}",
                           created_at=datetime.now(timezone.utc) - timedelta(days=8)))

    def choose(*, together, total):
        assert (together, total) == (3, 10)
        return "test_bundle"

    monkeypatch.setattr("app.core.checkout_trends.suggest_checkout_pair_action", choose)
    response = client.get(url, headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["tip"] == {
        "products": ["Teflon Tape", "Pipe"], "together": 3, "total": 15, "share": 20,
        "primary_total": 10, "primary_share": 30, "target_share": 40,
        "window_days": 7, "action": "test_bundle",
    }


def test_growth_tip_uses_recorded_demand_and_available_stock_without_pairs(setup):
    client, factory, owner, _ = setup
    with factory.begin() as db:
        item = db.get(InventoryItem, 1)
        item.reserved_qty, item.reorder_point = 2, 2
        other = InventoryItem(name="Pipe", sku="TEST-PIPE", category="Plumbing", unit="pcs", on_hand=12)
        db.add(other)
        db.flush()
        db.add_all([
            Transaction(user_id=2, inventory_item_id=1, transaction_type="CHECKOUT",
                        quantity=3, idempotency_key="legacy-tape-checkout"),
            Transaction(user_id=2, inventory_item_id=other.id, transaction_type="CHECKOUT",
                        quantity=50, idempotency_key=HISTORY_PREFIX + "pipe-checkout"),
        ])

    result = client.get("/api/inventory/ai-insights/growth-tip", headers=owner)
    assert result.status_code == 200, result.text
    assert result.json()["tip"] == {
        "product": "Teflon Tape", "available": 2, "units_checked_out": 3,
        "window_days": 30, "action": "review_stock",
    }
