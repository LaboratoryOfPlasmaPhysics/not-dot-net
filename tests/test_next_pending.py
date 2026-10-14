"""D6: after acting on a request, go where the next action is."""

from not_dot_net.backend.permissions import SYSTEM_ACTOR
from not_dot_net.backend.workflow_service import create_request, submit_step
from not_dot_net.frontend.workflow_detail import next_destination
from tests.test_workflow_service import _backdate, _create_user, _setup_roles


async def _vpn_waiting_for_approval(requester, name: str):
    req = await create_request(
        workflow_type="vpn_access", created_by=requester.id,
        data={"target_name": name, "target_email": f"{name.lower()}@test.com"}, actor=SYSTEM_ACTOR,
    )
    await submit_step(req.id, requester.id, "submit", data={}, actor_user=requester)
    return req


async def test_goes_to_the_next_pending_request_after_approving():
    await _setup_roles()
    staff = await _create_user(email="staff-next@test.com", role="staff")
    director = await _create_user(email="director-next@test.com", role="director")
    first = await _vpn_waiting_for_approval(staff, "First")
    second = await _vpn_waiting_for_approval(staff, "Second")

    await submit_step(first.id, director.id, "approve", actor_user=director)
    assert await next_destination(director, first.id) == f"/workflow/request/{second.id}"

    await submit_step(second.id, director.id, "approve", actor_user=director)
    assert await next_destination(director, second.id) == "/"


async def test_stays_when_the_request_still_waits_on_the_same_person():
    await _setup_roles()
    staff = await _create_user(email="staff-stay@test.com", role="staff")
    director = await _create_user(email="director-stay@test.com", role="director")
    req = await _vpn_waiting_for_approval(staff, "Stay")
    assert await next_destination(director, req.id) == f"/workflow/request/{req.id}"


async def test_oldest_pending_request_comes_next():
    await _setup_roles()
    staff = await _create_user(email="staff-old@test.com", role="staff")
    director = await _create_user(email="director-old@test.com", role="director")
    current = await _vpn_waiting_for_approval(staff, "Current")
    oldest = await _vpn_waiting_for_approval(staff, "Oldest")
    await _vpn_waiting_for_approval(staff, "Newest")
    await _backdate(oldest.id, 10)

    await submit_step(current.id, director.id, "approve", actor_user=director)
    assert await next_destination(director, current.id) == f"/workflow/request/{oldest.id}"
