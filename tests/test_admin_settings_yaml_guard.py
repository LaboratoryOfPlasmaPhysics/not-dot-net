"""The YAML settings editor re-checks permission on save, like the form editor.

Review 2026-10-12 S4: `_render_yaml_editor` save/reset skipped the
`check_permission` that `_render_form` does, so a user revoked while their
tab was open could still rewrite complex config sections.
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.roles import RoleDefinition, RolesConfig, roles_config
from not_dot_net.frontend.admin_settings import _render_yaml_editor


async def test_yaml_save_refused_after_permission_revoked(user: UiUser):
    await roles_config.set(RolesConfig(roles={"admin": RoleDefinition(label="Admin")}))
    async with session_scope() as session:
        root = User(email="yaml-root@test.com", hashed_password="x", is_active=True, is_superuser=True)
        session.add(root)
        await session.commit()
        await session.refresh(root)

    @ui.page("/yaml-guard-test")
    async def page():
        await _render_yaml_editor("roles", roles_config, await roles_config.get(), root)

    await user.open("/yaml-guard-test")
    editor = next(iter(user.find(ui.codemirror).elements))
    editor.value = (
        "default_role: ''\nroles:\n"
        "  admin: {label: Admin}\n"
        "  evil: {label: Evil, permissions: [manage_roles]}\n"
    )

    async with session_scope() as session:
        (await session.get(User, root.id)).is_superuser = False
        await session.commit()

    user.find(marker="yaml-save").click()
    await user.should_see("Permission denied")
    assert set((await roles_config.get()).roles) == {"admin"}
