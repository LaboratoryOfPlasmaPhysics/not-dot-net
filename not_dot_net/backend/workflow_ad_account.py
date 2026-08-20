"""AD account creation for the `ad_account_creation` workflow step type.

The LDAP primitives are imported into this module's namespace on purpose: the
handler reaches them as module globals, so tests monkeypatch them here.
"""

import asyncio
import logging
import re
import secrets
import string
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select

from not_dot_net.backend import mail as mailer
from not_dot_net.backend.ad_account_config import ad_account_config
from not_dot_net.backend.audit import log_audit
from not_dot_net.backend.db import User, session_scope
from not_dot_net.backend.email_templates import render_email
from not_dot_net.backend.uid_allocator import allocate_uid
from not_dot_net.backend.workflow_config import workflows_config
from not_dot_net.config import org_config

# LDAP primitives — imported directly so tests can monkeypatch them via this module's namespace.
from not_dot_net.backend.auth.ldap import (
    ldap_config as _ldap_cfg_section,
    ldap_lookup_by_sam,
    ldap_create_user,
    ldap_reset_password,
    ldap_add_to_groups,
    NewAdUser,
    LdapModifyError,
    get_ldap_connect,
    USERNAME_RE,
)

logger = logging.getLogger(__name__)


def _normalize_name(s: str) -> str:
    """Lowercase + accent-strip + drop non-alphanumeric."""
    if not s:
        return ""
    decomposed = unicodedata.normalize("NFKD", s)
    no_accent = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]", "", no_accent.lower())


def derive_sam_candidates(first_name: str, last_name: str, max_steps: int = 5) -> list[str]:
    """Return sAM candidates in cascading order: {last}, {last}{first[0]}, {last}{first[:2]}, ..."""
    last = _normalize_name(last_name)
    first = _normalize_name(first_name)
    candidates = [last]
    for i in range(1, min(len(first), max_steps) + 1):
        candidates.append(f"{last}{first[:i]}")
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def render_mail(template: str, first_name: str, last_name: str) -> str:
    return template.format(first=_normalize_name(first_name), last=_normalize_name(last_name))


def render_home(template: str, sam: str) -> str:
    return template.format(sam=sam)


def generate_initial_password(length: int = 16) -> str:
    """Strong password with at least one upper, lower, digit, symbol — passes AD complexity."""
    alpha = string.ascii_letters
    digits = string.digits
    symbols = "!@#$%^&*-_=+"
    pool = alpha + digits + symbols
    while True:
        pwd = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in symbols for c in pwd)):
            return pwd


@dataclass(frozen=True)
class AdAccountCreationResult:
    request_id: uuid.UUID
    new_dn: str
    sam_account: str
    uid: int
    initial_password: str
    group_failures: dict[str, str]



def should_adopt_orphan_account(
    *,
    existing: dict | None,
    intended_mail: str,
    target_ldap_username: str | None,
    target_ldap_dn: str | None,
) -> bool:
    """Whether an existing AD account is this target's, left by a failed retry.

    The AD account is created before the local write-back that records ldap_dn /
    ldap_username. If that write-back never lands, the account exists but nothing
    locally points at it, and every retry dead-ends on "already exists" — with no
    fix short of editing the database by hand.

    Adoption requires AD itself to confirm the account carries the mail we were
    about to set. Without that proof we refuse: a same-named colleague who left
    years ago would otherwise get their password reset out from under them.
    """
    if not existing:
        return False
    if target_ldap_username or target_ldap_dn:
        return False  # already linked — that is the ordinary reprovision path
    existing_mail = (existing.get("mail") or "").strip().lower()
    return bool(existing_mail) and existing_mail == (intended_mail or "").strip().lower()


