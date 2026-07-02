"""The audit tab must be gated on the declared `view_audit_log` permission —
not on `manage_settings`. Roles are pure config: an admin can define an
"auditor" role, and the shell must honor it like dashboard.py and
workflow_service.can_view_request already do."""

from contextlib import asynccontextmanager

from nicegui.testing import User

from not_dot_net.backend.db import User as DbUser, get_user_db, session_scope
from not_dot_net.backend.roles import RoleDefinition, roles_config
from not_dot_net.backend.schemas import UserCreate
from not_dot_net.backend.users import get_jwt_strategy, get_user_manager
from not_dot_net.frontend.i18n import t


async def _login_with_role(user: User, email: str, role: str) -> DbUser:
    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as manager:
                db_user = await manager.create(UserCreate(email=email, password="secret-pw"))
    async with session_scope() as session:
        row = await session.get(DbUser, db_user.id)
        row.role = role
        await session.commit()
        await session.refresh(row)
        db_user = row
    token = await get_jwt_strategy().write_token(db_user)
    user.http_client.cookies.set("fastapiusersauth", token)
    return db_user


async def _define_roles():
    cfg = await roles_config.get()
    cfg.roles["auditor"] = RoleDefinition(label="Auditor", permissions=["view_audit_log"])
    cfg.roles["plain"] = RoleDefinition(label="Plain", permissions=[])
    await roles_config.set(cfg)


async def test_view_audit_log_role_sees_audit_tab(user: User) -> None:
    await _define_roles()
    await _login_with_role(user, "auditor@not-dot-net.dev", "auditor")
    await user.open("/")
    # Wait for the dashboard panel content (timer-deferred) so page init
    # settles before teardown, like the deeplink tests do.
    await user.should_see(t("no_requests"))
    await user.should_see(t("audit_log"))


async def test_plain_role_has_no_audit_tab(user: User) -> None:
    await _define_roles()
    await _login_with_role(user, "plain@not-dot-net.dev", "plain")
    await user.open("/")
    await user.should_see(t("dashboard"))
    await user.should_not_see(t("audit_log"))
