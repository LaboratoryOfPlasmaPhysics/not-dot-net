import logging
from html import escape as html_escape
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from nicegui import app, ui

from not_dot_net.backend.users import get_user_manager, cookie_transport, get_jwt_strategy
from not_dot_net.frontend.i18n import t

logger = logging.getLogger("not_dot_net.login")

login_router = APIRouter(tags=["auth"])


@login_router.get("/logout")
async def handle_logout():
    response = RedirectResponse("/login", status_code=303)
    logout_response = await cookie_transport.get_logout_response()
    for header_value in logout_response.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", header_value)
    return response


@login_router.post("/auth/login")
async def handle_login(
    request: Request,
    user_manager=Depends(get_user_manager),
):
    form = await request.form()
    redirect_to = _safe_redirect(str(form.get("redirect_to", "/")))
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    # Try local auth first
    credentials = OAuth2PasswordRequestForm(
        username=username, password=password, scope="", grant_type="password",
    )
    user = await user_manager.authenticate(credentials)

    # Fallback to LDAP/AD if local auth failed
    if user is None or not user.is_active:
        user = await _try_ldap_auth(username, password)

    if user is None or not user.is_active:
        return RedirectResponse("/login?error=1", status_code=303)

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)
    response = RedirectResponse(redirect_to, status_code=303)
    cookie_response = await cookie_transport.get_login_response(token)
    for header_value in cookie_response.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", header_value)

    await user_manager.on_after_login(user, request)
    return response


async def _try_ldap_auth(username: str, password: str):
    """Attempt LDAP auth. Returns User or None. Syncs AD attrs on success."""
    from not_dot_net.backend.auth.ldap import (
        USERNAME_RE, ldap_config, ldap_authenticate, get_ldap_connect,
        provision_ldap_user, sync_user_from_ldap,
    )
    from not_dot_net.backend.db import session_scope, get_user_db, User
    from not_dot_net.backend.roles import roles_config
    from contextlib import asynccontextmanager

    if not USERNAME_RE.match(username):
        return None

    cfg = await ldap_config.get()
    user_info = ldap_authenticate(username, password, cfg, get_ldap_connect())
    if user_info is None:
        return None

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            user = await user_db.get_by_email(user_info.email)

    if user is not None:
        if not user.is_active:
            return None
        from not_dot_net.backend.db import AuthMethod
        if user.auth_method == AuthMethod.LOCAL:
            logger.warning(
                "LDAP user '%s' blocked — email collides with local account %s",
                user_info.email, user.id,
            )
            return None
        await sync_user_from_ldap(user.id, user_info)
        async with session_scope() as session:
            return await session.get(User, user.id)

    if not cfg.auto_provision:
        logger.info("LDAP user '%s' has no local account and auto_provision is off", user_info.email)
        return None

    roles_cfg = await roles_config.get()
    default_role = roles_cfg.default_role or ""
    return await provision_ldap_user(user_info, default_role)


def _safe_redirect(redirect_to: str) -> str:
    """Only allow plain local paths — reject anything that could redirect off-site."""
    parsed = urlparse(redirect_to)
    if parsed.scheme or parsed.netloc:
        return "/"
    if not redirect_to.startswith("/") or redirect_to.startswith("//") or redirect_to.startswith("/\\"):
        return "/"
    return redirect_to


def setup():
    @ui.page("/login")
    def login(redirect_to: str = "/", error: str = "") -> Optional[RedirectResponse]:
        safe_dest = _safe_redirect(redirect_to)

        if app.storage.user.get("authenticated", False):
            return RedirectResponse(safe_dest)

        ui.colors(primary="#0F52AC")
        with ui.column().classes("absolute-center items-center gap-4"):
            ui.label(t("app_name")).classes("text-h4 text-weight-light").style(
                "color: #0F52AC"
            )
            with ui.card().classes("w-80"):
                if error:
                    ui.label(t("invalid_credentials")).classes("text-negative")

                ui.html(f"""
                    <form action="/auth/login" method="post"
                          style="display:flex; flex-direction:column; gap:12px; width:100%;">
                        <input type="hidden" name="redirect_to" value="{html_escape(safe_dest)}">
                        <label>{t("email_or_username")}
                            <input name="username" type="text"
                                   style="width:100%; padding:8px; border:1px solid #ccc; border-radius:4px;">
                        </label>
                        <label>{t("password")}
                            <input name="password" type="password"
                                   style="width:100%; padding:8px; border:1px solid #ccc; border-radius:4px;">
                        </label>
                        <button type="submit"
                                style="padding:10px; background:#0F52AC; color:white; border:none;
                                       border-radius:4px; cursor:pointer; font-size:14px;">
                            {t("log_in")}
                        </button>
                    </form>
                """)
        return None
