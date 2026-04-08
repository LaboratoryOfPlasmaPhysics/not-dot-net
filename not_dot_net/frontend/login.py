from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from nicegui import app, ui

from not_dot_net.backend.users import get_user_manager, cookie_transport, get_jwt_strategy
from not_dot_net.frontend.i18n import t

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

    credentials = OAuth2PasswordRequestForm(
        username=str(form.get("username", "")),
        password=str(form.get("password", "")),
        scope="",
        grant_type="password",
    )
    user = await user_manager.authenticate(credentials)
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


def _safe_redirect(redirect_to: str) -> str:
    """Reject absolute URLs and anything that isn't a plain local path."""
    parsed = urlparse(redirect_to)
    if parsed.scheme or parsed.netloc:
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
                        <input type="hidden" name="redirect_to" value="{safe_dest}">
                        <label>{t("email")}
                            <input name="username" type="email"
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
