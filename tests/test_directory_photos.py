"""Directory photos are served by URL, not inlined (review 2026-10-12 F9).

Every directory open read every photo blob and pushed it over the websocket
as base64: about 6 MB for 300 people, before anyone clicked anything.
"""

from io import BytesIO

from PIL import Image
from sqlalchemy import inspect as sa_inspect

from not_dot_net.backend.db import User, session_scope
from not_dot_net.frontend.directory import _load_people, _photo_sizes, photo_url


def _jpeg() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (40, 40), "red").save(buf, format="JPEG")
    return buf.getvalue()


async def _person(email: str, *, active: bool = True, photo: bytes | None = None) -> User:
    async with session_scope() as session:
        person = User(email=email, hashed_password="x", is_active=active, photo=photo)
        session.add(person)
        await session.commit()
        await session.refresh(person)
        return person


async def test_directory_list_does_not_load_photo_bytes():
    await _person("with-photo@test.com", photo=_jpeg())
    people = await _load_people()
    assert all("photo" in sa_inspect(p).unloaded for p in people)


async def test_photo_sizes_version_the_url():
    person = await _person("versioned@test.com", photo=_jpeg())
    size = (await _photo_sizes())[person.id]
    assert photo_url(person.id, size) == f"/directory/photo/{person.id}?v={size}"


async def test_active_person_photo_is_served(user):
    photo = _jpeg()
    person = await _person("served@test.com", photo=photo)
    response = await user.http_client.get(f"/directory/photo/{person.id}")
    assert response.status_code == 200
    assert response.content == photo
    assert response.headers["content-type"] == "image/jpeg"


async def test_inactive_person_photo_is_not_public(user):
    person = await _person("gone@test.com", active=False, photo=_jpeg())
    response = await user.http_client.get(f"/directory/photo/{person.id}")
    assert response.status_code == 404


async def test_missing_photo_is_404(user):
    person = await _person("nophoto@test.com")
    assert (await user.http_client.get(f"/directory/photo/{person.id}")).status_code == 404
    assert (await user.http_client.get("/directory/photo/not-a-uuid")).status_code == 404
