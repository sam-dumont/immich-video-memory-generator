"""NiceGUI UI for Immich Memories."""

from immich_memories.ui.state import AppState, get_app_state, reset_app_state

# WHY: app.py is deliberately not imported here. Loading it used to call
# configure_logging() at import time, re-adding a StreamHandler that broke
# LiveDisplay's log routing during CLI generation. Callers that need it import
# immich_memories.ui.app directly.
__all__ = ["AppState", "get_app_state", "reset_app_state"]
