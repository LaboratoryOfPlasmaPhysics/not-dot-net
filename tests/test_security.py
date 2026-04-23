"""Tests for critical security fixes."""

from not_dot_net.frontend.login import _safe_redirect


class TestOpenRedirect:
    def test_relative_path_allowed(self):
        assert _safe_redirect("/dashboard") == "/dashboard"

    def test_root_allowed(self):
        assert _safe_redirect("/") == "/"

    def test_absolute_url_rejected(self):
        assert _safe_redirect("https://evil.com") == "/"

    def test_protocol_relative_rejected(self):
        assert _safe_redirect("//evil.com") == "/"

    def test_scheme_with_netloc_rejected(self):
        assert _safe_redirect("http://evil.com/path") == "/"

    def test_empty_string_rejected(self):
        assert _safe_redirect("") == "/"

    def test_path_with_query_allowed(self):
        assert _safe_redirect("/page?foo=bar") == "/page?foo=bar"

    def test_triple_slash_rejected(self):
        assert _safe_redirect("///evil.com") == "/"

    def test_backslash_rejected(self):
        assert _safe_redirect("/\\evil.com") == "/"

    def test_no_leading_slash_rejected(self):
        assert _safe_redirect("evil.com") == "/"

    def test_javascript_scheme_rejected(self):
        assert _safe_redirect("javascript:alert(1)") == "/"

    def test_data_scheme_rejected(self):
        assert _safe_redirect("data:text/html,hi") == "/"


class TestAuditResolveNames:
    def test_target_id_not_mutated(self):
        """_resolve_names should not overwrite target_id with display name (#4)."""
        from not_dot_net.backend.audit import AuditEvent

        ev = AuditEvent(
            category="test", action="test",
            target_type="user", target_id="some-uuid-string",
        )
        # Before resolution, _target_display should not exist
        assert not hasattr(ev, "_target_display")
        # After resolution sets _target_display, target_id should remain a UUID string
        ev._target_display = "John Doe"
        assert ev.target_id == "some-uuid-string"
        assert ev._target_display == "John Doe"


class TestNoPublicRestApi:
    """FastAPI-Users routers exposed PATCH /users/me which let any authenticated
    user escalate themselves by setting role='admin' (custom fields bypass the
    library's is_superuser strip). The whole public REST surface was removed —
    only NiceGUI and the HTML login form are reachable."""

    def _routes(self):
        from nicegui import app
        from not_dot_net.app import create_app
        paths = {getattr(r, "path", None) for r in app.routes}
        if "/login" not in paths and "/auth/login" not in paths:
            create_app()
            paths = {getattr(r, "path", None) for r in app.routes}
        return paths

    def test_users_router_not_mounted(self):
        paths = self._routes()
        assert "/users/me" not in paths
        assert not any(p and p.startswith("/users/") for p in paths)

    def test_fastapi_users_auth_routers_not_mounted(self):
        paths = self._routes()
        assert not any(p and p.startswith("/auth/jwt") for p in paths)
        assert not any(p and p.startswith("/auth/cookie") for p in paths)

    def test_auth_local_jwt_endpoint_not_mounted(self):
        assert "/auth/local" not in self._routes()


class TestEmailCollision:
    """LDAP auth must not hijack a local account that shares the same email."""

    async def test_ldap_login_upgrades_local_account(self):
        """When AD auth succeeds for an email matching a LOCAL account,
        upgrade auth_method to LDAP (the user proved AD ownership) but
        preserve the existing role — no escalation."""
        from contextlib import asynccontextmanager
        from not_dot_net.backend.auth.ldap import (
            LdapConfig, ldap_config, set_ldap_connect,
        )
        from not_dot_net.backend.db import AuthMethod, User, session_scope, get_user_db
        from not_dot_net.backend.users import get_user_manager
        from not_dot_net.backend.schemas import UserCreate
        from not_dot_net.frontend.login import _try_ldap_auth
        from tests.test_ldap_provision import _make_fake_connect

        async with session_scope() as session:
            async with asynccontextmanager(get_user_db)(session) as user_db:
                async with asynccontextmanager(get_user_manager)(user_db) as mgr:
                    user = await mgr.create(
                        UserCreate(email="admin@example.com", password="localpass", is_active=True)
                    )
                    user.role = "admin"
                    user.auth_method = AuthMethod.LOCAL
                    session.add(user)
                    await session.commit()
                    user_id = user.id

        fake_users = {
            "admin": {
                "mail": "admin@example.com",
                "displayName": "AD Admin",
                "password": "ldappass",
            },
        }
        cfg = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com", auto_provision=True)
        await ldap_config.set(cfg)
        set_ldap_connect(_make_fake_connect(fake_users))

        result = await _try_ldap_auth("admin", "ldappass")
        assert result is not None
        assert result.id == user_id
        assert result.role == "admin"

        async with session_scope() as session:
            refreshed = await session.get(User, user_id)
            assert refreshed.auth_method == AuthMethod.LDAP

    async def test_ldap_login_allowed_for_existing_ldap_account(self):
        from not_dot_net.backend.auth.ldap import (
            LdapConfig, ldap_config, set_ldap_connect,
        )
        from not_dot_net.backend.db import AuthMethod, User, session_scope
        from not_dot_net.frontend.login import _try_ldap_auth
        from tests.test_ldap_provision import _make_fake_connect

        async with session_scope() as session:
            user = User(
                email="ldapuser@example.com", hashed_password="x",
                is_active=True, auth_method=AuthMethod.LDAP,
            )
            session.add(user)
            await session.commit()
            original_id = user.id

        fake_users = {
            "ldapuser": {
                "mail": "ldapuser@example.com",
                "displayName": "LDAP User",
                "password": "pass",
            },
        }
        cfg = LdapConfig(url="fake", domain="example.com", base_dn="dc=example,dc=com", auto_provision=True)
        await ldap_config.set(cfg)
        set_ldap_connect(_make_fake_connect(fake_users))

        result = await _try_ldap_auth("ldapuser", "pass")
        assert result is not None
        assert result.id == original_id


class TestLdapEscaping:
    def test_special_chars_escaped(self):
        from ldap3.utils.conv import escape_filter_chars

        malicious = "admin)(objectClass=*"
        escaped = escape_filter_chars(malicious)
        assert "(" not in escaped
        assert ")" not in escaped

    def test_asterisk_escaped(self):
        from ldap3.utils.conv import escape_filter_chars

        assert "*" not in escape_filter_chars("*")
