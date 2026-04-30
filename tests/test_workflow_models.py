import uuid

from not_dot_net.backend.workflow_models import WorkflowRequest, WorkflowEvent, WorkflowFile


WORKFLOW_REQUEST_FIELDS = [
    "id", "type", "current_step", "status", "data", "created_by",
    "target_email", "token", "token_expires_at", "created_at", "updated_at",
    "verification_code_hash", "code_expires_at", "code_attempts",
]

WORKFLOW_EVENT_FIELDS = [
    "id", "request_id", "step_key", "action", "actor_id",
    "data_snapshot", "comment", "created_at",
]

WORKFLOW_FILE_FIELDS = [
    "id", "request_id", "step_key", "field_name", "filename",
    "storage_path", "uploaded_by", "uploaded_at", "encrypted_file_id",
]


def test_workflow_request_has_all_fields():
    for field in WORKFLOW_REQUEST_FIELDS:
        assert hasattr(WorkflowRequest, field), f"Missing field: {field}"


def test_workflow_event_has_all_fields():
    for field in WORKFLOW_EVENT_FIELDS:
        assert hasattr(WorkflowEvent, field), f"Missing field: {field}"


def test_workflow_file_has_all_fields():
    for field in WORKFLOW_FILE_FIELDS:
        assert hasattr(WorkflowFile, field), f"Missing field: {field}"


def test_workflow_request_defaults():
    req = WorkflowRequest(type="test", current_step="step1")
    assert req.status == "in_progress"
    assert req.data == {}
    assert req.token is None
    assert req.token_expires_at is None
    assert req.verification_code_hash is None
    assert req.code_expires_at is None
    assert req.code_attempts == 0


def test_workflow_file_encrypted_file_link_defaults_to_none():
    wf_file = WorkflowFile(
        request_id=uuid.uuid4(),
        step_key="newcomer_info",
        field_name="id_document",
        filename="id.pdf",
        storage_path="encrypted",
    )
    assert wf_file.encrypted_file_id is None
