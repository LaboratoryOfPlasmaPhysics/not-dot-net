# Booking a computer for a newcomer

Date: 2026-10-13. Simple version, agreed with the user.

1. **Departure date.** The default onboarding form gets an optional `end_date`
   next to `start_date`. The tenure hook gets `end_date_field`, so a recorded
   tenure keeps its end.
2. **"Book a computer" button.** Shown after an onboarding form is submitted,
   and on a pending onboarding's page. It opens the Bookings tab with the
   newcomer's dates filled in. Workflows opt in with
   `WorkflowConfig.computer_booking` (on for the default onboarding).
3. **Booking linked to the request.** `Booking.workflow_request_id`, nullable,
   `ondelete=SET NULL`. The booking belongs to whoever booked it, with the
   newcomer's name in the note. Rejecting or cancelling the request frees it.
   Only the request's creator or a `manage_bookings` holder may link a
   booking to it, and only while it is in progress.
4. **"Book for" picker on the booking page.** Lists pending onboardings the
   viewer may book for, and (for `manage_bookings`) every active person.
   Picking one sets the date range from their dates: request dates, or the
   person's tenure covering today, else their next one. The range is fitted
   to the booking limits (minimum notice, maximum length).

Rollout: once deployed, production's onboarding config gets `computer_booking`
and the `start_date` / `end_date` fields, through the app's validated config
code, with the user's OK.
