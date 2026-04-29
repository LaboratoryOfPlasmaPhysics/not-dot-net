"""C3: secrets.py must not call sys.exit() — that breaks tests and any caller
that wants to handle the error. It should raise instead."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from not_dot_net.backend.secrets import load_or_create, read_secrets_file


def test_read_missing_file_raises_not_exits():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "does-not-exist.key"
        with pytest.raises(FileNotFoundError):
            read_secrets_file(missing)


def test_load_or_create_in_production_raises_when_missing():
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "does-not-exist.key"
        with pytest.raises(FileNotFoundError):
            load_or_create(missing, dev_mode=False)


def test_load_or_create_prod_missing_file_encryption_key_raises():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "secrets.key"
        # Pre-existing file but missing file_encryption_key — production path.
        path.write_text(json.dumps({
            "jwt_secret": "j", "storage_secret": "s", "file_encryption_key": "",
        }))
        os.chmod(path, 0o600)
        with pytest.raises(RuntimeError, match=r"(?i)file_encryption_key"):
            load_or_create(path, dev_mode=False)
