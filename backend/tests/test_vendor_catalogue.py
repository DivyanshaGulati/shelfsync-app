from decimal import Decimal

from sqlalchemy import func, select

from app.db.models import InventoryItem, PurchaseOrder, Vendor, VendorItem, VendorQuotation
from test_quotation_collection import create_request, setup


CATALOGUE = "/api/vendors/sample-quotations"


def test_existing_inventory_gets_two_distinct_persistent_mock_quotes(setup):
    client, factory, owner, employee = setup
    with factory.begin() as db:
        db.add(InventoryItem(
            name="Custom fastener", sku="CUSTOM-1", unit="box", category="Hardware",
            unit_cost=Decimal("0.00"), on_hand=12,
        ))

    assert client.get(CATALOGUE).status_code == 401
    assert client.post(CATALOGUE, headers=employee).status_code == 403
    assert client.get(CATALOGUE, headers=owner).json() == {"vendors": [], "quotations": []}
    before_inventory = client.get("/api/inventory", headers=owner).json()
    before_forecast = client.get("/api/inventory/1/forecast", headers=owner).json()

    response = client.post(CATALOGUE, headers=owner)
    assert response.status_code == 200, response.text
    result = response.json()
    vendor_ids = {vendor["id"] for vendor in result["vendors"]}
    assert len(vendor_ids) == 2
    for item in before_inventory["items"]:
        quotes = [q for q in result["quotations"] if q["inventory_item_id"] == item["id"]]
        assert {q["vendor_id"] for q in quotes} == vendor_ids
        assert {q["sku"] for q in quotes} == {item["sku"]}
        assert len({Decimal(q["unit_price"]) for q in quotes}) == 2
        assert {Decimal(q["delivery_days"]) for q in quotes} == {Decimal(2), Decimal(4)}
    assert client.get(CATALOGUE, headers=owner).json() == result
    assert client.post(CATALOGUE, headers=owner).json() == result
    assert client.get("/api/inventory", headers=owner).json() == before_inventory
    assert client.get("/api/inventory/1/forecast", headers=owner).json() == before_forecast
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PurchaseOrder)) == 0
        assert db.scalar(select(func.count()).select_from(VendorQuotation)) == 0
        assert all(not vendor.is_active for vendor in db.scalars(select(Vendor).where(Vendor.id.in_(vendor_ids))))
        assert db.scalar(select(func.count()).select_from(VendorItem).where(VendorItem.vendor_id.in_(vendor_ids))) == 4

    purchase_id = create_request(setup)
    actual_quotes = client.get(f"/api/purchase-requests/{purchase_id}/quotations", headers=owner).json()["quotations"]
    assert {quote["vendor_name"] for quote in actual_quotes} == {"Shreeji", "Bharat"}


def test_sync_adds_quotes_for_subsequent_inventory_without_repricing_existing_quotes(setup):
    client, factory, owner, _ = setup
    saved = client.post(CATALOGUE, headers=owner).json()
    with factory.begin() as db:
        db.get(InventoryItem, 1).unit_cost = Decimal("99.00")
        db.add(InventoryItem(
            name="Later product", sku="NEW-1", unit="unit", category="Hardware",
            unit_cost=Decimal("9999999999.99"),
        ))
    response = client.post(CATALOGUE, headers=owner)
    assert response.status_code == 200, response.text
    quotes = response.json()["quotations"]
    assert [quote for quote in quotes if quote["inventory_item_id"] == 1] == saved["quotations"]
    added = [Decimal(q["unit_price"]) for q in quotes if q["sku"] == "NEW-1"]
    assert sorted(added) == [Decimal("9999999999.98"), Decimal("9999999999.99")]
