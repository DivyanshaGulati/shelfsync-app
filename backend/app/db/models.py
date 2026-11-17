"""database schema. No tables are created at import time.

All prices are INR per inventory item's unit. A PurchaseOrder is single-item
in the ShelfSync MVP, enabling a database-enforced one-open-request-per-item rule.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, Numeric,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


def timestamp() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('OWNER', 'EMPLOYEE')", name="ck_users_role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = timestamp()

    transactions: Mapped[list[Transaction]] = relationship(back_populates="user")
    created_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="creator")
    approval_logs: Mapped[list[ApprovalLog]] = relationship(back_populates="owner")


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint("on_hand >= 0 AND reserved_qty >= 0 AND reserved_qty <= on_hand", name="ck_inventory_quantities"),
        CheckConstraint("unit_cost >= 0", name="ck_inventory_unit_cost"),
        CheckConstraint("avg_daily_demand >= 0 AND demand_std_dev >= 0 AND avg_lead_time_days >= 0 AND lead_time_std_dev >= 0 AND safety_stock >= 0 AND reorder_point >= 0", name="ck_inventory_forecast_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    on_hand: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    reserved_qty: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default=text("0"), nullable=False)
    avg_daily_demand: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default=text("0"), nullable=False)
    demand_std_dev: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default=text("0"), nullable=False)
    avg_lead_time_days: Mapped[Decimal] = mapped_column(Numeric(10, 3), server_default=text("0"), nullable=False)
    lead_time_std_dev: Mapped[Decimal] = mapped_column(Numeric(10, 3), server_default=text("0"), nullable=False)
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default=text("0"), nullable=False)
    reorder_point: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default=text("0"), nullable=False)
    low_confidence: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    created_at: Mapped[datetime] = timestamp()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    vendor_items: Mapped[list[VendorItem]] = relationship(back_populates="inventory_item")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="inventory_item")
    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="inventory_item")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="inventory_item")
    quotations: Mapped[list[VendorQuotation]] = relationship(back_populates="inventory_item", foreign_keys="VendorQuotation.inventory_item_id")
    threshold_logs: Mapped[list[AIThresholdLog]] = relationship(back_populates="inventory_item")


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    is_new_vendor: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)
    is_slow: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    created_at: Mapped[datetime] = timestamp()

    vendor_items: Mapped[list[VendorItem]] = relationship(back_populates="vendor")
    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="vendor")
    quotations: Mapped[list[VendorQuotation]] = relationship(back_populates="vendor", foreign_keys="VendorQuotation.vendor_id")


class VendorItem(Base):
    __tablename__ = "vendor_items"
    __table_args__ = (
        UniqueConstraint("inventory_item_id", "vendor_id", name="uq_vendor_items_item_vendor"),
        CheckConstraint("last_known_unit_price IS NULL OR last_known_unit_price >= 0", name="ck_vendor_items_price"),
        CheckConstraint("expected_lead_time_days IS NULL OR expected_lead_time_days >= 0", name="ck_vendor_items_lead_time"),
        CheckConstraint("min_order_qty > 0", name="ck_vendor_items_min_qty"),
        Index("ix_vendor_items_one_preferred", "inventory_item_id", unique=True, postgresql_where=text("is_preferred = true"), sqlite_where=text("is_preferred = 1")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), index=True, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    last_known_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    expected_lead_time_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    min_order_qty: Mapped[int] = mapped_column(Integer, server_default=text("1"), nullable=False)

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="vendor_items")
    vendor: Mapped[Vendor] = relationship(back_populates="vendor_items")


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_transactions_positive_qty"),
        CheckConstraint("transaction_type IN ('CHECKOUT', 'ADJUSTMENT', 'RECEIPT')", name="ck_transactions_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    usage_note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = timestamp()

    user: Mapped[User] = relationship(back_populates="transactions")
    inventory_item: Mapped[InventoryItem] = relationship(back_populates="transactions")


Index("ix_transactions_created_at", Transaction.created_at)


OPEN_ORDER_STATUSES = ("WAITING_FOR_QUOTES", "PENDING_APPROVAL", "PLACED", "PARTIALLY_RECEIVED")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        CheckConstraint("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED', 'RECEIVED', 'REJECTED')", name="ck_purchase_orders_status"),
        CheckConstraint("total_amount IS NULL OR total_amount >= 0", name="ck_purchase_orders_total"),
        CheckConstraint("status != 'WAITING_FOR_QUOTES' OR (vendor_id IS NULL AND total_amount IS NULL)", name="ck_purchase_orders_waiting_no_price"),
        UniqueConstraint("id", "inventory_item_id", name="uq_purchase_orders_id_item"),
        # MVP requests have one inventory item. This direct FK enforces the rule
        # that PostgreSQL cannot enforce across a join to order_items.
        Index("ix_purchase_orders_one_open_per_item", "inventory_item_id", unique=True, postgresql_where=text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')"), sqlite_where=text("status IN ('WAITING_FOR_QUOTES', 'PENDING_APPROVAL', 'PLACED', 'PARTIALLY_RECEIVED')")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(30), server_default=text("'WAITING_FOR_QUOTES'"), index=True, nullable=False)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    approval_reason: Mapped[str | None] = mapped_column(Text)
    mock_order: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)
    mock_in_transit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mock_delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quote_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ai_recommended_quotation_id: Mapped[int | None] = mapped_column(ForeignKey("vendor_quotations.id", ondelete="SET NULL", use_alter=True, name="fk_po_ai_recommended_quote"))
    ai_recommendation_reason: Mapped[str | None] = mapped_column(Text)
    backend_candidate_quotation_id: Mapped[int | None] = mapped_column(ForeignKey("vendor_quotations.id", ondelete="SET NULL", use_alter=True, name="fk_po_backend_candidate_quote"))
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = timestamp()

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="purchase_orders")
    vendor: Mapped[Vendor | None] = relationship(back_populates="purchase_orders")
    creator: Mapped[User | None] = relationship(back_populates="created_orders")
    order_items: Mapped[list[OrderItem]] = relationship(back_populates="purchase_order", foreign_keys="OrderItem.purchase_order_id")
    quotations: Mapped[list[VendorQuotation]] = relationship(back_populates="purchase_order", foreign_keys="VendorQuotation.purchase_order_id")
    approval_logs: Mapped[list[ApprovalLog]] = relationship(back_populates="purchase_order")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "inventory_item_id", name="uq_order_items_order_item"),
        ForeignKeyConstraint(["purchase_order_id", "inventory_item_id"], ["purchase_orders.id", "purchase_orders.inventory_item_id"], name="fk_order_items_matching_order", ondelete="RESTRICT"),
        CheckConstraint("ordered_quantity > 0 AND received_quantity >= 0 AND received_quantity <= ordered_quantity", name="ck_order_items_qty"),
        CheckConstraint("unit_price IS NULL OR unit_price >= 0", name="ck_order_items_unit_price"),
        CheckConstraint("total_price IS NULL OR total_price >= 0", name="ck_order_items_total_price"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True, nullable=False)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    ordered_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    received_quantity: Mapped[int] = mapped_column(Integer, server_default=text("0"), nullable=False)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    is_open: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="order_items", foreign_keys=[purchase_order_id])
    inventory_item: Mapped[InventoryItem] = relationship(back_populates="order_items")


class VendorQuotation(Base):
    __tablename__ = "vendor_quotations"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "vendor_id", name="uq_vendor_quotations_order_vendor"),
        ForeignKeyConstraint(["purchase_order_id", "inventory_item_id"], ["purchase_orders.id", "purchase_orders.inventory_item_id"], name="fk_quotations_matching_order", ondelete="RESTRICT"),
        ForeignKeyConstraint(["inventory_item_id", "vendor_id"], ["vendor_items.inventory_item_id", "vendor_items.vendor_id"], name="fk_quotations_mapped_vendor", ondelete="RESTRICT"),
        CheckConstraint("status IN ('REQUESTED', 'RECEIVED', 'SELECTED', 'EXPIRED')", name="ck_vendor_quotations_status"),
        CheckConstraint("quoted_unit_price IS NULL OR quoted_unit_price >= 0", name="ck_vendor_quotations_price"),
        CheckConstraint("quoted_lead_time_days IS NULL OR quoted_lead_time_days >= 0", name="ck_vendor_quotations_lead_time"),
        CheckConstraint("status NOT IN ('RECEIVED', 'SELECTED') OR (quoted_unit_price IS NOT NULL AND quoted_lead_time_days IS NOT NULL AND responded_at IS NOT NULL)", name="ck_vendor_quotations_received_data"),
        Index("ix_vendor_quotations_one_selected", "purchase_order_id", unique=True, postgresql_where=text("status = 'SELECTED'"), sqlite_where=text("status = 'SELECTED'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True, nullable=False)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id", ondelete="RESTRICT"), index=True, nullable=False)
    quoted_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    quoted_lead_time_days: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    status: Mapped[str] = mapped_column(String(20), server_default=text("'REQUESTED'"), index=True, nullable=False)
    requested_at: Mapped[datetime] = timestamp()
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_simulated: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), nullable=False)

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="quotations", foreign_keys=[purchase_order_id])
    inventory_item: Mapped[InventoryItem] = relationship(back_populates="quotations", foreign_keys=[inventory_item_id])
    vendor: Mapped[Vendor] = relationship(back_populates="quotations", foreign_keys=[vendor_id])


class AIThresholdLog(Base):
    __tablename__ = "ai_threshold_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), index=True, nullable=False)
    avg_daily_demand: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    demand_std_dev: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    avg_lead_time_days: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    lead_time_std_dev: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    safety_stock: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    reorder_point: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    inventory_position: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = timestamp()

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="threshold_logs")


class ApprovalLog(Base):
    __tablename__ = "approval_logs"
    __table_args__ = (CheckConstraint("action IN ('APPROVED', 'REJECTED')", name="ck_approval_logs_action"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True, nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = timestamp()

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="approval_logs")
    owner: Mapped[User] = relationship(back_populates="approval_logs")


class QuotationEmailDispatch(Base):
    """A single durable RFQ delivery reservation per quotation.

    IN_PROGRESS/UNCERTAIN must never be automatically retried: SMTP success
    cannot be made atomic with a database commit.
    """
    __tablename__ = "quotation_email_dispatches"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS', 'MOCKED', 'SENT', 'UNCERTAIN')",
                        name="ck_quotation_email_dispatch_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_quotations.id", ondelete="RESTRICT"), unique=True, nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    message_id: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = timestamp()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Avoid saving SMTP credentials, message bodies or full exceptions here.
    error_code: Mapped[str | None] = mapped_column(String(60))


class PurchaseOrderEmailDispatch(Base):
    """Durable, unique final PO dispatch reservation per order.

    SMTP and the database cannot commit atomically. IN_PROGRESS/UNCERTAIN
    are never automatically retried; operator reconciliation is required.
    """
    __tablename__ = "purchase_order_email_dispatches"
    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS', 'MOCKED', 'SENT', 'UNCERTAIN')",
                        name="ck_purchase_order_email_dispatch_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="RESTRICT"), unique=True, index=True, nullable=False)
    quotation_id: Mapped[int] = mapped_column(
        ForeignKey("vendor_quotations.id", ondelete="RESTRICT"), nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    recipient: Mapped[str] = mapped_column(String(254), nullable=False)
    message_id: Mapped[str] = mapped_column(String(254), nullable=False)
    created_at: Mapped[datetime] = timestamp()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(60))


class StockReceipt(Base):
    """Physical receipt audit; immutable after commit and idempotent by request key."""
    __tablename__ = "stock_receipts"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_stock_receipts_positive_quantity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id", ondelete="RESTRICT"), index=True, nullable=False)
    inventory_item_id: Mapped[int] = mapped_column(ForeignKey("inventory_items.id", ondelete="RESTRICT"), nullable=False)
    received_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    received_at: Mapped[datetime] = timestamp()
