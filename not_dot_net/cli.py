import asyncio
import os
from contextlib import asynccontextmanager

from cyclopts import App

app = App(name="NotDotNet", version="0.1.0")


@app.command
def serve(
    host: str = "localhost",
    port: int = 8088,
    secrets_file: str = "./secrets.key",
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    seed_fake_users: bool = False,
):
    """Serve the NotDotNet application."""
    from not_dot_net.app import main
    main(
        host, port, secrets_file,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        seed_fake_users=seed_fake_users,
    )


@app.command
def migrate(
    revision: str = "head",
):
    """Run database migrations to the given revision."""
    from not_dot_net.backend.migrate import run_upgrade
    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    run_upgrade(database_url, revision)
    print(f"Migrated to {revision}.")


@app.command
def stamp(
    revision: str = "head",
):
    """Stamp the database with a migration revision without running it."""
    from not_dot_net.backend.migrate import stamp_head, _alembic_config
    from alembic import command
    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    cfg = _alembic_config(database_url)
    command.stamp(cfg, revision)
    print(f"Stamped at {revision}.")


@app.command
def promote(user: str):
    """Grant admin role to a user (match by email, name, or substring)."""
    asyncio.run(_set_role(user, "admin"))


@app.command
def revoke(user: str):
    """Remove admin role from a user (match by email, name, or substring)."""
    asyncio.run(_set_role(user, "member"))


async def _find_user(session, query: str):
    from not_dot_net.backend.db import User
    from sqlalchemy import select, func

    result = await session.execute(select(User).where(User.email == query))
    user = result.scalar_one_or_none()
    if user:
        return user

    pattern = f"%{query}%"
    result = await session.execute(
        select(User).where(
            func.lower(User.email).like(pattern.lower())
            | func.lower(User.full_name).like(pattern.lower())
        )
    )
    matches = result.scalars().all()
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        print(f"Ambiguous — '{query}' matches {len(matches)} users:")
        for m in matches:
            print(f"  {m.email}  ({m.full_name or '-'})")
        raise SystemExit(1)
    return None


async def _set_role(query: str, role: str):
    from not_dot_net.backend.db import init_db, session_scope

    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    init_db(database_url)

    async with session_scope() as session:
        user = await _find_user(session, query)
        if not user:
            print(f"Error: no user matching '{query}'.")
            raise SystemExit(1)
        old_role = user.role
        user.role = role
        user.is_superuser = (role == "admin")
        await session.commit()
        print(f"'{user.email}' ({user.full_name or '-'}): {old_role or '(none)'} → {role}")


@app.command
def test_ldap(username: str, password: str):
    """Test LDAP authentication and print the result."""
    asyncio.run(_test_ldap(username, password))


