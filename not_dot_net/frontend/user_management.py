"""Superuser-only user management tab: filter, sort, edit, bulk enable/disable in AD."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from nicegui import ui
from sqlalchemy import select
from sqlalchemy.orm import defer

from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.auth.ldap import (
    LdapModifyError, ldap_config, ldap_set_account_enabled,
)
from not_dot_net.backend.db import AuthMethod, User, session_scope
from not_dot_net.frontend.i18n import t


_LAST_LOGON_BUCKETS: dict[str, timedelta | None] = {
    "any": None,
    "never": None,
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "180d": timedelta(days=180),
    "1y": timedelta(days=365),
}


def _normalize_logon(dt: datetime | None) -> datetime | None:
    """Coerce a possibly-naive datetime read back from SQLite to UTC-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class UserFilter:
    query: str = ""
    auth_method: str = "all"          # all | local | ldap
    active: str = "all"               # all | active | inactive
    last_logon: str = "any"           # any | never | 30d | 90d | 180d | 1y

    def matches(self, u: User, *, now: datetime) -> bool:
        if self.query:
            q = self.query.lower()
            haystack = f"{u.full_name or ''} {u.email or ''} {u.ldap_username or ''}".lower()
            if q not in haystack:
                return False
        if self.auth_method == "local" and u.auth_method != AuthMethod.LOCAL:
            return False
        if self.auth_method == "ldap" and u.auth_method != AuthMethod.LDAP:
            return False
        if self.active == "active" and not u.is_active:
            return False
        if self.active == "inactive" and u.is_active:
            return False
        if self.last_logon == "never":
            if u.last_ad_logon is not None:
                return False
        elif self.last_logon != "any":
            threshold = _LAST_LOGON_BUCKETS[self.last_logon]
            stamp = _normalize_logon(u.last_ad_logon)
            if stamp is None:
                return True  # no logon ever counts as "≥ N days ago"
            if (now - stamp) < threshold:
                return False
        return True


def filter_users(users: list[User], spec: UserFilter, *, now: datetime | None = None) -> list[User]:
    now = now or datetime.now(timezone.utc)
    return [u for u in users if spec.matches(u, now=now)]


@dataclass
class BulkResult:
    succeeded: list[User]
    failed: list[tuple[User, str]]


async def apply_bulk_ad_state(
    targets: list[User],
    *,
    enabling: bool,
    bind_username: str,
    bind_password: str,
    actor: User,
) -> BulkResult:
    """Push enable/disable to AD for each target, mirror locally, audit each.

    Skips non-AD users and self. Errors are collected per-user; the loop continues.
    """
    cfg = await ldap_config.get()
    succeeded: list[User] = []
    failed: list[tuple[User, str]] = []
    for person in targets:
        if person.id == actor.id:
            failed.append((person, "self"))
            continue
        if person.auth_method != AuthMethod.LDAP or not person.ldap_dn:
            failed.append((person, "not_ad"))
            continue
        try:
            await asyncio.to_thread(
                ldap_set_account_enabled,
                dn=person.ldap_dn, enabled=enabling,
                bind_username=bind_username, bind_password=bind_password,
                ldap_cfg=cfg,
            )
        except LdapModifyError as e:
            failed.append((person, str(e)))
            continue
        async with session_scope() as session:
            db_person = await session.get(User, person.id)
            if db_person is not None:
                db_person.is_active = enabling
                await session.commit()
        await log_audit(
            "users", "enable" if enabling else "disable",
            actor_id=actor.id, actor_email=actor.email,
            target_type="user", target_id=person.id,
            detail=f"ad_dn={person.ldap_dn} (bulk)",
        )
        succeeded.append(person)
    return BulkResult(succeeded=succeeded, failed=failed)


async def _load_all_users() -> list[User]:
    async with session_scope() as session:
        # defer(photo): this table never renders photos, and LargeBinary is not
        # deferred on the model, so every row otherwise dragged its image along.
        result = await session.execute(select(User).options(defer(User.photo)))
        return list(result.scalars().all())


