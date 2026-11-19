# Architecture

ShelfSync separates browser interaction, HTTP authorization, business services and persistence.

| Layer | Responsibility |
| --- | --- |
| React pages and shared frontend libraries | Display stock, quotations, purchasing decisions and delivery status; submit authenticated API requests |
| FastAPI routes | Validate request models, enforce roles and invoke application services |
| Core services | Forecast demand, create replenishment requests, compare quotes, validate purchasing decisions, dispatch mail and reconcile receipts |
| SQLAlchemy models and Alembic | Define persistence, constraints and versioned schema upgrades |
| PostgreSQL | Persist transactions and enforce row locks, uniqueness and integrity constraints |
| Compose and Caddy | Run the application services, serve browser assets and route same-origin API traffic |

## Stock and purchasing

Checkout records consumption and updates inventory within a database transaction. Forecasting and replenishment reuse that transaction. Conditional stock updates and PostgreSQL row locks protect concurrent deductions. Idempotency keys prevent the same checkout or receipt from applying twice.

Stock health distinguishes physical On Hand from outstanding On Order. Reorder calculations can account for inventory position, but a pending delivery does not replenish physical stock. Receiving updates On Hand and the outstanding order quantity together.

The detailed purchasing workflow collects fresh quotations, compares them deterministically and applies backend authorization rules or an Owner decision. Approval revalidates the selected quotation. `PLACED` represents internal authorization; email dispatch has its own audit state.

The streamlined mock purchasing workflow is a separate, explicitly enabled demo path. It uses simulated catalogue quotations, validates an advisory AI selection and applies its own INR 1,00,000 approval threshold. The detailed workflow uses configured `AUTO_ORDER_LIMIT` (default INR 2,000) and additional routine-purchase checks. These are distinct paths; the mock threshold does not describe live supplier purchasing.

## AI boundary

Claude, Groq and optional Ollama integrations provide bounded advisory responses. The backend supplies inventory and quotation context and validates model output. AI does not receive a generic database-write or email-send tool. Authentication, eligibility, authorization, dispatch and receipt mutations remain backend responsibilities. Model configuration and fallback are described in [AI configuration](AI_CONFIGURATION.md).

## Email boundary

RFQ and final-order dispatch default to mock mode. Real supplier delivery requires the SMTP configuration and explicit purchasing opt-in. Simulated quotations cannot be sent as real supplier orders. A durable dispatch reservation is committed before SMTP; uncertain outcomes require manual reconciliation rather than automatic retries. SMTP acceptance does not guarantee inbox delivery.

The public contact endpoint uses a fixed Owner recipient, a honeypot, input validation and in-process limits. `ENABLE_CONTACT_EMAIL` is independent of purchasing dispatch. The current limiter is local to one backend process and is not a distributed rate limiter.

## Operating limits

- Incoming supplier emails are not automatically parsed or imported.
- Quotation collection and expiry use explicit application actions, not a background scheduler.
- Busy Week and simulated delivery mutate the configured database and belong in an isolated demo.
- Provider availability and mailbox delivery require separate live verification.
- Python dependencies are declared in the backend requirements files; frontend dependencies use an npm lockfile.

