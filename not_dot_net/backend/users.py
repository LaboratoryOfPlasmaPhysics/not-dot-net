import uuid
from contextlib import asynccontextmanager

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
from not_dot_net.config import get_settings


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    @property
    def reset_password_token_secret(self):
        return get_settings().jwt_secret

    @property
    def verification_token_secret(self):
        return get_settings().jwt_secret

    async def on_after_register(self, user: User, request: Request | None = None):
        print(f"User {user.id} has registered.")

    async def on_after_update(self, user: User, update_dict: dict, request=None):
        if "role" in update_dict:
            user.is_superuser = (user.role == Role.ADMIN)


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
    return JWTStrategy(secret=get_settings().jwt_secret, lifetime_seconds=3600)


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


async def ensure_default_admin() -> None:
    """Create default admin user if it doesn't exist yet."""
    from not_dot_net.backend.db import get_async_session, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from fastapi_users.exceptions import UserAlreadyExists

    settings = get_settings()

    get_session_ctx = asynccontextmanager(get_async_session)
    get_user_db_ctx = asynccontextmanager(get_user_db)
    get_user_manager_ctx = asynccontextmanager(get_user_manager)

    async with get_session_ctx() as session:
        async with get_user_db_ctx(session) as user_db:
            async with get_user_manager_ctx(user_db) as user_manager:
                try:
                    user = await user_manager.create(
                        UserCreate(
                            email=settings.admin_email,
                            password=settings.admin_password,
                            is_active=True,
                            is_superuser=True,
                        )
                    )
                    user.role = Role.ADMIN
                    session.add(user)
                    await session.commit()
                    print(f"Default admin '{settings.admin_email}' created.")
                except UserAlreadyExists:
                    pass


FAKE_USERS = [
    {"email": "marie.curie@lpp.polytechnique.fr", "full_name": "Marie Curie", "team": "Plasma Physics", "office": "B210", "phone": "+33 1 69 33 4001", "title": "Research Director", "employment_status": "researcher", "role": "staff"},
    {"email": "pierre.dumont@lpp.polytechnique.fr", "full_name": "Pierre Dumont", "team": "Instrumentation", "office": "A115", "phone": "+33 1 69 33 4002", "title": "Senior Engineer", "employment_status": "researcher", "role": "staff"},
    {"email": "sophie.martin@lpp.polytechnique.fr", "full_name": "Sophie Martin", "team": "Space Weather", "office": "C302", "phone": "+33 1 69 33 4003", "title": "Postdoc", "employment_status": "researcher", "role": "staff"},
    {"email": "lucas.bernard@lpp.polytechnique.fr", "full_name": "Lucas Bernard", "team": "Theory & Simulation", "office": "B108", "phone": "+33 1 69 33 4004", "title": "PhD Student", "employment_status": "phd_student", "role": "member"},
    {"email": "emma.petit@lpp.polytechnique.fr", "full_name": "Emma Petit", "team": "Plasma Physics", "office": "B212", "phone": "+33 1 69 33 4005", "title": "PhD Student", "employment_status": "phd_student", "role": "member"},
    {"email": "thomas.leroy@lpp.polytechnique.fr", "full_name": "Thomas Leroy", "team": "Instrumentation", "office": "A120", "phone": "+33 1 69 33 4006", "title": "Intern", "employment_status": "intern", "role": "member"},
    {"email": "camille.moreau@lpp.polytechnique.fr", "full_name": "Camille Moreau", "team": "Administration", "office": "A001", "phone": "+33 1 69 33 4007", "title": "Administrative Assistant", "employment_status": "researcher", "role": "staff"},
    {"email": "jean.dupont@lpp.polytechnique.fr", "full_name": "Jean Dupont", "team": "Space Weather", "office": "C305", "phone": "+33 1 69 33 4008", "title": "Research Scientist", "employment_status": "researcher", "role": "staff"},
    {"email": "alice.roux@lpp.polytechnique.fr", "full_name": "Alice Roux", "team": "Theory & Simulation", "office": "B110", "phone": "+33 1 69 33 4009", "title": "Visiting Researcher", "employment_status": "visitor", "role": "member"},
    {"email": "nicolas.lambert@lpp.polytechnique.fr", "full_name": "Nicolas Lambert", "team": "Plasma Physics", "office": "B215", "phone": "+33 1 69 33 4010", "title": "Professor", "employment_status": "researcher", "role": "director"},
]


async def seed_fake_users() -> None:
    """Seed fake users for development. Skips users that already exist."""
    from not_dot_net.backend.db import get_async_session, get_user_db
    from not_dot_net.backend.schemas import UserCreate
    from fastapi_users.exceptions import UserAlreadyExists

    get_session_ctx = asynccontextmanager(get_async_session)
    get_user_db_ctx = asynccontextmanager(get_user_db)
    get_user_manager_ctx = asynccontextmanager(get_user_manager)

    async with get_session_ctx() as session:
        async with get_user_db_ctx(session) as user_db:
            async with get_user_manager_ctx(user_db) as user_manager:
                count = 0
                for fake in FAKE_USERS:
                    try:
                        user = await user_manager.create(
                            UserCreate(
                                email=fake["email"],
                                password="dev",
                                is_active=True,
                                is_superuser=False,
                            )
                        )
                        for field in ("full_name", "phone", "office", "team", "title", "employment_status", "role"):
                            setattr(user, field, fake.get(field))
                        session.add(user)
                        count += 1
                    except UserAlreadyExists:
                        pass
                await session.commit()
                if count:
                    print(f"Seeded {count} fake users.")


async def authenticate_and_get_token(email: str, password: str) -> str | None:
    """Authenticate a user and return a cookie token, or None on failure.

    Intended for use in NiceGUI page callbacks where FastAPI DI is not available.
    """
    from not_dot_net.backend.db import get_async_session, get_user_db
    from fastapi.security import OAuth2PasswordRequestForm

    get_session_ctx = asynccontextmanager(get_async_session)
    get_user_db_ctx = asynccontextmanager(get_user_db)
    get_user_manager_ctx = asynccontextmanager(get_user_manager)

    async with get_session_ctx() as session:
        async with get_user_db_ctx(session) as user_db:
            async with get_user_manager_ctx(user_db) as user_manager:
                credentials = OAuth2PasswordRequestForm(
                    username=email, password=password, scope="", grant_type="password"
                )
                user = await user_manager.authenticate(credentials)
                if user is None or not user.is_active:
                    return None
                strategy = cookie_backend.get_strategy()
                return await strategy.write_token(user)
