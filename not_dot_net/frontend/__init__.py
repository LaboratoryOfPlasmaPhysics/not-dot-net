from nicegui import app, ui


def safe_timer(interval, callback, *, once=False):
    """Create a ui.timer that auto-deactivates on client disconnect."""
    timer = ui.timer(interval, callback, once=once)
    app.on_disconnect(timer.deactivate)
    return timer
