import pytest

from not_dot_net.backend.permissions import (
    PermissionInfo,
    permission,
    get_permissions,
    _registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Isolate registry between tests."""
    saved = dict(_registry)
    _registry.clear()
    yield
    _registry.clear()
    _registry.update(saved)


def test_permission_registers_and_returns_key():
    key = permission("do_thing", "Do Thing", "Can do the thing")
    assert key == "do_thing"
    assert "do_thing" in get_permissions()
    info = get_permissions()["do_thing"]
    assert isinstance(info, PermissionInfo)
    assert info.label == "Do Thing"
    assert info.description == "Can do the thing"


def test_get_permissions_returns_all():
    permission("a", "A")
    permission("b", "B")
    assert set(get_permissions().keys()) == {"a", "b"}


def test_duplicate_registration_overwrites():
    permission("x", "X1")
    permission("x", "X2")
    assert get_permissions()["x"].label == "X2"
