"""The remaining verified findings from the 2026-08-19 review.

B2  audit actor filter crashed when cleared
S8  secrets file briefly world-readable
D10 workflow-editor save skipped the permission re-check
D16 concurrent UID allocation reported as range exhaustion
B7  AD-provisioned emails stored with AD's casing
U23 setup wizard accepted any email/password for the first superuser
P13 audit actor filter defeated the index for full-address searches
"""
import os
import uuid

import pytest

from not_dot_net.backend.db import User, session_scope


# --- B2 -------------------------------------------------------------------

def test_audit_filter_tolerates_a_cleared_input():
    """Quasar emits None on clear; every other clearable input is guarded."""
    from not_dot_net.frontend.audit_log import normalize_filter

    assert normalize_filter(None) is None
    assert normalize_filter("") is None
    assert normalize_filter("   ") is None
    assert normalize_filter("  a@b.c  ") == "a@b.c"


# --- S8 -------------------------------------------------------------------

def test_secrets_file_is_never_world_readable(tmp_path):
    from not_dot_net.backend.secrets import load_or_create

    path = tmp_path / "secrets.key"
    load_or_create(path, dev_mode=True)

    mode = os.stat(path).st_mode & 0o777
    assert mode == 0o600, f"secrets file mode is {mode:o}"


def test_secrets_file_is_created_without_a_readable_window(tmp_path, monkeypatch):
    """The mode must come from the open() call, not a later chmod."""
    from not_dot_net.backend import secrets as secrets_module

    seen = {}
    real_open = os.open

    def spy(path, flags, mode=0o777):
        seen["mode"] = mode
        return real_open(path, flags, mode)

    monkeypatch.setattr(secrets_module.os, "open", spy)
    secrets_module.load_or_create(tmp_path / "s.key", dev_mode=True)
    assert seen.get("mode") == 0o600, "secrets file was not opened with 0600"


# --- D16 ------------------------------------------------------------------

async def test_concurrent_uid_allocation_is_not_reported_as_exhaustion():
    """An IntegrityError from a racing allocator means 'retry', not 'range full'."""
    from not_dot_net.backend.uid_allocator import UidAllocation, allocate_uid

    async with session_scope() as session:
        user = User(email="uidrace@example.com", hashed_password="x", is_active=True, role="")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    first = await allocate_uid(user_id, "uidrace")

    # Simulate the racing insert landing first: the next pick collides once.
    async with session_scope() as session:
        session.add(UidAllocation(uid=first + 1, source="allocated", sam_account="other"))
        await session.commit()

    second = await allocate_uid(user_id, "uidrace2")
    assert second not in (first, first + 1)


# --- B7 -------------------------------------------------------------------

async def test_provisioned_ldap_email_is_lowercased():
    from not_dot_net.backend.auth.ldap import LdapUserInfo, provision_ldap_user

    info = LdapUserInfo(
        email="Jean.DUPONT@LPP.fr",
        dn="cn=jdupont,dc=x",
        full_name="Jean Dupont",
        is_active=True,
    )
    user = await provision_ldap_user(info, "")
    assert user.email == "jean.dupont@lpp.fr"


# --- U23 ------------------------------------------------------------------

def test_setup_wizard_rejects_a_malformed_email():
    from not_dot_net.frontend.setup_wizard import validate_setup_credentials

    assert validate_setup_credentials("not-an-email", "hunter22!") == "setup_invalid_email"


def test_setup_wizard_rejects_a_short_password():
    from not_dot_net.frontend.setup_wizard import validate_setup_credentials

    assert validate_setup_credentials("boss@example.com", "abc") == "setup_password_too_short"


def test_setup_wizard_accepts_a_reasonable_pair():
    from not_dot_net.frontend.setup_wizard import validate_setup_credentials

    assert validate_setup_credentials("boss@example.com", "a-long-enough-passphrase") is None


def test_setup_wizard_still_requires_both():
    from not_dot_net.frontend.setup_wizard import validate_setup_credentials

    assert validate_setup_credentials("", "whatever-long") == "setup_email_password_required"
    assert validate_setup_credentials("boss@example.com", "") == "setup_email_password_required"


# --- P13 ------------------------------------------------------------------

def test_full_address_filter_uses_an_exact_match():
    """A complete address is the common case and can use the index."""
    from not_dot_net.frontend.audit_log import normalize_filter
    from not_dot_net.backend.audit import actor_email_clause

    clause = actor_email_clause("Boss@Example.com")
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "%" not in rendered, "full address still used a leading-wildcard scan"


def test_partial_filter_still_matches_substrings():
    from not_dot_net.backend.audit import actor_email_clause

    clause = actor_email_clause("boss")
    rendered = str(clause.compile(compile_kwargs={"literal_binds": True}))
    assert "%" in rendered


# --- P12 ------------------------------------------------------------------

def test_floorplan_upload_cap_is_defined_and_used():
    """The dialog reads the whole file into memory before Pillow sees it."""
    import inspect

    from not_dot_net.frontend import floorplan

    assert floorplan.FLOORPLAN_MAX_UPLOAD_MB > 0
    source = inspect.getsource(floorplan._show_add_plan_dialog)
    assert "FLOORPLAN_MAX_UPLOAD_MB" in source
    assert "max_file_size" in source, "no client-side cap on the upload widget"


# --- D10 ------------------------------------------------------------------

def test_workflow_editor_save_rechecks_the_permission():
    """The most powerful config write in the app skipped the re-check that
    admin_settings and pages both do."""
    import inspect

    from not_dot_net.frontend.workflow_editor import WorkflowEditorDialog

    source = inspect.getsource(WorkflowEditorDialog.save)
    assert "check_permission" in source
