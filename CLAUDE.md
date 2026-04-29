# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this

**not-dot-net** is the LPP (Laboratoire de Physique des Plasmas) intranet. NiceGUI frontend, FastAPI-Users for cookie-backed auth (local + LDAP/AD), SQLAlchemy 2.x async with PostgreSQL in production and SQLite in dev. Configuration is DB-backed via a typed `ConfigSection` registry; secrets live in a separate local file.

## Commands

```bash
# Install (uv recommended)
uv pip install -e .

# Serve (dev mode is auto-detected: no DATABASE_URL → SQLite + auto-admin)
uv run python -m not_dot_net.cli serve --host localhost --port 8088

# Production: export DATABASE_URL first, then serve
DATABASE_URL=postgresql+asyncpg://... \
  uv run python -m not_dot_net.cli serve \
    --secrets-file /secrets/secrets.key \
    --ssl-certfile /tls/cert.pem --ssl-keyfile /tls/key.pem

# Migrations (production)
uv run python -m not_dot_net.cli migrate           # → head
uv run python -m not_dot_net.cli stamp head        # mark migrations as applied

# User admin
uv run python -m not_dot_net.cli create-user <email> <password> --role admin
uv run python -m not_dot_net.cli promote <email|name>
uv run python -m not_dot_net.cli revoke <email|name>
uv run python -m not_dot_net.cli drop-user <email|name>
uv run python -m not_dot_net.cli drop-users        # nukes all non-admins

# LDAP diagnostics
uv run python -m not_dot_net.cli test-ldap <username> <password>

# Tests (NiceGUI testing plugin)
uv run pytest
```

## Architecture

### Bootstrap

`cli.py serve` → `app.main()` → `app.create_app(secrets_file)`:
1. `dev_mode` is determined by `"DATABASE_URL" not in os.environ`.
2. `init_db(database_url)` creates the async engine + session maker.
3. `load_or_create(secrets_file, dev_mode)` reads or generates `secrets.key` (JWT, storage, file_encryption).
4. `init_user_secrets(secrets)` and `set_dev_mode(dev_mode)` wire FastAPI-Users (the latter flips `cookie_secure`).
5. Production: `run_upgrade(database_url)` runs Alembic migrations synchronously *before* the event loop. Dev: `create_db_and_tables` (`Base.metadata.create_all`) on startup.
6. NiceGUI page setups (`login`, `shell`, `workflow_token`, `workflow_detail`, `public_pages`; `setup_wizard` only in production).

### No public REST API

All FastAPI-Users HTTP routers (`/users/*`, `/auth/jwt/*`, `/auth/cookie/*`) and the custom `/auth/local` JWT endpoint were removed — `PATCH /users/me` accepted `{"role":"admin"}` and FastAPI-Users only strips `is_superuser`/`is_active`/`is_verified`. The `cookie_backend` + `current_active_user` dependency are kept to power NiceGUI auth.

The remaining HTTP surface is:
- `POST /auth/login`, `GET /logout` (`frontend/login.py`)
- `GET /workflow/token/{token}` (`frontend/workflow_token.py`)
- `GET /workflow/request/{id}` (`frontend/workflow_detail.py`)
- `GET /pages/{slug}` (`frontend/public_page.py`)
- The NiceGUI shell at `/`

### Module-level dependency injection

Following idiomatic FastAPI-Users:
- `backend/db.py`: `init_db()`, `get_async_session()`, `get_user_db()`, `session_scope()` (for non-DI services).
- `backend/users.py`: `init_user_secrets()`, `set_dev_mode()`, `get_user_manager()`, `current_active_user`, `current_active_user_optional`.

Both must be initialized before dependencies are usable. `create_app()` orchestrates the order.

### Configuration & secrets

- `backend/app_config.py`: `ConfigSection[T]` generic backed by an `app_setting` JSON row per prefix. Each module declares its schema (`section("ldap", LdapConfig, ...)`, etc.). Admin UI at `/` → Settings auto-renders forms from the registry.
- `backend/secrets.py`: `AppSecrets` lives in a 0o600 JSON file (`secrets.key` by default). Library functions raise (`FileNotFoundError`, `RuntimeError`); `app.create_app` translates failures into `SystemExit(1)`.
- The only env var is `DATABASE_URL` (and optional `ALLOWED_ORIGINS` for Socket.IO CORS).

### Workflow tokens

- `WorkflowRequest.token` is a UUID4 string. `submit_step` regenerates it whenever the next step is assigned to `target_person`; the matching email goes out via `_fire_notifications` / `_send_token_link`.
- The `WorkflowEvent.actor_token` column was dropped (migration 0009) — `save_draft` had been persisting the cleartext target_person token in every event row, leaking it to anyone with audit-log access.
- Regenerating a token resets `verification_code_hash` so the previous code can't be reused on the new URL.

### Email handling

Emails are normalized to lowercase on write (e.g. `target_email`); auth-relevant comparisons (`target_person` step, actionable filters) use case-insensitive lookups. AD-provisioned users keep whatever case AD returned, but lookups still match.

### Encrypted file storage

`backend/encrypted_storage.py` — AES-256-GCM envelope encryption per file, DEK wrapped with the master key from `secrets.key`. `access_personal_data` permission gates download. `mark_for_retention` schedules eventual deletion.

Plain-file workflow uploads land in `data/uploads/<request_id>/`. `_safe_upload_path` validates the resolved path stays under `UPLOAD_ROOT` before serving — guards against corrupted DB rows pointing at arbitrary paths.

### Frontend pages

NiceGUI pages in `frontend/` expose a `setup()` or `render(user)` that registers `@ui.page` routes. They import dependencies from `backend/users.py` and use `check_permission` for callback-level guards.

### Audit log

`list_audit_events()` returns `AuditEventView` DTOs (not the ORM rows) — resolved display names live in `actor_display`/`target_display`, the persisted `actor_id`/`actor_email`/`target_id` columns are never overwritten. Non-UUID actor/target ids are tolerated (skipped during resolution rather than crashing).

### Testing

Tests use `nicegui.testing.User` (configured via pytest plugin in `pyproject.toml`: `-p nicegui.testing.user_plugin`). The test entry point is `not_dot_net/app.py`. Shared `conftest.py` provides an autouse in-memory SQLite fixture and dev secrets.
