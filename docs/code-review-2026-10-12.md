# Code Review — 2026-10-12

Full-codebase review at `f1404f0`: security, broken features, UX, UI.
Four parallel reviewers; each finding was checked against the code, and the
items marked **✓** were re-verified independently (code read, script, or live
request). Issue numbers are meant to be referenced by fix commits.

Prefixes: **S** security · **F** broken feature · **W** workflow · **U** UX flow ·
**V** visual / a11y / i18n · **T** tests.

## Status (updated 2026-10-12)

**Fixed:** S1, S2, S4, S5, S7, S11, F1, F2, F3 (dialog only), F4, F5, F6, F8,
F15, W1, W2, W3, W4, W5, U1-U10, U15, V1-V5, T1. Also: overlapping
dashboard refreshes drew the action cards twice (seen in the live check). Also a bug found while fixing F2: `t(name, key=...)`
raised TypeError, so the roles panel and role/vocabulary deletes crashed.
**By design:** S6.
**F3 legacy data:** checked on production 2026-10-12: all 27 resources already
store site codes (`palaiseau`/`jussieu`), so no migration is needed. Only the
dev demo data (`seed_data.py`) uses capitalized names. Production vocabulary
terms have no labels, so raw codes are displayed (V6). Fill in labels in
Settings → Vocabularies.
**V5 fixed (8556686):** production Postgres ran with TimeZone=Europe/Paris,
so now()-filled columns held Paris time and Python-written ones UTC. App
sessions are now pinned to UTC, migration 0021 converts the old rows on the
next deploy, and pages show the viewer's browser timezone.
**Group 3 fixed (45bcc6d, 6a34954, cbf45a3, 7eea9ef, c5cf432):** S3, S8, S9
(LDAP section superuser-only; the tls_mode default is unchanged, production
already uses LDAPS), S10, S12, S13 (cross-site login/logout refused; the JWT
is still not revoked at logout), F7, F9, F10 (office buffer only), F11,
F12 (resource status/owner, tenure replace), F13, F14, W6-W9, U11-U14,
V6-V16. Migration 0022 (audit actor index) runs with 0021 on the next deploy.
**False positive:** F10 "out_of_service resources bookable". The
resource-lifecycle spec says out-of-service is temporary and does not block
future bookings; a test pins it.
**Not done:** F12 export of bookings, availability windows and floor plans
(the export is a content transfer, pg_dump is the backup); JWT revocation
at logout (S13). T2 not investigated: the suite now has 1412 tests.
**Production config to update by hand:** the stored onboarding workflow
has no `start_date` field (W6), and the stored VPN workflow label is still
"VPN Access Request".

## Test suite

- **T1 ✓** `tests/test_permissions.py::test_only_expected_auth_http_routes_are_exposed`
  fails (1 failed / 1275 passed). Not a prod bug: FastAPI 0.143 wraps included
  routers in `_IncludedRouter`, so `/auth/login` is no longer in `app.routes`.
  The route still answers (live `POST /auth/login` → 303, `GET /logout` → 303).
  Fix the test to walk included routers (or hit the route with `TestClient`).
- **T2** 1276 tests collected vs 1295 reported at f1404f0 — 19-test gap not yet
  explained. `pytest` is only in the `test` extra; run with `uv run --extra test`.

## Security

### S1 ✓ High — `manage_users` can grant any role, including to self
`frontend/directory.py:152, 513-523` → `_update_user` `:98-108`. The edit form
offers every role to anyone with `manage_users`; `safe=True` doesn't strip `role`;
nothing checks `manage_roles` or that granted perms ⊆ actor's perms. A secretary
with `manage_users` sets their own role to admin. The same form can rewrite a
superuser's email, which combines with S5 into superuser takeover.
**Fix:** role changes / editing a superuser require `manage_roles` (or superuser),
and refuse granting permissions the actor lacks. Enforce in `_update_user`, not
the UI.

### S2 ✓ High (behind stock HAProxy `option forwardfor`) — spoofable client IP
`backend/audit.py:59-63`. `headers.get("x-forwarded-for")` returns the *first*
header line; HAProxy appends its own as a *separate* line. The attacker-sent line
wins → login throttle (`(username, ip)`) bypassed by rotating XFF, audit IPs forged.
**Fix:** `headers.getlist(...)[-1]` then last comma element, or uvicorn
`proxy_headers` + `forwarded_allow_ips=<haproxy>`.

