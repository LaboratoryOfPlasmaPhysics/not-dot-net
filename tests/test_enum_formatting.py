"""Enums format as their value (review 2026-10-12 V-list).

The audit log showed "status=RequestStatus.REJECTED": a (str, Enum) mixin
formats as Class.MEMBER since Python 3.12, wherever it is interpolated.
"""

import pytest

from not_dot_net.backend.auth.ldap import TlsMode
from not_dot_net.backend.booking_models import ResourceStatus
from not_dot_net.backend.db import AuthMethod
from not_dot_net.backend.mail import SmtpTlsMode
from not_dot_net.backend.workflow_models import RequestStatus


@pytest.mark.parametrize("member", [
    RequestStatus.REJECTED, ResourceStatus.OUT_OF_SERVICE, AuthMethod.LDAP,
    TlsMode.LDAPS, next(iter(SmtpTlsMode)),
])
def test_enum_interpolates_as_its_value(member):
    assert f"{member}" == member.value
    assert str(member) == member.value
