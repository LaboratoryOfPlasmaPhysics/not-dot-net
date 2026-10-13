# Booking a computer for a newcomer — design

Date: 2026-10-13. Status: approved in chat; revised after an opencode review
(see "Review notes" at the end).

## Goal

Someone who onboards a newcomer can book them a computer right away. The booking
covers the newcomer's presence period without typing dates. On the booking page,
picking a person sets the range from their presence.

The onboarding form also gains a departure date, required for every
non-permanent status. The default config already has a required arrival date
(`default_workflows.py:75`); production's stored config has neither.

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
  - a new server-side check in `submit_step`. Today required fields are only
    checked in the browser (`workflow_step.py`); the server checks nothing.
    The new check rejects a submit that misses a required, visible field of
    the current step, or the departure date when the rule asks for it.
- A status that is not a known term (inactive or free entry) counts as not
  permanent, so the departure date stays required. Missing presence data is
  the worse failure.
- An end date before the start date is rejected (same rule as tenures).
- Default onboarding config: the initiation step keeps its `start_date` and
  gets an `end_date` ("Departure date") date field.
- `compute_warnings` also flags a hook `end_date_field` that matches no field.

## 2. Booking linked to an onboarding request

- `Booking.workflow_request_id: UUID | None`, FK to `workflow_request.id`,
  `ondelete=CASCADE`, indexed. One Alembic migration (batch mode for SQLite).
- While the request is pending: `user_id` = the booking maker,
  `workflow_request_id` = the request.
- On COMPLETED (post-commit, like the tenure): resolve the newcomer with the
  same lookup the tenure uses, then set `user_id` to them and
  `workflow_request_id` to NULL. This step is independent of the tenure
  recording: `_record_tenure` returns early when status or employer is
  missing, and the transfer must not be skipped with it. Clearing the link
  keeps a later deletion of the old request from deleting the booking.
- If no user resolves, the booking stays with the maker and the audit event
  `workflow` / `booking_not_transferred` is logged. It sits in the `workflow`
  category so `audit_log._severity` can show it orange next to
  `tenure_not_recorded`; it gets an i18n label.
- Release on REJECTED and CANCELLED, at both call sites: `submit_step` (reject)
  and `cancel_request`, each in the session that changes the status. On
  `delete_request` the FK cascade removes them.
- Each transfer or release is audit-logged under `booking`.

## 3. Person picker on the booking page

- A "Book for" search box above the booking form, shown only when the viewer
  may book for someone else:
  - `manage_bookings`: any active person and any pending onboarding;
  - otherwise: pending onboardings the viewer created (`created_by`).
- New queries in the services:
  - `presence_period(user_id, today)`: the tenure covering today, else the
    next one to start. The existing `current_tenure` only returns open
    tenures, so it misses every fixed-term person.
  - `pending_onboardings_for(actor)`: IN_PROGRESS requests of workflows with a
    tenure hook; all of them for `manage_bookings`, else `created_by == actor`.
- Picking a person sets the range from `presence_period`. An open end leaves
  the end for the viewer to pick.
- Picking a pending onboarding sets the range from the request's
  `start_date_field` / `end_date_field` and books as in §2.
- Presence ends are inclusive; `Booking.end_date` is exclusive. The range maps
  the last day of presence to `end + 1 day`, in one helper.
- `create_booking` keeps its limits (minimum lead days, `max_booking_days`).
  The presence range is clamped to both before it is shown, with a notice
  naming which limit moved it. A newcomer arriving next week gets a booking
  from the earliest allowed day; a 3-year PhD gets the first
  `max_booking_days` and a note to extend later.
- Server-side rules in `create_booking`, the UI gate is not trusted:
  - booking for another existing person needs `manage_bookings`;
  - with `workflow_request_id`: the request must be IN_PROGRESS, its workflow
    must have a tenure hook, and the actor must be its creator or hold
    `manage_bookings`. The booking's `user_id` is the actor, never a value
    from the client.

## 4. "Book a computer" link

- Shown after the initiator submits an onboarding form, and on the detail page
  of a pending onboarding request the viewer may book for.
- Opens the Bookings tab with that request preselected in the picker. Same
  pattern as cloning a request: `app.storage.user["book_for"]` plus the stored
  tab key (`workflow_detail.py:257`, read by `new_request.py:153`).
- Only workflows with a tenure hook get the link.

## 5. Production rollout (not code)

After the code is deployed, and each write confirmed with the user first:

1. Read production's stored `workflows` config and `employment_statuses`
   vocabulary (read-only), and show the user the proposed changes.
2. Update the onboarding workflow through `workflows_config.set` run inside the
   app container (validated by the Pydantic model, not raw SQL):
   - add `start_date` and `end_date` date fields to the initiation step
     (production has neither);
   - add `end_date_field` to the tenure hook;
   - rename "VPN Access Request" to "VPN Access".
3. Set `permanent` on the permanent statuses, through `vocabularies_config.set`,
   with the list the user approved.
4. Check in the browser: the form shows both dates, the departure date turns
   required for a non-permanent status, and the booking link appears.

## Testing

- Reproducer-first unit tests for `departure_required`, the new server-side
  required check (a crafted submit without a required field fails today),
  `_record_tenure` with an end date, `presence_period` with a bounded tenure,
  the inclusive→exclusive end, clamping, transfer on completion (also when
  the tenure is skipped), release on reject and on cancel, cascade on delete,
  and each `create_booking` rule (non-creator linking someone else's request
  is refused).
- NiceGUI user tests for the picker range and the link.
- Migration tested on SQLite; full suite before each commit.

## Out of scope

- Bookings tied to workflows without a tenure hook.
- Moving a linked booking when the request's dates change after booking.

## Review notes (opencode, 2026-10-13)

Accepted and folded in above: no server-side required check exists; arrival
date already in the default config; `current_tenure` ignores bounded tenures;
transfer must not depend on the tenure being recorded; `booking` category is
never orange; lead-days and max-days limits; booking ownership and request
validation in `create_booking`; release at two call sites; inclusive vs
exclusive end; missing queries; unknown status; reuse the clone-prefill
storage pattern.

Not taken:
- "Make the departure date always required": the user chose the per-status
  flag on purpose.
- "Reuse `can_user_act` for the picker gate": it answers who may act on the
  current step, not who may book for the newcomer. The creator usually is not
  the actor of later steps.
- Race between a transfer and a direct booking cancel: the transfer updates by
  `workflow_request_id`, so a cancelled booking simply isn't there to move.
