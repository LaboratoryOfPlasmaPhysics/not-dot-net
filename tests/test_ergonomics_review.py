"""Fixes from the 2026-11-12 ergonomics review (docs/ux-review-2026-11-12.md)."""

from types import SimpleNamespace

from nicegui import ui
from nicegui.testing import User as UiUser

from not_dot_net.backend.page_service import create_page
from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.config import ServiceEntry, ServicesConfig, services_config
from not_dot_net.frontend import new_request, operations, pages, services
from tests.test_operations import _configure

ADMIN = SimpleNamespace(
    id="00000000-0000-0000-0000-000000000001", email="admin@test",
    is_superuser=True, is_active=True, role="admin", ldap_username=None,
)


async def test_services_with_nothing_configured_do_not_blame_a_search(user: UiUser):
    await services_config.set(ServicesConfig(services=[]))

    @ui.page("/_svc_none")
    def _page():
        services.render(ADMIN)

    await user.open("/_svc_none")
    await user.should_see("No services yet")
    await user.should_not_see("No service matches")


async def test_services_search_without_a_hit_says_so(user: UiUser):
    await services_config.set(ServicesConfig(services=[ServiceEntry(name="Wiki")]))

    @ui.page("/_svc_search")
    def _page():
        services.render(ADMIN)

    await user.open("/_svc_search")
    await user.should_see("Wiki")
    user.find("Search services").type("zzz")
    await user.should_see("No service matches")


async def test_new_request_offers_a_way_back(user: UiUser):
    @ui.page("/_nr_back")
    async def _page():
        await new_request.render(ADMIN)

    await user.open("/_nr_back")
    await user.should_see("What do you need?")
    await user.should_see("Back to dashboard")


async def test_missing_ou_warning_links_to_where_it_is_fixed(user: UiUser):
    await _configure(users_ous=[], mail_dev_mode=False)

    @ui.page("/_ops_link")
    def _page():
        operations.render()

    await user.open("/_ops_link")
    await user.should_see("No user OU is set")
    links = user.find(ui.link).elements
    assert any(link.props.get("href") == "/?tab=ad_accounts" for link in links)


async def test_page_icon_buttons_have_a_name(user: UiUser):
    await create_page(title="Welcome", slug="welcome", content="hi", author_id=None,
                      published=True, actor=SYSTEM_ACTOR)

    @ui.page("/_pages_names")
    def _page():
        pages.render(ADMIN)

    await user.open("/_pages_names")
    await user.should_see("Welcome")
    icon_only = [b for b in user.find(ui.button).elements if not b.text]
    assert icon_only
    assert all(b.props.get("aria-label") for b in icon_only)


async def test_page_editor_does_not_close_on_escape(user: UiUser):
    @ui.page("/_pages_editor")
    async def _page():
        await pages._show_editor(ui.column(), ADMIN)

    await user.open("/_pages_editor")
    dialog = next(iter(user.find(ui.dialog).elements))
    assert "persistent" in dialog.props


def test_page_editor_knows_when_something_was_typed():
    assert not pages._is_dirty(("Title", "body"), ("Title", "body"))
    assert pages._is_dirty(("Title", "body"), ("Title", "body!"))
