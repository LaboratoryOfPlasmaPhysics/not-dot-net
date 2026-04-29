# Workflow Editor Friendliness Pass — Design

**Date:** 2026-04-29
**Status:** Draft

## Problem

The workflow form editor that just landed is functional but unfriendly: it
exposes Pydantic field names as labels (`start_role`, `target_email_field`,
`partial_save`, `options_key`, `corrections_target`, …), surfaces engineer
conventions as magic strings (`permission:foo`, `target_person`, `requester`,
`request_corrections`), uses a "Role / Permission / Contextual" radio-plus-text
combo for assignee, and offers no help text. Admins need to be familiar with
the codebase to use it. The user's words: "you have to be a smart-ass geek to
understand it."

## Goals

- Eliminate engineer jargon from the user-visible UI; relabel everything in
  plain English with helper tooltips.
- Replace free-text inputs that accept conventional magic strings with smart
  pickers that show labeled options sourced from the live `RolesConfig` and
  permission registry.
- Auto-generate field internal names from display names so admins don't have to
  understand the `name` vs `label` distinction unless they actively want to.
- Preserve every persisted-config shape and external compatibility — no schema
  migration, no breaking change to YAML configs, no engine change.

## Non-goals

- Live preview of the rendered request form.
- Drag-to-reorder steps / fields (existing non-goal).
- Adding new engine events / actions beyond the five it already supports.
- Migrating in-flight requests when an admin renames or reshapes a workflow.

## Design

### Architecture & file layout

**Modified:**

- `not_dot_net/frontend/workflow_editor.py` (~600 → ~750 lines) — the
  `WorkflowEditorDialog` class is extended in place. New helper methods on the
  class for the smart pickers; new render functions for the relabeled sections;
  the YAML tab is removed and replaced by a header `</>` button that swaps the
  dialog body.
- `not_dot_net/frontend/i18n.py` — ~25 new keys (EN + FR) for relabeled fields,
  section headers, helper tooltips, empty-state copy.

**New:**

- `not_dot_net/frontend/workflow_editor_options.py` (~60 lines) — pure
  functions, no UI imports. Builds labeled option lists and slugifies
  display names. Three exports plus one helper:
  - `assignee_options(roles, permissions) → list[dict]` — returns option
    dicts of shape `{"value": str, "label": str, "kind": str}`. The four
    kinds are `role`, `permission`, `contextual_requester`,
    `contextual_target_person`. The `value` is a stable string the dialog
    code uses to encode/decode against the schema fields.
  - `recipient_options(roles, permissions) → list[dict]` — same shape but
    for the notification recipients picker. Each entry has a `group`
    field for Quasar's `option-group` rendering: "People in this request"
    / "Roles" / "Permissions". Values are `requester`, `target_person`,
    `<role_key>`, `permission:<perm_key>` — exactly the strings the
    workflow engine already expects.
  - `event_options() → list[dict]` — the five engine events with friendly
    labels: submit / approve / reject / request_corrections / cancel →
    "When submitted" / "When approved" / "When rejected" / "When changes
    are requested" / "When cancelled".
  - `_slugify(label: str, taken: set[str]) → str` — lowercase ASCII,
    non-alphanumeric → `_`, dedup with `_2`/`_3` suffix against `taken`,
    fall back to `field_<n>` if the label is empty after normalization.

The `_active_tab` state machine and the `apply_yaml` / `dump_yaml` methods
are unchanged. Save flow, validation, audit log, working-copy semantics —
all unchanged.

### The four smart widgets

#### 1. Assignee picker (two-step)

In `_render_step_editor`, replaces the "Role / Permission / Contextual" radio
+ free-text input with a two-step picker:

```
Who handles this step?
  [▾ Anyone with role                                  ]   ← kind select
  [▾ Admin                                             ]   ← value select
```

- The kind select uses four labeled options matching the four entries from
  `assignee_options(...)` grouped by `kind`. Switching the kind clears the
  prior assignment in the working copy (sets all of `assignee_role`,
  `assignee_permission`, `assignee` to `None`) and triggers
  `_refresh_detail()` so the second select shows the right options.
