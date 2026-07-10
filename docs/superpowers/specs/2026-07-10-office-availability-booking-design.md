# Office Availability Booking — Design

## Motivation

Use case (not a system requirement, just the scenario that prompted this): during heat waves, many staff are on vacation and their offices sit empty and unused while colleagues swelter in shared/hot offices. The system doesn't need to know anything about heat or vacations — it just needs a clean way for an office's owner to say "this office is available from X to Y" and for anyone else to book a slot within that window.

This builds directly on the Floor Plan Foundation (Phase 1, shipped 2026-07-10, `docs/superpowers/plans/2026-07-10-floorplan-foundation.md`): `MapPoint.resource_id` was scaffolded specifically for this kind of extension, and the existing equipment booking system (`booking_service.py`) already solves reservation, conflict-checking, and row-locking correctly — this feature reuses that machinery rather than duplicating it.

## Goals

- An office's declared owner (or an admin) can open a date-range window during which the office is bookable.
- Any employee can book a slot inside an open window, exactly like booking equipment today.
- Discovery and booking happen through the floor plan: click a room pin, see availability, book or offer.
- Zero duplication of the booking/conflict/audit/notification logic that already exists and works.

## Non-goals (out of scope for this phase)

- No notification email specifically for "a window opened" — the existing booking-confirmation email is enough for v1.
- No recurring/annual availability windows — one-off date ranges only.
- No capacity beyond one booking at a time per office — this falls out of reusing the existing single-resource conflict check, no new logic needed.
- No formal building-wide office-ownership import/sync — owners are assigned one at a time via the admin resource editor, same manual process used for equipment today.

## Data model

`Resource` (existing table) gains two nullable columns, used only when `resource_type == "office"`:

- `owner_user_id: uuid | None` — FK → `user.id`, `ondelete="SET NULL"`.
- `resource_type` gains a new valid value, `"office"`, alongside `"desktop"`/`"laptop"`.

New file `backend/office_availability.py` owns a new table and its own small service, kept separate from `booking_service.py`:

