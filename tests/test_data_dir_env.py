"""Storage roots must honor NDN_DATA_DIR so the prod /data mount is actually used.

In the container WORKDIR is /data and the PVC mounts there; with hardcoded
relative paths uploads landed at /data/data/... (nested, and unwritable).
NDN_DATA_DIR lets the deployment point storage straight at the mount.
"""
import subprocess
import sys
from pathlib import Path


def _resolved_roots(env_value: str | None) -> tuple[str, str]:
    code = (
        "from not_dot_net.backend.encrypted_storage import ENCRYPTED_DIR\n"
        "from not_dot_net.backend.workflow_uploads import UPLOAD_ROOT\n"
        "print(ENCRYPTED_DIR); print(UPLOAD_ROOT)"
    )
    env = {"PATH": __import__("os").environ.get("PATH", "")}
    if env_value is not None:
        env["NDN_DATA_DIR"] = env_value
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, check=True, env=env,
    ).stdout.split()
    return out[0], out[1]


def test_default_data_dir_is_relative_data():
    enc, up = _resolved_roots(None)
    assert enc == str(Path("data/encrypted"))
    assert up == str(Path("data/uploads"))


def test_ndn_data_dir_points_storage_at_mount():
    enc, up = _resolved_roots("/data")
    assert enc == "/data/encrypted"
    assert up == "/data/uploads"
