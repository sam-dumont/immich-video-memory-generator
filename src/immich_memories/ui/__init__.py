"""NiceGUI UI for Immich Memories."""

from immich_memories.ui.state import AppState, get_app_state, reset_app_state

# WHY: Lazy import of app.main — eager import triggers app.py module loading
# which used to call configure_logging() at import time, re-adding a StreamHandler
# that broke LiveDisplay's log routing during CLI generation.
__all__ = ["main", "AppState", "get_app_state", "reset_app_state"]


def __getattr__(name: str):
    if name == "main":
        from immich_memories.ui.app import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
