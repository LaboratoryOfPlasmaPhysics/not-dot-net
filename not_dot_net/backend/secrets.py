"""Secrets file management — read, write, generate.

Library-style: errors are raised, not exited. The CLI surface (`cli.py`,
`app.py`) is responsible for translating these into user-facing exits.
"""

import json
import logging
import os
import secrets
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger("not_dot_net.secrets")


class AppSecrets(BaseModel):
    jwt_secret: str
    storage_secret: str
    file_encryption_key: str = ""


def generate_secrets_file(path: Path) -> AppSecrets:
    app_secrets = AppSecrets(
        jwt_secret=secrets.token_urlsafe(32),
        storage_secret=secrets.token_urlsafe(32),
        file_encryption_key=secrets.token_urlsafe(32),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app_secrets.model_dump(), indent=2))
    os.chmod(path, 0o600)
    logger.info("Generated secrets file: %s", path)
    return app_secrets


def read_secrets_file(path: Path) -> AppSecrets:
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found: {path}")
    data = json.loads(path.read_text())
    return AppSecrets.model_validate(data)


def load_or_create(path: Path, dev_mode: bool) -> AppSecrets:
    if path.exists():
        app_secrets = read_secrets_file(path)
        if not app_secrets.file_encryption_key:
            if dev_mode:
                app_secrets.file_encryption_key = secrets.token_urlsafe(32)
                path.write_text(json.dumps(app_secrets.model_dump(), indent=2))
                os.chmod(path, 0o600)
                logger.info("Generated missing file_encryption_key in %s", path)
            else:
                raise RuntimeError(
                    f"file_encryption_key missing in secrets file: {path}"
                )
        return app_secrets
    if dev_mode:
        logger.info("Dev mode: generating secrets file %s", path)
        return generate_secrets_file(path)
    raise FileNotFoundError(f"Secrets file not found in production mode: {path}")
