"""Reusable input widgets used in admin settings forms."""

import inspect

from nicegui import ui

from not_dot_net.frontend.i18n import t


class ConfirmDialog(ui.dialog):
    """Dialog that runs `on_confirm` only after the user confirms.

    `confirm()` is the same entry point the button uses, so tests exercise the
    real path. A raising action leaves the dialog open — the caller's notify
    would otherwise disappear along with it.
    """

    def __init__(self, message: str, on_confirm, *, confirm_label: str, confirm_icon: str):
        super().__init__()
        self._on_confirm = on_confirm
        with self, ui.card():
            ui.label(message).classes("text-bold")
            with ui.row():
                ui.button(confirm_label, icon=confirm_icon, on_click=self.confirm).props(
                    "color=negative"
                )
                ui.button(t("cancel"), on_click=self.close).props("flat")

    async def confirm(self) -> None:
        result = self._on_confirm()
        if inspect.isawaitable(result):
            await result
        self.close()


def confirm_dialog(
    message: str,
    on_confirm,
    *,
    confirm_label: str | None = None,
    confirm_icon: str = "delete_forever",
) -> ConfirmDialog:
    """Build (but do not open) a confirmation dialog. Call `.open()` to show it."""
    return ConfirmDialog(
        message, on_confirm,
        confirm_label=confirm_label or t("confirm"),
        confirm_icon=confirm_icon,
    )


def chip_list_editor(
    value: list[str],
    *,
    label: str = "",
    suggestions: list[str] | None = None,
):
    """Chip-style multi-value text input. Reads/writes a `list[str]`.

    Without suggestions: a free-form tags editor (`ui.input_chips`).
    With suggestions: a Quasar q-select in `use-chips` + `use-input` mode.
    Current values are merged into the options — QSelect only renders chips
    for values present in options, so out-of-list values would otherwise
    be invisible in the browser.
    """
    if suggestions is None:
        return ui.input_chips(
            label or None,
            value=list(value),
            new_value_mode="add-unique",
        ).props("outlined dense stack-label").classes("w-full")
    options = list(dict.fromkeys([*suggestions, *value]))
    select = ui.select(
        options=options,
        value=list(value),
        label=label or None,
        multiple=True,
        new_value_mode="add-unique",
    ).props('use-chips use-input outlined dense stack-label input-debounce=0').classes("w-full")
    return select



class KeyedChipEditor:
    """Editor for `dict[str, list[str]]`.

    Renders a vertical stack of rows: `[key input | chip_list_editor | trash]`,
    plus an "Add" row at the bottom. The current value is exposed via the
    `value` property.
    """

    def __init__(self, value: dict[str, list[str]], *, key_label: str = "Key"):
        self._key_label = key_label
        self._rows: dict[str, dict] = {}
        self._container = ui.column().classes("w-full gap-2")
        with self._container:
            for k, vs in (value or {}).items():
                self._add_row(k, list(vs))
            self._add_button = ui.button(t("add_row"), on_click=self._on_add).props("flat dense color=primary")

    @property
    def value(self) -> dict[str, list[str]]:
        """Current contents, keyed by whatever the key inputs now say.

        Two rows can end up sharing a key (the admin renames one to match
        another); a plain dict comprehension silently kept only the last, losing
        the other row's values on save. Merge instead, and drop blank keys
        rather than producing a "" entry.
        """
        merged: dict[str, list[str]] = {}
        for row in self._rows.values():
            key = (row["key_input"].value or "").strip()
            if not key:
                continue
            existing = merged.setdefault(key, [])
            existing.extend(v for v in row["chip"].value if v not in existing)
        return merged

    def add_key(self, key: str, values: list[str] | None = None) -> None:
        with self._container:
            self._add_row(key, values or [])
            self._add_button.move(self._container)

    def remove_key(self, key: str) -> None:
        row = self._rows.pop(key, None)
        if row:
            row["container"].delete()

    def set_values(self, key: str, values: list[str]) -> None:
        row = self._rows.get(key)
        if row:
            row["chip"].value = list(values)

    def _add_row(self, key: str, values: list[str]):
        row_container = ui.row().classes("w-full items-center gap-2 no-wrap")
        with row_container:
            key_input = ui.input(label=self._key_label, value=key).props("dense outlined stack-label").classes("w-40")
            chip = chip_list_editor(values)
            ui.button(
                icon="delete", on_click=lambda k=key: self.remove_key(k),
            ).props("flat dense round color=negative").tooltip(t("delete"))
        self._rows[key] = {"container": row_container, "key_input": key_input, "chip": chip}

    def _on_add(self):
        new_key = f"key_{len(self._rows) + 1}"
        self.add_key(new_key, [])

    def tooltip(self, text: str) -> "KeyedChipEditor":
        self._container.tooltip(text)
        return self


def keyed_chip_editor(value: dict[str, list[str]], *, key_label: str = "Key") -> KeyedChipEditor:
    return KeyedChipEditor(value, key_label=key_label)
