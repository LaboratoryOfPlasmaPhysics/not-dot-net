"""Reusable step renderer — form fields or approval UI."""

from nicegui import ui

from not_dot_net.backend.workflow_engine import get_completion_status
from not_dot_net.config import WorkflowStepConfig
from not_dot_net.frontend.i18n import t


async def render_step_form(
    step: WorkflowStepConfig,
    data: dict,
    on_submit,
    on_save_draft=None,
    files: dict | None = None,
    on_file_upload=None,
):
    """Render a form step's fields. Returns dict of field name -> ui element."""
    fields = {}
    for field_cfg in step.fields:
        label = field_cfg.label or field_cfg.name
        value = data.get(field_cfg.name, "")

        if field_cfg.type == "textarea":
            fields[field_cfg.name] = ui.textarea(
                label=label, value=value
            ).props("outlined dense").classes("w-full")
        elif field_cfg.type == "date":
            with ui.input(label=label, value=value).props("outlined dense") as inp:
                with ui.menu().props("no-parent-event") as menu:
                    with ui.date(on_change=lambda e, i=inp, m=menu: _set_date(i, m, e)):
                        pass
                with inp.add_slot("append"):
                    ui.icon("edit_calendar").on("click", menu.open).classes("cursor-pointer")
            fields[field_cfg.name] = inp
        elif field_cfg.type == "select":
            options = await _resolve_options(field_cfg.options_key)
            fields[field_cfg.name] = ui.select(
                label=label, options=options, value=value if value in options else None
            ).props("outlined dense").classes("w-full")
        elif field_cfg.type == "file":
            uploaded = (files or {}).get(field_cfg.name)
            if uploaded:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.icon("check_circle", color="positive", size="sm")
                    ui.label(f"{label}: {uploaded}").classes("text-positive text-sm")
            elif on_file_upload:
                req_mark = " *" if field_cfg.required else ""
                ui.upload(
                    label=f"{label}{req_mark}",
                    auto_upload=True,
                    on_upload=lambda e, name=field_cfg.name: on_file_upload(name, e),
                ).props("outlined flat accept='.pdf,.jpg,.jpeg,.png,.doc,.docx'").classes("w-full")
            else:
                ui.label(f"{label}: no upload available").classes("text-grey text-sm")
            fields[field_cfg.name] = None
        elif field_cfg.type == "email":
            fields[field_cfg.name] = ui.input(
                label=label, value=value, validation={"Invalid email": lambda v: "@" in v if v else True}
            ).props("outlined dense type=email").classes("w-full")
        else:
            fields[field_cfg.name] = ui.input(
                label=label, value=value
            ).props("outlined dense").classes("w-full")

    # Completion status for partial-save steps
    if step.partial_save:
        _render_completion_indicator(step, data, files or {})

    with ui.row().classes("mt-4 gap-2"):
        if on_save_draft and step.partial_save:
            ui.button(t("save_draft"), on_click=lambda: on_save_draft(_collect_data(fields))).props(
                "flat"
            )

        async def validated_submit():
            collected = _collect_data(fields)
            missing = [
                f.label or f.name for f in step.fields
                if f.required and f.type != "file" and not collected.get(f.name)
            ]
            if missing:
                ui.notify(f"{t('required_field')}: {', '.join(missing)}", color="negative")
                return
            await on_submit(collected)

        ui.button(t("submit"), on_click=validated_submit).props("color=primary")

    return fields


def _render_completion_indicator(step: WorkflowStepConfig, data: dict, files: dict):
    """Show which required fields are filled for partial-save steps."""
    required = [f for f in step.fields if f.required]
    if not required:
        return
    filled = sum(
        1 for f in required
        if (f.type == "file" and files.get(f.name)) or (f.type != "file" and data.get(f.name))
    )
    ui.linear_progress(value=filled / len(required)).classes("w-full mb-2")
    ui.label(f"{filled}/{len(required)}").classes("text-sm text-grey")


def render_approval(
    request_data: dict,
    workflow,
    step: WorkflowStepConfig,
    on_approve,
    on_reject,
    on_request_corrections=None,
):
    """Render approval view: read-only data + approve/reject/corrections."""
    ui.label(workflow.label).classes("text-h6")

    for key, value in request_data.items():
        if value:
            ui.label(f"{key}: {value}").classes("text-sm")

    comment_input = ui.textarea(label=t("comment")).props("outlined dense").classes("w-full mt-2")

    with ui.row().classes("mt-4 gap-2"):
        ui.button(
            t("approve"),
            icon="check",
            on_click=lambda: on_approve(comment_input.value),
        ).props("color=positive")
        if on_request_corrections:
            ui.button(
                "Request Corrections",
                icon="edit_note",
                on_click=lambda: on_request_corrections(comment_input.value),
            ).props("color=warning")
        ui.button(
            t("reject"),
            icon="close",
            on_click=lambda: on_reject(comment_input.value),
        ).props("color=negative")


def render_status_badge(status: str):
    """Render a colored status badge."""
    colors = {
        "in_progress": "primary",
        "completed": "positive",
        "rejected": "negative",
    }
    color = colors.get(status, "grey")
    ui.badge(t(status), color=color)


def render_urgency_badge(age_days: int, fresh_days: int = 2, aging_days: int = 7):
    """Render a colored urgency badge based on step age."""
    if age_days < fresh_days:
        color = "positive"
    elif age_days < aging_days:
        color = "warning"
    else:
        color = "negative"
    ui.badge(f"⏱ {age_days}d", color=color).props("outline")


def render_step_progress(current_step: str, status: str, steps: list):
    """Render a named step progress bar.

    Args:
        current_step: key of the current step
        status: request status (in_progress, completed, rejected)
        steps: list of WorkflowStepConfig from the workflow
    """
    step_keys = [s.key for s in steps]
    current_idx = step_keys.index(current_step) if current_step in step_keys else 0
    is_completed = status == "completed"

    with ui.row().classes("w-full gap-1 items-center"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                color = "bg-positive"
            elif i == current_idx:
                color = "bg-primary"
            else:
                color = "bg-grey-4"
            height = "h-[6px]" if i == current_idx and not is_completed else "h-[4px]"
            ui.element("div").classes(f"flex-1 rounded {color} {height}")

    with ui.row().classes("w-full gap-1"):
        for i, step in enumerate(steps):
            if is_completed or i < current_idx:
                label = f"✓ {step.key}"
                cls = "text-[11px] text-grey flex-1"
            elif i == current_idx:
                label = f"● {step.key}"
                cls = "text-[11px] text-primary font-semibold flex-1"
            else:
                label = step.key
                cls = "text-[11px] text-grey-4 flex-1"
            ui.label(label).classes(cls)


def _collect_data(fields: dict) -> dict:
    """Collect values from UI fields."""
    return {
        name: (el.value if el is not None else None)
        for name, el in fields.items()
        if el is not None
    }


def _set_date(inp, menu, event):
    inp.value = event.value
    menu.close()


async def _resolve_options(options_key: str | None) -> list[str]:
    """Resolve select field options from config."""
    if not options_key:
        return []
    if options_key == "teams":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.teams
    if options_key == "roles":
        from not_dot_net.backend.roles import roles_config
        cfg = await roles_config.get()
        return list(cfg.roles.keys())
    if options_key == "employment_statuses":
        from not_dot_net.config import org_config
        cfg = await org_config.get()
        return cfg.employment_statuses
    return []
