"""B1: cookie_secure must be set explicitly via dev_mode, not heuristically
from the presence of DATABASE_URL in os.environ.

Old behavior: a developer who exported DATABASE_URL=sqlite:./dev.db locally
would suddenly get Secure-only cookies and be unable to log in over HTTP.
"""

import os

from not_dot_net.backend.users import (
    cookie_transport,
    init_user_secrets,
    set_dev_mode,
)
from not_dot_net.backend.secrets import AppSecrets


def test_cookie_secure_follows_dev_mode_explicit_false(monkeypatch):
    """Production: cookies must be Secure regardless of env-var fingerprint."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    init_user_secrets(AppSecrets(jwt_secret="x"*32, storage_secret="y"*32, file_encryption_key="z"*32))
    set_dev_mode(False)
    assert cookie_transport.cookie_secure is True


def test_cookie_secure_follows_dev_mode_explicit_true(monkeypatch):
    """Dev: even if DATABASE_URL is set, dev_mode=True keeps cookies plain."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./dev.db")
    init_user_secrets(AppSecrets(jwt_secret="x"*32, storage_secret="y"*32, file_encryption_key="z"*32))
    set_dev_mode(True)
    assert cookie_transport.cookie_secure is False