- The value select appears only for `role` and `permission` kinds; for the
  two contextual kinds it is hidden because the value is fully determined.
- On change of either select, the dialog calls
  `set_step_assignee(wf_key, step_key, mode=..., value=...)`. The mapping
  from "kind + value" to the schema fields is the same as the existing
  `set_step_assignee` logic; no changes there.

#### 2. Recipient multi-select

In `_render_notification_table`, replaces the existing chip widget for the
`notify` column with a `q-select` in `multiple` mode whose options are
`recipient_options(roles, permissions)` rendered with Quasar's
`option-group` so users see three sections in the dropdown ("People in this
request", "Roles", "Permissions"). Selected items render as chips with
friendly labels. On change, the dialog writes back
`[opt["value"] for opt in selected]` — these are exactly the strings the
engine already consumes (`requester`, `target_person`, `staff`,
`permission:approve_workflows`).

When loading a saved config, the picker's initial value comes from
mapping each existing `notify` string to the matching option's `value`.
Strings that don't map to any current option (e.g. a permission that was
removed from the registry) display as "Unknown: <raw string>" and remain
in the value list — the user can remove them but we don't silently drop
them.

#### 3. Event + step selects

The existing notification rules table's three columns get tightened:

- **Event** (currently free-text q-select with-input) → fixed `q-select`
  whose options come from `event_options()`. Each option's value is the
  raw event name; the label is the friendly translation. No add-new-value
  mode — a typo'd event would never fire anyway, and the engine's events
  are fixed.
- **Step** (currently `[None, *step_keys]`) → same options but the blank
  option's label changes from "" to "Any step" and the select gets a
  `prefix-icon` indicating its "any" meaning when no step is selected.
- **Recipients** — the multi-select from #2.

#### 4. Field row with display-name auto-slug + lock

Today each field row exposes both `name` and `label` as plain text inputs.
After:

- The visible row shows: **Display name** (was `label`), **Type**
  (existing select), **Required** switch, "Show side-by-side" switch
  (was `half_width`), **More…** button, trash. Width allocation favours
  the display-name input.
- Clicking **More…** opens an inline collapsible row beneath, showing
  the rare flags: **Internal name**, **Pull options from organization
  list** (was `options_key`), **Encrypt at rest** (was `encrypted`).
- For a *new* field (one not present in `self.original` at the time of
  the edit), typing in **Display name** auto-sets
  `field.name = _slugify(label, taken=set(other_field_names))` on every
  on_change. The internal-name input in the More… row reflects this and
  stays editable.
- For a *saved* field (present in `self.original`), the internal name is
  locked: the More… row shows it as a readonly input with subtitle
  "Renaming may break workflow references" and an **Unlock** button.
  Clicking **Unlock** sets a per-field session flag
  (`self._unlocked_fields.add((wf_key, step_key, field_name))`) that
  re-enables the input. The auto-slug-from-label behaviour does not
  apply to locked fields — once locked, the display-name change does
  not silently retype the internal name.
- `set_field_attr(wf_key, step_key, idx, "name", value)` checks the
  lock state; an unlocked rename calls `set_field_attr` directly,
  while typing a display name on a locked field never triggers the
  rename path.

### Microcopy & layout

#### i18n keys (EN; FR mirrors)

