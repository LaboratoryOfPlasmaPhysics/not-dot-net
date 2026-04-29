"""C7: plain-file workflow downloads must validate that the storage path
sits under the upload root. A corrupted WorkflowFile.storage_path row must
not be able to make the server read arbitrary files."""

import pytest

from not_dot_net.backend.workflow_service import _safe_upload_path, UPLOAD_ROOT


def test_safe_upload_path_accepts_subpath(tmp_path):
    inside = tmp_path / "abc-id" / "file.pdf"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(b"data")
    assert _safe_upload_path(str(inside), root=tmp_path) == inside.resolve()


def test_safe_upload_path_rejects_traversal(tmp_path):
    outside = (tmp_path / ".." / "evil.txt").resolve()
    with pytest.raises(ValueError, match=r"(?i)outside upload root"):
        _safe_upload_path(str(outside), root=tmp_path)


def test_safe_upload_path_rejects_absolute_secret(tmp_path):
    with pytest.raises(ValueError, match=r"(?i)outside upload root"):
        _safe_upload_path("/etc/passwd", root=tmp_path)