- **`OfficeAvailability`**: `resource_id` (FK → `resource.id`, `ondelete="CASCADE"`), `start_date`, `end_date` (exclusive, same convention as `Booking.end_date`), `offered_by` (FK → `user.id`), `id`, `created_at`.
- `offer_availability(resource_id, start_date, end_date, actor) -> OfficeAvailability` — requires `actor.id == resource.owner_user_id or has_permissions(actor, "manage_bookings")`; audit-logged.
- `list_availability_windows(resource_id) -> list[OfficeAvailability]`.
- `revoke_availability(window_id, actor) -> None` — same permission rule; raises if any `Booking` on that resource falls within the window being revoked (mirrors `delete_resource`'s "resolve conflicts first" posture — no migration-dialog complexity needed, since cancelling the offending booking is already a one-click admin/owner action elsewhere).
- `is_covered(windows: list[OfficeAvailability], start_date, end_date) -> bool` — pure function, no DB access. True if `[start_date, end_date)` is fully covered by the union of the given windows. Overlapping/duplicate windows need no special handling — the union check absorbs them for free.

## Booking integration

`booking_service.create_booking` gets exactly one new branch, gated by resource type:

```python
if resource.resource_type == "office":
    windows = await list_availability_windows(resource_id)
    if not is_covered(windows, start_date, end_date):
        raise BookingValidationError("Requested dates are outside the offered availability window")
```

Everything else in `create_booking` — the resource row lock, the overlap-conflict check against existing `Booking` rows, audit logging, email notification — is untouched and shared with equipment bookings. Equipment bookings never call `is_covered`. This is the only integration point between the two domains: `office_availability.py` owns windows and ownership, `booking_service.py` owns reservations and conflicts.

`Booking.os_choice`/`Booking.software_tags` are already nullable and are simply left unset for office bookings — no schema change needed there.

## Frontend

**Floor plan (`frontend/floorplan.py`)**: clicking a room-kind pin linked to an office `Resource` extends the existing popup:
- Shows currently open `OfficeAvailability` windows (if any).
- If the viewer is the owner or an admin: an **"Offer availability"** button opens a small date-range dialog → `offer_availability`.
- If a window is open: a **"Book"** button opens the same date-range + note booking widget already used in `bookings.py`'s resource detail (no new booking UI invented) — with `os_choice`/`software_tags` omitted since those are equipment-only. If there is exactly one open window, the date picker is clamped to it directly. If there are multiple, non-contiguous open windows, the dialog first asks which window to book against (a plain select), then clamps the date picker to that window — avoids building a multi-range calendar widget for what should be a rare case. `create_booking`'s server-side check remains the authoritative guard either way, not just the UI hint.

**Admin resource editor (`bookings.py::_show_resource_dialog`)** becomes type-aware:
- `RESOURCE_TYPES` gains `"office"`.
- For `resource_type="office"`, the dialog shows a user-picker for `owner_user_id` instead of the cpu/ram/hdd/gpu spec inputs (those don't apply to a room).

**Equipment Bookings tab (`bookings.py`)**: office resources are excluded from this list (`resource_type != "office"` filter) — booking an office only happens through the floor plan, avoiding two divergent UX paths (a free-form date picker here vs. a clamped one there) for the same reservation.

**Status FSM hidden for offices**: the existing admin status-transition buttons (`available`/`booked`/`ready`/`in_use`/`returned`/`out_of_service`) in `_render_resource_detail` are wrapped in `if res.resource_type != "office":` — a room has no IT-prep/pickup/return lifecycle. `status` stays at its default `"available"` and is never transitioned for offices.

## Permissions

No new permission key. `manage_bookings` continues to cover admin override for everything (create office resources, assign/reassign owners, force-revoke any window, book on behalf of others) — identical to how it already works for equipment. Offering/revoking a window additionally allows `actor.id == resource.owner_user_id`. Booking an available office follows the existing self-booking rule in `create_booking` unchanged.

## Edge cases

- **Overlapping windows**: allowed, no merge/dedup logic — `is_covered`'s union check handles it.
- **Owner deleted/deactivated**: `owner_user_id` is `SET NULL` (same convention as other user-referencing FKs in this codebase); an ownerless office can only get a new owner via an admin editing the resource. Existing windows and bookings are unaffected.
- **Date semantics**: `end_date` is exclusive (the hand-back day) on both `OfficeAvailability` and `Booking`, identical to today's equipment bookings — reuses `booking_last_day`/`_format_booking_period` for display rather than inventing new date-formatting rules.
- **Revoking a window with a booking inside it**: blocked with a clear error; the owner/admin asks the booker to cancel (or an admin cancels on their behalf via the existing cancel action) before revoking.

## Testing approach

TDD throughout, following the conventions established in the Floor Plan Foundation work:
- Model tests: `OfficeAvailability` cascade-delete on resource deletion, FK integrity.
- Service tests: `offer_availability`/`revoke_availability` permission gates (owner, admin, neither), `revoke_availability` blocked by an in-window booking, `is_covered` as a pure-function table of cases (fully inside, partially outside, exactly matching boundary, multiple overlapping windows, empty window list).
- Integration test: `create_booking` against an office resource — inside a window succeeds, outside a window raises `BookingValidationError`, equipment resources are provably unaffected (no `is_covered` call, existing equipment tests stay green with zero changes).
- Frontend: pure-helper tests for the date-clamp logic and the type-aware resource dialog field selection, plus a manual/Playwright smoke check of the offer → book → cancel → revoke flow through the floor plan, mirroring how Task 6 of the floor-plan plan caught a real bug that only a live browser check would find.

New Alembic migration: two nullable columns on `resource` (`owner_user_id`, and widening `resource_type` if it's constrained anywhere — currently it's a plain `String(50)`, so no constraint change needed) + the new `office_availability` table. Per the lesson from the floor-plan feature's own final review: remember all **four** model-registration sites (`db.py::create_db_and_tables`, `tests/conftest.py::setup_db`, `migrate.py::_create_all`, `alembic/env.py`), not just the two most obviously exercised by tests.