```
"wf_label":                  "Display name"
"wf_label_help":             "Shown to users in the dashboard and request lists."
"wf_start_role":             "Who can create new requests?"
"wf_start_role_help":        "Anyone with this role sees this workflow on the new-request page."
"wf_target_email":           "Which field holds the target person's email?"
"wf_target_email_help":      "For workflows about another person (e.g. onboarding) — pick which form field holds their email so we can send them token links."
"wf_section_basics":         "Basics"
"wf_section_about_other":    "About another person? (advanced)"
"wf_section_notifications":  "Notifications"
"wf_section_doc_instructions": "Document instructions"
"step_type_form":            "Collect data from the assignee"
"step_type_approval":        "Approval decision (approve / reject)"
"step_partial_save":         "Allow saving as draft"
"step_corrections_target":   "Send back to step"
"step_assignee":             "Who handles this step?"
"step_actions":              "What can the assignee do?"
"field_display_name":        "Display name"
"field_internal_name":       "Internal name"
"field_internal_name_warn":  "Renaming may break workflow references."
"field_internal_name_unlock": "Unlock to rename"
"field_more":                "More…"
"field_options_key":         "Pull options from organization list"
"field_encrypted":           "Encrypt at rest (for personal documents)"
"field_half_width":          "Show side-by-side with next field"
"empty_workflows":           "No workflows yet — click Add workflow to create your first one."
"empty_steps":               "This workflow has no steps yet — click Add step in the panel on the left."
"empty_fields":              "This step has no fields yet — click Add field below."
"empty_notifications":       "No notification rules yet — click Add notification rule below."
"key_prompt_help":           "Lowercase, no spaces. Used in URLs and config — pick something short and stable."
"yaml_advanced":             "Edit as YAML (advanced)"
"yaml_back_to_form":         "Back to form"
"event_submit":              "When submitted"
"event_approve":             "When approved"
"event_reject":              "When rejected"
"event_request_corrections": "When changes are requested"
"event_cancel":              "When cancelled"
"any_step":                  "Any step"
"recipient_group_people":    "People in this request"
"recipient_group_roles":     "Roles"
"recipient_group_permissions": "Permissions"
"recipient_requester":       "Requester"
"recipient_target_person":   "Target person"
"recipient_role_prefix":     "Role: "
"recipient_permission_prefix": "Permission: "
"assignee_kind_role":        "Anyone with role"
"assignee_kind_permission":  "Anyone with permission"
"assignee_kind_requester":   "The person who created the request"
"assignee_kind_target":      "The person this request is about"
```

#### Workflow editor right pane layout

Today: a flat scroll of inputs. After: three collapsible sections, only
**Basics** open by default:

```
┌─────────────────────────────────────────────────────────┐
│ Workflow: vpn_access                                    │
│                                                         │
│ ▾ Basics                                                │
│   Display name           [_____________________]   ⓘ    │
│   Who can create reqs?   [▾ staff               ]   ⓘ   │
│   ▸ About another person? (advanced)                    │
│                                                         │
│ ▸ Notifications  (3)                                    │
│ ▸ Document instructions  (4 status keys)                │
└─────────────────────────────────────────────────────────┘
```

The "About another person? (advanced)" expander wraps the
target-email-field input. When expanded, the input becomes a
`q-select` populated from this workflow's field names (so
`target_email_field` can no longer be a typo); a free-text fallback
appears if the value doesn't match any current field, with a warning.

#### YAML reposition

The `ui.tabs()` `[Form][YAML]` is removed. The dialog header gets a
small icon button: `</>` with tooltip "Edit as YAML (advanced)".
Clicking swaps the dialog body for the existing codemirror YAML
view, with a "← Back to form" button at the top of the YAML view
that swaps back. The bidirectional sync logic
(`apply_yaml`/`dump_yaml`/`_collect_widget_state`) is unchanged.
`_active_tab` semantics stay so `save()` still applies pending YAML
when the YAML view is active.

#### Dirty indicator

The Save button gets a small Quasar `q-badge` overlay (a coloured
dot) when `is_dirty()` returns True. Toggled at the end of every
`_refresh_detail()`. No new state — `is_dirty()` already exists.

### Encoding/decoding tables

For unambiguous review, the smart-picker value strings map to schema
fields as follows:

| Picker | Picker value | Decodes to working_copy |
|--------|---|---|
| Assignee | `role:<key>` | `assignee_role=<key>`, others None |
| Assignee | `permission:<key>` | `assignee_permission=<key>`, others None |
| Assignee | `contextual:requester` | `assignee="requester"`, others None |
| Assignee | `contextual:target_person` | `assignee="target_person"`, others None |
| Recipient | `requester` | `notify` list contains `"requester"` |
| Recipient | `target_person` | `notify` list contains `"target_person"` |
| Recipient | `<role_key>` | `notify` list contains `<role_key>` |
| Recipient | `permission:<key>` | `notify` list contains `permission:<key>` |
| Event | one of `submit`/`approve`/`reject`/`request_corrections`/`cancel` | `event=<value>` |

