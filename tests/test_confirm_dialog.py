"""U2-U6, U14 — destructive one-click actions must ask first.

Cancel-request, booking cancel, page delete, role delete, reset-to-defaults and
floor-plan pin delete all fired immediately, while their neighbours in the same
files (delete request, delete user, retire resource) already used a dialog.
"""
import pytest
from nicegui import ui
from nicegui.testing import User


async def test_confirm_dialog_does_not_run_the_action_up_front(user: User):
    from not_dot_net.frontend.widgets import confirm_dialog

    ran = []

    @ui.page("/confirm-noop")
    def page():
        confirm_dialog("Delete it?", lambda: ran.append(True))

    await user.open("/confirm-noop")
    assert ran == [], "action ran without confirmation"


async def test_confirm_dialog_runs_the_action_when_confirmed(user: User):
    from not_dot_net.frontend.widgets import confirm_dialog

    ran = []
    holder = {}

    async def action():
        ran.append(True)

    @ui.page("/confirm-runs")
    def page():
        holder["dlg"] = confirm_dialog("Delete it?", action)

    await user.open("/confirm-runs")
    await holder["dlg"].confirm()
    assert ran == [True]
    assert holder["dlg"].value is False, "dialog stayed open after a successful action"


async def test_confirm_dialog_stays_open_when_the_action_fails(user: User):
    """A failed action must leave the dialog up, so its notify is still visible."""
    from not_dot_net.frontend.widgets import confirm_dialog

    holder = {}

    async def boom():
        raise RuntimeError("nope")

    @ui.page("/confirm-raises")
    def page():
        holder["dlg"] = confirm_dialog("Delete it?", boom)
        holder["dlg"].open()

    await user.open("/confirm-raises")
    with pytest.raises(RuntimeError):
        await holder["dlg"].confirm()
    assert holder["dlg"].value is True


async def test_confirm_dialog_accepts_a_sync_action(user: User):
    from not_dot_net.frontend.widgets import confirm_dialog

    ran = []
    holder = {}

    @ui.page("/confirm-sync")
    def page():
        holder["dlg"] = confirm_dialog("Delete it?", lambda: ran.append(True))

    await user.open("/confirm-sync")
    await holder["dlg"].confirm()
    assert ran == [True]


class TestKeyedChipEditorValue:
    """U18 — duplicate key inputs silently dropped a row's data on save."""

    async def test_duplicate_keys_merge_instead_of_overwriting(self, user: User):
        from not_dot_net.frontend.widgets import keyed_chip_editor

        holder = {}

        @ui.page("/keyed-dupes")
        def page():
            holder["editor"] = keyed_chip_editor({"Intern": ["g1"], "Staff": ["g2"]})

        await user.open("/keyed-dupes")
        editor = holder["editor"]
        # The admin renames "Staff" to "Intern" — previously the first row's
        # groups just vanished on save.
        editor._rows["Staff"]["key_input"].value = "Intern"

        assert editor.value == {"Intern": ["g1", "g2"]}

    async def test_blank_keys_are_dropped(self, user: User):
        from not_dot_net.frontend.widgets import keyed_chip_editor

        holder = {}

        @ui.page("/keyed-blank")
        def page():
            holder["editor"] = keyed_chip_editor({"Intern": ["g1"], "Staff": ["g2"]})

        await user.open("/keyed-blank")
        editor = holder["editor"]
        editor._rows["Staff"]["key_input"].value = "   "

        assert editor.value == {"Intern": ["g1"]}

    async def test_merge_does_not_duplicate_shared_values(self, user: User):
        from not_dot_net.frontend.widgets import keyed_chip_editor

        holder = {}

        @ui.page("/keyed-shared")
        def page():
            holder["editor"] = keyed_chip_editor({"A": ["g1", "g2"], "B": ["g2", "g3"]})

        await user.open("/keyed-shared")
        editor = holder["editor"]
        editor._rows["B"]["key_input"].value = "A"

        assert editor.value == {"A": ["g1", "g2", "g3"]}
