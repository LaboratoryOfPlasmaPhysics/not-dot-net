# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this

**not-dot-net** is a simple intranet application for LPP (Laboratoire de Physique des Plasmas). It uses NiceGUI for the frontend, FastAPI-Users for authentication (local + LDAP), and SQLAlchemy with async SQLite for persistence. Configuration is via pydantic-settings with YAML files.

## Commands

```bash
# Install (uses flit, uv recommended)
uv pip install -e .

# Run the app
uv run python -m not_dot_net.cli serve --host localhost --port 8000 --env-file config.yaml

# Create a user
uv run python -m not_dot_net.cli create-user <email> <password> --env-file config.yaml

# Dump default config
uv run python -m not_dot_net.cli default-config

# Run tests (uses nicegui testing plugin)
uv run pytest
```

## Architecture

### Startup flow

`cli.py serve` → `app.main()` → `create_app(config_file)`:
1. `init_settings()` loads YAML + env config into module-level singleton
2. `init_db()` creates async engine + session maker at module level
3. `app.on_startup(create_db_and_tables)` schedules table creation
4. FastAPI-Users routers are included (`/auth/jwt`, `/auth/cookie`, `/users`)
5. Custom auth routers are included (`/auth/local`, `/auth/ldap`, `/auth/register`)
6. Frontend pages are set up (`/login`, `/user/profile`)

### Module-level dependency injection

Following idiomatic FastAPI-Users patterns, dependencies are module-level:
- `backend/db.py`: `get_async_session()`, `get_user_db()` — async generators for `Depends()`
- `backend/users.py`: `get_user_manager()`, `current_active_user`, `current_active_user_optional`

Both `db.py` and `config.py` use `init_*()` functions that must be called before dependencies are usable. `create_app()` handles this.

### Auth endpoints

`backend/auth/` contains APIRouter-based endpoints:
- `local.py`: POST `/auth/local` (password login)
- `ldap.py`: POST `/auth/ldap` (LDAP bind + JWT)

### Frontend pages

NiceGUI pages in `frontend/` expose a `setup()` function that registers `@ui.page` routes. They import dependencies directly from `backend/users.py`.

`login.py` posts to `/auth/cookie/login` via JS fetch — the browser receives an httponly cookie via `Set-Cookie` header. No server-side DI escape hatch needed.

### Configuration

`config.py` uses nested Pydantic models under a single `BaseSettings` root. Sources: init args > env vars > YAML file. JWT secret is in `Settings.jwt_secret`.

### Testing

Tests use `nicegui.testing.User` (configured via pytest plugin in pyproject.toml: `-p nicegui.testing.user_plugin`). The test entry point is `main_file = "app.py"` in `[tool.pytest]`.
