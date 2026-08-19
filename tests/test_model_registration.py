"""D12 — the model-registration import list was duplicated in four places.

db.py (dev create_all), migrate.py (fresh-deploy bootstrap), alembic/env.py
(autogenerate) and conftest.py (test schema) each kept their own copy. They had
already drifted: conftest was missing page_models, so the `page` table existed
only when some earlier test happened to import it — order-dependent and silent
for months. The floor-plan work hit the same class of bug from the other side,
with two of the four sites missed.
"""
import importlib

import pytest


def test_every_mapped_table_comes_from_the_shared_list():
    """Importing the shared list must register every table the app has."""
    from not_dot_net.backend.db import Base
    from not_dot_net.backend.models import register_all_models

    register_all_models()
    tables = set(Base.metadata.tables)

    # A few load-bearing ones across different modules, so a dropped import
    # in the shared list fails loudly here.
    for expected in (
        "user", "workflow_request", "workflow_event", "workflow_file",
        "workflow_failed_effect", "booking", "resource", "page", "audit_event",
        "app_setting", "encrypted_file", "user_tenure", "mail_outbox",
        "uid_allocation", "floor_plan", "map_point", "office_availability",
    ):
        assert expected in tables, f"{expected} missing — model module not registered"


def test_registration_is_idempotent():
    """It runs from four entry points; calling it twice must not blow up."""
    from not_dot_net.backend.models import register_all_models

    register_all_models()
    register_all_models()


def test_all_model_modules_are_importable():
    from not_dot_net.backend.models import MODEL_MODULES

    assert MODEL_MODULES, "shared model list is empty"
    for name in MODEL_MODULES:
        importlib.import_module(name)


def test_no_site_keeps_its_own_import_list():
    """The four entry points must defer to the shared list, not re-list modules."""
    from pathlib import Path

    import not_dot_net.backend.db as db_module

    root = Path(db_module.__file__).parent.parent.parent
    sites = [
        root / "not_dot_net" / "backend" / "db.py",
        root / "not_dot_net" / "backend" / "migrate.py",
        root / "alembic" / "env.py",
        root / "tests" / "conftest.py",
    ]
    for path in sites:
        source = path.read_text()
        stray = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith("import not_dot_net.backend.")
            and "models" not in line
            and "noqa: F401" in line
        ]
        assert not stray, f"{path.name} still keeps its own model list: {stray}"
