"""I1 / E1: the Operations panel and the regrouped navigation."""

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.effect_retry import enqueue_failed_effect
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.workflow_service import create_request
from not_dot_net.frontend.operations import render
from tests.test_operations import _configure
from tests.test_workflow_service import _create_user


async def test_panel_says_all_clear(user: UiUser):
    await _configure(users_ous=["OU=Users,DC=lpp"], mail_dev_mode=False)

    @ui.page("/_ops_clear")
    def _page():
        render()

    await user.open("/_ops_clear")
    await user.should_see("All clear")


async def test_panel_links_each_failed_ad_step_to_its_request(user: UiUser):
    await _configure(users_ous=[], mail_dev_mode=False)
    requester = await _create_user(email="ops-ui@test.com")
    req = await create_request(
        workflow_type="vpn_access", created_by=requester.id,
        data={"target_name": "Anna Müller", "target_email": "anna@x"}, actor=SYSTEM_ACTOR,
    )
    await enqueue_failed_effect(
        request_id=req.id, step_key="approval", action="approve",
        kind="ad_add_to_groups", params={}, error="target has no ldap_dn",
    )

    @ui.page("/_ops_problems")
    def _page():
        render()

    await user.open("/_ops_problems")
    await user.should_see("Anna Müller")
    await user.should_see("target has no ldap_dn")
    await user.should_see("No user OU")
    link = next(iter(user.find(kind=ui.link, content="Anna Müller").elements))
    assert link.props["href"] == f"/workflow/request/{req.id}"
