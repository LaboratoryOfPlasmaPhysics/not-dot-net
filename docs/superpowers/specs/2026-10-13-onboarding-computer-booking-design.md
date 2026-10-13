# Booking a computer for a newcomer — design

Date: 2026-10-13. Status: approved in chat, awaiting spec review.

## Goal

Someone who onboards a newcomer can book them a computer right away. The booking
covers the newcomer's presence period without typing dates. On the booking page,
picking a person sets the range from their presence.

The onboarding form also gains the presence dates it lacks: an arrival date for
everyone, and a departure date for every non-permanent status.

## Decisions (from the user)

1. A booking made before the newcomer has an account belongs to the initiator,
   is linked to the onboarding request, and moves to the newcomer when the
   request completes. Rejected, cancelled or deleted requests release it.
2. Departure date is required unless the chosen status is permanent. Each
   employment-status term carries a `permanent` flag.
3. The booking-page person picker books *for* the picked person, so it is
   permission-gated.
4. Production's stored config (onboarding form, statuses) is updated as part of
   the rollout, not left to manual edits.

## 1. Presence dates

- `TenureHookConfig` (`config.py`) gains `end_date_field: str = "end_date"`.
- `_record_tenure` reads it and passes `end_date` to `add_tenure`. An unparsable
  or missing value records an open tenure, as the start date does today.
- `VocabularyTerm` gains `permanent: bool = False`. The vocabulary editor shows
  the checkbox only for the `employment_statuses` vocabulary.
- Rule, owned by the tenure hook (no generic conditional-required system):
  a workflow with a hook requires `end_date_field` when the value of
  `status_field` is a term that is not `permanent`. One pure function,
  `departure_required(hook, data, statuses) -> bool`, used by:
  - the form, which shows the field as required and re-evaluates when the
    status changes;
  - the server-side step validation, so a crafted submit cannot skip it.
- An end date before the start date is rejected (same rule as tenures).
- Default onboarding config: the initiation step gets `start_date` ("Arrival
  date", required) and `end_date` ("Departure date") date fields.
- `compute_warnings` also flags a hook `end_date_field` that matches no field.

## 2. Booking linked to an onboarding request

- `Booking.workflow_request_id: UUID | None`, FK to `workflow_request.id`,
  `ondelete=CASCADE`, indexed. One Alembic migration (batch mode for SQLite).
- While the request is pending: `user_id` = the booking maker,
  `workflow_request_id` = the request.
- On COMPLETED, next to the tenure recording (post-commit, same resolved user):
  set `user_id` to the newcomer and `workflow_request_id` to NULL. Clearing the
  link keeps a later deletion of the old request from deleting the booking.
  If no user resolves, the booking stays with the maker and an audit event
  `booking_not_transferred` is logged (shown orange, like `tenure_not_recorded`).
- On REJECTED or CANCELLED: delete the linked bookings in the same session as
  the status change. On delete: the FK cascade removes them.
- Each transfer or release is audit-logged under `booking`.

## 3. Person picker on the booking page

- A "Book for" search box above the booking form, shown only when the viewer
  may book for someone else:
  - `manage_bookings`: any active person and any pending onboarding;
  - otherwise: pending onboardings the viewer created (`created_by`).
- Picking a person sets the range to their current tenure, or else their next
  one. An open end leaves the end for the viewer to pick.
- Picking a pending onboarding sets the range from the request's
  `start_date_field` / `end_date_field` and books as in §2.
- The range still goes through `_normalize_booking_range` (minimum lead days)
  and shows the "range shifted" notice when it moves.
- `create_booking` gains `for_user_id` / `workflow_request_id` handling and
  checks the same permission server-side; the UI gate alone is not trusted.

## 4. "Book a computer" link

- Shown after the initiator submits an onboarding form, and on the detail page
  of a pending onboarding request the viewer may book for.
- Opens the Bookings tab with that request preselected in the picker (through
  the shell's stored tab key plus a stored "book for" target).
- Only workflows with a tenure hook get the link.

## 5. Production rollout (not code)

After the code is deployed, and each write confirmed with the user first:

1. Read production's stored `workflows` config and `employment_statuses`
   vocabulary (read-only), and show the user the proposed changes.
2. Update the onboarding workflow through `workflows_config.set` run inside the
   app container (validated by the Pydantic model, not raw SQL):
   - add `start_date` and `end_date` date fields to the initiation step;
   - add `end_date_field` to the tenure hook;
   - rename "VPN Access Request" to "VPN Access".
3. Set `permanent` on the permanent statuses, through `vocabularies_config.set`,
   with the list the user approved.
4. Check in the browser: the form shows both dates, the departure date turns
   required for a non-permanent status, and the booking link appears.

## Testing

- Reproducer-first unit tests for `departure_required`, `_record_tenure` with an
  end date, transfer on completion, release on reject/cancel, cascade on delete,
  and the permission check in `create_booking`.
- NiceGUI user tests for the picker range and the link.
- Migration tested on SQLite; full suite before each commit.

## Out of scope

- Bookings tied to workflows without a tenure hook.
- Moving a linked booking when the request's dates change after booking.
