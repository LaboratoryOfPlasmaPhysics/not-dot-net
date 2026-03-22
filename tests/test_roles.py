from not_dot_net.backend.roles import Role, has_role


def test_role_values_are_strings():
    assert Role.MEMBER.value == "member"
    assert Role.STAFF.value == "staff"
    assert Role.DIRECTOR.value == "director"
    assert Role.ADMIN.value == "admin"


def test_has_role_exact_match():
    class FakeUser:
        role = Role.STAFF
    assert has_role(FakeUser(), Role.STAFF)


def test_has_role_higher_passes():
    class FakeUser:
        role = Role.DIRECTOR
    assert has_role(FakeUser(), Role.STAFF)


def test_has_role_lower_fails():
    class FakeUser:
        role = Role.MEMBER
    assert not has_role(FakeUser(), Role.STAFF)


def test_has_role_with_string_value():
    class FakeUser:
        role = "director"
    assert has_role(FakeUser(), Role.STAFF)
    assert has_role(FakeUser(), Role.DIRECTOR)
    assert not has_role(FakeUser(), Role.ADMIN)
