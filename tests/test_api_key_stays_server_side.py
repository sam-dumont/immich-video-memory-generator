"""The stored Immich API key must not reach the browser.

`ui.input(password=True, password_toggle_button=True)` masks the value on
screen; it does not withhold it. A two-way binding sends the real key to every
client that loads Step 1, and the eye icon reveals it in one click. Auth is off
by default and the UI binds 0.0.0.0, so on a default deployment that is anyone
on the LAN.

Every other reader of `state.immich_api_key` runs server-side -- NiceGUI event
handlers execute on the server -- so the binding is the whole exposure.
"""

from __future__ import annotations

from pathlib import Path

from immich_memories.ui.state import AppState, apply_api_key_entry

_STEP1 = Path(__file__).resolve().parent.parent / "src/immich_memories/ui/pages/step1_config.py"


class TestNoWidgetBindsTheStoredKey:
    def test_step1_never_binds_the_stored_key(self):
        """The one line that would send it to every browser."""
        source = _STEP1.read_text()

        assert 'bind_value(state, "immich_api_key")' not in source

    def test_step1_binds_the_entry_field_instead(self):
        source = _STEP1.read_text()

        assert 'bind_value(state, "api_key_entry")' in source


class TestApplyApiKeyEntry:
    def test_a_typed_key_replaces_the_stored_one(self):
        state = AppState()
        state.immich_api_key = "old-key"
        state.api_key_entry = "new-key"

        apply_api_key_entry(state)

        assert state.immich_api_key == "new-key"

    def test_an_untouched_field_keeps_the_stored_key(self):
        """The form loads empty, so empty means "unchanged", not "clear it"."""
        state = AppState()
        state.immich_api_key = "stored-key"
        state.api_key_entry = ""

        apply_api_key_entry(state)

        assert state.immich_api_key == "stored-key"

    def test_the_entry_is_cleared_so_it_is_not_re_sent(self):
        state = AppState()
        state.api_key_entry = "new-key"

        apply_api_key_entry(state)

        assert state.api_key_entry == ""

    def test_surrounding_whitespace_is_dropped(self):
        state = AppState()
        state.api_key_entry = "  pasted-key\n"

        apply_api_key_entry(state)

        assert state.immich_api_key == "pasted-key"
