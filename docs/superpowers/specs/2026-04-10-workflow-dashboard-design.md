# Workflow Dashboard Improvements — Design Spec

## Goal

Improve the workflow dashboard so users can scan, prioritize, and act on requests with confidence. The current cards lack context (no timeline, no people, no urgency), and there's no dedicated view for a single request.

## Approach

Enriched actionable cards + dedicated detail page. Cards become read-only summaries that link to a full detail page. No more inline expand/collapse with embedded forms.

---

## 1. Enriched Actionable Cards

Cards in the "Awaiting My Action" grid become compact-enriched (single vertical flow):

- **Header**: target name + workflow type label
- **Named step progress bar**: colored segments per step, labels below (✓ request · ✓ newcomer_info · ● admin_validation)
- **People**: requester name, last actor + action
- **Last comment** (if any): quoted block with author and date
- **Urgency badge**: age at current step, color-coded (green/orange/red)
- **Click anywhere** → navigates to `/workflow/request/{id}`

Cards sorted by urgency (oldest first). Grid stays responsive (1/2/3 columns).

## 2. Request Detail Page

New NiceGUI page at `/workflow/request/{id}`.

**Access control:**
- Request creator (created_by)
- Anyone who can act on the current step
- Users with `view_audit_log` permission

**Layout (timeline-centered, top to bottom):**

1. **Header**: back link, "Workflow Label — Target Name", requester + date, status badge, urgency badge
2. **Named step progress bar**: larger version with step labels
3. **Vertical timeline**: one entry per WorkflowEvent
   - Timestamp, actor name (resolved from user ID, or "via token link")
   - Action label (created, submitted, approved, rejected, saved draft)
   - Data snapshot collapsed by default (expandable toggle for form steps)
   - File attachments as download links
   - Comments as quoted blocks
   - Current step: pulsing dot indicator
4. **Action panel** (visible only if user can act):
   - Approval steps: comment textarea + Approve/Reject buttons
   - Form steps: `render_step_form` reuse
   - Hidden if request is terminal (completed/rejected) or user lacks permission

**File download endpoint**: `GET /workflow/file/{file_id}` with auth check (creator, current step actor, or admin).

## 3. Notifications Badge

**Dashboard tab**: dynamic label "Dashboard (N)" showing actionable count. Refreshes on tab selection.

**Browser tab title**: `(N) Not-dot-net` prefix when count > 0. Polled every 60 seconds via `ui.timer` + `ui.run_javascript` to set `document.title`.

## 4. Stale Request Highlighting

**Urgency thresholds** — configurable via `DashboardConfig` ConfigSection:
- Green (fresh): < 2 days at current step
- Orange (aging): 2–7 days
- Red (stale): > 7 days

Age computed from last event timestamp on current step (not `created_at`).

**Applied to:**
- Actionable cards: colored badge with "⏱ Nd" label
- My Requests table: new "age" column with same badge, sortable
- Detail page header: same badge

## 5. "My Requests" Table

- Rows click-navigate to `/workflow/request/{id}` (no inline event expansion)
- Named step progress bar replaces "2/3" text
- New age column with urgency badge
- Existing filters (type, status, search) unchanged

---

## Files

**New:**
- `frontend/workflow_detail.py` — `/workflow/request/{id}` page
- `backend/workflow_file_routes.py` — `GET /workflow/file/{file_id}` download endpoint

**Modified:**
- `frontend/dashboard.py` — enriched cards, table links, named progress, age column, remove inline expand
- `frontend/shell.py` — badge count on Dashboard tab, browser title timer
- `frontend/workflow_step.py` — extract urgency badge + named step progress as reusable functions
- `config.py` — `DashboardConfig` model (urgency thresholds)
- `backend/workflow_service.py` — `get_request(id)`, `get_actionable_count(user)`, step age helper

**Unchanged:**
- `workflow_engine.py` (stays pure)
- `workflow_models.py` (no schema changes)
- `notifications.py`
