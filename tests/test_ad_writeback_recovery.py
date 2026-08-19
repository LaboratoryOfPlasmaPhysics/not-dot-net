"""I3 — AD create succeeded, local write-back failed: permanently wedged.

_handle_ad_account_creation creates the AD account, then writes ldap_dn /
ldap_username / uid_number back to the local User in a separate session. If that
write fails (or the process dies in between), every retry hits:

    sam_exists=True, target.ldap_username=None -> reprovision=False
    -> ValueError("sAMAccountName already exists in AD")

forever, with no way out but hand-editing the database. The retry now adopts the
orphan account when AD confirms its mail is the one we were about to set.
"""
import pytest


class _FakeEntry:
    def __init__(self, dn, mail, uid_number=None):
        self.entry_dn = dn
        self.mail = mail
        self.uidNumber = uid_number


def test_lookup_returns_none_when_absent(monkeypatch):
    from not_dot_net.backend.auth import ldap as ldap_module

    class _Conn:
        entries = []

        def search(self, *a, **k):
            return False

        def unbind(self):
            pass

    monkeypatch.setattr(ldap_module, "_ldap_bind", lambda *a, **k: _Conn())
    assert ldap_module.ldap_lookup_by_sam(
        "ghost", "admin", "pw", ldap_module.LdapConfig(base_dn="dc=x")
    ) is None


def test_lookup_returns_dn_and_mail(monkeypatch):
    from not_dot_net.backend.auth import ldap as ldap_module

    class _Attr:
        def __init__(self, v):
            self.value = v

    class _Entry:
        entry_dn = "CN=jdoe,OU=Users,dc=x"
        mail = _Attr("j.doe@lpp.fr")
        uidNumber = _Attr(10042)

    class _Conn:
        entries = [_Entry()]

        def search(self, *a, **k):
            return True

        def unbind(self):
            pass

    monkeypatch.setattr(ldap_module, "_ldap_bind", lambda *a, **k: _Conn())
    found = ldap_module.ldap_lookup_by_sam(
        "jdoe", "admin", "pw", ldap_module.LdapConfig(base_dn="dc=x")
    )
    assert found == {
        "dn": "CN=jdoe,OU=Users,dc=x",
        "mail": "j.doe@lpp.fr",
        "uid_number": 10042,
    }


def test_orphan_account_with_matching_mail_is_adopted():
    from not_dot_net.backend.workflow_service import should_adopt_orphan_account

    assert should_adopt_orphan_account(
        existing={"dn": "CN=x,dc=y", "mail": "a.b@lpp.fr", "uid_number": 10042},
        intended_mail="A.B@lpp.fr",
        target_ldap_username=None,
        target_ldap_dn=None,
    ) is True


def test_orphan_with_a_different_mail_is_not_adopted():
    """A same-named person who already left must not have their password reset."""
    from not_dot_net.backend.workflow_service import should_adopt_orphan_account

    assert should_adopt_orphan_account(
        existing={"dn": "CN=x,dc=y", "mail": "someone.else@lpp.fr", "uid_number": 1},
        intended_mail="a.b@lpp.fr",
        target_ldap_username=None,
        target_ldap_dn=None,
    ) is False


def test_account_with_no_mail_is_not_adopted():
    """Without proof of identity, refuse rather than guess."""
    from not_dot_net.backend.workflow_service import should_adopt_orphan_account

    assert should_adopt_orphan_account(
        existing={"dn": "CN=x,dc=y", "mail": None, "uid_number": 1},
        intended_mail="a.b@lpp.fr",
        target_ldap_username=None,
        target_ldap_dn=None,
    ) is False


def test_already_linked_target_is_not_an_orphan_case():
    """That is the existing reprovision path, not adoption."""
    from not_dot_net.backend.workflow_service import should_adopt_orphan_account

    assert should_adopt_orphan_account(
        existing={"dn": "CN=x,dc=y", "mail": "a.b@lpp.fr", "uid_number": 1},
        intended_mail="a.b@lpp.fr",
        target_ldap_username="ab",
        target_ldap_dn="CN=x,dc=y",
    ) is False