async def _test_ldap(username: str, password: str):
    from not_dot_net.backend.db import init_db
    from not_dot_net.backend.app_config import AppSetting  # noqa: F401 — register model
    from not_dot_net.backend.auth.ldap import (
        ldap_config, ldap_authenticate, get_ldap_connect,
        default_ldap_connect,
    )

    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    init_db(database_url)

    cfg = await ldap_config.get()
    print(f"LDAP config: url={cfg.url} effective_url={cfg.effective_url} domain={cfg.domain}")
    print(f"  base_dn={cfg.base_dn} port={cfg.port} tls_mode={cfg.tls_mode}")
    print(f"  tls_verify={cfg.tls_verify}")
    print(f"  auto_provision={cfg.auto_provision} user_filter={cfg.user_filter!r}")

    print(f"\nStep 1: LDAP bind as '{username}@{cfg.domain}'...")
    try:
        conn = default_ldap_connect(cfg, username, password)
        print(f"  Bind OK — server: {conn.server.host}:{conn.server.port}")
        print(f"  TLS: {conn.tls_started}, bound: {conn.bound}")
        conn.unbind()
    except Exception as exc:
        print(f"  Bind FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)

    print(f"\nStep 2: manual search...")
    from ldap3 import SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    conn = default_ldap_connect(cfg, username, password)
    safe_user = escape_filter_chars(username)
    search_filter = f"(sAMAccountName={safe_user})"
    if cfg.user_filter:
        search_filter = f"(&{search_filter}{cfg.user_filter})"
    print(f"  base_dn: {cfg.base_dn}")
    print(f"  filter:  {search_filter}")
    ok = conn.search(cfg.base_dn, search_filter, search_scope=SUBTREE, attributes=["*"])
    print(f"  search returned: {ok}, entries: {len(conn.entries)}, result: {conn.result}")
    if conn.entries:
        entry = conn.entries[0]
        print(f"  DN: {entry.entry_dn}")
        print(f"  Attributes: {entry.entry_attributes_as_dict}")
    else:
        print("  No entries found. Trying without user_filter...")
        ok2 = conn.search(cfg.base_dn, f"(sAMAccountName={safe_user})", search_scope=SUBTREE, attributes=["*"])
        print(f"  search returned: {ok2}, entries: {len(conn.entries)}, result: {conn.result}")
        if conn.entries:
            entry = conn.entries[0]
            print(f"  DN: {entry.entry_dn}")
            print(f"  objectClass: {entry.entry_attributes_as_dict.get('objectClass', '?')}")
    conn.unbind()

    print(f"\nStep 3: full auth flow...")
    result = ldap_authenticate(username, password, cfg, get_ldap_connect())
    if result is None:
        print("  Auth+search returned None — check Step 2 output above.")
        raise SystemExit(1)
    print(f"Success:")
    print(f"  email: {result.email}")
    print(f"  dn: {result.dn}")
    print(f"  full_name: {result.full_name}")
    print(f"  phone: {result.phone}")
    print(f"  office: {result.office}")
    print(f"  title: {result.title}")
    print(f"  department: {result.department}")
    print(f"  company: {result.company}")
    print(f"  description: {result.description}")
    print(f"  webpage: {result.webpage}")
    print(f"  uid_number: {result.uid_number}")
    print(f"  gid_number: {result.gid_number}")
    print(f"  member_of: {result.member_of}")
    print(f"  photo: {'yes (' + str(len(result.photo)) + ' bytes)' if result.photo else 'no'}")


@app.command
def drop_user(user: str):
    """Delete a single non-admin user (match by email, name, or substring)."""
    asyncio.run(_drop_single_user(user))


async def _drop_single_user(query: str):
    from not_dot_net.backend.db import init_db, session_scope

    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    init_db(database_url)

    async with session_scope() as session:
        user = await _find_user(session, query)
        if not user:
            print(f"Error: no user matching '{query}'.")
            raise SystemExit(1)
        if user.role == "admin":
            print(f"Refusing to delete admin '{user.email}'.")
            raise SystemExit(1)
        email, name = user.email, user.full_name or "-"
        await session.delete(user)
        await session.commit()
        print(f"Deleted '{email}' ({name}).")


@app.command
def drop_users():
    """Delete all non-admin users from the database."""
    asyncio.run(_drop_users())


async def _drop_users():
    from not_dot_net.backend.db import init_db, session_scope, User
    from sqlalchemy import select, delete

    database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    init_db(database_url)

    async with session_scope() as session:
        result = await session.execute(select(User).where(User.role != "admin"))
        victims = result.scalars().all()
        if not victims:
            print("No non-admin users to delete.")
            return
        for u in victims:
            print(f"  deleting {u.email} ({u.full_name or '-'}, role={u.role or '(none)'})")
        await session.execute(delete(User).where(User.role != "admin"))
        await session.commit()
        print(f"Deleted {len(victims)} user(s).")


@app.command
def create_user(
    username: str,
    password: str,
    role: str = "member",
    secrets_file: str = "./secrets.key",
):
    """Create a new user."""
    async def _create():
        from pathlib import Path
        from not_dot_net.backend.db import init_db, create_db_and_tables, session_scope, get_user_db
        from not_dot_net.backend.secrets import load_or_create
        from not_dot_net.backend.users import get_user_manager, init_user_secrets
        from not_dot_net.backend.schemas import UserCreate
        database_url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
        dev_mode = "DATABASE_URL" not in os.environ

        init_db(database_url)
        secrets = load_or_create(Path(secrets_file), dev_mode=dev_mode)
        init_user_secrets(secrets)
        await create_db_and_tables()

        async with session_scope() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                    user = await user_manager.create(
                        UserCreate(
                            email=username,
                            password=password,
                            is_active=True,
                            is_superuser=(role == "admin"),
                        )
                    )
                    user.role = role
                    session.add(user)
                    await session.commit()
                    print(f"User '{user.email}' created with role '{role}'.")

    asyncio.run(_create())


if __name__ in {"__main__", "__mp_main__"}:
    app()
