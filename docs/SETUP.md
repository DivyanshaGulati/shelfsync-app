# Local development

Install [Homebrew](https://brew.sh/) and then install the prerequisites. Use Node 22.12 or newer on the Node 22 line, Python 3.12, and PostgreSQL 17. Git is needed when preparing a repository.

```bash
brew install node@22 python@3.12 postgresql@17
export PATH="$(brew --prefix node@22)/bin:$(brew --prefix postgresql@17)/bin:$PATH"
node --version
python3.12 --version
brew services start postgresql@17
```

Add the `PATH` export in each new shell or in your shell profile. Use a separate local database for development and demonstration. The commands below create a fresh database; if these names already exist, choose unused names and update the connection URL accordingly.

```bash
createuser --pwprompt shelfsync
createdb --owner=shelfsync shelfsync
```

The commands use the PostgreSQL administrator role for your current macOS user. If that role differs, use `-U YOUR_PG_ADMIN` with your actual administrator's role.

### Backend

From the repository root, open a terminal and run the following. Copy the example only when creating a new environment.

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp -n .env.example .env
chmod 600 .env
```

Edit `backend/.env`. Set `DATABASE_URL` to your chosen role, password and database. Use a URL-safe database password, or percent-encode special characters in the URL password component. Set `JWT_SECRET` to a separate random signing secret of at least 32 bytes. A private terminal can generate one with `openssl rand -hex 32`. Keep the current JWT secret for an existing local installation if you intend to preserve its active sessions.

Run these commands for a fresh database. For existing data, confirm the target, take a backup and decide whether to upgrade first.

```bash
python -m alembic upgrade head
python -m alembic check
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

From another terminal, `curl -fsS http://127.0.0.1:8000/api/health` checks FastAPI and `curl -fsS http://127.0.0.1:8000/api/health/db` checks database connectivity. Swagger is at `http://127.0.0.1:8000/docs`.

### Frontend

In another terminal, from the repository root, run the following.

```bash
cd frontend
npm ci
cp -n .env.example .env
npm run dev
```

Open `http://127.0.0.1:5173` and use `/setup` to check both connections. Leave `VITE_API_BASE_URL` blank for the Vite development proxy. The optional override is a public API origin. Keep credentials out of all `VITE_*` variables because Vite embeds them in browser assets.

### First sign-in and demo data

An existing database or restored backup retains its user accounts. A freshly migrated database is empty. For a disposable demo database, set `OWNER_EMAIL`, `SEED_OWNER_PASSWORD` and `SEED_EMPLOYEE_PASSWORD` in the backend environment. Use a valid, non-placeholder owner email. Each password must be at least 12 characters. Verify the target database and run `python -m app.db.seed --confirm-demo` from `backend/`. The seed inserts demonstration data and preserves existing user passwords. The application has no self-registration route. A fresh live installation needs approved account and data provisioning or an authorized backup restore.


## Tests and builds

From the repository root, run:

```bash
(cd backend && .venv/bin/python -m pytest -q --tb=short)
(cd frontend && node --test tests/*.test.js && npm run build)
```

PostgreSQL concurrency tests require a separate disposable database whose name ends in `_test`. Create it with `createdb --owner=shelfsync shelfsync_test`, set `SHELFSYNC_TEST_DATABASE_URL` to that database in a private shell, and rerun the backend tests. The fixtures create and remove isolated test schemas. Never use the application database as the test target.

## Optional integrations

See [AI configuration](AI_CONFIGURATION.md) for provider routing and [demo guide](DEMO_GUIDE.md) for simulated purchasing. Keep SMTP disabled while testing with seeded vendor addresses. Set `ENABLE_CONTACT_EMAIL=true` only when configuring the fixed-recipient contact form with working SMTP credentials and `OWNER_EMAIL`.
