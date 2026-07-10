# Office Availability Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an office's owner (or an admin) open a date-range availability window, and let any employee book a slot inside it, through the floor plan UI — reusing `booking_service.py`'s existing reservation/conflict/audit machinery rather than duplicating it.

**Architecture:** `Resource` (existing table) gains a nullable `owner_user_id` and a new `resource_type == "office"` value. A new, deliberately separate module `backend/office_availability.py` owns a new `OfficeAvailability` table (start/end date windows) and its own small service (`offer_availability` / `list_availability_windows` / `revoke_availability` / the pure `is_covered` function). `booking_service.create_booking` gets exactly one new branch that calls `is_covered` before its existing conflict check — everything else (row lock, conflict check, audit log) is untouched and shared with equipment bookings. The floor plan (`frontend/floorplan.py`) is extended so a room pin can be linked to a `Resource`, and clicking it shows/offers/books/revokes availability.

**Tech Stack:** NiceGUI, FastAPI-Users, SQLAlchemy 2.x async, Alembic, pytest (`nicegui.testing.User` for frontend tests), SQLite (dev/tests) / PostgreSQL (prod).

## Global Constraints

- Design spec: `docs/superpowers/specs/2026-07-10-office-availability-booking-design.md` — read it before starting if anything below is ambiguous.
- **No new permission key.** Reuse the existing `"manage_bookings"` permission string. `office_availability.py` calls `has_permissions(actor, "manage_bookings")` with the literal string rather than importing `MANAGE_BOOKINGS` from `booking_service.py` — `booking_service.py` imports *from* `office_availability.py` (for `is_covered`/`list_availability_windows`), so importing the other way would create a circular import.
- **`end_date` is exclusive everywhere** (`OfficeAvailability.end_date`, `Booking.end_date`) — same convention already used by `Booking`. Reuse `booking_last_day()` (`backend/booking_service.py:506`) and `_format_booking_period()` (`frontend/bookings.py:72`) for display; do not invent new date formatting.
- **Every new i18n key must be added to BOTH the `"en"` and `"fr"` blocks** in `not_dot_net/frontend/i18n.py` — `validate_translations()` asserts the two blocks have identical key sets.
- **Four model-registration sites, every time a new model module is added:** `not_dot_net/backend/db.py::create_db_and_tables`, `tests/conftest.py::setup_db`, `not_dot_net/backend/migrate.py::_create_all`, `alembic/env.py`. This is the exact lesson the floor-plan foundation's final review caught (2 of 4 sites were missed there) — don't repeat it.
- **Migrations only run in production** (Postgres, via Alembic). Dev mode (`DATABASE_URL` unset) uses `Base.metadata.create_all` directly and never touches `alembic/versions/`. This means `op.create_foreign_key` is safe to use directly for adding an FK to an existing table — no SQLite batch-mode workaround is needed anywhere in this codebase's migration history, and none is needed here either.
- **Correction to the design doc:** section "Non-goals" states "the existing booking-confirmation email is enough for v1." As of this plan, `create_booking` (`backend/booking_service.py:329-397`) sends **no** confirmation email at all — only status-change, migration, and reminder emails exist. No task below adds one; this note just prevents anyone from hunting for an email hook that isn't there. Office bookings get the same (zero) confirmation behavior as equipment bookings today.
- **Deliberate scope decision on the "Book" widget:** the design says the floor-plan Book dialog reuses "the same date-range + note booking widget already used in `bookings.py`'s resource detail." That widget is ~100 lines of inline code entangled with OS/software-tag selection that doesn't apply to offices, and isn't factored into a standalone function. Rather than refactor it (out of scope, YAGNI), Task 9 below reuses the *same* `create_booking()` backend call and the *same* `ui.date(...).props("range :options=...")` clamping technique, in a small dedicated widget. This produces equivalent UX with far less code than threading office bookings through the equipment form's OS/software machinery.

---

### Task 1: Data model — `OfficeAvailability` + `Resource.owner_user_id` + migration + registration

**Files:**
- Modify: `not_dot_net/backend/booking_models.py`
- Create: `not_dot_net/backend/office_availability.py`
- Create: `alembic/versions/0018_add_office_availability.py`
- Modify: `not_dot_net/backend/db.py` (`create_db_and_tables`, ~line 65-79)
- Modify: `tests/conftest.py` (`setup_db`, ~line 81-89)
- Modify: `not_dot_net/backend/migrate.py` (`_create_all`, ~line 43-56)
- Modify: `alembic/env.py` (~line 11-22)
- Test: Create `tests/test_office_availability_models.py`

**Interfaces:**
- Produces: `Resource.owner_user_id: uuid.UUID | None` (new field on the existing `Resource` dataclass, `mapped_column(ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None)` — kw-only with a default, so no existing `Resource(...)` call site breaks).
- Produces: `OfficeAvailability` dataclass model — `resource_id: uuid.UUID`, `start_date: date`, `end_date: date`, `offered_by: uuid.UUID`, `id: uuid.UUID`, `created_at: datetime` — importable from `not_dot_net.backend.office_availability`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_availability_models.py
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.office_availability import OfficeAvailability


async def _create_user(email="owner@test.com") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_office(owner: User | None = None, **kwargs) -> Resource:
    defaults = {"name": "Room 101", "resource_type": "office", "location": "Palaiseau"}
    defaults.update(kwargs)
    async with session_scope() as session:
        resource = Resource(owner_user_id=owner.id if owner else None, **defaults)
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        return resource


async def test_resource_owner_user_id_defaults_to_none():
    resource = await _create_office()
    assert resource.owner_user_id is None


async def test_resource_owner_user_id_set_null_when_owner_deleted():
    owner = await _create_user()
    resource = await _create_office(owner=owner)
    async with session_scope() as session:
        stored = await session.get(User, owner.id)
        await session.delete(stored)
        await session.commit()
    async with session_scope() as session:
        refreshed = await session.get(Resource, resource.id)
    assert refreshed.owner_user_id is None


async def test_office_availability_cascades_on_resource_delete():
    offeror = await _create_user(email="admin@test.com")
    resource = await _create_office()
    async with session_scope() as session:
        window = OfficeAvailability(
            resource_id=resource.id, start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 15), offered_by=offeror.id,
        )
        session.add(window)
        await session.commit()

    async with session_scope() as session:
        stored = await session.get(Resource, resource.id)
        await session.delete(stored)
        await session.commit()

    async with session_scope() as session:
        remaining = (await session.execute(select(OfficeAvailability))).scalars().all()
    assert remaining == []


async def test_office_availability_offered_by_fk_enforced():
    resource = await _create_office()
    async with session_scope() as session:
        window = OfficeAvailability(
            resource_id=resource.id, start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 15), offered_by=uuid.uuid4(),
        )
        session.add(window)
        with pytest.raises(Exception):
            await session.commit()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_office_availability_models.py -v`
Expected: FAIL (collection error — `not_dot_net.backend.office_availability` doesn't exist, and `Resource(owner_user_id=...)` is an unexpected keyword argument).

- [ ] **Step 3: Add `owner_user_id` to `Resource`**

In `not_dot_net/backend/booking_models.py`, insert after the `status` field (line 34) and before `created_at`:

```python
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), nullable=True, default=None
    )
