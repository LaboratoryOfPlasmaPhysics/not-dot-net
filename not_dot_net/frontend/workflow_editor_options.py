"""Pure-functional option builders for the workflow editor's smart pickers.

Builds no UI — keep this testable in isolation. `t` is fine here: get_locale()
falls back to the default outside a request context, so these stay callable
from a plain unit test.
"""

import re
from typing import Mapping

from not_dot_net.backend.permissions import PermissionInfo
from not_dot_net.backend.roles import RoleDefinition
from not_dot_net.frontend.i18n import t


def assignee_options(
    roles: Mapping[str, RoleDefinition],
    permissions: Mapping[str, PermissionInfo],
) -> list[dict]:
    """Build labeled options for the step assignee two-step picker.

    Returns dicts of shape:
        {"value": str, "label": str, "kind": str}
    """
    out: list[dict] = []
    for key, definition in sorted(roles.items()):
        out.append({
            "value": f"role:{key}",
            "label": t("assignee_anyone_with_role", role=definition.label or key),
            "kind": "role",
        })
    for key, info in sorted(permissions.items()):
        out.append({
            "value": f"permission:{key}",
            "label": t("assignee_anyone_with_permission", permission=info.label or key),
            "kind": "permission",
        })
    out.append({
        "value": "contextual:requester",
        "label": t("assignee_requester_long"),
        "kind": "contextual_requester",
    })
    out.append({
        "value": "contextual:target_person",
        "label": t("assignee_target_long"),
        "kind": "contextual_target_person",
    })
    return out


def recipient_options(
    roles: Mapping[str, RoleDefinition],
    permissions: Mapping[str, PermissionInfo],
) -> list[dict]:
    """Build labeled options for the notification recipients multi-select."""
    out: list[dict] = [
        {"value": "requester", "label": t("recipient_requester"),
         "group": t("recipient_group_people")},
        {"value": "target_person", "label": t("recipient_target_person"),
         "group": t("recipient_group_people")},
    ]
    for key, definition in sorted(roles.items()):
        out.append({
            "value": key,
            "label": t("recipient_role", role=definition.label or key),
            "group": t("recipient_group_roles"),
        })
    for key, info in sorted(permissions.items()):
        out.append({
            "value": f"permission:{key}",
            "label": t("recipient_permission", permission=info.label or key),
            "group": t("recipient_group_permissions"),
        })
    return out


def event_options() -> list[dict]:
    """Build labeled options for the event trigger multi-select."""
    return [
        {"value": "submit", "label": t("event_when_submitted")},
        {"value": "approve", "label": t("event_when_approved")},
        {"value": "reject", "label": t("event_when_rejected")},
        {"value": "request_corrections", "label": t("event_when_corrections")},
        {"value": "complete", "label": t("event_when_completed")},
        {"value": "cancel", "label": t("event_when_cancelled")},
    ]


def action_options(existing: list[str] | None = None) -> list[dict]:
    """Labeled options for the step actions picker. The engine treats reject and
    request_corrections specially; every other action just advances — say so."""
    out = [
        {"value": "submit", "label": t("action_submit")},
        {"value": "approve", "label": t("action_approve")},
        {"value": "complete", "label": t("action_complete")},
        {"value": "request_corrections", "label": t("action_request_corrections")},
        {"value": "reject", "label": t("action_reject")},
    ]
    known = {o["value"] for o in out}
    out.extend(
        {"value": a, "label": f"{a} (custom)"}
        for a in existing or [] if a not in known
    )
    return out


def assignee_summary(step, roles: Mapping[str, RoleDefinition],
                     permissions: Mapping[str, PermissionInfo]) -> str:
    """One-line description of who handles a step.

    Same precedence as workflow_engine.effective_assignee:
    contextual assignee > permission > role.
    """
    if step.assignee == "target_person":
        return t("assignee_kind_target")
    if step.assignee == "requester":
        return t("assignee_kind_requester")
    if step.assignee_permission:
        info = permissions.get(step.assignee_permission)
        name = getattr(info, "label", None) or step.assignee_permission
        return t("assignee_summary_permission", name=name)
    if step.assignee_role:
        definition = roles.get(step.assignee_role)
        name = getattr(definition, "label", None) or step.assignee_role
        return t("assignee_summary_role", name=name)
    return t("assignee_none")


def effect_kind_options() -> list[dict]:
    """Labeled options for the four AD effect kinds."""
    return [
        {"value": "ad_add_to_groups", "label": t("effect_kind_ad_add_to_groups")},
        {"value": "ad_remove_from_groups", "label": t("effect_kind_ad_remove_from_groups")},
        {"value": "ad_enable_account", "label": t("effect_kind_ad_enable_account")},
        {"value": "ad_disable_account", "label": t("effect_kind_ad_disable_account")},
    ]


_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _slugify(label: str, taken: set[str]) -> str:
    """Generate a unique snake_case identifier from a display label.

    Lowercase ASCII; non-alphanumeric runs collapse to underscore; trailing
    underscores stripped. If the result is empty, falls back to `field_<n>`
    where n is the smallest positive integer not already in `taken`.
    Dedup with `_2`, `_3`, etc. against `taken`.
    """
    base = _NON_ALNUM.sub("_", label.lower()).strip("_")
    if not base:
        n = 1
        while f"field_{n}" in taken:
            n += 1
        return f"field_{n}"
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def display_name_to_key(name: str, taken: set[str], *, fallback_prefix: str = "workflow") -> str:
    """Derive a valid config key from a human display name.

    `_slugify` can produce a digit-leading slug ("3D printer" -> "3d_printer"),
    which the editor's key validator rejects — prefix with a word in that case.
    """
    slug = _slugify(name, taken)
    if _KEY_RE.fullmatch(slug):
        return slug
    return _slugify(f"{fallback_prefix} {name}", taken)
