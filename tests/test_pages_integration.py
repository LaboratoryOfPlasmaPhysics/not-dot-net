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


async def _ensure_no_page(slug: str):
    existing = await get_page(slug)
    if existing is not None:
        await delete_page(existing.id)


async def test_full_page_lifecycle():
    await _ensure_no_page("faq")
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
    await _ensure_no_page("public-faq")
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


async def test_public_page_route_missing_page_shows_not_found(user: User):
    await _ensure_no_page("missing-public-page")

    await user.open("/pages/missing-public-page")

    await user.should_see(t("app_name"))
    await user.should_see(t("page_not_found"))


async def test_public_page_route_hides_draft_content(user: User):
    await _ensure_no_page("private-draft")
    await create_page(
        title="Private Draft",
        slug="private-draft",
        content="This should stay hidden.",
        author_id=None,
        published=False,
    )

    await user.open("/pages/private-draft")
    await user.should_see(t("page_not_found"))
    await user.should_not_see("Private Draft")
    await user.should_not_see("This should stay hidden.")


async def test_public_page_route_does_not_normalize_slug(user: User):
    await _ensure_no_page("case-sensitive-page")
    await create_page(
        title="Case Sensitive",
        slug="case-sensitive-page",
        content="Only exact lowercase slug should resolve.",
        author_id=None,
        published=True,
    )

    await user.open("/pages/Case-Sensitive-Page")

    await user.should_see(t("page_not_found"))
    await user.should_not_see("Only exact lowercase slug should resolve.")


async def test_public_page_route_reflects_publish_and_unpublish(user: User):
    await _ensure_no_page("release-notes-public")
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


async def test_public_page_route_reflects_slug_rename(user: User):
    await _ensure_no_page("old-public-slug")
    await _ensure_no_page("new-public-slug")
    page = await create_page(
        title="Renamed Public Page",
        slug="old-public-slug",
        content="Content after rename.",
        author_id=None,
        published=True,
    )

    await user.open("/pages/old-public-slug")
    await user.should_see("Renamed Public Page")

    await update_page(page.id, slug="new-public-slug")

    await user.open("/pages/old-public-slug")
    await user.should_see(t("page_not_found"))
    await user.should_not_see("Content after rename.")

    await user.open("/pages/new-public-slug")
    await user.should_see("Renamed Public Page")
    await user.should_see("Content after rename.")


def test_slugify_generates_valid_page_slug():
    from not_dot_net.frontend.pages import _slugify

    assert _slugify("  My New Page!  ") == "my-new-page"
    assert _slugify("LPP_Intranet   FAQ") == "lpp-intranet-faq"
    assert _slugify("---Already---Dashed---") == "already-dashed"


async def test_manage_pages_permission_exists():
    from not_dot_net.backend.permissions import get_permissions
    perms = get_permissions()
    assert MANAGE_PAGES in perms
    assert perms[MANAGE_PAGES].label == "Manage pages"
