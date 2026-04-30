"""Import/export pages and booking resources as JSON."""

import asyncio
from datetime import datetime, UTC

from sqlalchemy import select

from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import session_scope, User
from not_dot_net.backend.page_models import Page
from not_dot_net.backend.tenure_service import UserTenure


def _iter_import_items(data) -> tuple[list[dict], int]:
    if not isinstance(data, list):
        return [], 1
    skipped = sum(1 for item in data if not isinstance(item, dict))
    return [item for item in data if isinstance(item, dict)], skipped


def _clean_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _serialize_page(p: Page) -> dict:
    return {
        "title": p.title,
        "slug": p.slug,
        "content": p.content,
        "sort_order": p.sort_order,
        "published": p.published,
    }


def _serialize_resource(r: Resource) -> dict:
    return {
        "name": r.name,
        "resource_type": r.resource_type,
        "description": r.description,
        "location": r.location,
        "specs": r.specs,
        "active": r.active,
    }


async def export_pages() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(select(Page).order_by(Page.sort_order, Page.title))
        return [_serialize_page(p) for p in result.scalars().all()]


async def export_resources() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(select(Resource).order_by(Resource.name))
        return [_serialize_resource(r) for r in result.scalars().all()]


def _serialize_tenure(t: UserTenure, email: str) -> dict:
    return {
        "user_email": email,
        "status": t.status,
        "employer": t.employer,
        "start_date": t.start_date.isoformat(),
        "end_date": t.end_date.isoformat() if t.end_date else None,
        "notes": t.notes,
    }


async def export_tenures() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(
            select(UserTenure).order_by(UserTenure.user_id, UserTenure.start_date)
        )
        tenures = result.scalars().all()
        user_ids = {t.user_id for t in tenures}
        if user_ids:
            users_result = await session.execute(
                select(User.id, User.email).where(User.id.in_(user_ids))
            )
            email_map = {uid: email for uid, email in users_result.all()}
        else:
            email_map = {}
        return [_serialize_tenure(t, email_map.get(t.user_id, "unknown")) for t in tenures]


async def export_all() -> dict:
    pages, resources, tenures = await asyncio.gather(
        export_pages(), export_resources(), export_tenures(),
    )
    return {
        "version": 1,
        "exported_at": datetime.now(UTC).isoformat(),
        "pages": pages,
        "resources": resources,
        "tenures": tenures,
    }


async def import_pages(data: list[dict], *, replace: bool = False) -> dict[str, int]:
    items, skipped = _iter_import_items(data)
    created, updated = 0, 0
    async with session_scope() as session:
        for item in items:
            slug = _clean_text(item.get("slug"))
            title = _clean_text(item.get("title"))
            if not slug or not title:
                skipped += 1
                continue
            existing = (await session.execute(
                select(Page).where(Page.slug == slug)
            )).scalar_one_or_none()
            if existing:
                if replace:
                    existing.title = item.get("title", existing.title)
                    existing.content = item.get("content", existing.content)
                    existing.sort_order = item.get("sort_order", existing.sort_order)
                    existing.published = item.get("published", existing.published)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Page(
                    title=item["title"],
                    slug=slug,
                    content=item.get("content", ""),
                    sort_order=item.get("sort_order", 0),
                    published=item.get("published", False),
                ))
                created += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def import_resources(data: list[dict], *, replace: bool = False) -> dict[str, int]:
    items, skipped = _iter_import_items(data)
    created, updated = 0, 0
    async with session_scope() as session:
        for item in items:
            name = _clean_text(item.get("name"))
            if not name:
                skipped += 1
                continue
            existing = (await session.execute(
                select(Resource).where(Resource.name == name)
            )).scalar_one_or_none()
            if existing:
                if replace:
                    existing.resource_type = item.get("resource_type", existing.resource_type)
                    existing.description = item.get("description", existing.description)
                    existing.location = item.get("location", existing.location)
                    existing.specs = item.get("specs", existing.specs)
                    existing.active = item.get("active", existing.active)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Resource(
                    name=name,
                    resource_type=item.get("resource_type", "desktop"),
                    description=item.get("description"),
                    location=item.get("location"),
                    specs=item.get("specs"),
                    active=item.get("active", True),
                ))
                created += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def import_tenures(data: list[dict], *, replace: bool = False) -> dict[str, int]:
    from datetime import date as dt_date
    from not_dot_net.backend.tenure_service import _ensure_no_overlap, _validate_tenure_dates

    items, skipped = _iter_import_items(data)
    created, updated = 0, 0
    async with session_scope() as session:
        for item in items:
            email = _clean_text(item.get("user_email"))
            if not email or not item.get("status") or not item.get("employer") or not item.get("start_date"):
                skipped += 1
                continue
            user_result = await session.execute(
                select(User).where(User.email == email)
            )
            user = user_result.scalar_one_or_none()
            if user is None:
                skipped += 1
                continue
            try:
                start_date = dt_date.fromisoformat(item["start_date"])
                end_date = dt_date.fromisoformat(item["end_date"]) if item.get("end_date") else None
                _validate_tenure_dates(start_date, end_date)
                await _ensure_no_overlap(session, user.id, start_date, end_date)
                session.add(UserTenure(
                    user_id=user.id,
                    status=item["status"],
                    employer=item["employer"],
                    start_date=start_date,
                    end_date=end_date,
                    notes=item.get("notes"),
                ))
                created += 1
            except (TypeError, ValueError):
                skipped += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def import_all(data: dict, *, replace: bool = False) -> dict:
    result = {}
    if "pages" in data:
        result["pages"] = await import_pages(data["pages"], replace=replace)
    if "resources" in data:
        result["resources"] = await import_resources(data["resources"], replace=replace)
    if "tenures" in data:
        result["tenures"] = await import_tenures(data["tenures"], replace=replace)
    return result
