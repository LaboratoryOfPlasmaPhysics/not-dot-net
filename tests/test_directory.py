import asyncio

from nicegui.testing import User

from not_dot_net.backend.users import authenticate_and_get_token


async def test_directory_shows_search(user: User) -> None:
    await user.open("/login")
    # Wait for startup tasks (DB creation, admin seeding) to complete
    await asyncio.sleep(0.5)
    token = await authenticate_and_get_token("admin@not-dot-net.dev", "admin")
    assert token is not None
    user.http_client.cookies.set("fastapiusersauth", token)
    await user.open("/")
    await user.should_see("Search")