```

- [ ] **Step 4: Create `office_availability.py` with the model**

```python
# not_dot_net/backend/office_availability.py
"""Office availability — owner-declared booking windows for office resources.

Kept separate from booking_service.py: this module owns windows and
ownership; booking_service.py owns reservations and conflicts. The only
integration point is create_booking's is_covered() check.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, func
from sqlalchemy.orm import Mapped, MappedAsDataclass, mapped_column

from not_dot_net.backend.db import Base


class OfficeAvailability(MappedAsDataclass, Base, kw_only=True):
    __tablename__ = "office_availability"

    resource_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("resource.id", ondelete="CASCADE"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    offered_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE")
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default_factory=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), default=None)
```

- [ ] **Step 5: Create the Alembic migration**

```python
# alembic/versions/0018_add_office_availability.py
"""Add owner_user_id to resource and the office_availability table.

Revision ID: 0018
Revises: 0017
"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resource", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_resource_owner_user_id", "resource", "user",
        ["owner_user_id"], ["id"], ondelete="SET NULL",
    )
    op.create_table(
        "office_availability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("offered_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["resource_id"], ["resource.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["offered_by"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_office_availability_resource_id", "office_availability", ["resource_id"])


def downgrade() -> None:
    op.drop_index("ix_office_availability_resource_id", table_name="office_availability")
    op.drop_table("office_availability")
    op.drop_constraint("fk_resource_owner_user_id", "resource", type_="foreignkey")
    op.drop_column("resource", "owner_user_id")
```

- [ ] **Step 6: Register the new module at all four sites**

In `not_dot_net/backend/db.py`, inside `create_db_and_tables` (right after the `floorplan_models` import, ~line 70):

```python
    import not_dot_net.backend.office_availability  # noqa: F401 — register OfficeAvailability with Base
```

In `tests/conftest.py`, inside `setup_db` (right after the `floorplan_models` import, ~line 83):

```python
    import not_dot_net.backend.office_availability  # noqa: F401
```

In `not_dot_net/backend/migrate.py`, inside `_create_all` (right after the `floorplan_models` import, ~line 55):

```python
    import not_dot_net.backend.office_availability  # noqa: F401 — register OfficeAvailability with Base
```

In `alembic/env.py`, right after the `floorplan_models` import (~line 21, before `from not_dot_net.backend.db import Base`):

```python
import not_dot_net.backend.office_availability  # noqa: F401
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_office_availability_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/backend/booking_models.py not_dot_net/backend/office_availability.py \
        alembic/versions/0018_add_office_availability.py not_dot_net/backend/db.py \
        tests/conftest.py not_dot_net/backend/migrate.py alembic/env.py \
        tests/test_office_availability_models.py
git commit -m "feat: add OfficeAvailability model and Resource.owner_user_id"
```

---

### Task 2: `is_covered` pure function

**Files:**
- Modify: `not_dot_net/backend/office_availability.py`
- Create: `tests/test_office_availability_service.py`

**Interfaces:**
- Consumes: `OfficeAvailability(resource_id, start_date, end_date, offered_by, ...)` from Task 1.
- Produces: `is_covered(windows: list[OfficeAvailability], start_date: date, end_date: date) -> bool` — pure, no DB access. Consumed by Task 5 (`booking_service.create_booking`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_office_availability_service.py
import uuid
from datetime import date

from not_dot_net.backend.office_availability import OfficeAvailability, is_covered

_RESOURCE = uuid.uuid4()
_USER = uuid.uuid4()


def _window(start: str, end: str) -> OfficeAvailability:
    return OfficeAvailability(
        resource_id=_RESOURCE, start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end), offered_by=_USER,
    )


def test_is_covered_empty_window_list():
    assert is_covered([], date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_exact_match():
    windows = [_window("2026-08-01", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_fully_inside_a_wider_window():
    windows = [_window("2026-07-25", "2026-08-20")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_partially_outside_returns_false():
    windows = [_window("2026-08-01", "2026-08-05")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_gap_between_windows_returns_false():
    windows = [_window("2026-08-01", "2026-08-04"), _window("2026-08-06", "2026-08-10")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is False


def test_is_covered_multiple_overlapping_windows_union():
    windows = [
        _window("2026-08-01", "2026-08-04"),
        _window("2026-08-03", "2026-08-07"),
        _window("2026-08-06", "2026-08-10"),
    ]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True


def test_is_covered_unordered_windows_still_evaluated_correctly():
    windows = [_window("2026-08-06", "2026-08-10"), _window("2026-08-01", "2026-08-06")]
    assert is_covered(windows, date(2026, 8, 1), date(2026, 8, 10)) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_office_availability_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_covered'`

- [ ] **Step 3: Implement `is_covered`**

Append to `not_dot_net/backend/office_availability.py`:

```python
def is_covered(windows: list[OfficeAvailability], start_date: date, end_date: date) -> bool:
    """True if [start_date, end_date) is fully covered by the union of the
    given windows. Pure/no DB — overlapping/duplicate windows are absorbed
    for free by this sweep."""
    day = start_date
    for window in sorted(windows, key=lambda w: w.start_date):
        if window.start_date > day:
            break
        if window.end_date > day:
            day = window.end_date
    return day >= end_date
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_office_availability_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/office_availability.py tests/test_office_availability_service.py
git commit -m "feat: add is_covered pure function for availability windows"
```

---

### Task 3: `offer_availability` / `list_availability_windows` / `revoke_availability`

**Files:**
- Modify: `not_dot_net/backend/office_availability.py`
- Modify: `tests/test_office_availability_service.py`

**Interfaces:**
- Consumes: `has_permissions(user, *permissions) -> bool` (`backend/permissions.py:35`), `session_scope()` / `Base` (`backend/db.py`), `Resource`/`Booking` (`backend/booking_models.py`), `log_audit(...)` (`backend/audit.py:108`).
- Produces: `offer_availability(resource_id, start_date, end_date, actor=None) -> OfficeAvailability`, `list_availability_windows(resource_id) -> list[OfficeAvailability]`, `revoke_availability(window_id, actor=None) -> None`, `OfficeAvailabilityError` exception class. Consumed by Task 5 (integration), Task 6 (create_resource/update_resource don't need these, but Task 9's frontend does).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_office_availability_service.py`:

```python
import pytest

from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.office_availability import (
    OfficeAvailabilityError,
    list_availability_windows,
    offer_availability,
    revoke_availability,
)
from not_dot_net.backend.roles import RoleDefinition, roles_config


async def _setup_roles():
    cfg = await roles_config.get()
    cfg.roles["admin"] = RoleDefinition(label="Admin", permissions=["manage_bookings"])
    cfg.roles["staff"] = RoleDefinition(label="Staff", permissions=["create_workflows"])
    await roles_config.set(cfg)


async def _create_user(email="user@test.com", role="staff") -> User:
    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email=email, hashed_password="x", role=role)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _create_office(owner=None) -> Resource:
    async with session_scope() as session:
        resource = Resource(
            name="Room 101", resource_type="office", location="Palaiseau",
            owner_user_id=owner.id if owner else None,
        )
        session.add(resource)
        await session.commit()
        await session.refresh(resource)
        return resource