def _format_logon(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    norm = _normalize_logon(dt)
    return norm.strftime("%Y-%m-%d %H:%M")


def _user_to_row(u: User) -> dict:
    return {
        "_id": str(u.id),
        "name": u.full_name or "—",
        "email": u.email,
        "auth_method": u.auth_method.value if hasattr(u.auth_method, "value") else str(u.auth_method),
        "role": u.role or "",
        "is_superuser": u.is_superuser,
        "is_active": u.is_active,
        "employment_status": u.employment_status or "",
        "last_ad_logon": _format_logon(u.last_ad_logon),
        "last_ad_logon_sort": _normalize_logon(u.last_ad_logon).timestamp() if u.last_ad_logon else 0,
    }


_COLUMNS = [
    {"name": "name",              "label": "name",              "field": "name",              "sortable": True, "align": "left"},
    {"name": "email",             "label": "email",             "field": "email",             "sortable": True, "align": "left"},
    {"name": "auth_method",       "label": "auth_method",       "field": "auth_method",       "sortable": True, "align": "left"},
    {"name": "role",              "label": "role",              "field": "role",              "sortable": True, "align": "left"},
    {"name": "is_superuser",      "label": "is_superuser",      "field": "is_superuser",      "sortable": True, "align": "center"},
    {"name": "is_active",         "label": "is_active",         "field": "is_active",         "sortable": True, "align": "center"},
    {"name": "employment_status", "label": "employment_status", "field": "employment_status", "sortable": True, "align": "left"},
    {"name": "last_ad_logon",     "label": "last_ad_logon",     "field": "last_ad_logon_sort","sortable": True, "align": "left",
     ":format": "(val,row) => row.last_ad_logon"},
]


def _localized_columns() -> list[dict]:
    return [{**c, "label": t(c["label"])} for c in _COLUMNS]


async def render(current_user: User):
    if not current_user.is_superuser:
        ui.label(t("forbidden")).classes("text-negative")
        return

    state = {"users": [], "spec": UserFilter()}
    table_container = ui.column().classes("w-full")
    summary = ui.label("").classes("text-sm text-gray-500")

    async def reload():
        state["users"] = await _load_all_users()
        rerender()

    def rerender():
        spec = state["spec"]
        filtered = filter_users(state["users"], spec)
        rows = [_user_to_row(u) for u in filtered]
        table_container.clear()
        with table_container:
            table = ui.table(
                columns=_localized_columns(), rows=rows, row_key="_id",
                selection="multiple",
                pagination={"rowsPerPage": 50, "sortBy": "last_ad_logon", "descending": False},
            ).classes("w-full")
            table.props("flat bordered dense")
            table.add_slot("body-cell-is_active", r'''
                <q-td :props="props">
                    <q-icon :name="props.value ? 'check_circle' : 'block'"
                            :color="props.value ? 'positive' : 'negative'" />
                </q-td>
            ''')
            table.add_slot("body-cell-is_superuser", r'''
                <q-td :props="props">
                    <q-icon v-if="props.value" name="star" color="amber" />
                </q-td>
            ''')
            with ui.row().classes("items-center gap-2 mt-2"):
                async def bulk(enabling: bool):
                    selected_ids = {r["_id"] for r in (table.selected or [])}
                    targets = [u for u in state["users"] if str(u.id) in selected_ids]
                    if not targets:
                        ui.notify(t("nothing_selected"), color="warning")
                        return
                    await _open_bulk_dialog(targets, enabling=enabling, actor=current_user, on_done=reload)

                ui.button(t("bulk_enable"), icon="check_circle",
                          on_click=lambda: bulk(True)).props("flat color=positive")
                ui.button(t("bulk_disable"), icon="block",
                          on_click=lambda: bulk(False)).props("flat color=negative")
                ui.label(t("hint_dblclick_to_edit")).classes("text-xs text-gray-400 ml-auto")

            table.on("rowDblclick", lambda e: _open_edit_dialog(e.args[1], current_user, reload))

        summary.set_text(t("user_count", shown=len(rows), total=len(state["users"])))

    with ui.row().classes("items-center gap-2 w-full mb-2"):
        search = ui.input(placeholder=t("search_placeholder")).props("outlined dense clearable").classes("flex-grow")
        auth_select = ui.select(
            {"all": t("all"), "local": t("auth_local"), "ldap": t("auth_ldap")},
            value="all", label=t("auth_method"),
        ).props("outlined dense stack-label")
        active_select = ui.select(
            {"all": t("all"), "active": t("active"), "inactive": t("inactive")},
            value="all", label=t("status"),
        ).props("outlined dense stack-label")
        logon_select = ui.select(
            {
                "any": t("logon_any"),
                "never": t("logon_never"),
                "30d": t("logon_over_30d"),
                "90d": t("logon_over_90d"),
                "180d": t("logon_over_180d"),
                "1y": t("logon_over_1y"),
            },
            value="any", label=t("last_ad_logon"),
        ).props("outlined dense stack-label")

    def on_filter_change():
        state["spec"] = UserFilter(
            query=(search.value or "").strip(),
            auth_method=auth_select.value,
            active=active_select.value,
            last_logon=logon_select.value,
        )
        rerender()

    search.on_value_change(lambda _: on_filter_change())
    auth_select.on_value_change(lambda _: on_filter_change())
    active_select.on_value_change(lambda _: on_filter_change())
    logon_select.on_value_change(lambda _: on_filter_change())

    await reload()
    return reload


async def _open_bulk_dialog(targets: list[User], *, enabling: bool, actor: User, on_done):
    """Two-step dialog: confirm + AD admin credentials."""
    label = t("bulk_enable") if enabling else t("bulk_disable")
    dialog = ui.dialog()
    with dialog, ui.card():
        ui.label(t("confirm_bulk", action=label, n=len(targets)))
        username_input = ui.input(t("ad_admin_username")).props("outlined dense")
        password_input = ui.input(t("password"), password=True).props("outlined dense")
        error_label = ui.label("").classes("text-negative")

        async def submit():
            bind_user = (username_input.value or "").strip()
            if not bind_user or not password_input.value:
                error_label.set_text(t("username_password_required"))
                return
            error_label.set_text("")
            result = await apply_bulk_ad_state(
                targets, enabling=enabling,
                bind_username=bind_user, bind_password=password_input.value,
                actor=actor,
            )
            dialog.close()
            ui.notify(
                t("bulk_done", ok=len(result.succeeded), failed=len(result.failed)),
                color="positive" if not result.failed else "warning",
                multi_line=True,
            )
            if result.failed:
                # Show first 3 failure reasons
                preview = "\n".join(
                    f"{p.email}: {reason}" for p, reason in result.failed[:3]
                )
                ui.notify(preview, color="negative", multi_line=True)
            await on_done()

        with ui.row():
            ui.button(t("submit"), on_click=submit).props("flat color=primary")
            ui.button(t("cancel"), on_click=dialog.close).props("flat")
    dialog.open()


def _open_edit_dialog(row: dict, current_user: User, on_done) -> None:
    """Open the directory edit form in a dialog."""
    from not_dot_net.frontend.directory import _render_edit

    user_id = row["_id"]
    dialog = ui.dialog().props("maximized=false")
    with dialog, ui.card().classes("min-w-[520px]"):
        ui.label(row.get("name") or row.get("email")).classes("text-h6")
        container = ui.column().classes("w-full")

        async def populate():
            async with session_scope() as session:
                from uuid import UUID
                person = await session.get(User, UUID(user_id))
            if person is None:
                container.clear()
                with container:
                    ui.label(t("not_found")).classes("text-negative")
                return
            await _render_edit(container, person, current_user, state={})

        async def close():
            dialog.close()
            await on_done()

        ui.button(t("close"), on_click=close).props("flat")
    dialog.open()
    ui.timer(0, populate, once=True)