### S3 Medium — password spraying / unthrottled AD bind dialogs
`frontend/login_throttle.py:24-25`, `frontend/ad_credentials.py:34-50`,
`directory.py:429-446`, `user_management.py:280`. Throttle is per (user, ip) only;
the AD credential dialog binds any `bind_user` (no `USERNAME_RE`) with no throttle
at all. **Fix:** per-IP + global failure budget; route all AD binds through the
throttle.

### S4 Medium — revoked/deactivated users keep power in open tabs
`frontend/shell.py:60-65`, `backend/permissions.py:61-66`. The `User` is captured
at page load; `has_permissions` reads the stale role/`is_superuser`; `is_active`
is never re-checked. Also `admin_settings.py:212-227` YAML save/reset skip
`check_permission`. **Fix:** reload role/flags/is_active by id in
`check_permission`.

### S5 Medium — AD login auto-links local accounts (incl. superuser) by email
`frontend/login.py:157-179`. Any AD object whose `mail`/UPN equals a local
account's email logs in as it and converts it to LDAP. **Fix:** never auto-link
`is_superuser`; require an explicit `ldap_dn`/`ldap_username` link.

### S6 — staff directory is public — **by design, won't fix**
`shell.py:178-179`, `directory.py:252-281`. Guests get the People tab with email,
phone, contract dates, photo. The app is internet-facing (token links). GDPR /
phishing source.
**Resolution:** intentional — it is the lab phone directory, and everything
except the onboarding token page stays on the LAN. Only `/workflow/token/*`
(and its uploads, S7) faces the internet; rank other findings with a LAN
attacker in mind (e.g. S13 is low priority).

### S7 Medium — upload size enforced only client-side; Pillow bomb limit default
`directory.py:550-554`, `workflow_token.py:163-169`, `new_request.py:211-216`,
`profile_photo.py:51-70`. `max_file_size` is a Quasar prop; handlers `read()` the
whole body first. **Fix:** check `upload.size` before reading, cap body at
HAProxy, lower `Image.MAX_IMAGE_PIXELS`.

### S8–S13 Low
- **S8** `auth/ldap.py:187, 268-284` — bind as UPN `user@domain`, lookup by
  `sAMAccountName=user`; if they differ, A's password logs in as B. Look up the
  bound identity.
- **S9** `ldap.py:58-59` — `tls_mode` defaults to NONE; any `manage_settings`
  holder can repoint `url` and harvest passwords. Default LDAPS/StartTLS; make the
  `ldap` section superuser-only.
- **S10** `admin_settings.py:136-141` — SMTP password masked visually only; the
  value is still sent to the browser. Send blank, keep stored value when blank.
- **S11** `workflow_uploads.py:56-61` — `_check_magic` passes unknown content.
  Require a matching signature for the claimed extension.
- **S12** `setup_wizard.py:73-114` — `/setup` open whenever no superuser exists,
  non-atomic check. Gate with a one-time token printed at startup.
- **S13** `login.py:32-44` — no CSRF on login (login-CSRF) / GET logout; JWT not
  revoked on logout (valid 12 h).

## Broken features

### F1 ✓ High — admin save on a role-less user crashes
`directory.py:519-523, 622, 79-86`. Role select is `None` for `role == ""`;
`compute_update_diff` normalizes only the submitted side → every save sends
`role=None` → `NOT NULL constraint failed: user.role`, nothing saved, no message.
Most users have no role (`default_role=""`). **Fix:** normalize both sides in
`compute_update_diff`, map `None` role to `""`.

### F2 High — deleting the `default_role` bricks the Roles page
`admin_roles.py:79-84, 147-149`. The guard only counts holders; re-render does
`ui.select(value=<deleted>)` → `ValueError`. LDAP provisioning keeps assigning it.
**Fix:** refuse (or reset default) when `role_key == cfg.default_role`.

### F3 High — editing a resource silently rewrites its location
`bookings.py:743-748, 803` (+ filter/grouping `278, 310-313`). Options are active
site codes only; legacy `"Jussieu"` falls back to `sites[0]`. Site filter/grouping
never match legacy values. **Fix:** include stored value in options; migrate legacy
locations to codes.

### F4–F6 Medium — `ui.select` with a value not in options → ValueError
Same NiceGUI mechanism as F2. Shared fix idea: a helper that merges the current
value into options.
- **F4** `bookings.py:767-774` — office edit crashes when owner is inactive.
- **F5** `directory.py:831-832` — tenure edit crashes when status/employer term is
  deactivated, and for every `personnel_import` tenure (raw `PostDoc` vs code
  `postdoc`).
- **F6** `floorplan.py:578-584, 623-626`, `bookings.py:220` — QDate `range` mode
  emits a string for a single day: offer dialog says "required", book dialog books
  the whole window. *(Inferred from Quasar behavior; confirm in browser.)*

