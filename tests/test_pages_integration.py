"""Integration tests for custom pages feature."""

from nicegui.testing import User

from not_dot_net.backend.page_service import (
    MANAGE_PAGES,
    create_page,
    delete_page,
    get_page,
    list_pages,
    update_page,
)
from not_dot_net.frontend.i18n import t


async def test_full_page_lifecycle():
    page = await create_page(
        title="FAQ", slug="faq", content="## FAQ\n\nNothing yet.",
        author_id=None, published=False,
    )

    # Not visible in published list
    published = await list_pages(published_only=True)
    assert not any(p.slug == "faq" for p in published)

    # Visible in all-pages list
    all_p = await list_pages(published_only=False)
    assert any(p.slug == "faq" for p in all_p)

    # Publish it
    await update_page(page.id, published=True)

    # Now visible
    published = await list_pages(published_only=True)
    assert any(p.slug == "faq" for p in published)

    # Public fetch by slug
    fetched = await get_page("faq", published_only=True)
    assert fetched is not None
    assert fetched.published is True

    # Delete
    await delete_page(page.id)
    assert await get_page("faq") is None


async def test_public_page_route_shows_published_content(user: User):
    await create_page(
        title="Public FAQ",
        slug="public-faq",
        content="## FAQ\n\nVisible to everyone.",
        author_id=None,
        published=True,
    )

    await user.open("/pages/public-faq")
    await user.should_see("Public FAQ")
    await user.should_see("Visible to everyone.")


async def test_public_page_route_hides_draft_content(user: User):
    await create_page(
        title="Private Draft",
        slug="private-draft",
        content="This should stay hidden.",
        author_id=None,
        published=False,
    )

    await user.open("/pages/private-draft")
    await user.should_see(t("page_not_found"))
    await user.should_not_see("This should stay hidden.")


async def test_public_page_route_reflects_publish_and_unpublish(user: User):
    page = await create_page(
        title="Release Notes",
        slug="release-notes-public",
        content="Deployment window tonight.",
        author_id=None,
        published=False,
    )

    await user.open("/pages/release-notes-public")
    await user.should_see(t("page_not_found"))

    await update_page(page.id, published=True)
    await user.open("/pages/release-notes-public")
    await user.should_see("Release Notes")
    await user.should_see("Deployment window tonight.")

    await update_page(page.id, published=False)
    await user.open("/pages/release-notes-public")
    await user.should_see(t("page_not_found"))
    await user.should_not_see("Deployment window tonight.")


async def test_manage_pages_permission_exists():
    from not_dot_net.backend.permissions import get_permissions
    perms = get_permissions()
    assert MANAGE_PAGES in perms
    assert perms[MANAGE_PAGES].label == "Manage pages"
