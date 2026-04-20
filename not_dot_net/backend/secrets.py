"""Secrets file management — read, write, generate."""

import json
import logging
import os
import secrets
import sys
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger("not_dot_net.secrets")


class AppSecrets(BaseModel):
    jwt_secret: str
    storage_secret: str


def generate_secrets_file(path: Path) -> AppSecrets:
    app_secrets = AppSecrets(
        jwt_secret=secrets.token_urlsafe(32),
        storage_secret=secrets.token_urlsafe(32),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app_secrets.model_dump(), indent=2))
    os.chmod(path, 0o600)
    logger.info("Generated secrets file: %s", path)
    return app_secrets


def read_secrets_file(path: Path) -> AppSecrets:
    if not path.exists():
        logger.error("Secrets file not found: %s", path)
        sys.exit(1)
    data = json.loads(path.read_text())
    return AppSecrets.model_validate(data)


def load_or_create(path: Path, dev_mode: bool) -> AppSecrets:
    if path.exists():
        return read_secrets_file(path)
    if dev_mode:
        logger.info("Dev mode: generating secrets file %s", path)
        return generate_secrets_file(path)
    logger.error("Secrets file not found in production mode: %s", path)
    sys.exit(1)