### F7–F9 Medium
- **F7** `audit_log.py:236-237, 350` — severity filter applied in Python after
  `limit=500`; older critical events invisible, no truncation notice. Push into SQL.
- **F8** `auth/ldap.py:759-791` — bulk sync matches by email only (mail change →
  duplicate account) and never deactivates users deleted from AD; the `try` wraps
  in-memory setattr while the batch commit is unguarded.
- **F9** `directory.py:89-95, 209` — every directory open loads all photo blobs and
  pushes them inline (~6 MB / 300 users). `_finish_save` ignores inactive users →
  stale card. Defer `photo`, serve via cached route.

### F10–F15 Low
- **F10** `booking_service.py:389-412` — `out_of_service` resources bookable;
  equipment buffer also applied to offices.
- **F11** `personnel_import.py:111-151` — groups by name (homonyms merged),
  synthetic email collisions, skips tenure date/overlap validation.
- **F12** `data_io.py` — export drops resource owner/status, availability windows,
  floor plans, bookings; `import_tenures` ignores `replace`.
- **F13** `alembic/versions/0018_*.py:17, 39` — FK ops outside batch mode fail on
  SQLite; `migrate._schema_initialized` misdetects a `create_all` dev.db. Prod
  (Postgres) unaffected.
- **F14** `audit.py:159-161` — `lower(actor_email)` defeats the index on Postgres.
- **F15** `directory.py:118` — `_delete_user` still uses the opt-in
  `if actor is not None` guard D2 removed elsewhere; the D2 guard test only scans
  `backend/`.

## Workflow

### W1 ✓ Medium — token link only emailed for events named submit/request_corrections
`backend/notifications.py:103-105`. A `approve → target_person` transition
regenerates the token but the target gets the plain email linking to a login-only
page → request stuck until Resend. Conversely, every recipient of a token-creating
`submit` gets the token URL. **Fix:** pick the token template when the token was
just (re)generated *and* the recipient is the target person.

### W2 Medium — partially-successful AD group retry never resolves
`effect_retry.py:110-114`, `ldap.py:933-938`. Retry re-adds all groups; AD answers
`attributeOrValueExists` for already-added ones → counted as failure forever
(reproduced: 3 retries, `0 resolved, 1 failing`). **Fix:** treat
`attributeOrValueExists` (add) / `noSuchAttribute` (remove) as success.

