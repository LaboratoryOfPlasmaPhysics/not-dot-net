"""Import/export pages, booking resources and tenures as JSON.

A content transfer between instances, not a backup: bookings, availability
windows and floor plans stay behind. Back up with pg_dump.
"""

import asyncio
import uuid
from datetime import datetime, UTC

from sqlalchemy import func, select

from not_dot_net.backend.booking_models import Resource, ResourceStatus
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


def _serialize_resource(r: Resource, owner_email: str | None) -> dict:
    return {
        "name": r.name,
        "resource_type": r.resource_type,
        "description": r.description,
        "location": r.location,
        "specs": r.specs,
        "active": r.active,
        "status": r.status,
        "owner_email": owner_email,
    }


async def _emails_by_id(session, user_ids) -> dict:
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.email).where(User.id.in_(user_ids)))
    return dict(rows.all())


async def _user_id_by_email(session, email) -> uuid.UUID | None:
    email = _clean_text(email)
    if not email:
        return None
    # Case-insensitive: AD-provisioned users keep whatever case AD returned.
    return (await session.execute(
        select(User.id).where(func.lower(User.email) == email.lower())
    )).scalar_one_or_none()


def _as_status(value, default: str) -> str:
    return value if value in {s.value for s in ResourceStatus} else default


async def export_pages() -> list[dict]:
    async with session_scope() as session:
        result = await session.execute(select(Page).order_by(Page.sort_order, Page.title))
        return [_serialize_page(p) for p in result.scalars().all()]


async def export_resources() -> list[dict]:
    async with session_scope() as session:
        resources = (await session.execute(select(Resource).order_by(Resource.name))).scalars().all()
        emails = await _emails_by_id(session, {r.owner_user_id for r in resources if r.owner_user_id})
        return [_serialize_resource(r, emails.get(r.owner_user_id)) for r in resources]


def _serialize_tenure(t: UserTenure, email: str) -> dict:
    return {
        "user_email": email,
        "status": t.status,
        "employer": t.employer,
        "team": t.team,
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
        email_map = await _emails_by_id(session, {t.user_id for t in tenures})
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
                    existing.status = _as_status(item.get("status"), existing.status)
                    if "owner_email" in item:  # files exported before it existed keep the owner
                        existing.owner_user_id = await _user_id_by_email(session, item["owner_email"])
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
                    status=_as_status(item.get("status"), ResourceStatus.AVAILABLE.value),
                    owner_user_id=await _user_id_by_email(session, item.get("owner_email")),
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
            user_id = await _user_id_by_email(session, email)
            if user_id is None:
                skipped += 1
                continue
            try:
                start_date = dt_date.fromisoformat(item["start_date"])
                end_date = dt_date.fromisoformat(item["end_date"]) if item.get("end_date") else None
                _validate_tenure_dates(start_date, end_date)
                same_start = await _tenure_starting(session, user_id, start_date) if replace else None
                await _ensure_no_overlap(session, user_id, start_date, end_date,
                                         exclude_id=same_start.id if same_start else None)
                fields = dict(status=status, employer=employer, end_date=end_date,
                              notes=_as_str(item.get("notes"), None))
                # An export from before tenures had a team must not wipe one.
                if "team" in item:
                    fields["team"] = _as_str(item.get("team"), None)
                if same_start:
                    for name, value in fields.items():
                        setattr(same_start, name, value)
                    updated += 1
                else:
                    session.add(UserTenure(user_id=user_id, start_date=start_date, **fields))
                    created += 1
            except (TypeError, ValueError):
                skipped += 1
        await session.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


async def _tenure_starting(session, user_id, start_date) -> UserTenure | None:
    """The tenure an import row replaces: same person, same start."""
    return (await session.execute(
        select(UserTenure).where(UserTenure.user_id == user_id, UserTenure.start_date == start_date)
    )).scalars().first()


async def import_all(data: dict, *, replace: bool = False, actor=None) -> dict:
    """Bulk-import pages, resources and tenures. Requires `manage_settings`."""
    from not_dot_net.backend.permissions import check_permission
    await check_permission(actor, "manage_settings")
    result = {}
    if "pages" in data:
        result["pages"] = await import_pages(data["pages"], replace=replace)
    if "resources" in data:
        result["resources"] = await import_resources(data["resources"], replace=replace)
    if "tenures" in data:
        result["tenures"] = await import_tenures(data["tenures"], replace=replace)
    return result
