"""Dev entry point with auto-reload.

Usage: uv run python not_dot_net/_dev.py [--seed-fake-users]
"""
import sys

from not_dot_net.app import create_app

from nicegui import ui

from not_dot_net.config import get_settings

create_app(_seed_fake_users="--seed-fake-users" in sys.argv)
settings = get_settings()
ui.run(
    storage_secret=settings.storage_secret,
    host="localhost",
    port=8088,
    reload=True,
    title="NotDotNet",
)
