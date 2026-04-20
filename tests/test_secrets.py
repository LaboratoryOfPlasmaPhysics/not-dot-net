import json
import stat
from pathlib import Path

import pytest


@pytest.fixture
def tmp_secrets(tmp_path):
    return tmp_path / "secrets.key"


def test_generate_creates_file_with_correct_permissions(tmp_secrets):
    from not_dot_net.backend.secrets import generate_secrets_file
    generate_secrets_file(tmp_secrets)
    assert tmp_secrets.exists()
    mode = stat.S_IMODE(tmp_secrets.stat().st_mode)
    assert mode == 0o600


def test_generate_creates_valid_json_with_both_keys(tmp_secrets):
    from not_dot_net.backend.secrets import generate_secrets_file
    generate_secrets_file(tmp_secrets)
    data = json.loads(tmp_secrets.read_text())
    assert "jwt_secret" in data
    assert "storage_secret" in data
    assert len(data["jwt_secret"]) >= 32
    assert len(data["storage_secret"]) >= 32


def test_read_returns_secrets(tmp_secrets):
    from not_dot_net.backend.secrets import generate_secrets_file, read_secrets_file
    generate_secrets_file(tmp_secrets)
    secrets = read_secrets_file(tmp_secrets)
    assert secrets.jwt_secret
    assert secrets.storage_secret


def test_read_missing_file_raises(tmp_secrets):
    from not_dot_net.backend.secrets import read_secrets_file
    with pytest.raises(SystemExit):
        read_secrets_file(tmp_secrets)


def test_load_or_create_dev_mode_generates_on_first_run(tmp_secrets):
    from not_dot_net.backend.secrets import load_or_create
    secrets = load_or_create(tmp_secrets, dev_mode=True)
    assert secrets.jwt_secret
    assert tmp_secrets.exists()
    mode = stat.S_IMODE(tmp_secrets.stat().st_mode)
    assert mode == 0o600


def test_load_or_create_reads_on_subsequent_run(tmp_secrets):
    from not_dot_net.backend.secrets import load_or_create
    first = load_or_create(tmp_secrets, dev_mode=True)
    second = load_or_create(tmp_secrets, dev_mode=False)
    assert first == second


def test_load_or_create_prod_mode_refuses_if_missing(tmp_secrets):
    from not_dot_net.backend.secrets import load_or_create
    with pytest.raises(SystemExit):
        load_or_create(tmp_secrets, dev_mode=False)


def test_load_or_create_dev_mode_regenerates_if_missing(tmp_secrets):
    from not_dot_net.backend.secrets import load_or_create
    secrets = load_or_create(tmp_secrets, dev_mode=True)
    assert secrets.jwt_secret
    tmp_secrets.unlink()
    secrets2 = load_or_create(tmp_secrets, dev_mode=True)
    assert secrets2.jwt_secret
    assert secrets2.jwt_secret != secrets.jwt_secret


def test_load_or_create_prod_mode_refuses_if_deleted(tmp_secrets):
    from not_dot_net.backend.secrets import load_or_create
    load_or_create(tmp_secrets, dev_mode=True)
    tmp_secrets.unlink()
    with pytest.raises(SystemExit):
        load_or_create(tmp_secrets, dev_mode=False)


def test_generate_logs_path_but_not_secret_values(tmp_secrets, caplog):
    from not_dot_net.backend.secrets import generate_secrets_file

    with caplog.at_level("INFO", logger="not_dot_net.secrets"):
        secrets = generate_secrets_file(tmp_secrets)

    assert str(tmp_secrets) in caplog.text
    assert secrets.jwt_secret not in caplog.text
    assert secrets.storage_secret not in caplog.text
