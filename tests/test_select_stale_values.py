"""ui.select raises ValueError when its value isn't among its options.

Review 2026-10-12 F2-F5: stored values that stopped being offered — a deleted
default role, an inactive office owner, a deactivated vocabulary term, a
legacy resource location — crashed the dialogs that edit them.
"""

from not_dot_net.backend.roles import RoleDefinition, RolesConfig
from not_dot_net.frontend.admin_roles import role_delete_blocker
from not_dot_net.frontend.widgets import options_with


def test_options_with_keeps_options_containing_value():
    assert options_with(["a", "b"], "a") == ["a", "b"]


def test_options_with_appends_missing_list_value():
    assert options_with(["palaiseau"], "Jussieu") == ["palaiseau", "Jussieu"]


def test_options_with_adds_missing_dict_value_with_label():
    assert options_with({None: "none", 1: "Alice"}, 2, label="Bob") == {None: "none", 1: "Alice", 2: "Bob"}


def test_options_with_ignores_none_value():
    assert options_with(["a"], None) == ["a"]


ROLES = RolesConfig(default_role="member", roles={
    "member": RoleDefinition(label="Member"),
    "admin": RoleDefinition(label="Admin"),
})


def test_cannot_delete_the_default_role():
    assert role_delete_blocker("member", ROLES, user_counts={}) is not None


def test_cannot_delete_a_role_in_use():
    assert role_delete_blocker("admin", ROLES, user_counts={"admin": 2}) is not None


def test_can_delete_unused_non_default_role():
    assert role_delete_blocker("admin", ROLES, user_counts={}) is None
