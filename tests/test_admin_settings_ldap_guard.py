"""Only a superuser edits the LDAP section (review 2026-10-12 S9).

Whoever sets the LDAP url receives every login password the intranet relays
to AD. `manage_settings` is a delegable role permission, so it must not be
enough to repoint it.
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.auth.ldap import LdapConfig, ldap_config
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config
from not_dot_net.frontend.admin_settings import _render_form


async def _settings_admin() -> User:
    await roles_config.set(RolesConfig(roles={
        "settings_admin": RoleDefinition(label="Settings admin", permissions=["manage_settings"]),
    }))
    async with session_scope() as session:
        admin = User(email="settings-admin@test.com", hashed_password="x", is_active=True, role="settings_admin")
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    return admin


async def test_settings_admin_cannot_repoint_ldap(user: UiUser):
    await ldap_config.set(LdapConfig(url="ldaps://dc.lpp.example"))
    admin = await _settings_admin()

    @ui.page("/ldap-guard")
    async def page():
        await _render_form("ldap", ldap_config, await ldap_config.get(), admin)

    await user.open("/ldap-guard")
    url_input = next(iter(user.find(kind=ui.input, marker="url").elements))
    url_input.value = "ldap://attacker.example"
    user.find(marker="form-save").click()
    await user.should_see("Permission denied")
    assert (await ldap_config.get()).url == "ldaps://dc.lpp.example"
