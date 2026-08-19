"""Tests for import/export of pages and resources."""

import uuid

from sqlalchemy import select

from not_dot_net.backend.data_io import (
    export_all, export_pages, export_resources,
    import_all, import_pages, import_resources,
)
from not_dot_net.backend.page_models import Page
from not_dot_net.backend.booking_models import Resource
from not_dot_net.backend.db import session_scope, User


async def _create_user(email: str) -> User:
    async with session_scope() as session:
        user = User(
            id=uuid.uuid4(),
            email=email,
            hashed_password="x",
            is_active=True,
            is_verified=True,
            is_superuser=False,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_page(title="Test", slug="test", content="# Hello", published=True):
    async with session_scope() as session:
        p = Page(title=title, slug=slug, content=content, published=published)
        session.add(p)
        await session.commit()


async def _seed_resource(name="PC-01", resource_type="desktop"):
    async with session_scope() as session:
        r = Resource(name=name, resource_type=resource_type)
        session.add(r)
        await session.commit()


async def test_export_pages():
    await _seed_page()
    pages = await export_pages()
    assert len(pages) == 1
    assert pages[0]["slug"] == "test"
    assert "id" not in pages[0]


async def test_export_resources():
    await _seed_resource()
    resources = await export_resources()
    assert len(resources) == 1
    assert resources[0]["name"] == "PC-01"
    assert "id" not in resources[0]


async def test_export_all_structure():
    await _seed_page()
    await _seed_resource()
    data = await export_all()
    assert data["version"] == 1
    assert "exported_at" in data
    assert len(data["pages"]) == 1
    assert len(data["resources"]) == 1


async def test_import_pages_creates():
    result = await import_pages([
        {"title": "New", "slug": "new", "content": "body", "published": True},
    ])
    assert result == {"created": 1, "updated": 0, "skipped": 0}
    pages = await export_pages()
    assert len(pages) == 1
    assert pages[0]["slug"] == "new"


async def test_import_pages_skips_existing():
    await _seed_page(slug="existing")
    result = await import_pages([
        {"title": "Changed", "slug": "existing", "content": "new body"},
    ])
    assert result == {"created": 0, "updated": 0, "skipped": 1}


async def test_import_pages_replaces_existing():
    await _seed_page(title="Old", slug="replace-me", content="old")
    result = await import_pages([
        {"title": "New Title", "slug": "replace-me", "content": "new"},
    ], replace=True)
    assert result == {"created": 0, "updated": 1, "skipped": 0}
    pages = await export_pages()
    assert pages[0]["title"] == "New Title"
    assert pages[0]["content"] == "new"


async def test_import_pages_skips_empty_slug():
    result = await import_pages([{"title": "Bad", "slug": "", "content": "x"}])
    assert result["skipped"] == 1


async def test_import_pages_skips_invalid_records():
    result = await import_pages([
        {"slug": "missing-title", "content": "x"},
        {"title": "Missing slug", "content": "x"},
        {"title": "Bad slug type", "slug": 123},
        "not-a-record",
    ])

    assert result == {"created": 0, "updated": 0, "skipped": 4}
    assert await export_pages() == []


async def test_import_pages_ignores_immutable_fields_on_replace():
    await _seed_page(title="Old", slug="safe-page", content="old")
    async with session_scope() as session:
        original = (await session.execute(select(Page))).scalar_one()
        original_id = original.id
        original_created_at = original.created_at

    result = await import_pages([
        {
            "id": uuid.uuid4(),
            "created_at": "1999-01-01T00:00:00",
            "title": "New",
            "slug": "safe-page",
            "content": "new",
        },
    ], replace=True)

    assert result == {"created": 0, "updated": 1, "skipped": 0}
    async with session_scope() as session:
        updated = (await session.execute(select(Page))).scalar_one()
        assert updated.id == original_id
        assert updated.created_at == original_created_at
        assert updated.title == "New"


async def test_import_resources_creates():
    result = await import_resources([
        {"name": "Laptop-01", "resource_type": "laptop", "location": "Room 1"},
    ])
    assert result == {"created": 1, "updated": 0, "skipped": 0}


async def test_import_resources_skips_existing():
    await _seed_resource(name="PC-01")
    result = await import_resources([{"name": "PC-01", "resource_type": "laptop"}])
    assert result == {"created": 0, "updated": 0, "skipped": 1}


async def test_import_resources_replaces_existing():
    await _seed_resource(name="PC-02", resource_type="desktop")
    result = await import_resources([
        {"name": "PC-02", "resource_type": "laptop", "location": "New Room"},
    ], replace=True)
    assert result == {"created": 0, "updated": 1, "skipped": 0}
    resources = await export_resources()
    r = next(r for r in resources if r["name"] == "PC-02")
    assert r["resource_type"] == "laptop"
    assert r["location"] == "New Room"


async def test_import_resources_skips_invalid_records():
    result = await import_resources([
        {"resource_type": "desktop"},
        {"name": 123, "resource_type": "desktop"},
        "not-a-record",
    ])

    assert result == {"created": 0, "updated": 0, "skipped": 3}
    assert await export_resources() == []


async def test_import_resources_ignores_immutable_fields_on_replace():
    await _seed_resource(name="PC-03")
    async with session_scope() as session:
        original = (await session.execute(select(Resource))).scalar_one()
        original_id = original.id
        original_created_at = original.created_at

    result = await import_resources([
        {
            "id": uuid.uuid4(),
            "created_at": "1999-01-01T00:00:00",
            "name": "PC-03",
            "resource_type": "laptop",
        },
    ], replace=True)

    assert result == {"created": 0, "updated": 1, "skipped": 0}
    async with session_scope() as session:
        updated = (await session.execute(select(Resource))).scalar_one()
        assert updated.id == original_id
        assert updated.created_at == original_created_at
        assert updated.resource_type == "laptop"


async def test_import_all_roundtrip():
    await _seed_page(title="P1", slug="p1")
    await _seed_resource(name="R1")
    exported = await export_all()
    # Clear DB
    async with session_scope() as session:
        from sqlalchemy import delete
        await session.execute(delete(Page))
        await session.execute(delete(Resource))
        await session.commit()
    result = await import_all(exported)
    assert result["pages"]["created"] == 1
    assert result["resources"]["created"] == 1


async def test_export_includes_tenures():
    from not_dot_net.backend.data_io import export_all
    from not_dot_net.backend.tenure_service import add_tenure
    from datetime import date

    user = await _create_user("tenure-export@test.com")
    await add_tenure(
        user_id=user.id, status="PhD", employer="CNRS",
        start_date=date(2025, 9, 1),
    )
    data = await export_all()
    assert "tenures" in data
    assert len(data["tenures"]) == 1
    assert data["tenures"][0]["status"] == "PhD"
    assert data["tenures"][0]["employer"] == "CNRS"


async def test_import_tenures():
    from not_dot_net.backend.data_io import import_all
    from not_dot_net.backend.tenure_service import list_tenures

    user = await _create_user("import-tenure@test.com")
    data = {
        "tenures": [
            {
                "user_email": "import-tenure@test.com",
                "status": "Intern",
                "employer": "Polytechnique",
                "start_date": "2025-03-01",
                "end_date": "2025-08-31",
            }
        ],
    }
    result = await import_all(data)
    assert result["tenures"]["created"] == 1
    assert result["tenures"]["updated"] == 0
    assert result["tenures"]["skipped"] == 0
    tenures = await list_tenures(user.id)
    assert len(tenures) == 1
    assert tenures[0].employer == "Polytechnique"


async def test_import_tenures_skips_invalid_records():
    from not_dot_net.backend.tenure_service import list_tenures

    user = await _create_user("invalid-tenure@test.com")
    result = await import_all({
        "tenures": [
            {"user_email": "missing-required@test.com"},
            {
                "user_email": "unknown@test.com",
                "status": "Intern",
                "employer": "CNRS",
                "start_date": "2025-03-01",
            },
            {
                "user_email": user.email,
                "status": "Intern",
                "employer": "CNRS",
                "start_date": "not-a-date",
            },
            "not-a-record",
        ],
    })

    assert result["tenures"] == {"created": 0, "updated": 0, "skipped": 4}
    assert await list_tenures(user.id) == []


async def test_import_tenures_skips_overlapping_periods():
    from not_dot_net.backend.tenure_service import list_tenures

    user = await _create_user("overlap-import@test.com")
    result = await import_all({
        "tenures": [
            {
                "user_email": user.email,
                "status": "Intern",
                "employer": "CNRS",
                "start_date": "2025-03-01",
                "end_date": "2025-08-31",
            },
            {
                "user_email": user.email,
                "status": "PhD",
                "employer": "Polytechnique",
                "start_date": "2025-08-01",
            },
        ],
    })

    assert result["tenures"] == {"created": 1, "updated": 0, "skipped": 1}
    tenures = await list_tenures(user.id)
    assert len(tenures) == 1


async def test_import_all_empty_tenures_result_has_updated_key():
    result = await import_all({"tenures": []})

    assert result["tenures"] == {"created": 0, "updated": 0, "skipped": 0}


async def test_import_pages_coerces_malformed_scalar_fields():
    """A malformed sort_order/published value must be coerced to a safe default
    rather than stored raw — on PostgreSQL a string in an int column aborts the
    whole import (SQLite silently accepts it, hiding the bug)."""
    from not_dot_net.backend.data_io import import_pages
    from not_dot_net.backend.page_models import Page

    data = [
        {"slug": "p1", "title": "One", "sort_order": "not-a-number", "published": "yes"},
        {"slug": "p2", "title": "Two", "sort_order": "3", "published": True},
    ]
    result = await import_pages(data)

    assert result["created"] == 2
    async with session_scope() as session:
        rows = {
            p.slug: p for p in
            (await session.execute(select(Page))).scalars().all()
        }
    # Uncoercible sort_order falls back to 0; a numeric string coerces to int.
    assert rows["p1"].sort_order == 0
    assert isinstance(rows["p1"].published, bool)
    assert rows["p2"].sort_order == 3


async def test_import_pages_tolerates_null_string_fields():
    """An explicit JSON null in content (or a non-string title) must not abort
    the batch — on PostgreSQL the NOT NULL violation at the single commit rolls
    back every row of the import."""
    from not_dot_net.backend.data_io import import_pages

    data = [
        {"slug": "null-content", "title": "Has Null", "content": None},
        {"slug": "good", "title": "Good", "content": "body"},
    ]
    result = await import_pages(data)

    assert result["created"] == 2
    async with session_scope() as session:
        rows = {p.slug: p for p in (await session.execute(select(Page))).scalars().all()}
    assert rows["null-content"].content == ""
    assert rows["good"].content == "body"


async def test_import_pages_replace_tolerates_null_content():
    from not_dot_net.backend.data_io import import_pages

    await import_pages([{"slug": "p", "title": "P", "content": "original"}])
    result = await import_pages([{"slug": "p", "title": "P2", "content": None}], replace=True)

    assert result["updated"] == 1
    async with session_scope() as session:
        page = (await session.execute(select(Page))).scalars().one()
    assert page.title == "P2"
    assert page.content == "original"


async def test_import_resources_tolerates_non_string_type():
    from not_dot_net.backend.data_io import import_resources

    result = await import_resources([
        {"name": "R1", "resource_type": 123},
        {"name": "R2", "resource_type": "laptop"},
    ])

    assert result["created"] == 2
    async with session_scope() as session:
        rows = {r.name: r for r in (await session.execute(select(Resource))).scalars().all()}
    assert rows["R1"].resource_type == "desktop"
    assert rows["R2"].resource_type == "laptop"


async def test_import_tenures_matches_email_case_insensitively():
    """AD-provisioned users keep whatever case AD returned; a lowercase email
    in the import file must still attach the tenure."""
    from not_dot_net.backend.data_io import import_tenures
    from not_dot_net.backend.tenure_service import UserTenure

    async with session_scope() as session:
        user = User(id=uuid.uuid4(), email="John.Doe@Lab.FR", hashed_password="x", role="")
        session.add(user)
        await session.commit()
        user_id = user.id

    result = await import_tenures([{
        "user_email": "john.doe@lab.fr", "status": "CDD",
        "employer": "CNRS", "start_date": "2024-01-01",
    }])

    assert result["created"] == 1
    async with session_scope() as session:
        tenure = (await session.execute(select(UserTenure))).scalars().one()
    assert tenure.user_id == user_id


class TestResourceSpecsRoundTrip:
    """D4 — the importer ran the JSON `specs` dict through _as_str, which
    returns the default for anything that isn't a str. Every imported resource
    silently lost its hardware specs, with no error and no skip count."""

    async def test_specs_survive_export_then_import(self):
        from not_dot_net.backend.booking_models import Resource
        from not_dot_net.backend.data_io import export_resources, import_resources
        from not_dot_net.backend.db import session_scope

        specs = {"cpu": "Xeon 6338", "ram": "256GB", "gpu": "A100"}
        async with session_scope() as session:
            session.add(Resource(
                name="Compute Node 1", resource_type="server",
                location="LPP", specs=specs,
            ))
            await session.commit()

        exported = await export_resources()
        assert exported[0]["specs"] == specs

        async with session_scope() as session:
            resource = (await session.execute(
                select(Resource).where(Resource.name == "Compute Node 1")
            )).scalar_one()
            await session.delete(resource)
            await session.commit()

        result = await import_resources(exported)
        assert result["created"] == 1

        async with session_scope() as session:
            restored = (await session.execute(
                select(Resource).where(Resource.name == "Compute Node 1")
            )).scalar_one()
            assert restored.specs == specs

    async def test_replace_updates_specs(self):
        from not_dot_net.backend.booking_models import Resource
        from not_dot_net.backend.data_io import import_resources
        from not_dot_net.backend.db import session_scope

        async with session_scope() as session:
            session.add(Resource(
                name="Node 2", resource_type="server", specs={"cpu": "old"},
            ))
            await session.commit()

        await import_resources(
            [{"name": "Node 2", "resource_type": "server", "specs": {"cpu": "new"}}],
            replace=True,
        )
        async with session_scope() as session:
            resource = (await session.execute(
                select(Resource).where(Resource.name == "Node 2")
            )).scalar_one()
            assert resource.specs == {"cpu": "new"}

    async def test_garbage_specs_falls_back_to_the_default(self):
        """A non-dict must not reach a JSON column and abort the batch."""
        from not_dot_net.backend.booking_models import Resource
        from not_dot_net.backend.data_io import import_resources
        from not_dot_net.backend.db import session_scope

        await import_resources([
            {"name": "Node 3", "resource_type": "server", "specs": "not-a-dict"},
            {"name": "Node 4", "resource_type": "server", "specs": None},
        ])
        async with session_scope() as session:
            for name in ("Node 3", "Node 4"):
                resource = (await session.execute(
                    select(Resource).where(Resource.name == name)
                )).scalar_one()
                assert resource.specs is None
