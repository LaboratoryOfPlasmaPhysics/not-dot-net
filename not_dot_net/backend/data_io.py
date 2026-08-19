"""Import/export pages and booking resources as JSON."""

import asyncio
from datetime import datetime, UTC

from sqlalchemy import func, select

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


def _as_int(value, default: int) -> int:
    """Coerce a JSON scalar to int; fall back to `default` on garbage.

    Untyped import JSON may carry a string where an int column is expected; on
    PostgreSQL that aborts the whole batch INSERT. Coerce defensively instead.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_dict(value, default):
    """Coerce a JSON object to dict; fall back to `default` on null/garbage.

    `Resource.specs` is a JSON column, not a string — running it through
    _as_str discarded every imported resource's hardware specs silently.
    """
    return value if isinstance(value, dict) else default


def _as_str(value, default):
    """Coerce a JSON scalar to str; fall back to `default` on null/garbage.

    Same rationale as _as_int: an explicit null or a non-string in a NOT NULL
    string column aborts the whole batch at commit on PostgreSQL.
    """
    return value if isinstance(value, str) else default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return default


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
                    existing.title = title
                    existing.content = _as_str(item.get("content"), existing.content)
                    existing.sort_order = _as_int(item.get("sort_order", existing.sort_order), existing.sort_order)
                    existing.published = _as_bool(item.get("published", existing.published), existing.published)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Page(
                    title=title,
                    slug=slug,
                    content=_as_str(item.get("content"), ""),
                    sort_order=_as_int(item.get("sort_order", 0), 0),
                    published=_as_bool(item.get("published", False), False),
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
                    existing.resource_type = _as_str(item.get("resource_type"), existing.resource_type)
                    existing.description = _as_str(item.get("description"), existing.description)
                    existing.location = _as_str(item.get("location"), existing.location)
                    existing.specs = _as_dict(item.get("specs"), existing.specs)
                    existing.active = _as_bool(item.get("active", existing.active), existing.active)
                    updated += 1
                else:
                    skipped += 1
            else:
                session.add(Resource(
                    name=name,
                    resource_type=_as_str(item.get("resource_type"), "desktop"),
                    description=_as_str(item.get("description"), None),
                    location=_as_str(item.get("location"), None),
                    specs=_as_dict(item.get("specs"), None),
                    active=_as_bool(item.get("active", True), True),
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
            status = _as_str(item.get("status"), "")
            employer = _as_str(item.get("employer"), "")
            if not email or not status or not employer or not item.get("start_date"):
                skipped += 1
                continue
            # Case-insensitive: AD-provisioned users keep whatever case AD returned.
            user_result = await session.execute(
                select(User).where(func.lower(User.email) == email.lower())
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
                    status=status,
                    employer=employer,
                    start_date=start_date,
                    end_date=end_date,
                    notes=_as_str(item.get("notes"), None),
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