@pytest.mark.asyncio
async def test_retry_recovers_when_the_write_back_never_landed(monkeypatch):
    """End to end: AD has the account, the local User has no link, retry works."""
    from unittest.mock import MagicMock

    import not_dot_net.backend.workflow_service as ws
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import AuthMethod, User, session_scope
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x"], "eligible_groups": [],
    }))

    # AD already holds the account a previous attempt created.
    monkeypatch.setattr(ws, "ldap_lookup_by_sam", lambda *a, **kw: {
        "dn": "CN=orphan,OU=Users,DC=x",
        "mail": "orphan@lpp.fr",
        "uid_number": 10099,
    }, raising=False)

    reset_calls = []
    monkeypatch.setattr(ws, "ldap_reset_password",
                        lambda dn, *a, **kw: reset_calls.append(dn), raising=False)

    def must_not_create(*a, **kw):
        raise AssertionError("tried to create an account that already exists")

    monkeypatch.setattr(ws, "ldap_create_user", must_not_create, raising=False)

    # ...but the local user was never linked to it.
    async with session_scope() as session:
        target = User(
            email="orphan@lpp.fr", full_name="Orphan One", hashed_password="x",
            auth_method=AuthMethod.LOCAL, role="", is_active=False,
        )
        session.add(target)
        await session.commit()
        await session.refresh(target)
        target_id = target.id
        assert target.ldap_dn is None and target.ldap_username is None

    request = MagicMock(target_email="orphan@lpp.fr", id="req-orphan", type="onboarding")
    form = {
        "first_name": "Orphan", "last_name": "One", "sam_account": "orphan",
        "ou_dn": "OU=Users,DC=x", "mail": "orphan@lpp.fr", "home_directory": "/h",
    }

    await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())

    assert reset_calls == ["CN=orphan,OU=Users,DC=x"], "password was not reset on adoption"
    async with session_scope() as session:
        linked = await session.get(User, target_id)
        assert linked.ldap_dn == "CN=orphan,OU=Users,DC=x"
        assert linked.ldap_username == "orphan"
        assert linked.uid_number == 10099


@pytest.mark.asyncio
async def test_retry_still_refuses_a_stranger_with_the_same_sam(monkeypatch):
    from unittest.mock import MagicMock

    import not_dot_net.backend.workflow_service as ws
    from not_dot_net.backend.ad_account_config import ad_account_config
    from not_dot_net.backend.db import AuthMethod, User, session_scope
    from not_dot_net.backend.workflow_service import _handle_ad_account_creation

    cfg = await ad_account_config.get()
    await ad_account_config.set(cfg.model_copy(update={
        "users_ous": ["OU=Users,DC=x"], "eligible_groups": [],
    }))
    monkeypatch.setattr(ws, "ldap_lookup_by_sam", lambda *a, **kw: {
        "dn": "CN=jdupont,OU=Users,DC=x",
        "mail": "jean.dupont.senior@lpp.fr",   # a different person
        "uid_number": 10001,
    }, raising=False)

    async with session_scope() as session:
        session.add(User(
            email="jean.dupont@lpp.fr", full_name="Jean Dupont", hashed_password="x",
            auth_method=AuthMethod.LOCAL, role="", is_active=False,
        ))
        await session.commit()

    request = MagicMock(target_email="jean.dupont@lpp.fr", id="req-x", type="onboarding")
    form = {
        "first_name": "Jean", "last_name": "Dupont", "sam_account": "jdupont",
        "ou_dn": "OU=Users,DC=x", "mail": "jean.dupont@lpp.fr", "home_directory": "/h",
    }
    with pytest.raises(ValueError, match="already exists"):
        await _handle_ad_account_creation(request, form, ("a", "p"), MagicMock())