async def test_offer_availability_by_owner():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    assert window.resource_id == resource.id
    assert window.offered_by == owner.id


async def test_offer_availability_by_manager():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)
    assert window.offered_by == admin.id


async def test_offer_availability_denied_for_non_owner_non_manager():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    stranger = await _create_user(email="stranger@test.com")
    resource = await _create_office(owner=owner)
    with pytest.raises(PermissionError):
        await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=stranger)


async def test_offer_availability_rejects_inverted_range():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    with pytest.raises(OfficeAvailabilityError):
        await offer_availability(resource.id, date(2026, 8, 15), date(2026, 8, 1), actor=admin)


async def test_list_availability_windows_returns_offered_windows():
    await _setup_roles()
    admin = await _create_user(email="admin@test.com", role="admin")
    resource = await _create_office()
    await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)
    windows = await list_availability_windows(resource.id)
    assert len(windows) == 1


async def test_revoke_availability_by_owner():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    await revoke_availability(window.id, actor=owner)
    assert await list_availability_windows(resource.id) == []


async def test_revoke_availability_denied_for_non_owner_non_manager():
    await _setup_roles()
    owner = await _create_user(email="owner@test.com")
    stranger = await _create_user(email="stranger@test.com")
    resource = await _create_office(owner=owner)
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=owner)
    with pytest.raises(PermissionError):
        await revoke_availability(window.id, actor=stranger)


async def test_revoke_availability_blocked_by_booking_inside_window():
    await _setup_roles()
    admin = await _create_user(email="admin2@test.com", role="admin")
    booker = await _create_user(email="booker@test.com")
    resource = await _create_office()
    window = await offer_availability(resource.id, date(2026, 8, 1), date(2026, 8, 15), actor=admin)

    async with session_scope() as session:
        booking = Booking(
            resource_id=resource.id, user_id=booker.id,
            start_date=date(2026, 8, 3), end_date=date(2026, 8, 6),
        )
        session.add(booking)
        await session.commit()

    with pytest.raises(OfficeAvailabilityError):
        await revoke_availability(window.id, actor=admin)
```

Also add `import uuid` at the top of `tests/test_office_availability_service.py` (needed for `_create_user`'s `uuid.uuid4()`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_office_availability_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'offer_availability'`

- [ ] **Step 3: Implement the service functions**

Append to `not_dot_net/backend/office_availability.py` (after the imports, before or after `is_covered` — exact position doesn't matter, but keep `OfficeAvailabilityError` near the top with the other class definitions):

```python
from sqlalchemy import select

from not_dot_net.backend.booking_models import Booking, Resource
from not_dot_net.backend.db import session_scope
from not_dot_net.backend.permissions import has_permissions


class OfficeAvailabilityError(Exception):
    pass


async def _check_owner_or_manager(resource: Resource, actor) -> None:
    if actor is None:
        return
    is_owner = actor.id == resource.owner_user_id
    if not is_owner and not await has_permissions(actor, "manage_bookings"):
        raise PermissionError("Only the office owner or a booking manager can do this")


async def offer_availability(
    resource_id: uuid.UUID, start_date: date, end_date: date, actor=None,
) -> OfficeAvailability:
    if start_date >= end_date:
        raise OfficeAvailabilityError("End date must be after start date")
    async with session_scope() as session:
        resource = await session.get(Resource, resource_id)
        if resource is None:
            raise ValueError(f"Resource {resource_id} not found")
        await _check_owner_or_manager(resource, actor)
        window = OfficeAvailability(
            resource_id=resource_id, start_date=start_date, end_date=end_date,
            offered_by=actor.id if actor is not None else resource.owner_user_id,
        )
        session.add(window)
        await session.commit()
        await session.refresh(window)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "office_availability", "offer",
        actor_id=(actor.id if actor is not None else None),
        target_type="resource", target_id=resource_id,
        detail=f"{start_date} → {end_date}",
    )
    return window


async def list_availability_windows(resource_id: uuid.UUID) -> list[OfficeAvailability]:
    async with session_scope() as session:
        result = await session.execute(
            select(OfficeAvailability)
            .where(OfficeAvailability.resource_id == resource_id)
            .order_by(OfficeAvailability.start_date)
        )
        return list(result.scalars().all())


async def revoke_availability(window_id: uuid.UUID, actor=None) -> None:
    async with session_scope() as session:
        window = await session.get(OfficeAvailability, window_id)
        if window is None:
            raise ValueError(f"Availability window {window_id} not found")
        resource = await session.get(Resource, window.resource_id)
        await _check_owner_or_manager(resource, actor)

        conflicting = await session.execute(
            select(Booking).where(
                Booking.resource_id == window.resource_id,
                Booking.start_date < window.end_date,
                Booking.end_date > window.start_date,
            ).limit(1)
        )
        if conflicting.scalars().first() is not None:
            raise OfficeAvailabilityError(
                "A booking falls within this window — cancel it before revoking"
            )
        resource_id = window.resource_id
        await session.delete(window)
        await session.commit()

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "office_availability", "revoke",
        actor_id=(actor.id if actor is not None else None),
        target_type="resource", target_id=resource_id,
        detail=f"window={window_id}",
    )
```

Note: `_check_owner_or_manager` calls `has_permissions(actor, "manage_bookings")` with the literal string, not the `MANAGE_BOOKINGS` constant from `booking_service.py` — see Global Constraints for why (avoids a circular import once Task 5 makes `booking_service.py` import from this module).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_office_availability_service.py -v`
Expected: PASS (15 tests total in the file)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/office_availability.py tests/test_office_availability_service.py
git commit -m "feat: add offer/list/revoke_availability service functions"
```

---

### Task 4: Resource owner assignment (`create_resource` / `update_resource`)

**Files:**
- Modify: `not_dot_net/backend/booking_service.py`
- Modify: `tests/test_booking_service.py`

**Interfaces:**
- Produces: `create_resource(..., owner_user_id: uuid.UUID | None = None, ...)`, `_RESOURCE_MUTABLE` gains `"owner_user_id"`. Consumed by Task 7 (admin resource editor).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_booking_service.py` (near the other resource-CRUD tests, e.g. after `test_update_resource`):

```python
async def test_create_resource_with_owner_user_id():
    owner = await _create_user(email="owner2@test.com")
    resource = await create_resource(
        name="Room 101", resource_type="office", location="Palaiseau",
        owner_user_id=owner.id,
    )
    assert resource.owner_user_id == owner.id


async def test_update_resource_owner_user_id():
    owner = await _create_user(email="owner3@test.com")
    r = await _create_test_resource(name="Room 102", resource_type="office")
    updated = await update_resource(r.id, owner_user_id=owner.id)
    assert updated.owner_user_id == owner.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py -k owner_user_id -v`
Expected: FAIL — `TypeError: create_resource() got an unexpected keyword argument 'owner_user_id'`

- [ ] **Step 3: Implement**

In `not_dot_net/backend/booking_service.py`, change `create_resource`'s signature and body (lines 61-88):

```python
async def create_resource(name: str, resource_type: str, description: str = "",
                          location: str = "", specs: dict | None = None,
                          owner_user_id: uuid.UUID | None = None,
                          actor=None) -> Resource:
    if actor is not None:
        await check_permission(actor, MANAGE_BOOKINGS)
    async with session_scope() as session:
        resource = Resource(
            name=name,
            resource_type=resource_type,
            description=description or None,
            location=location or None,
            specs=specs,
            owner_user_id=owner_user_id,
        )
        session.add(resource)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ValueError(f"Resource name '{name}' already exists") from exc
        await session.refresh(resource)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "resource", "create",
        target_type="resource", target_id=resource.id,
        detail=f"name={name} type={resource_type}",
    )
    return resource
