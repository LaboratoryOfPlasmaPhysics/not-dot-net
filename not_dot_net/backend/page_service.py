"""Page service — CRUD for custom markdown pages."""

import uuid

from sqlalchemy import select

from not_dot_net.backend.db import session_scope
from not_dot_net.backend.page_models import Page
from not_dot_net.backend.permissions import permission

MANAGE_PAGES = permission("manage_pages", "Manage pages", "Create/edit/delete custom pages")


async def list_pages(published_only: bool = True) -> list[Page]:
    async with session_scope() as session:
        query = select(Page).order_by(Page.sort_order, Page.title)
        if published_only:
            query = query.where(Page.published == True)  # noqa: E712
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_page(slug: str, published_only: bool = False) -> Page | None:
    async with session_scope() as session:
        query = select(Page).where(Page.slug == slug)
        if published_only:
            query = query.where(Page.published == True)  # noqa: E712
        result = await session.execute(query)
        return result.scalars().first()


async def create_page(
    title: str,
    slug: str,
    content: str,
    author_id: uuid.UUID | None,
    sort_order: int = 0,
    published: bool = False,
) -> Page:
    existing = await get_page(slug)
    if existing is not None:
        raise ValueError(f"Page with slug '{slug}' already exists")

    async with session_scope() as session:
        page = Page(
            title=title,
            slug=slug,
            content=content,
            author_id=author_id,
            sort_order=sort_order,
            published=published,
        )
        session.add(page)
        await session.commit()
        await session.refresh(page)
        return page


_PAGE_MUTABLE = frozenset({"title", "slug", "content", "sort_order", "published"})


async def update_page(page_id: uuid.UUID, **kwargs) -> Page:
    async with session_scope() as session:
        page = await session.get(Page, page_id)
        if page is None:
            raise ValueError(f"Page {page_id} not found")
        for key, value in kwargs.items():
            if key not in _PAGE_MUTABLE:
                raise ValueError(f"Cannot update field '{key}'")
            setattr(page, key, value)
        await session.commit()
        await session.refresh(page)
        return page


async def delete_page(page_id: uuid.UUID) -> None:
    async with session_scope() as session:
        page = await session.get(Page, page_id)
        if page is None:
            raise ValueError(f"Page {page_id} not found")
        await session.delete(page)
        await session.commit()
