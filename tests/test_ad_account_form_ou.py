"""The AD account form explains a missing OU list (review 2026-10-12 U12).

With no users_ous configured the OU select was empty and Complete only said
"OU required", with no way to pick one and no hint where to fix it.
"""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.ad_account_config import ad_account_config
from not_dot_net.frontend.workflow_step import _render_ad_account_creation_form


async def _open_form(user: UiUser, path: str, ous: list[str]):
    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={"users_ous": ous}))
    submitted = []

    async def on_submit(action, payload):
        submitted.append(payload)
        ui.notify("form sent")

    @ui.page(path)
    async def page():
        await _render_ad_account_creation_form(None, {"first_name": "Ada", "last_name": "Lovelace"}, on_submit)

    await user.open(path)
    return submitted


async def test_no_configured_ou_says_where_to_fix_it(user: UiUser):
    await _open_form(user, "/_ad_no_ou", [])
    await user.should_see("No OU is configured for new accounts")


async def test_a_single_ou_is_preselected(user: UiUser):
    submitted = await _open_form(user, "/_ad_one_ou", ["OU=Users,DC=lpp,DC=example"])
    user.find("Complete").click()
    await user.should_see("form sent")
    assert submitted and submitted[0]["ou_dn"] == "OU=Users,DC=lpp,DC=example"
