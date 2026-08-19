"""S11/U8 — raw exception strings were shown verbatim to end users.

`ui.notify(str(e))` across 35 call sites surfaced whatever the exception
carried: SQLAlchemy's full SQL with parameters, filesystem paths, LDAP DNs and
server messages. Domain exceptions (BookingValidationError and friends) carry
authored, safe text and are still shown; anything else gets a generic message
and the detail goes to the log.
"""
import pytest

from not_dot_net.backend.booking_service import (
    BookingConflictError, BookingValidationError,
)
from not_dot_net.backend.office_availability import OfficeAvailabilityError
from not_dot_net.frontend.errors import user_facing_message


def test_domain_errors_keep_their_authored_message():
    for exc in (
        BookingValidationError("End date must be after start date"),
        BookingConflictError("Already booked for those dates"),
        OfficeAvailabilityError("Window not open"),
    ):
        assert user_facing_message(exc) == str(exc)


def test_value_errors_are_shown():
    """Services raise ValueError for authored validation text too."""
    assert user_facing_message(ValueError("Tenure periods cannot overlap")) == (
        "Tenure periods cannot overlap"
    )


def test_permission_errors_become_the_localized_denial():
    from not_dot_net.frontend.i18n import t

    assert user_facing_message(PermissionError("manage_users required")) == (
        t("permission_denied")
    )


def test_database_errors_are_not_leaked():
    from sqlalchemy.exc import OperationalError
    from not_dot_net.frontend.i18n import t

    exc = OperationalError(
        "SELECT user.hashed_password FROM user WHERE user.email = ?",
        {"email": "boss@example.com"},
        Exception("no such table: user"),
    )
    message = user_facing_message(exc)
    assert message == t("unexpected_error")
    assert "SELECT" not in message
    assert "hashed_password" not in message


def test_ldap_errors_are_not_leaked():
    from not_dot_net.backend.auth.ldap import LdapModifyError
    from not_dot_net.frontend.i18n import t

    exc = LdapModifyError(
        "bind failed: CN=svc-intranet,OU=Service,DC=lpp,DC=polytechnique,DC=fr"
    )
    message = user_facing_message(exc)
    assert message == t("ad_operation_failed")
    assert "DC=lpp" not in message


def test_unknown_exceptions_do_not_leak_paths():
    from not_dot_net.frontend.i18n import t

    exc = OSError("[Errno 13] Permission denied: '/data/uploads/secret/passport.pdf'")
    message = user_facing_message(exc)
    assert message == t("unexpected_error")
    assert "passport.pdf" not in message


def test_unexpected_exceptions_are_logged(caplog):
    """The detail must survive somewhere — just not on the user's screen."""
    import logging

    with caplog.at_level(logging.ERROR):
        user_facing_message(RuntimeError("connection reset by peer"))
    assert "connection reset by peer" in caplog.text
