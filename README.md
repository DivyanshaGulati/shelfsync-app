# ShelfSync

Inventory visibility and AI-assisted purchasing, from stock usage to delivery.

ShelfSync connects employee checkout with demand forecasting, replenishment, vendor quotation comparison, purchase authorization and goods receiving. Owners can review stock, understand purchase recommendations and track outstanding deliveries in one application.

## Why ShelfSync

Stock usage, supplier quotations and purchase decisions often sit in separate records. ShelfSync links them: consumption updates stock and demand calculations, low inventory can trigger replenishment, and purchasing decisions remain traceable through approval, dispatch and receiving.

## Core capabilities

- **Inventory:** On Hand, reservations, reorder points and outstanding orders, with physical low stock kept distinct from incoming deliveries.
- **Employee checkout:** Authenticated stock issues with transaction history and idempotency protection.
- **Forecasting and replenishment:** Backend demand calculations and replenishment requests based on inventory state.
- **Quotation comparison:** Vendor prices, delivery terms and eligibility checks for the current purchase request.
- **AI assistance:** Inventory insights, quotation recommendations and draft email content through configurable providers.
- **Purchasing controls:** Backend authorization, Owner approval, dispatch records and delivery tracking.
- **Receiving:** Partial and complete receipts with stock reconciliation and duplicate-receipt protection.
- **Contact:** A fixed-recipient website contact form with a separate SMTP control.

## Workflow

Employee checkout updates stock and demand. Replenishment creates a purchase request when the applicable conditions are met. Quotations are collected and compared, the backend validates authorization or requests Owner approval, and dispatch is tracked separately from order placement. Receiving increases physical stock by the quantity actually received.

The application includes a detailed purchasing workflow and an explicitly enabled streamlined mock workflow. The mock workflow uses simulated catalogue quotations and its own INR 10,000 approval threshold. The detailed workflow uses `AUTO_ORDER_LIMIT` (default INR 2,000) together with additional purchasing rules.

## AI and purchasing safeguards

Claude, Groq and optional local Ollama provide bounded advisory operations. Provider routing and fallback are configurable. The backend validates model responses against application data and owns authorization, email dispatch and stock mutations.

Role checks distinguish Owner and Employee actions. Transactions, PostgreSQL row locks and database constraints protect stock changes and open requests. Idempotency keys protect checkout and receiving; durable dispatch reservations prevent automatic retries after uncertain SMTP outcomes.

An internally placed order does not prove supplier email delivery. An outstanding order also does not make physically low inventory healthy: stock changes when goods are received.

## Architecture

| Component | Technology and responsibility |
| --- | --- |
| Frontend | React, Vite, React Router, Tailwind CSS and Recharts |
| API | FastAPI request validation, JWT authentication and role authorization |
| Services | Forecasting, purchasing, provider integration, mail and receipts |
| Persistence | PostgreSQL, SQLAlchemy and Alembic |
| Deployment | Docker Compose and Caddy for frontend serving, API proxying and HTTPS |

See [architecture](docs/ARCHITECTURE.md) for service boundaries and operational limits.

## Run locally

Prerequisites: Python 3.12, Node 22.12 or newer on the Node 22 line, and PostgreSQL 17. Use a separate local database and keep secrets in private environment files.

Follow the [local setup guide](docs/SETUP.md) to create the database, install dependencies, configure the backend, apply migrations and provision demonstration accounts. Start the backend from `backend/`:

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, from `frontend/`:

```bash
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. For local development, API documentation is at `http://127.0.0.1:8000/docs`. Accounts are provisioned through the configured seed or deployment process; the application has no public self-registration.

For containers and public hosting, see [deployment](docs/DEPLOYMENT.md). For a repeatable demonstration, see the [demo guide](docs/DEMO_GUIDE.md).

## Configuration

| Settings | Purpose |
| --- | --- |
| `DATABASE_URL`, `JWT_SECRET` | Native backend database connection and token signing |
| `AI_PROVIDER`, `AI_FALLBACK_PROVIDER` | Advisory provider selection and fallback |
| `CLAUDE_API_KEY`, `GROQ_API_KEY` | Private cloud credentials |
| `EMAIL_MODE`, `ENABLE_REAL_EMAIL_DISPATCH` | Mock or explicitly enabled supplier dispatch |
| `ENABLE_QUOTE_SIMULATION`, `ENABLE_MOCK_PURCHASING` | Simulated quotation and streamlined demo workflow |
| `ENABLE_BUSY_WEEK_SIMULATION` | Owner-controlled demo consumption |
| `ENABLE_CONTACT_EMAIL`, `OWNER_EMAIL` | Independent contact-form delivery and recipient |

The root `.env.example` is for Compose; `backend/.env.example` is for native development. Frontend `VITE_*` settings are public browser configuration and must contain no secrets. See [AI configuration](docs/AI_CONFIGURATION.md) for provider details.

## Tests

After installing development dependencies, run from the repository root:

```bash
(cd backend && .venv/bin/python -m pytest -q --tb=short)
(cd frontend && node --test tests/*.test.js && npm run build)
```

Backend coverage includes authorization, stock calculations, purchasing decisions, provider fallback, dispatch safeguards and receiving. PostgreSQL concurrency tests require `SHELFSYNC_TEST_DATABASE_URL` pointing to a separate disposable database whose name ends in `_test`; otherwise those checks are skipped. See [setup](docs/SETUP.md#tests-and-builds).

## Demo boundaries

- Simulated quotations are demonstration data, not supplier commitments.
- Mock supplier email is recorded inside the application; it does not send a message to a mailbox.
- Real supplier SMTP requires explicit configuration and opt-in. Contact email is controlled independently.
- Supplier replies are entered through application workflows; incoming emails are not automatically imported.
- Busy Week and simulated receiving mutate their configured database. Run demonstrations against an isolated database.
- Provider access, mailbox delivery and public hosting depend on the configured services and require deployment-specific verification.
