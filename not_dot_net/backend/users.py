import logging
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

from not_dot_net.backend.db import User, get_user_db
from not_dot_net.backend.roles import Role
from not_dot_net.backend.secrets import AppSecrets


_secrets: AppSecrets | None = None


def init_user_secrets(secrets: AppSecrets) -> None:
    global _secrets
    _secrets = secrets


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

    async def on_after_update(self, user: User, update_dict: dict, request: Request | None = None):
        if "role" in update_dict:
            user.is_superuser = (user.role == Role.ADMIN)
            await self.user_db.update(user, {"is_superuser": user.role == Role.ADMIN})


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
cookie_transport = CookieTransport(
    cookie_name="fastapiusersauth",
    cookie_max_age=3600,
    cookie_httponly=False,
)


def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
    return JWTStrategy(secret=_get_secret(), lifetime_seconds=3600)


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
    """Create default admin user if it doesn't exist yet."""
    from not_dot_net.backend.db import session_scope, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from fastapi_users.exceptions import UserAlreadyExists

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                try:
                    user = await user_manager.create(
                        UserCreate(
                            email=email,
                            password=password,
                            is_active=True,
                            is_superuser=True,
                        )
                    )
                    user.role = Role.ADMIN
                    session.add(user)
                    await session.commit()
                    logger.info("Default admin '%s' created", email)
                except UserAlreadyExists:
                    pass


async def authenticate_and_get_token(email: str, password: str) -> str | None:
    """Authenticate a user and return a cookie token, or None on failure.

    Intended for use in NiceGUI page callbacks where FastAPI DI is not available.
    """
    from not_dot_net.backend.db import session_scope, get_user_db
    from fastapi.security import OAuth2PasswordRequestForm

    async with session_scope() as session:
        async with asynccontextmanager(get_user_db)(session) as user_db:
            async with asynccontextmanager(get_user_manager)(user_db) as user_manager:
                credentials = OAuth2PasswordRequestForm(
                    username=email, password=password, scope="", grant_type="password"
                )
                user = await user_manager.authenticate(credentials)
                if user is None or not user.is_active:
                    from not_dot_net.backend.audit import log_audit
                    await log_audit("auth", "login_failed", detail=f"email={email}")
                    return None
                from not_dot_net.backend.audit import log_audit
                await log_audit(
                    "auth", "login",
                    actor_id=user.id, actor_email=user.email,
                )
                strategy = cookie_backend.get_strategy()
                return await strategy.write_token(user)
