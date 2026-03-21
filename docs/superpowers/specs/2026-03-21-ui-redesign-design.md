# UI Redesign — Design Spec

## Overview

Redesign the not-dot-net intranet UI from placeholder tabs to a functional people directory with onboarding initiation. Built entirely with NiceGUI components + Tailwind CSS. No custom JS or CSS files.

## Pages

### 1. App Shell (all authenticated pages)

Top nav bar using `ui.header` + `ui.tabs`:

```
┌─────────────────────────────────────────────────┐
│  LPP Intranet    [People] [Onboarding]    👤 ▼  │
├─────────────────────────────────────────────────┤
│              Page content here                  │
└─────────────────────────────────────────────────┘
```

- App name on the left, tabs in the center, user dropdown on the right
- User dropdown: "My Profile" (switches to People tab and expands own card), "Logout"
- Tabs are role-aware: "Onboarding" visible to all logged-in users
- No footer, no drawer
- Route `/` is auth-protected (redirect to `/login` if unauthenticated, using `current_active_user_optional` + redirect pattern from `user_page.py`)
- People tab is the default view

### 2. People Directory (`/`, People tab)

**Search bar**: Single input at the top. Filtering is server-side via NiceGUI reactivity (Python `on_change` callback toggling card visibility). No custom JS. Filters by name, team, office, email. Lab is <500 people so no pagination needed.

**Card grid**: Responsive layout using Tailwind grid classes on `ui.element('div')` (e.g. `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4`). Each card shows:
- Avatar placeholder (or photo later)
- Full name (falls back to email when `full_name` is null)
- Team/department
- Office number

**Expandable cards**: Clicking a card expands it vertically — the card gets taller and pushes the row below it down. Simple CSS height transition. Shows full details:
- Phone, email, employment status (permanent, PhD, intern, visitor...), role/title
- Only one card expanded at a time (clicking another collapses the current one)
- Close button or click again to collapse

**Self-edit**: When viewing your own expanded card, an "Edit" button appears. Switches card to edit mode inline:
- Editable fields (v1): phone, office
- Read-only fields: full_name, email, team
- Save / Cancel buttons
- On save: API call via existing FastAPI-Users `/users/me` route, collapse card, toast notification

**Superuser edit**: Users with `is_superuser=True` see "Edit" on every card and can edit all fields (including full_name, email, team, employment_status, title). They also see a "Delete" button (with confirmation dialog). Deleting a user who has onboarding requests sets `created_by` to null (SET NULL cascade).

### 3. Onboarding (`/`, Onboarding tab)

A form to initiate bringing a new person into the lab. This is the first step of a future multi-step workflow.

**Form fields**:
- New person's name (text input)
- New person's email (email input)
- Expected role/status (dropdown: researcher, PhD student, intern, visitor)
- Team/department (dropdown — values from `Settings.teams` config list)
- Expected start date (date picker)
- Optional note/comment (textarea)

**On submit**: Record saved to DB with status "pending". Toast confirmation. No email/link/workflow yet — future work.

**Request list**: Below the form, a list of existing onboarding requests. Regular users see their own requests only. Superusers see all.

**Access**: Any logged-in user can create a request.

### 4. Login Page (`/login`)

Standalone page, no nav bar. Centered card:
- "LPP Intranet" title above the card
- Email + password inputs
- Login button
- No registration link (users come from AD or are created by superusers)
- Error feedback via toast notifications

No changes to auth logic — visual refresh only.

## Data Model Changes

**Person directory**: Extend the existing `User` model with profile columns. This follows the pattern already established (the model already extends `SQLAlchemyBaseUserTableUUID` with `auth_method`).

**New fields on User model**:
- `phone: Mapped[Optional[str]]`
- `office: Mapped[Optional[str]]`
- `team: Mapped[Optional[str]]`
- `title: Mapped[Optional[str]]`
- `employment_status: Mapped[Optional[str]]` (permanent, PhD, intern, visitor)
- `full_name: Mapped[Optional[str]]`

**Migration strategy**: No Alembic for v1. The app is pre-deployment with few/no real users. `create_db_and_tables()` handles table creation. For existing dev databases, delete and recreate. Document this in the upgrade notes.

**Onboarding request model** (new table `onboarding_request`):
- `id: UUID` (PK)
- `created_by: Optional[UUID]` (FK to User, SET NULL on delete)
- `person_name: str`
- `person_email: str`
- `role_status: str`
- `team: str`
- `start_date: date`
- `note: Optional[str]`
- `status: str` (pending — future: invited, completed, etc.)
- `created_at: datetime`
- `updated_at: datetime`

**Schema changes**: Extend `UserRead` and `UserUpdate` in `schemas.py` with the new profile fields. This lets the existing FastAPI-Users `/users` routes handle profile data without custom CRUD endpoints.

## API Endpoints

**Directory**: Reuse FastAPI-Users built-in routes by extending schemas:
- `GET /users` — list all users with profile info (existing route, extended schema)
- `GET /users/me` — current user profile (existing route)
- `PATCH /users/me` — self-edit limited fields (existing route, frontend enforces field restrictions)
- `PATCH /users/{id}` — superuser edit all fields (existing route)
- `DELETE /users/{id}` — superuser only (existing route)

**Onboarding** (new router at `/api/onboarding`):
- `POST /api/onboarding` — create request (any authenticated user)
- `GET /api/onboarding` — list requests (own for regular users, all for superusers)

## Configuration Changes

Add to `Settings`:
- `teams: list[str]` — list of team/department names for dropdowns (e.g. `["Plasma Physics", "Instrumentation", "Space Weather", ...]`)

## Technical Approach

- All UI built with NiceGUI components (`ui.card`, `ui.input`, `ui.tabs`, `ui.expansion`, etc.) and Tailwind grid classes on `ui.element('div')`
- Search filtering via NiceGUI server-side reactivity (Python callbacks toggling visibility)
- Existing auth system unchanged — `current_active_user` / `current_active_user_optional` dependencies used for all protected pages
- `is_superuser` flag used for permission checks (no new role system)
- SQLAlchemy async with existing engine/session setup
- The separate `/user/profile` page is removed — profile editing happens inline on the directory card

## Frontend Module Structure

Following existing pattern where each page module exports a `setup()` function:
- `frontend/directory.py` — People directory tab (card grid, search, expand/edit)
- `frontend/onboarding.py` — Onboarding tab (form + request list)
- `frontend/login.py` — Login page (refreshed styling)
- `frontend/user_page.py` — Removed (replaced by inline card edit)
- `app.py` — `main_page` replaced with app shell (header + tabs) calling the above modules

## Out of Scope (future work)

- AD sync (periodic import from Active Directory)
- Full onboarding workflow (email, secure link, document upload)
- Photo upload
- Role-based access beyond superuser
- Team/department management UI
- News/announcements section
- Alembic migrations (add when the app has real users)
