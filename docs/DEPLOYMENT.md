# Docker deployment

ShelfSync runs as three Compose services: PostgreSQL, FastAPI and Caddy. Caddy serves the frontend and proxies API requests. Database and certificate data use named volumes; PostgreSQL and FastAPI have no published host ports.

## Local container setup

Install Docker Engine with Compose or Docker Desktop. From the repository root:

```bash
cp -n .env.example .env
chmod 600 .env
```

Edit the root `.env`. Set separate random values for `POSTGRES_PASSWORD` and `JWT_SECRET`. Use a URL-safe database password because Compose constructs the connection URL from it. Keep loopback bindings and mock email enabled for local verification. Root `.env` configures Compose; `backend/.env` configures native local Uvicorn.

Use one consistent project name for all commands:

```bash
docker compose -p shelfsync-local config --quiet
docker compose -p shelfsync-local build
docker compose -p shelfsync-local up -d --wait db
docker compose -p shelfsync-local run --rm --no-deps backend alembic upgrade head
docker compose -p shelfsync-local run --rm --no-deps backend alembic check
docker compose -p shelfsync-local up -d --wait
docker compose -p shelfsync-local ps
```

Open `http://127.0.0.1:8080` and check `/api/health`, `/api/health/db`, sign-in and frontend deep links. The Caddy-served site returns 404 for `/setup`, `/docs`, `/redoc` and `/openapi.json`. Choose unused host ports if another local stack already uses the defaults. To provision a disposable demo, set Owner email and seed passwords in root `.env`, then run `docker compose -p shelfsync-local run --rm --no-deps backend python -m app.db.seed --confirm-demo`. Never seed a production database.

## Public hosting

On a Linux VPS, install Docker and Compose, check out the source and configure a private root `.env`. Keep the same Compose project name across upgrades so existing named volumes remain attached. Use the local checks above before opening public traffic.

Point the hostname's DNS A record at the VPS, allow inbound TCP 80 and 443, and configure:

```dotenv
CADDY_SITE=app.yourdomain.com
HTTP_BIND=0.0.0.0:80
HTTPS_BIND=0.0.0.0:443
```

Replace the example hostname. Caddy manages HTTPS when DNS and network access are configured correctly. Keep PostgreSQL and backend ports private. Verify public HTTPS, HTTP redirection, database health, sign-in and role permissions after startup.

## Mail and providers

Add AI keys only in the backend environment. The [AI configuration guide](AI_CONFIGURATION.md) explains provider selection and fallback.

For supplier SMTP, set `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USERNAME` and `EMAIL_PASSWORD`. Ports 465 and 587 are supported. Purchasing delivery additionally requires `EMAIL_MODE=smtp` and `ENABLE_REAL_EMAIL_DISPATCH=true`. Disable demo purchasing and simulation for live supplier workflows and verify recipient addresses before dispatch. Simulated quotations cannot be dispatched as real supplier orders.

The public contact form has a separate `ENABLE_CONTACT_EMAIL` control and sends to `OWNER_EMAIL`. It can use SMTP while supplier dispatch remains mocked. Mailbox authentication and actual delivery need verification with the configured service. `SENT` records SMTP acceptance; uncertain dispatch outcomes require mailbox reconciliation before another attempt.

Apply environment changes by recreating the relevant service, for example `docker compose -p shelfsync-local up -d --force-recreate backend` for the local project. On the VPS, use its actual project name.

## Upgrades and recovery

Record the deployed revision and migration state. Build and test the candidate release, stop writes for a consistent backup, and take a private PostgreSQL dump before migrating. Rehearse restoration into a separate database. Retain the prior application revision and confirm schema compatibility before rolling back.

The current migration head is `0011_inventory_insight_reports`. Run upgrades explicitly; application startup does not migrate the schema. Keep database credentials consistent with the initialized volume: editing an environment file alone does not change an existing PostgreSQL role password. Never remove production volumes during an update.
