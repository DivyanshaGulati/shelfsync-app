# Local demo

Use a separate database and local environment for demonstrations. Do not reuse production credentials or connect this copy to the VPS database. The [setup guide](SETUP.md) covers dependencies, migrations and startup.

## Configure

For local Uvicorn, configure `backend/.env`. For Compose, configure the root `.env` instead. Keep secrets out of Git. For the streamlined demo use:

```dotenv
EMAIL_MODE=mock
ENABLE_REAL_EMAIL_DISPATCH=false
ENABLE_QUOTE_SIMULATION=true
ENABLE_MOCK_PURCHASING=true
ENABLE_BUSY_WEEK_SIMULATION=true
ENABLE_CONTACT_EMAIL=false
```

Set a separate database such as `shelfsync_demo`, a strong local JWT secret, Owner email and seed passwords. Configure an authorized AI provider key if you want advisory operations. Apply Alembic migrations, then seed only this disposable demo database with `python -m app.db.seed --confirm-demo`.

The seed is idempotent; it does not reset prior checkouts, orders or passwords. For a pristine rerun, create another disposable database. The latest supplied migration is `0012_item_thumbnails`.

## Walkthrough

1. Sign in as Employee and record a small checkout. Confirm that On Hand decreases and that replaying the same idempotency key does not deduct twice.
2. Sign in as Owner. Review physical stock, reorder points and outstanding orders. To demonstrate replenishment, check out an item until its stock reaches its reorder point.
3. Review replenishment requests in Purchasing. With streamlined mock purchasing enabled, simulated catalogue quotes and advisory AI selection run after qualifying checkouts. If the provider is unavailable, the committed checkout remains valid and the Owner can retry processing the order.
4. Inspect the recommendation, quotation terms and actual decision. This mock path uses an INR 10,000 threshold; amounts above it require Owner approval. Do not assume a predetermined outcome because current stock and quotes affect it.
5. Review the mock final-order dispatch record. It does not send an email to a supplier or to your inbox.
6. Simulate transit and delivery using the available Owner controls. Receiving changes stock in this demo database. Verify the resulting On Hand, outstanding quantity and order status.

For the detailed purchasing interface, disable `ENABLE_MOCK_PURCHASING`. Prepare quotations, record verified replies or explicitly simulate them, close collection, compare and evaluate, obtain Owner approval when required, and explicitly dispatch the final order in mock mode. This path applies configured routine-purchase rules and `AUTO_ORDER_LIMIT`; it does not use the streamlined mock threshold.

## Verify

Run `python -m app.verification.demo_preflight` from `backend/` for a read-only configuration and seed check. It does not prove that every demo scenario will create an order. Run the existing backend tests and frontend build using the setup guide commands. PostgreSQL concurrency tests require their own database ending in `_test`.

Mock purchase emails are recorded in the application; they do not appear in a mailbox. The contact form uses its own SMTP opt-in.