Loading existing configs uses the inverse: read `assignee_role` /
`assignee_permission` / `assignee` and pick the matching option; for
`notify` strings, look up by `value`; unknown strings render as
"Unknown: <raw>" and are preserved on save.

### Validation impact

`compute_warnings()` gets one new check: if a notify-list entry doesn't
match any current option (unknown role/permission), surface
`[wf_key] notification recipient '<raw>' not found in current roles or
permissions`. Existing warnings unchanged.

### Edge cases

- **Roles or permissions deleted while the dialog is open:** the picker
  dropdowns are built from the snapshot taken at dialog open. Re-opening
  the dialog refreshes them. Editing the same workflow after a role
  delete shows the unknown-recipient warning above.
- **YAML edits introduce magic strings the picker doesn't know:** the
  values are preserved verbatim (Unknown: prefix in the picker UI),
  selection-by-value continues to work, save round-trips them faithfully.
- **A field's display name is changed in a way that produces the same
  slug as another existing field:** `_slugify` dedups against the
  current set of field names. The new field gets `email_2`, etc.
- **A field is renamed via the unlock path while another step references
  it via `target_email_field`:** the existing
  `target_email_field` warning in `compute_warnings()` already catches
  this. Unlock + rename does not auto-update references — admin
  resolves manually.

## Migration & rollout

Pure UI change. No DB schema changes, no Alembic migration, no
config-data migration. Existing configs continue to load and save
through the same Pydantic models. The YAML view (now behind the
`</>` button) is the safety hatch if a smart picker has a bug.

## Tests

### `tests/test_workflow_editor_options.py` (new, pure-functional)

- `assignee_options(roles, perms)` returns four kinds with stable
  labels and the documented value-string format.
- `recipient_options(roles, perms)` returns three groups with the
  documented value strings.
- `event_options()` returns five engine events with the EN labels
  declared above.
- `_slugify("Email Address", taken=set())` → `"email_address"`.
- `_slugify("Email", taken={"email"})` → `"email_2"`.
- `_slugify("!!!", taken=set())` → `"field_1"`.
- `_slugify("Email", taken={"email","email_2"})` → `"email_3"`.

### Extensions to `tests/test_workflow_editor.py`

- `test_assignee_picker_writes_role_correctly` — picking
  `value="role:admin"` results in `assignee_role="admin"` and the
  other two fields cleared.
- `test_assignee_picker_contextual_writes_target_person` — picking
  `value="contextual:target_person"` results in
  `assignee="target_person"` and the other two cleared.
- `test_recipient_picker_serializes_permissions` — picking
  `value="permission:approve_workflows"` produces
  `notify=["permission:approve_workflows"]`.
- `test_event_picker_known_values_only` — selecting `value="approve"`
  produces `event="approve"`.
- `test_field_internal_name_auto_generated_for_new_field` — adding
  a new field, then setting `label="Email"`, results in
  `field.name == "email"`.
- `test_field_internal_name_locked_after_save` — load with an existing
  field; `set_field_attr(..., "name", "renamed")` without prior unlock
  raises `ValueError("Internal name is locked; unlock to rename")`.
- `test_field_internal_name_unlock_allows_rename` — calling
  `unlock_field_name(wf_key, step_key, field_name)` then
  `set_field_attr(...)` succeeds.
- `test_existing_yaml_compat` — load a config with old-style
  `notify=["permission:foo"]` and `assignee_role="staff"`; the
  smart pickers display the right pre-selected values.
- `test_unknown_recipient_warning` — load a config with a `notify`
  entry that doesn't match any current role or permission; the new
  warning is surfaced by `compute_warnings()`.
- `test_yaml_button_swaps_body` — invoking the dialog's
  `_open_yaml_view()` toggles `_active_tab` to "YAML" and the YAML
  editor is bound to `_yaml_editor`. Saving from the YAML view
  applies pending YAML (existing test confirms the path).

## Out-of-scope follow-ups

- Live preview of the rendered request form.
- A "Test this workflow" affordance that creates a throwaway request
  in dev mode for clicking through.
- Workflow templates for common shapes (single-approval / multi-step).
- Drag-to-reorder.