async def handle_ad_account_creation(
    request,
    form_data: dict,
    ad_creds: tuple[str, str],
    actor_user,
) -> AdAccountCreationResult:
    """Allocate UID → create AD user → write back → apply groups.

    Raises on AD create failure (step stays pending). Group-add failures are returned, not raised.
    """
    # Resolved here, not at import time, so tests monkeypatching this module's
    # LDAP names are picked up per call.
    _ldap_lookup = ldap_lookup_by_sam
    _ldap_create = ldap_create_user
    _ldap_reset = ldap_reset_password
    _ldap_add_groups = ldap_add_to_groups
    _NewAdUser = NewAdUser
    _LdapModifyError = LdapModifyError
    _connect = get_ldap_connect()

    ad_cfg = await ad_account_config.get()
    ldap_cfg = await _ldap_cfg_section.get()
    bind_user, bind_pw = ad_creds

    sam = form_data["sam_account"].strip()
    if not sam or not USERNAME_RE.fullmatch(sam):
        raise ValueError(f"Invalid sAMAccountName: {sam!r} — must match [a-zA-Z0-9._-]{{1,64}}")

    ou_dn = form_data["ou_dn"]
    if ou_dn not in ad_cfg.users_ous:
        raise ValueError(f"OU not in eligible list: {ou_dn}")

    chosen_groups = list(form_data.get("groups") or [])
    bad_groups = [g for g in chosen_groups if g not in ad_cfg.eligible_groups]
    if bad_groups:
        raise ValueError(f"groups not in eligible_groups: {bad_groups}")

    async with session_scope() as session:
        target = (await session.execute(
            select(User).where(func.lower(User.email) == (request.target_email or "").lower())
        )).scalar_one_or_none()
    if not target:
        raise ValueError(f"No local User for target_email={request.target_email!r}")

    first = form_data["first_name"]
    last = form_data["last_name"]
    display_name = form_data.get("display_name") or f"{first} {last}"
    mail = form_data["mail"]
    gid_number = int(form_data.get("gid_number") or ad_cfg.default_gid_number)
    description = form_data.get("description")
    initial_password = generate_initial_password(ad_cfg.password_length)

    # ldap3 is synchronous — run AD round-trips off the event loop so a slow
    # DC doesn't freeze every connected client.
    existing = await asyncio.to_thread(_ldap_lookup, sam, bind_user, bind_pw, ldap_cfg, _connect)
    sam_exists = existing is not None
    # A retry after a partial failure (AD account created, but the workflow step
    # never committed) leaves the account existing AND already linked to this
    # target. Reprovision idempotently instead of dead-ending on "already
    # exists" — the one-time password is unrecoverable, so reset it afresh.
    relinked = (
        sam_exists
        and (target.ldap_username or "").lower() == sam.lower()
        and bool(target.ldap_dn)
    )
    # Same partial failure, one step earlier: the AD account exists but the
    # local write-back never landed, so nothing points at it. Adopt it when AD
    # confirms the mail matches — otherwise this dead-ends forever.
    adopted = should_adopt_orphan_account(
        existing=existing,
        intended_mail=mail,
        target_ldap_username=target.ldap_username,
        target_ldap_dn=target.ldap_dn,
    )
    reprovision = relinked or adopted
    if sam_exists and not reprovision:
        raise ValueError(f"sAMAccountName already exists in AD: {sam}")

    if reprovision:
        uid = target.uid_number or (existing or {}).get("uid_number")
        new_dn = target.ldap_dn or (existing or {}).get("dn")
        if adopted:
            logger.warning(
                "Adopting orphan AD account %s for %s — a previous creation "
                "wrote to AD but never linked it locally",
                new_dn, target.email,
            )
        try:
            await asyncio.to_thread(
                _ldap_reset, new_dn, initial_password, bind_user, bind_pw, ldap_cfg, _connect,
            )
        except _LdapModifyError as e:
            await log_audit(
                category="ad", action="reset_password",
                actor_id=str(actor_user.id) if actor_user else None,
                target_id=str(target.id),
                detail=f"sam={sam} dn={new_dn} error={e} succeeded=False",
            )
            raise
        await log_audit(
            category="ad", action="reset_password",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail=f"sam={sam} uid={uid} dn={new_dn} idempotent_retry succeeded=True",
        )
    else:
        uid = await allocate_uid(target.id, sam)
        new_user = _NewAdUser(
            sam_account=sam,
            given_name=first,
            surname=last,
            display_name=display_name,
            mail=mail,
            description=description,
            ou_dn=ou_dn,
            uid_number=uid,
            gid_number=gid_number,
            login_shell=form_data.get("login_shell") or ad_cfg.default_login_shell,
            home_directory=form_data["home_directory"],
            initial_password=initial_password,
            must_change_password=True,
        )
        try:
            new_dn = await asyncio.to_thread(_ldap_create, new_user, bind_user, bind_pw, ldap_cfg, _connect)
        except _LdapModifyError as e:
            # The UID row is deliberately NOT released here: UIDs are never
            # reused (see uid_allocator), because a UID that was handed out may
            # already own files on disk. Leaking one on a failed create is the
            # cheaper half of that trade — the range holds 50k of them.
            await log_audit(
                category="ad", action="create_user",
                actor_id=str(actor_user.id) if actor_user else None,
                target_id=str(target.id),
                detail=f"sam={sam} uid={uid} error={e} succeeded=False",
            )
            raise
        await log_audit(
            category="ad", action="create_user",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail=f"sam={sam} uid={uid} dn={new_dn} ou={ou_dn} succeeded=True",
        )

    async with session_scope() as session:
        u = await session.get(User, target.id)
        if u is not None:
            u.ldap_dn = new_dn
            u.ldap_username = sam
            u.uid_number = uid
            u.gid_number = gid_number
            u.description = description
            u.is_active = True
            await session.commit()

    group_failures: dict[str, str] = {}
    if chosen_groups:
        group_failures = await asyncio.to_thread(
            _ldap_add_groups, new_dn, chosen_groups, bind_user, bind_pw, ldap_cfg, _connect,
        )
        await log_audit(
            category="ad", action="add_to_groups",
            actor_id=str(actor_user.id) if actor_user else None,
            target_id=str(target.id),
            detail=f"groups={chosen_groups} failures={group_failures}",
        )

    contact_email = (request.target_email or "").strip()
    if contact_email:
        # Look up the workflow label for the email subject.
        wf_cfg = await workflows_config.get()
        wf = wf_cfg.workflows.get(request.type)
        workflow_label = (wf.label if wf else request.type) or "Workflow"
        _org_cfg = await org_config.get()
        _base_url = _org_cfg.base_url.rstrip("/")
        _app_name = (_org_cfg.app_name or "not-dot-net").strip() or "not-dot-net"
        ctx = {
            "app_name": _app_name,
            "app_url": f"{_base_url}/",
            "recipient_name": display_name,
            "workflow_label": workflow_label,
            "sam": sam,
            "display_name": display_name,
            "mail": mail,
        }
        subject, body = await render_email("account_created", ctx)
        await mailer.send_mail(contact_email, subject, body)

    return AdAccountCreationResult(
        request_id=request.id, new_dn=new_dn, sam_account=sam,
        uid=uid, initial_password=initial_password, group_failures=group_failures,
    )
