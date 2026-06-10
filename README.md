# not-dot-net

not-dot-net is the LPP intranet application for internal directory, workflows,
resource bookings, custom pages, audit logs, and LDAP/AD-backed account
management.

It is built with:

- NiceGUI for the frontend
- FastAPI-Users for cookie-based authentication
- SQLAlchemy 2.x async ORM
- PostgreSQL in production
- SQLite in development
- Alembic for production migrations

## Features

- People directory with LDAP/AD synchronization and local profile data
- Local password and LDAP/AD authentication
- Role-based access control with configurable permission sets
- Configurable multi-step workflows with approvals, corrections, file uploads,
  notifications, and AD effects
- Token-based workflow pages for external or target-person steps
- Resource booking system with conflict detection, setup lead time, OS/software
  selections, and reminder emails
- Custom markdown pages with public published routes
- Audit log for authentication, workflow, booking, page, and admin actions
- Durable mail outbox with background delivery worker
- Encrypted storage for sensitive workflow documents
- English/French UI with startup validation of translation keys

## Requirements

- Python 3.10+
- `uv` recommended
- PostgreSQL for production
- SQLite for development when `DATABASE_URL` is not set

## Development

Development mode is selected by the absence of `DATABASE_URL`.

In development, the app uses `sqlite+aiosqlite:///./dev.db`, creates tables
automatically, and creates a default admin account:

- Email: `admin@not-dot-net.dev`
- Password: `admin`

Start the app:

```bash
uv run python -m not_dot_net.cli serve
```

Start the auto-reload development entry point:

```bash
uv run not_dot_net/_dev.py
```

Start with fake development data:

```bash
uv run not_dot_net/_dev.py --seed-fake-users
```

or:

```bash
uv run python -m not_dot_net.cli serve --seed-fake-users
```

## Production

Production mode is selected by setting `DATABASE_URL`.

Example:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/not_dot_net \
  uv run python -m not_dot_net.cli serve \
  --secrets-file /secrets/secrets.key
```

With TLS files:

```bash
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/not_dot_net \
  uv run python -m not_dot_net.cli serve \
  --secrets-file /secrets/secrets.key \
  --ssl-certfile /tls/cert.pem \
  --ssl-keyfile /tls/key.pem
```

In production, `secrets.key` must already exist. Missing secrets cause startup
to fail.

Production startup runs Alembic migrations before the NiceGUI event loop starts.

## Configuration

The runtime environment uses only:

| Variable | Description |
|---|---|
| `DATABASE_URL` | Async SQLAlchemy database URL. If absent, development mode is used. |
| `ALLOWED_ORIGINS` | Optional comma-separated Socket.IO CORS allow-list. If absent in production, same-origin only is enforced. |

Application settings are stored in the database through typed config sections.
They are edited from the admin settings UI and registered in code with
`section(...)`.

Main config sections include:

- `org`
- `files`
- `bookings`
- `dashboard`
- `workflows`
- `ldap`
- `mail`
- `roles`
- `ad_account`

Secrets are stored separately in `secrets.key` and include JWT, NiceGUI storage,
and file-encryption secrets.

## Database

Development uses automatic table creation:

```bash
uv run python -m not_dot_net.cli serve
```

Production uses Alembic migrations:

```bash
DATABASE_URL=postgresql+asyncpg://... uv run python -m not_dot_net.cli migrate
```

To stamp a database without running migrations:

```bash
DATABASE_URL=postgresql+asyncpg://... uv run python -m not_dot_net.cli stamp --revision head
```

The migration history currently skips `0014`; existing databases stamped at that
removed revision should be corrected with `stamp --revision head`.

## CLI

Start the server:

```bash
uv run python -m not_dot_net.cli serve
```

Run migrations:

```bash
uv run python -m not_dot_net.cli migrate
```

Create a user:

```bash
uv run python -m not_dot_net.cli create-user user@example.org password
```

Create a superuser:

```bash
uv run python -m not_dot_net.cli create-user admin@example.org password --superuser
```

Promote or revoke superuser status:

```bash
uv run python -m not_dot_net.cli promote user@example.org
uv run python -m not_dot_net.cli revoke user@example.org
```

Delete users:

```bash
uv run python -m not_dot_net.cli drop-user user@example.org
uv run python -m not_dot_net.cli drop-users
```

Test LDAP authentication:

```bash
uv run python -m not_dot_net.cli test-ldap username password
```

## HTTP surface

This application does not expose the default FastAPI-Users REST routers.

The remaining explicit HTTP routes are:

- `POST /auth/login`
- `GET /logout`
- `GET /workflow/token/{token}`
- `GET /workflow/request/{id}`
- `GET /pages/{slug}`
- `/` for the NiceGUI application shell

## Tests

Run the full test suite:

```bash
uv run pytest
```

Run a focused test file:

```bash
uv run pytest tests/test_workflow_service.py
```

Tests use the NiceGUI testing plugin and an in-memory SQLite database with
foreign-key enforcement.

## Development notes

- Model and config registration depends on side-effect imports. Do not remove
  imports marked with `# noqa: F401` without checking table/config registration.
- Dev mode uses `Base.metadata.create_all`; production uses Alembic. A stale
  local `dev.db` can cause errors that are not production regressions.
- Mail is queued in `mail_outbox` and delivered by a background worker. In
  development, mail can be logged instead of sent.
- LDAP connections are cached per user and cleaned by a background reaper.
- CSRF middleware exists but is currently disabled because of NiceGUI ASGI
  compatibility issues.
- Translation keys are validated at startup.

## License

MIT
