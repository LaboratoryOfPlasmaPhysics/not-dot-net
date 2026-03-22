import pytest
from not_dot_net.backend.db import User

PROFILE_FIELDS = ["full_name", "phone", "office", "team", "title", "employment_status"]


@pytest.mark.parametrize("field", PROFILE_FIELDS)
def test_user_has_profile_field(field: str):
    user = User(email="test@example.com", hashed_password="x")
    assert hasattr(user, field)
    assert getattr(user, field) is None


def test_user_profile_fields_accept_values():
    user = User(
        email="test@example.com",
        hashed_password="x",
        full_name="Alice",
        phone="+33 1 23 45 67 89",
        office="B202",
        team="Plasma Physics",
        title="Researcher",
        employment_status="Permanent",
    )
    assert user.full_name == "Alice"
    assert user.phone == "+33 1 23 45 67 89"
    assert user.office == "B202"
    assert user.team == "Plasma Physics"
    assert user.title == "Researcher"
    assert user.employment_status == "Permanent"


def test_settings_has_teams_list():
    from not_dot_net.config import Settings
    settings = Settings(jwt_secret="x" * 34, storage_secret="x" * 34)
    assert hasattr(settings, "teams")
    assert len(settings.teams) > 0


def test_user_default_role():
    from not_dot_net.backend.roles import Role
    # SQLAlchemy INSERT defaults are not applied on Python object construction;
    # verify via column metadata.
    assert User.__table__.c.role.default.arg == Role.MEMBER


ONBOARDING_FIELDS = [
    "id", "created_by", "person_name", "person_email", "role_status",
    "team", "start_date", "note", "status", "created_at", "updated_at",
]


@pytest.mark.parametrize("field", ONBOARDING_FIELDS)
def test_onboarding_request_has_field(field: str):
    from not_dot_net.backend.onboarding import OnboardingRequest
    assert hasattr(OnboardingRequest, field)