### W3 ✓ Medium — detail-page form steps have no uploads, files, or draft
`workflow_detail.py:515-525`. `render_step_form` gets no `files`/`on_file_upload`/
`on_save_draft` → required file fields can never be satisfied (returning person on
`newcomer_info`; ordre_de_mission corrections can't replace the file). **Fix:**
pass them as `workflow_token.py` does.

### W4 Medium — `visible_when {cb: False}` field hidden but required
`workflow_step.py:404/421` vs `:474`. Initial render sees `None` (hidden), submit
sees `False` (visible + required) → "required field: X" for an invisible field.
**Fix:** seed visibility state from the rendered widgets' values.

### W5 Medium — editor can strand in-flight requests
`workflow_editor.py:203-209, 266-278`; `workflow_engine.py:62-68`. Deleting or
renaming a step key ignores open requests on it ("Unknown step"), doesn't rewrite
`corrections_target`, `notifications[].step`, `WorkflowFile.step_key`. **Fix:**
block save when removed keys have open requests; rewrite references on rename.

### W6–W9 Low
- **W6** `default_workflows.py:56-111` — stock onboarding has no `start_date`
  field; tenure starts on IT-completion day (already flagged by `compute_warnings`).
- **W7** `workflow_service.py:386-392` — returning person with an open tenure:
  overlap error only logged, tenure silently dropped.
- **W8** `default_workflows.py:41-47` — `vpn_access` effect with `groups: []`
  still prompts for AD creds, and queues an unresolvable retry if no `ldap_dn`.
- **W9** `effect_retry.py:81` — `retry_pending_effects` has no service-level
  permission check (D2 rule) and no row lock against concurrent retries.

## UX (flows)

- **U1 ✓ High** `workflow_detail.py:531-549` — one-time AD password dialog isn't
  `persistent`; Esc/backdrop closes it forever without `on_close`.
- **U2 ✓ High** `directory.py:744-756` — tenure delete with no confirmation
  (`confirm_delete_tenure` key exists, unused).
- **U3 Medium** `workflow_step.py:361, 486, 528-542`, `ad_credentials.py:53` — no
  button is disabled/loading during async work; double-click New request → two
  requests. One helper fixes all.
- **U4 Medium** `workflow_step.py:539` — Reject: no confirm, no required comment.
- **U5 Medium** `new_request.py:242-246` — collapsing the open card's header
  re-renders the form and loses typed data.
- **U6 Medium** `workflow_editor.py:96` — editor dialog not persistent; Esc drops
  unsaved edits, bypassing the dirty check.
- **U7 Medium** `user_management.py:181-252` — filters rendered below 50 rows;
  each filter change rebuilds the table and clears selection.
- **U8 Medium** `user_management.py:226` — edit only via row double-click
  (impossible on touch).
- **U9 Medium** `bookings.py:404-411` — "Booked by X" (orange) beside "Available"
  (green lifecycle badge) — contradictory.
- **U10 Medium** `admin_settings.py:68` — duplicate "Roles" section (editor +
  generic ConfigSection); "AD Accounts" both tab and section.
- **U11–U15 Low** — no "no results" state in directory search (`directory.py:162`);
  empty `users_ous` makes Complete fail with no hint (`workflow_step.py:316`);
  bookings view can't show today (`bookings.py:36-64`); active tab stored by
  translated label, lost on language switch (`shell.py:105-111`); Enter doesn't
  submit the AD credential dialog (`ad_credentials.py:29`).

## UI (visual / a11y / i18n)

- **V1 Medium** `app.py:192` — no Quasar `language`, built-ins stay English in FR
  ("Records per page", date pickers).
- **V2 Medium** `dashboard.py:236, 261, 273` — raw status keys (`in_progress`).
- **V3 Medium** `workflow_detail.py:307`, `dashboard.py:387` — timeline shows
  `admin_validation: approve`; unused `event_*` keys exist for this.
- **V4 Medium** `workflow_step.py:113-139` — required markers only on file fields;
  missing fields reported in one toast, not highlighted.
- **V5 Medium** `workflow_detail.py:304`, `audit_log.py:134`,
  `user_management.py:140` — UTC times shown unlabeled (look 2 h off).
- **V6–V16 Low** — raw enum/vocab codes in users table, person status, tenure chip,
  bookings type filter, audit detail (`RequestStatus.REJECTED`); hardcoded EN in
  `admin_settings.py:68, 89` and ConfigSection labels; age unit `d` in FR
  (`dashboard.py:134`); dashboard rows not keyboard-reachable (`dashboard.py:232`);
  10 tabs wrap the header at 1280 px, no mobile scroll arrows, duplicate
  `manage_accounts` icon, account button named "person", title "NotDotNet" vs
  "LPP Intranet" (`shell.py:115-163`); AD-account form inputs unsized
  (`workflow_step.py:298-336`); user-management filter selects truncated
  (`user_management.py:232`); editor tree `w-72` clips at 390 px, low-contrast
  warning text; page preview shows raw Markdown table rows (`dashboard.py:32`);
  mail subject "…VPN Access Request request…"; unused i18n keys hinting at
  half-done features (`back_to_dashboard`, `session_expired`, `waiting_since`,
  `your_action_needed`, `resend_confirm`, `floorplan_pin_delete_confirm`,
  `auth_error`).

## False positives checked

- `/auth/login` "missing" (T1) — route works; only the test is stale.
- Open redirect via `redirect_to` — `/\t/evil.com`, `/\\evil.com`, `\\/…`, `/\n/`
  all map to `/`.
- LDAP filter/DN injection — `escape_filter_chars`, `USERNAME_RE`, `escape_rdn`.
- XSS — NiceGUI 3.14+ sanitizes `ui.html`/`ui.markdown`; Leaflet labels and audit
  detail escaped; email Jinja is sandboxed + autoescaped; `EmailMessage` rejects
  CR/LF.
- Hidden/disabled button events — NiceGUI drops them server-side;
  `readonly_fields` skipped on save.
- Token/code brute force — UUID4 tokens; 5 attempts per window under row lock.
- Upload path traversal — basenames at the sink; containment checks hold.
- Refactor seams (D17) — imports resolve; `UPLOAD_ROOT`/`send_mail` patches still
  reach their call sites; no `actor=None` callers broken by D2.
- Legacy configs without `tenure` — backfill works.
- Booking overlap/races — `FOR UPDATE` on the resource; exclusive `end_date`
  consistent everywhere; no reminder off-by-one.
- Migrations vs models match; config cache staleness bounded at 30 s by design.
- EN/FR key sets identical; no console errors on any page; no horizontal page
  overflow at 390 px.
