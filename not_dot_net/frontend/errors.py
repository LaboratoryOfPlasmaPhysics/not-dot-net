"""Turning exceptions into something safe to put in front of a user.

`ui.notify(str(e))` was the house style, which meant SQLAlchemy handed users the
failing SQL and its parameters, OSError handed them filesystem paths, and ldap3
handed them service-account DNs and raw server messages.

The split is by intent, not by severity: a domain exception's message was
written to be read by whoever triggered it, so it is shown as-is. Everything
else is an internal failure the user can do nothing with — they get a generic
line and the detail goes to the log.
"""

import logging

from nicegui import ui

from not_dot_net.frontend.i18n import t

logger = logging.getLogger("not_dot_net.errors")


def _domain_exception_types() -> tuple[type, ...]:
    """Exceptions whose message is authored for the person who triggered it.

    Imported lazily: this module is pulled in by nearly every frontend page and
    must not drag the service layer along with it.
    """
    from not_dot_net.backend.booking_service import (
        BookingConflictError, BookingValidationError,
    )
    from not_dot_net.backend.office_availability import OfficeAvailabilityError

    return (
        BookingConflictError,
        BookingValidationError,
        OfficeAvailabilityError,
        ValueError,  # services raise this for authored validation text
    )


def user_facing_message(exc: Exception) -> str:
    """A message safe to show for `exc`. Logs anything it refuses to show."""
    from not_dot_net.backend.auth.ldap import LdapModifyError

    if isinstance(exc, PermissionError):
        return t("permission_denied")
    if isinstance(exc, LdapModifyError):
        # Carries bind DNs and raw directory-server text.
        logger.warning("AD operation failed: %s", exc)
        return t("ad_operation_failed")
    if isinstance(exc, _domain_exception_types()):
        return str(exc)

    logger.exception("Unhandled error surfaced to a user: %s", exc)
    return t("unexpected_error")


def notify_error(exc: Exception) -> None:
    """Show `exc` to the user without leaking its internals."""
    ui.notify(user_facing_message(exc), color="negative", multi_line=True)
