import logging
import os
import uuid
from contextlib import asynccontextmanager

logger = logging.getLogger("not_dot_net.users")

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase

from not_dot_net.backend.audit import (
    log_audit,
    log_request_network_debug,
    request_network_metadata,
    request_ip,
    request_user_agent,
)
from not_dot_net.backend import security_alerts
from not_dot_net.backend.db import User, get_user_db
from not_dot_net.backend.secrets import AppSecrets


_secrets: AppSecrets | None = None
# Default true (safe production posture) until set explicitly. Tests and
# `app.create_app` call `set_dev_mode` before any cookie is issued.
_dev_mode: bool = False


def init_user_secrets(secrets: AppSecrets) -> None:
    global _secrets
    _secrets = secrets


def dev_mode() -> bool:
    return _dev_mode


def set_dev_mode(dev: bool) -> None:
    """Toggle the cookie's Secure flag. Production must call set_dev_mode(False)."""
    global _dev_mode
    _dev_mode = dev
    cookie_transport.cookie_secure = not dev


def _get_secret() -> str:
    if _secrets is None:
        raise RuntimeError("Secrets not initialized — call init_user_secrets() first")
    return _secrets.jwt_secret


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @property
    def reset_password_token_secret(self):
        return _get_secret()

    @property
    def verification_token_secret(self):
        return _get_secret()

    async def on_after_register(self, user: User, request: Request | None = None):
        logger.info("User %s registered", user.id)

    async def on_after_login(self, user: User, request: Request | None = None, response=None):
        log_request_network_debug(request, context="login_success")
        ip = request_ip(request)
        user_agent = request_user_agent(request)
        network = request_network_metadata(request)
        if not user.is_superuser:
            await log_audit(
                "auth", "login",
                actor_id=user.id,
                actor_email=user.email,
                metadata={
                    "ip": ip,
                    "user_agent": user_agent,
                    "network": network,
                    "is_superuser": False,
                    "success": True,
                },
            )
            return

        await log_audit(
            "auth", "login",
            actor_id=user.id,
            actor_email=user.email,
            detail=f"Login Success ip={ip or 'unknown'} role={user.role or '(none)'} is_superuser=True",
            metadata={
                "ip": ip,
                "user_agent": user_agent,
                "network": network,
                "role": user.role,
                "is_superuser": True,
                "success": True,
            },
        )
        await security_alerts.notify_superuser_login_success(
            user,
            ip=ip,
            user_agent=user_agent,
        )

    async def update(
        self,
        user_update,
        user: User,
        safe: bool = False,
        request: Request | None = None,
        actor_email: str | None = None,
    ) -> User:
        # FastAPI-Users does not provide the actor on update hooks; callers that
        # have request context can pass it for security alert emails.
        was_superuser = user.is_superuser
        updated_user = await super().update(user_update, user, safe=safe, request=request)
        if not was_superuser and updated_user.is_superuser:
            if actor_email is None:
                await security_alerts.notify_superuser_granted(updated_user)
            else:
                await security_alerts.notify_superuser_granted(
                    updated_user,
                    actor_email=actor_email,
                )
        return updated_user

async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


# 12h: long enough to cover a workday — 1h silently logged people out
# mid-morning (no refresh mechanism on the cookie/JWT pair).
SESSION_LIFETIME_S = 12 * 3600

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
cookie_transport = CookieTransport(
    cookie_name="fastapiusersauth",
    cookie_max_age=SESSION_LIFETIME_S,
    cookie_secure=True,  # production posture by default; set_dev_mode() flips it.
)


def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
    return JWTStrategy(secret=_get_secret(), lifetime_seconds=SESSION_LIFETIME_S)


jwt_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

cookie_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager, [jwt_backend, cookie_backend]
)

current_active_user = fastapi_users.current_user(active=True)
current_active_user_optional = fastapi_users.current_user(active=True, optional=True)


async def ensure_default_admin(email: str, password: str) -> None:
    """Create the bootstrap super-user if none exists yet.

    The super-user is the server admin (is_superuser=True) — it bypasses
    every permission check via permissions.has_permissions(). No role is
    assigned: roles are pure config data and the super-user doesn't need one.
    """
    from not_dot_net.backend.db import session_scope, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from fastapi_users.exceptions import UserAlreadyExists

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                try:
                    await user_manager.create(
                        UserCreate(
                            email=email,
                            password=password,
                            is_active=True,
                            is_superuser=True,
                        )
                    )
                    logger.info("Default super-user '%s' created", email)
                except UserAlreadyExists:
                    pass
