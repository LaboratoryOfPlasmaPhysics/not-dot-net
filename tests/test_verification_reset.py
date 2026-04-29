"""A3: when submit_step regenerates a target_person token, the old verification
code must be invalidated.

Without this, a request_corrections cycle within the 15-min code expiry would
let someone enter the *previous* code on the *new* token URL — bypassing the
"you have access to this mailbox" check, since the previous code may have been
seen by anyone who could read the previous email."""

from not_dot_net.backend.verification import (
    generate_verification_code,
    has_valid_code,
    verify_code,
)
from not_dot_net.backend.workflow_service import (
    create_request,
    submit_step,
)
from tests.test_workflow_service import _create_user, _setup_roles


async def _onboarding_at_admin_validation():
    await _setup_roles()
    creator = await _create_user(role="admin")
    # admin_validation requires access_personal_data; bypass via superuser.
    from not_dot_net.backend.db import session_scope as _ss, User as _U
    async with _ss() as session:
        u = await session.get(_U, creator.id)
        u.is_superuser = True
        await session.commit()
    creator.is_superuser = True
    req = await create_request(
        workflow_type="onboarding",
        created_by=creator.id,
        data={"contact_email": "bob@test.com", "status": "Intern", "employer": "CNRS"},
    )
    # initiation -> newcomer_info (mints token #1)
    req = await submit_step(req.id, creator.id, "submit", data={}, actor_user=creator)
    # newcomer_info -> admin_validation
    plaintext_token_1 = req.token
    await submit_step(
        req.id, actor_id=None, action="submit",
        data={"first_name": "Bob", "last_name": "Smith"},
        actor_token=plaintext_token_1,
    )
    return creator, req.id, plaintext_token_1


async def test_request_corrections_invalidates_previous_verification_code():
    creator, req_id, _old_token = await _onboarding_at_admin_validation()

    # Target person enters and gets a code on token #1
    code = await generate_verification_code(req_id)
    assert code is not None
    assert await has_valid_code(req_id)

    # Admin requests corrections — this generates token #2 for the same step.
    req2 = await submit_step(
        req_id, creator.id, "request_corrections",
        comment="please fix",
        actor_user=creator,
    )
    assert req2.current_step == "newcomer_info"
    assert req2.token  # plaintext returned to caller

    # The OLD code must no longer satisfy the verification check.
    assert not await has_valid_code(req_id), (
        "old verification code is still valid on a freshly-minted token — leak"
    )
    assert not await verify_code(req_id, code), (
        "old code accepted on new token — defeats the email-receipt check"
    )
