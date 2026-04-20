"""Tests for custom page CRUD."""

import pytest

from not_dot_net.backend.page_models import Page
from not_dot_net.backend.page_service import (
    MANAGE_PAGES,
    create_page,
    delete_page,
    get_page,
    list_pages,
    update_page,
)


async def test_page_model_exists():
    p = Page(
        title="Hello",
        slug="hello",
        content="# Hello\nWorld",
        author_id=None,
    )
    assert p.title == "Hello"
    assert p.slug == "hello"
    assert p.published is False
    assert p.sort_order == 0


async def test_create_and_get_page():
    page = await create_page(
        title="Welcome", slug="welcome", content="# Welcome\nHello!", author_id=None,
    )
    assert page.id is not None
    assert page.slug == "welcome"
    assert page.published is False

    fetched = await get_page("welcome")
    assert fetched is not None
    assert fetched.title == "Welcome"


async def test_get_page_not_found():
    result = await get_page("nonexistent")
    assert result is None


async def test_get_page_published_only_hides_draft():
    await create_page(
        title="Draft",
        slug="draft-hidden",
        content="secret",
        author_id=None,
        published=False,
    )

    assert await get_page("draft-hidden", published_only=True) is None


async def test_get_page_published_only_returns_published_page():
    await create_page(
        title="Public",
        slug="public-visible",
        content="visible",
        author_id=None,
        published=True,
    )

    fetched = await get_page("public-visible", published_only=True)
    assert fetched is not None
    assert fetched.slug == "public-visible"
    assert fetched.published is True


async def test_list_pages_published_only():
    await create_page(title="Draft", slug="draft", content="x", author_id=None)
    await create_page(
        title="Public", slug="public", content="y", author_id=None, published=True,
    )
    published = await list_pages(published_only=True)
    assert all(p.published for p in published)
    assert any(p.slug == "public" for p in published)
    assert not any(p.slug == "draft" for p in published)

    all_pages = await list_pages(published_only=False)
    slugs = [p.slug for p in all_pages]
    assert "draft" in slugs
    assert "public" in slugs


async def test_list_pages_sort_order():
    await create_page(title="B", slug="b-page", content="", author_id=None, sort_order=2, published=True)
    await create_page(title="A", slug="a-page", content="", author_id=None, sort_order=1, published=True)
    pages = await list_pages(published_only=True)
    slugs = [p.slug for p in pages]
    assert slugs.index("a-page") < slugs.index("b-page")


async def test_update_page():
    page = await create_page(title="Old", slug="upd", content="old", author_id=None)
    updated = await update_page(page.id, title="New", content="new")
    assert updated.title == "New"
    assert updated.content == "new"
    assert updated.slug == "upd"


async def test_update_page_publication_changes_public_visibility():
    page = await create_page(
        title="Release Notes",
        slug="release-notes",
        content="v1",
        author_id=None,
        published=False,
    )

    assert await get_page("release-notes", published_only=True) is None

    await update_page(page.id, published=True)
    assert await get_page("release-notes", published_only=True) is not None

    await update_page(page.id, published=False)
    assert await get_page("release-notes", published_only=True) is None
    assert await get_page("release-notes", published_only=False) is not None


async def test_page_content_round_trip_preserves_markdown_and_html_like_text():
    content = "# Hello\n\n<script>alert('xss')</script>\n\n**bold**"
    page = await create_page(
        title="Content",
        slug="content-check",
        content=content,
        author_id=None,
        published=True,
    )

    fetched = await get_page("content-check")
    assert fetched is not None
    assert fetched.content == content

    updated = await update_page(page.id, content=content + "\n\n<p>raw html</p>")
    assert updated.content.endswith("<p>raw html</p>")


async def test_delete_page():
    page = await create_page(title="Bye", slug="bye", content="", author_id=None)
    await delete_page(page.id)
    assert await get_page("bye") is None


async def test_create_duplicate_slug_raises():
    await create_page(title="One", slug="dup", content="", author_id=None)
    with pytest.raises(ValueError, match="slug"):
        await create_page(title="Two", slug="dup", content="", author_id=None)


@pytest.mark.parametrize(
    "slug",
    ["../admin", "hello world", "hello/world", "hello?", "-hello", "Hello"],
)
async def test_create_page_rejects_invalid_slug(slug: str):
    with pytest.raises(ValueError, match="slug"):
        await create_page(title="Invalid", slug=slug, content="", author_id=None)


async def test_update_page_rejects_invalid_slug():
    page = await create_page(title="Page", slug="valid-slug", content="", author_id=None)

    with pytest.raises(ValueError, match="slug"):
        await update_page(page.id, slug="not valid")


async def test_update_page_duplicate_slug_raises():
    first = await create_page(title="One", slug="one", content="", author_id=None)
    await create_page(title="Two", slug="two", content="", author_id=None)

    with pytest.raises(ValueError, match="slug"):
        await update_page(first.id, slug="two")


async def test_update_page_rejects_immutable_fields():
    page = await create_page(title="One", slug="immutable", content="", author_id=None)

    with pytest.raises(ValueError, match="Cannot update field"):
        await update_page(page.id, author_id=None)


async def test_manage_pages_permission_registered():
    from not_dot_net.backend.permissions import get_permissions
    assert MANAGE_PAGES in get_permissions()
