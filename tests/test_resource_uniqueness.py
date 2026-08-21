"""B5: Resource.name must be unique.

Import/export matches resources by name; audit logs target by name. Duplicates
silently corrupt those flows.
"""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
import pytest

from not_dot_net.backend.booking_service import create_resource


async def test_duplicate_resource_name_rejected():
    await create_resource(name="GPU-Box-01", resource_type="desktop", actor=SYSTEM_ACTOR)
    with pytest.raises(ValueError, match=r"(?i)already exists|name"):
        await create_resource(name="GPU-Box-01", resource_type="desktop", actor=SYSTEM_ACTOR)
