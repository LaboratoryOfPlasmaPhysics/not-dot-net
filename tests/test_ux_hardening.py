"""U22, U24 — dead ends and a timer that only survives some failures.

U22: the invalid/expired-token page and the lockout state left an external user
with no next step and no way to reach anyone.

U24: the dashboard badge timer caught only RuntimeError/TimeoutError, so a
transient DB error propagated as an unhandled timer exception every 60 seconds.
"""
import pytest


def test_expired_token_page_offers_a_next_step():
    from not_dot_net.frontend.i18n import TRANSLATIONS

    for locale in ("en", "fr"):
        assert "token_expired_help" in TRANSLATIONS[locale]
        assert TRANSLATIONS[locale]["token_expired_help"].strip()


def test_lockout_message_tells_the_user_what_to_do():
    from not_dot_net.frontend.i18n import TRANSLATIONS

    for locale in ("en", "fr"):
        assert "too_many_attempts_help" in TRANSLATIONS[locale]


async def test_badge_timer_survives_an_arbitrary_error(monkeypatch):
    """A transient DB failure must not raise out of the 60s timer."""
    from not_dot_net.backend import workflow_service
    from not_dot_net.frontend import shell

    async def boom(*a, **k):
        raise ValueError("database went away")

    # Patch the SOURCE module: shell imports the name inside the function.
    monkeypatch.setattr(workflow_service, "get_actionable_count", boom)

    # _safe_actionable_count is the seam the timer uses; it must swallow and
    # report rather than let the timer die.
    result = await shell._safe_actionable_count(object())
    assert result == 0