```

And widen `_RESOURCE_MUTABLE` (line 91):

```python
_RESOURCE_MUTABLE = frozenset(
    {"name", "resource_type", "description", "location", "specs", "active", "owner_user_id"}
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_service.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat: support owner_user_id in create_resource/update_resource"
```

---

### Task 5: `create_booking` office integration

**Files:**
- Modify: `not_dot_net/backend/booking_service.py`
- Modify: `tests/test_booking_service.py`

**Interfaces:**
- Consumes: `is_covered`, `list_availability_windows` (Task 2/3), `offer_availability` (Task 3, test-only).
- Produces: `create_booking` now raises `BookingValidationError` for office resources outside every offered window; equipment resources are unaffected (no `is_covered` call).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_booking_service.py`:

```python
from not_dot_net.backend.office_availability import offer_availability


async def test_create_booking_office_inside_window_succeeds():
    await _setup_roles()
    admin = await _create_user(email="office-admin@test.com", role="admin")
    user = await _create_user(email="booker2@test.com")
    resource = await _create_test_resource(name="Room 201", resource_type="office")
    start = _valid_start()
    await offer_availability(resource.id, start, start + timedelta(days=20), actor=admin)
    booking = await create_booking(resource.id, user.id, start, start + timedelta(days=3), actor=user)
    assert booking.resource_id == resource.id


async def test_create_booking_office_outside_window_raises():
    await _setup_roles()
    admin = await _create_user(email="office-admin2@test.com", role="admin")
    user = await _create_user(email="booker3@test.com")
    resource = await _create_test_resource(name="Room 202", resource_type="office")
    start = _valid_start()
    await offer_availability(resource.id, start, start + timedelta(days=3), actor=admin)
    with pytest.raises(BookingValidationError):
        await create_booking(resource.id, user.id, start, start + timedelta(days=10), actor=user)


async def test_create_booking_office_with_no_windows_raises():
    await _setup_roles()
    user = await _create_user(email="booker4@test.com")
    resource = await _create_test_resource(name="Room 203", resource_type="office")
    start = _valid_start()
    with pytest.raises(BookingValidationError):
        await create_booking(resource.id, user.id, start, start + timedelta(days=3), actor=user)


async def test_create_booking_equipment_unaffected_by_office_check():
    """No availability window exists anywhere, but equipment booking must
    still succeed — is_covered is never consulted for non-office resources."""
    r = await _create_test_resource(name="PC-99", resource_type="desktop")
    pc_user = await _create_user(email="pcuser@test.com")
    start = _valid_start()
    booking = await create_booking(r.id, pc_user.id, start, start + timedelta(days=3))
    assert booking.resource_id == r.id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_service.py -k office -v`
Expected: FAIL — office bookings outside a window are not rejected (no `BookingValidationError` raised) because the branch doesn't exist yet.

- [ ] **Step 3: Implement**

In `not_dot_net/backend/booking_service.py`, add the import at the top (with the other `not_dot_net.backend.*` imports):

```python
from not_dot_net.backend.office_availability import is_covered, list_availability_windows
```

In `create_booking`, insert the new branch right after the `if not resource.active:` check (between lines 364 and 366):

```python
            if not resource.active:
                raise BookingValidationError("Resource is not active")

            if resource.resource_type == "office":
                windows = await list_availability_windows(resource_id)
                if not is_covered(windows, start_date, end_date):
                    raise BookingValidationError(
                        "Requested dates are outside the offered availability window"
                    )

            conflicts = await session.execute(
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_service.py tests/test_office_availability_service.py -v`
Expected: PASS (full files)

- [ ] **Step 5: Commit**

```bash
git add not_dot_net/backend/booking_service.py tests/test_booking_service.py
git commit -m "feat: gate office bookings on offered availability windows"
```

---

### Task 6: Full test suite checkpoint

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass (read the actual pass count and exit code — this is a hard gate before continuing to frontend work).

- [ ] **Step 2: If anything fails, stop and fix before proceeding to Task 7.**

No commit for this task (nothing changes).

---

### Task 7: Admin resource editor type-aware + status FSM hidden + Equipment tab exclusion

**Files:**
- Modify: `not_dot_net/frontend/bookings.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Modify: `tests/test_booking_ui_helpers.py`

**Interfaces:**
- Consumes: `create_resource`/`update_resource` with `owner_user_id` (Task 4).
- Produces: `RESOURCE_TYPES = ["desktop", "laptop", "office"]`; pure helpers `_office_fields_visible(resource_type) -> bool`, `_resource_icon(resource_type) -> str`, `_exclude_offices(resources) -> list`. Consumed nowhere outside this file (frontend leaf).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_booking_ui_helpers.py`:

```python
from not_dot_net.frontend.bookings import _exclude_offices, _office_fields_visible, _resource_icon


def test_office_fields_visible_only_for_office_type():
    assert _office_fields_visible("office") is True
    assert _office_fields_visible("desktop") is False
    assert _office_fields_visible("laptop") is False


def test_resource_icon_maps_known_types():
    assert _resource_icon("desktop") == "desktop_windows"
    assert _resource_icon("laptop") == "laptop"
    assert _resource_icon("office") == "meeting_room"
    assert _resource_icon("nonsense") == "devices"


def test_exclude_offices_filters_out_office_resource_type():
    from types import SimpleNamespace

    resources = [
        SimpleNamespace(resource_type="desktop"),
        SimpleNamespace(resource_type="office"),
        SimpleNamespace(resource_type="laptop"),
    ]
    filtered = _exclude_offices(resources)
    assert [r.resource_type for r in filtered] == ["desktop", "laptop"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_booking_ui_helpers.py -v`
Expected: FAIL — `ImportError: cannot import name '_office_fields_visible'`

- [ ] **Step 3: Add the pure helpers and `RESOURCE_TYPES`**

In `not_dot_net/frontend/bookings.py`, change line 30:

```python
RESOURCE_TYPES = ["desktop", "laptop", "office"]
```

Add near the other small helpers (e.g. right after `_status_color`, ~line 320):

```python
_RESOURCE_ICON = {"desktop": "desktop_windows", "laptop": "laptop", "office": "meeting_room"}


def _resource_icon(resource_type: str) -> str:
    return _RESOURCE_ICON.get(resource_type, "devices")


def _office_fields_visible(resource_type: str) -> bool:
    return resource_type == "office"


def _exclude_offices(resources: list) -> list:
    return [r for r in resources if r.resource_type != "office"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_booking_ui_helpers.py -v`
Expected: PASS

- [ ] **Step 5: Wire the helpers into the UI**

In `not_dot_net/frontend/bookings.py`:

1. Add the `office` label and `resource_owner`/`no_owner` i18n keys — see Step 6 below first, then come back here.

2. `_resource_card` (line 336) — replace:
```python
                    icon = "desktop_windows" if res.resource_type == "desktop" else "laptop"
```
with:
```python
                    icon = _resource_icon(res.resource_type)
```

3. `_render_resource_detail` (line 532) — replace:
```python
        if res.active:
            with ui.row().classes("items-center gap-2 mt-2"):
```
with:
```python
        if res.resource_type != "office" and res.active:
            with ui.row().classes("items-center gap-2 mt-2"):
```
(the rest of that block, lines 533-549, is unchanged — it's now nested inside the widened condition).

4. `_render_bookings` (line 91) — replace:
```python
    resources = await list_resources(active_only=not is_admin)
```
with:
```python
    resources = _exclude_offices(await list_resources(active_only=not is_admin))
```

5. `_render_bookings`'s type filter (lines 168-172) — replace:
```python
            all_types = [t("all_types")] + RESOURCE_TYPES
            type_select = ui.select(
                options=all_types, value=all_types[0],
                label=t("resource_type"),
            ).props("outlined dense").classes("min-w-[150px]")
```
with:
```python
            equipment_types = [rt for rt in RESOURCE_TYPES if rt != "office"]
            all_types = [t("all_types")] + equipment_types
            type_select = ui.select(
                options=all_types, value=all_types[0],
                label=t("resource_type"),
            ).props("outlined dense").classes("min-w-[150px]")
```
and update the `apply_filter` closure's type-filter line (line 190) from `type_select.value in RESOURCE_TYPES` to `type_select.value in equipment_types`.

6. `_show_resource_dialog` (lines 670-745) — add the imports at the top of the file first:
```python
from sqlalchemy import func, select
from not_dot_net.backend.db import User, resolve_user_names, session_scope
```
(replace the existing `from not_dot_net.backend.db import User, resolve_user_names` line with the one above, adding `session_scope`.)

Add a module-level helper (near `_get_resource_for_booking`, ~line 323):
```python
async def _load_active_users() -> list[User]:
    async with session_scope() as session:
        result = await session.execute(
            select(User).where(User.is_active == True).order_by(  # noqa: E712
                func.lower(func.coalesce(User.full_name, User.email))
            )
        )
        return list(result.scalars().all())
```

Replace the specs block (lines 698-705) and everything up to the save button with:
```python
        specs_container = ui.column().classes("w-full")
        owner_container = ui.column().classes("w-full")

        existing_specs = (resource.specs or {}) if editing else {}
        spec_inputs = {}
        with specs_container:
            ui.label(t("specs")).classes("text-subtitle2 mt-2")
            for key in ("cpu", "ram", "hdd", "gpu"):
                spec_inputs[key] = ui.input(
                    t(key), value=existing_specs.get(key, ""),
                ).props("outlined dense").classes("w-full")

        active_users = await _load_active_users()
        owner_options = {None: t("no_owner"), **{u.id: (u.full_name or u.email) for u in active_users}}
        with owner_container:
            owner_select = ui.select(
                owner_options,
                value=resource.owner_user_id if editing else None,
                label=t("resource_owner"),
            ).props("outlined dense with-input").classes("w-full")

        def _toggle_type_fields(resource_type: str):
            is_office = _office_fields_visible(resource_type)
            specs_container.set_visibility(not is_office)
            owner_container.set_visibility(is_office)

        _toggle_type_fields(type_select.value)
        type_select.on_value_change(lambda e: _toggle_type_fields(e.value))
```

Replace `do_save` (lines 710-739) with:
```python
            async def do_save():
                if not name_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                is_office = _office_fields_visible(type_select.value)
                specs = None if is_office else (
                    {k: v.value.strip() for k, v in spec_inputs.items() if v.value.strip()} or None
                )
                owner_user_id = owner_select.value if is_office else None
                try:
                    if editing:
                        await update_resource(
                            resource.id,
                            actor=user,
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            location=location_select.value,
                            description=desc_input.value.strip() or None,
                            specs=specs,
                            owner_user_id=owner_user_id,
                        )
                        ui.notify(t("resource_updated"), color="positive")
                    else:
                        await create_resource(
                            name=name_input.value.strip(),
                            resource_type=type_select.value,
                            description=desc_input.value.strip(),
                            location=location_select.value,
                            specs=specs,
                            owner_user_id=owner_user_id,
                            actor=user,
                        )
                        ui.notify(t("resource_created"), color="positive")
                except Exception as e:
                    ui.notify(str(e), color="negative")
                    return
                dialog.close()
                await _render_bookings(outer_container, user)
```

- [ ] **Step 6: Add i18n keys**

In `not_dot_net/frontend/i18n.py`, English block — insert after line 253 (`"laptop": "Laptop",`):

```python
        "office": "Office",
```

and after line 297 (`"floorplan_pin_added": "Pin added",`), before `"floorplan_pin_deleted"` — actually keep insertion order simple, just add these two new keys anywhere in the `# Bookings` group, e.g. right after `"resource_location": "Location",` (line 250):

```python
        "resource_owner": "Owner",
        "no_owner": "— No owner —",
```

French block — mirror both insertions at the matching lines (`"laptop": "Ordinateur portable",` area and `"resource_location": "Emplacement",` area):

```python
        "office": "Bureau",
```
```python
        "resource_owner": "Propriétaire",
        "no_owner": "— Aucun propriétaire —",
```

- [ ] **Step 7: Run the full booking test files**

Run: `uv run pytest tests/test_booking_ui_helpers.py tests/test_booking_service.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/frontend/bookings.py not_dot_net/frontend/i18n.py tests/test_booking_ui_helpers.py
git commit -m "feat: type-aware admin resource editor for office resources"
```

---

### Task 8: Floor plan — link a pin to a resource

**Files:**
- Modify: `not_dot_net/backend/floorplan_service.py`
- Modify: `not_dot_net/frontend/floorplan.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Modify: `tests/test_floorplan_map_points.py`

**Interfaces:**
- Produces: `add_map_point(floor_plan_id, label, kind, x, y, resource_id=None, actor=None) -> MapPoint` — `resource_id` is new and optional (default `None`, fully backward compatible). Consumed by Task 9.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_floorplan_map_points.py`:

```python
async def test_add_map_point_with_resource_id_links_to_resource(monkeypatch, tmp_path):
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)
    resource = await create_resource("Room 101", "office", location="Palaiseau")

    point = await add_map_point(fp.id, "Room 101", "room", 50, 60, resource_id=resource.id, actor=admin)
    assert point.resource_id == resource.id


async def test_add_map_point_without_resource_id_defaults_none(monkeypatch, tmp_path):
    from not_dot_net.backend.floorplan_service import add_map_point

    await _setup_roles()
    admin = await _create_user(role="admin")
    fp = await _create_floor_plan(admin, monkeypatch, tmp_path)

    point = await add_map_point(fp.id, "Plug 1", "wall_plug", 5, 5, actor=admin)
    assert point.resource_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_map_points.py -v`
Expected: FAIL — `TypeError: add_map_point() got an unexpected keyword argument 'resource_id'`

- [ ] **Step 3: Extend `add_map_point`**

In `not_dot_net/backend/floorplan_service.py`, replace lines 137-155:

```python
async def add_map_point(
    floor_plan_id: uuid.UUID, label: str, kind: str, x: int, y: int,
    resource_id: uuid.UUID | None = None, actor=None,
) -> MapPoint:
    if actor is not None:
        await check_permission(actor, MANAGE_FLOORPLANS)
    async with session_scope() as session:
        point = MapPoint(
            floor_plan_id=floor_plan_id, label=label, kind=kind, x=x, y=y,
            resource_id=resource_id,
        )
        session.add(point)
        await session.commit()
        await session.refresh(point)

    from not_dot_net.backend.audit import log_audit
    await log_audit(
        "floorplan", "add_point",
        actor_id=(actor.id if actor else None),
        target_type="floor_plan", target_id=floor_plan_id,
        detail=f"label={label} kind={kind}",
    )
    return point
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_map_points.py -v`
Expected: PASS

- [ ] **Step 5: Wire the admin add-pin dialog to offer a resource picker**

In `not_dot_net/frontend/floorplan.py`, add to the imports (with the other `booking_service` usage that Task 9 will also need):

```python
from not_dot_net.backend.booking_service import get_resource_by_id, list_resources
```

Replace `_show_add_pin_dialog` (lines 211-234):

```python
async def _show_add_pin_dialog(plan_area, state, user, is_admin, floor_plan_id, x, y):
    offices = [r for r in await list_resources(active_only=True) if r.resource_type == "office"]
    resource_options = {None: t("floorplan_no_resource"), **{r.id: r.name for r in offices}}

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_pin_label")).classes("text-subtitle2")
        label_input = ui.input(t("floorplan_pin_label")).props("outlined dense").classes("w-full")
        kind_select = ui.select(
            _pin_kind_select_options(), value="room", label=t("floorplan_pin_kind"),
        ).props("outlined dense").classes("w-full")
        resource_select = ui.select(
            resource_options, value=None, label=t("floorplan_link_resource"),
        ).props("outlined dense with-input").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                if not label_input.value.strip():
                    ui.notify(t("required_field"), color="negative")
                    return
                await add_map_point(
                    floor_plan_id, label_input.value.strip(), kind_select.value, x, y,
                    resource_id=resource_select.value, actor=user,
                )
                ui.notify(t("floorplan_pin_added"), color="positive")
                dialog.close()
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()
```

(`get_resource_by_id` isn't used yet in this task — it's imported here because Task 9 needs it in the same import line and this keeps the diff for that task smaller. If you prefer, defer that half of the import to Task 9 instead; either is fine.)

- [ ] **Step 6: Add i18n keys**

English block, near the other `floorplan_pin_*` keys (after line 297 `"floorplan_pin_added": "Pin added",`):

```python
        "floorplan_no_resource": "— None —",
        "floorplan_link_resource": "Link to resource",
```

French block, matching position:

```python
        "floorplan_no_resource": "— Aucune —",
        "floorplan_link_resource": "Lier à une ressource",
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_floorplan_map_points.py tests/test_floorplan_ui_helpers.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add not_dot_net/backend/floorplan_service.py not_dot_net/frontend/floorplan.py \
        not_dot_net/frontend/i18n.py tests/test_floorplan_map_points.py
git commit -m "feat: link floor plan pins to a Resource"
```

---

### Task 9: Floor plan — view / offer / book / revoke availability from the pin popup

**Files:**
- Modify: `not_dot_net/frontend/floorplan.py`
- Modify: `not_dot_net/frontend/i18n.py`
- Modify: `tests/test_floorplan_ui_helpers.py`

**Interfaces:**
- Consumes: `list_availability_windows`, `offer_availability`, `revoke_availability`, `OfficeAvailabilityError` (Task 3); `create_booking`, `BookingConflictError`, `BookingValidationError`, `get_resource_by_id` (existing `booking_service.py` / Task 5); `add_map_point(..., resource_id=...)` (Task 8); `_format_booking_period` (`frontend/bookings.py:72`, cross-module reuse per design doc — see Global Constraints).
- Produces: `_clamp_range_to_window(value, window_start, window_end) -> dict[str, str]` pure helper (frontend-only, tested directly).

- [ ] **Step 1: Write the failing pure-helper tests**

Append to `tests/test_floorplan_ui_helpers.py`:

```python
from datetime import date


def test_clamp_range_to_window_keeps_value_inside_bounds():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-08-05", "to": "2026-08-08"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-05", "to": "2026-08-08"}


def test_clamp_range_to_window_clamps_start_before_window():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-07-20", "to": "2026-08-08"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-01", "to": "2026-08-08"}


def test_clamp_range_to_window_clamps_end_after_window():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(
        {"from": "2026-08-05", "to": "2026-09-01"},
        date(2026, 8, 1), date(2026, 8, 15),
    )
    assert result == {"from": "2026-08-05", "to": "2026-08-14"}


def test_clamp_range_to_window_falls_back_on_invalid_value():
    from not_dot_net.frontend.floorplan import _clamp_range_to_window

    result = _clamp_range_to_window(None, date(2026, 8, 1), date(2026, 8, 15))
    assert result == {"from": "2026-08-01", "to": "2026-08-14"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k clamp -v`
Expected: FAIL — `ImportError: cannot import name '_clamp_range_to_window'`

- [ ] **Step 3: Implement `_clamp_range_to_window` and `_qdate_option_date`**

Add to `not_dot_net/frontend/floorplan.py`, near the other pure helpers (after `_pin_kind_select_options`):

First, add `from datetime import date, timedelta` to the top-level imports of `not_dot_net/frontend/floorplan.py` (it currently has none) — every function added in this task uses `date`/`timedelta`.

```python
def _qdate_option_date(value) -> str:
    return value.isoformat().replace("-", "/")


def _clamp_range_to_window(value, window_start, window_end_exclusive) -> dict[str, str]:
    """Clamp a date-range dict to [window_start, window_end_exclusive). Falls
    back to the full window when value is missing/invalid."""
    window_last_day = window_end_exclusive - timedelta(days=1)
    default = {"from": str(window_start), "to": str(window_last_day)}
    if not isinstance(value, dict):
        return default
    try:
        start = date.fromisoformat(value["from"])
        end = date.fromisoformat(value["to"])
    except (KeyError, TypeError, ValueError):
        return default
    start = max(start, window_start)
    end = min(end, window_last_day)
    if start > end:
        return default
    return {"from": str(start), "to": str(end)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k clamp -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing live-render tests**

Append to `tests/test_floorplan_ui_helpers.py`:

```python
async def _create_staff_user(email: str) -> DbUser:
    async with session_scope() as session:
        db_user = DbUser(id=uuid.uuid4(), email=email, hashed_password="x", is_active=True)
        session.add(db_user)
        await session.commit()
        await session.refresh(db_user)
        return db_user


async def test_pin_actions_shows_offer_button_for_owner(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner@test.com")
    resource = await create_resource("Room 301", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 301", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-owner-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, owner, False, point)

    await user.open("/pin-actions-owner-test")
    await user.should_see(t("floorplan_offer_availability"))


async def test_pin_actions_hides_offer_button_for_stranger(user: User, monkeypatch, tmp_path) -> None:
    from not_dot_net.backend.booking_service import create_resource
    from not_dot_net.backend.floorplan_service import add_map_point
    from not_dot_net.frontend.floorplan import _show_pin_actions
    from not_dot_net.frontend.i18n import t
    import not_dot_net.backend.floorplan_service as fs
    import pytest

    monkeypatch.setattr(fs, "FLOORPLAN_ROOT", tmp_path)

    admin = await _make_admin()
    owner = await _create_staff_user(email="owner2@test.com")
    stranger = await _create_staff_user(email="stranger@test.com")
    resource = await create_resource("Room 302", "office", location="Palaiseau",
                                     owner_user_id=owner.id, actor=admin)
    plan = await create_floor_plan("Office Plan 2", _make_image_bytes(), actor=admin)
    point = await add_map_point(plan.id, "Room 302", "room", 50, 50,
                                resource_id=resource.id, actor=admin)

    @ui.page("/pin-actions-stranger-test")
    async def page():
        area = ui.column()
        state = {"selected": plan, "highlight_id": None, "place_mode": False}
        await _show_pin_actions(area, state, stranger, False, point)

    await user.open("/pin-actions-stranger-test")
    with pytest.raises(AssertionError):
        await user.should_see(t("floorplan_offer_availability"))
```

Add `import uuid` and `import pytest` at the top of `tests/test_floorplan_ui_helpers.py` if not already present (`uuid` is already imported there; `pytest` is not — add it, or keep the local `import pytest` inside the second test as shown above).

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -k pin_actions -v`
Expected: FAIL — `_show_pin_actions` doesn't yet show any office-availability UI.

- [ ] **Step 7: Implement the popup, offer dialog, and book dialog**

In `not_dot_net/frontend/floorplan.py`, replace the `on_mouse` handler inside `_render_plan_area` (lines 140-154) so non-admins also reach the popup instead of a bare toast:

```python
        async def on_mouse(e):
            x, y = round(e.image_x), round(e.image_y)
            if is_admin and state.get("place_mode", False):
                await _show_add_pin_dialog(plan_area, state, user, is_admin, plan.id, x, y)
                return
            hit = nearest_map_point(points, x, y)
            state["highlight_id"] = hit.id if hit else None
            image.content = _points_svg(points, state["highlight_id"])
            if hit:
                await _show_pin_actions(plan_area, state, user, is_admin, hit)

        image.on_mouse(on_mouse)
```

Replace `_show_pin_actions` (lines 237-252) entirely:

```python
async def _show_pin_actions(plan_area, state, user, is_admin, point):
    resource = None
    if point.resource_id is not None:
        resource = await get_resource_by_id(point.resource_id)
    is_office = point.kind == "room" and resource is not None and resource.resource_type == "office"

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(point.label).classes("text-h6")
        ui.label(t(f"kind_{point.kind}")).classes("text-sm text-grey")

        if is_office:
            await _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource)

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            if is_admin:
                async def do_delete():
                    dialog.close()
                    await delete_map_point(point.id, actor=user)
                    ui.notify(t("floorplan_pin_deleted"), color="positive")
                    await _render_plan_area(plan_area, state, user, is_admin)

                ui.button(t("delete"), icon="delete", on_click=do_delete).props("color=negative")
    dialog.open()


async def _render_office_availability_section(dialog, plan_area, state, user, is_admin, resource):
    from not_dot_net.backend.office_availability import (
        OfficeAvailabilityError,
        list_availability_windows,
        revoke_availability,
    )
    from not_dot_net.frontend.bookings import _format_booking_period

    is_owner = user.is_active and user.id == resource.owner_user_id
    can_offer = is_owner or is_admin
    windows = await list_availability_windows(resource.id)
    today = date.today()
    open_windows = [w for w in windows if w.end_date > today]

    ui.separator().classes("my-2")
    if open_windows:
        ui.label(t("floorplan_availability_open")).classes("text-sm font-bold")
        for w in open_windows:
            with ui.row().classes("items-center gap-2"):
                ui.label(_format_booking_period(w.start_date, w.end_date)).classes("text-sm text-grey-8")
                if can_offer:
                    async def do_revoke(window=w):
                        try:
                            await revoke_availability(window.id, actor=user)
                        except OfficeAvailabilityError as exc:
                            ui.notify(str(exc), color="negative")
                            return
                        ui.notify(t("floorplan_availability_revoked"), color="positive")
                        dialog.close()
                        await _render_plan_area(plan_area, state, user, is_admin)

                    ui.button(icon="close", on_click=do_revoke).props(
                        "flat dense round size=xs color=negative"
                    )
    else:
        ui.label(t("floorplan_availability_none")).classes("text-sm text-grey")

    with ui.row().classes("gap-2 mt-2"):
        if can_offer:
            ui.button(
                t("floorplan_offer_availability"),
                on_click=lambda: _show_offer_dialog(dialog, plan_area, state, user, is_admin, resource),
            ).props("flat dense color=primary")
        if open_windows and user.is_active:
            ui.button(
                t("book"),
                on_click=lambda: _show_office_book_dialog(
                    dialog, plan_area, state, user, is_admin, resource, open_windows,
                ),
            ).props("flat dense color=primary")


async def _show_offer_dialog(parent_dialog, plan_area, state, user, is_admin, resource):
    from not_dot_net.backend.office_availability import OfficeAvailabilityError, offer_availability

    parent_dialog.close()
    today = date.today()
    default_range = {"from": str(today), "to": str(today + timedelta(days=14))}

    with ui.dialog() as dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_offer_availability")).classes("text-h6")
        min_option = _qdate_option_date(today)
        date_picker = ui.date(default_range).props(
            f"range :options=\"date => date >= '{min_option}'\""
        )

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=dialog.close).props("flat")

            async def do_save():
                val = date_picker.value
                if not isinstance(val, dict):
                    ui.notify(t("required_field"), color="negative")
                    return
                start = date.fromisoformat(val["from"])
                end = date.fromisoformat(val["to"]) + timedelta(days=1)
                try:
                    await offer_availability(resource.id, start, end, actor=user)
                except (PermissionError, OfficeAvailabilityError) as exc:
                    ui.notify(str(exc), color="negative")
                    return
                ui.notify(t("floorplan_availability_offered"), color="positive")
                dialog.close()
                await _render_plan_area(plan_area, state, user, is_admin)

            ui.button(t("save"), on_click=do_save).props("color=primary")
    dialog.open()


async def _show_office_book_dialog(parent_dialog, plan_area, state, user, is_admin, resource, open_windows):
    from not_dot_net.backend.booking_service import BookingConflictError, BookingValidationError, create_booking
    from not_dot_net.frontend.bookings import _format_booking_period

    parent_dialog.close()

    async def _open_for_window(window):
        with ui.dialog() as dialog, ui.card().classes("w-80"):
            ui.label(t("book")).classes("text-h6")
            default_range = _clamp_range_to_window(None, window.start_date, window.end_date)
            min_option = _qdate_option_date(window.start_date)
            max_option = _qdate_option_date(window.end_date - timedelta(days=1))
            date_picker = ui.date(default_range).props(
                f"range :options=\"date => date >= '{min_option}' && date <= '{max_option}'\""
            )
            note_input = ui.input(t("note")).props("outlined dense").classes("w-full")

            with ui.row().classes("justify-end gap-2 mt-2"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")

                async def do_book():
                    val = _clamp_range_to_window(date_picker.value, window.start_date, window.end_date)
                    start = date.fromisoformat(val["from"])
                    end = date.fromisoformat(val["to"]) + timedelta(days=1)
                    try:
                        await create_booking(
                            resource.id, user.id, start, end,
                            note=note_input.value, actor=user,
                        )
                    except (BookingConflictError, BookingValidationError) as exc:
                        ui.notify(str(exc), color="negative")
                        return
                    ui.notify(t("booking_created"), color="positive")
                    dialog.close()
                    await _render_plan_area(plan_area, state, user, is_admin)

                ui.button(t("book"), on_click=do_book).props("color=primary")
        dialog.open()

    if len(open_windows) == 1:
        await _open_for_window(open_windows[0])
        return

    with ui.dialog() as select_dialog, ui.card().classes("w-80"):
        ui.label(t("floorplan_choose_window")).classes("text-h6")
        options = {
            i: _format_booking_period(w.start_date, w.end_date) for i, w in enumerate(open_windows)
        }
        window_select = ui.select(
            options, label=t("floorplan_choose_window"),
        ).props("outlined dense").classes("w-full")

        with ui.row().classes("justify-end gap-2 mt-2"):
            ui.button(t("cancel"), on_click=select_dialog.close).props("flat")

            async def do_continue():
                if window_select.value is None:
                    ui.notify(t("required_field"), color="negative")
                    return
                chosen = open_windows[window_select.value]
                select_dialog.close()
                await _open_for_window(chosen)

            ui.button(t("continue"), on_click=do_continue).props("color=primary")
    select_dialog.open()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_floorplan_ui_helpers.py -v`
Expected: PASS

- [ ] **Step 9: Add i18n keys**

English block, near the other `floorplan_*` keys:

```python
        "floorplan_availability_open": "Available",
        "floorplan_availability_none": "No availability offered",
        "floorplan_offer_availability": "Offer availability",
        "floorplan_availability_offered": "Availability offered",
        "floorplan_availability_revoked": "Availability revoked",
        "floorplan_choose_window": "Choose a window",
        "continue": "Continue",
```

French block, matching position:

```python
        "floorplan_availability_open": "Disponible",
        "floorplan_availability_none": "Aucune disponibilité proposée",
        "floorplan_offer_availability": "Proposer une disponibilité",
        "floorplan_availability_offered": "Disponibilité proposée",
        "floorplan_availability_revoked": "Disponibilité révoquée",
        "floorplan_choose_window": "Choisir une période",
        "continue": "Continuer",
```

- [ ] **Step 10: Run the full suite**

Run: `uv run pytest -q`
Expected: all tests pass — read the actual count and exit code.

- [ ] **Step 11: Commit**

```bash
git add not_dot_net/frontend/floorplan.py not_dot_net/frontend/i18n.py tests/test_floorplan_ui_helpers.py
git commit -m "feat: offer/book/revoke office availability from the floor plan"
```

---

### Task 10: Manual smoke check through the floor plan

**Files:** none (verification only — mirrors how Task 6 of the floor-plan foundation plan caught a real bug that only a live browser check found).

- [ ] **Step 1: Start the dev server**

```bash
uv run python -m not_dot_net.cli serve --host localhost --port 8088
```

- [ ] **Step 2: As an admin, create an office resource and assign an owner**

Open the Bookings tab → Add Resource → type = Office → pick an owner from the dropdown → Save. Confirm no CPU/RAM/HDD/GPU fields appear, and no status badge/transition buttons appear on the resulting card.

- [ ] **Step 3: As the same admin, link a floor plan pin to that office**

Floor Plan tab → toggle "Place pin" → click a spot → kind = Room, link to the office resource just created → Save. Confirm the pin appears.

- [ ] **Step 4: Offer availability**

Click the new pin. Confirm the popup shows "No availability offered" and an "Offer availability" button (since the logged-in admin passes the owner-or-manager check). Offer a window a few days out. Confirm the popup now shows the window and a "Book" button.

- [ ] **Step 5: As a different (non-owner, non-admin) user, book inside the window**

Log in as a plain staff account. Click the same pin. Confirm no "Offer availability" button, but a "Book" button is present. Book a sub-range inside the window. Confirm success and that the booking appears in "My Bookings" on the Bookings tab.

- [ ] **Step 6: Cancel the booking**

From "My Bookings," cancel it. Confirm it disappears.

- [ ] **Step 7: As the admin, revoke the window**

Click the pin again. Confirm the small "×" revoke button next to the window works now that the booking is gone (it should have been blocked with an error if attempted while the booking still existed — optionally verify that by attempting revoke before Step 6 and confirming the error message appears).

- [ ] **Step 8: Confirm equipment booking is unaffected**

Book an existing desktop/laptop resource through the normal Bookings tab flow end-to-end, confirming no regression.

No commit for this task — if a bug is found, return to the relevant earlier task, fix it there with a regression test, and re-run that task's tests plus the full suite before re-attempting this checklist.
