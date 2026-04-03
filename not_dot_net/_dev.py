"""Dev entry point with auto-reload.

Usage: uv run python not_dot_net/_dev.py [--seed-fake-users]
"""
import sys
from pathlib import Path

from not_dot_net.app import create_app
from not_dot_net.backend.secrets import read_secrets_file
from nicegui import ui

create_app(
    secrets_file="./secrets.key",
    _seed_fake_users="--seed-fake-users" in sys.argv,
)
secrets = read_secrets_file(Path("./secrets.key"))
ui.run(
    storage_secret=secrets.storage_secret,
    host="localhost",
    port=8088,
    reload=True,
    title="NotDotNet",
)
