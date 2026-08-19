"""The one list of modules that define ORM models.

Four places need every model registered against `Base.metadata` before they can
do their job: `db.create_db_and_tables` (dev), `migrate.bootstrap_schema`
(fresh deploy), `alembic/env.py` (autogenerate) and the test schema fixture.

They each used to keep their own copy of the import list, and they drifted:
conftest was missing page_models, so the `page` table existed only when an
earlier test happened to import it. Adding a model module means adding one line
here, and all four follow.
"""

import importlib

MODEL_MODULES = (
    "not_dot_net.backend.db",
    "not_dot_net.backend.workflow_models",
    "not_dot_net.backend.booking_models",
    "not_dot_net.backend.floorplan_models",
    "not_dot_net.backend.office_availability",
    "not_dot_net.backend.audit",
    "not_dot_net.backend.app_config",
    "not_dot_net.backend.page_models",
    "not_dot_net.backend.encrypted_storage",
    "not_dot_net.backend.tenure_service",
    "not_dot_net.backend.mail_outbox",
    "not_dot_net.backend.uid_allocator",
    "not_dot_net.backend.effect_retry",
)


def register_all_models() -> None:
    """Import every model module so its tables land on Base.metadata.

    Idempotent — importlib returns the cached module on later calls.
    """
    for name in MODEL_MODULES:
        importlib.import_module(name)
