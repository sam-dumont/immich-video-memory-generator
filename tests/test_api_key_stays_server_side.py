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

from immich_memories.ui.state import AppState, apply_connection_entry

_STEP1 = Path(__file__).resolve().parent.parent / "src/immich_memories/ui/pages/step1_config.py"


class TestNoWidgetBindsTheStoredKey:
    def test_step1_never_binds_the_stored_key(self):
        """The one line that would send it to every browser."""
        source = _STEP1.read_text()

        assert 'bind_value(state, "immich_api_key")' not in source

    def test_step1_binds_the_entry_field_instead(self):
        source = _STEP1.read_text()

        assert 'bind_value(state, "api_key_entry")' in source


class TestApplyConnectionEntry:
    def test_a_typed_key_replaces_the_stored_one(self):
        state = AppState()
        state.immich_api_key = "old-key"
        state.api_key_entry = "new-key"

        apply_connection_entry(state)

        assert state.immich_api_key == "new-key"

    def test_an_untouched_field_keeps_the_stored_key(self):
        """The form loads empty, so empty means "unchanged", not "clear it"."""
        state = AppState()
        state.immich_api_key = "stored-key"
        state.api_key_entry = ""

        apply_connection_entry(state)

        assert state.immich_api_key == "stored-key"

    def test_the_entry_is_cleared_so_it_is_not_re_sent(self):
        state = AppState()
        state.api_key_entry = "new-key"

        apply_connection_entry(state)

        assert state.api_key_entry == ""

    def test_surrounding_whitespace_is_dropped(self):
        state = AppState()
        state.api_key_entry = "  pasted-key\n"

        apply_connection_entry(state)

        assert state.immich_api_key == "pasted-key"


class TestTheStoredKeyStaysWithItsUrl:
    """Issue #1212: with auth off, anyone who reaches the UI could type their own
    server URL, press Test Connection, and receive the stored key. The stored key
    belongs to the URL it was stored with; a new URL needs a key typed for it."""

    def _connected(self) -> AppState:
        state = AppState()
        state.immich_url = "https://photos.example.com"
        state.immich_api_key = "stored-key"
        state.immich_url_entry = "https://photos.example.com"
        return state

    def test_a_new_url_without_a_new_key_is_refused(self):
        state = self._connected()
        state.immich_url_entry = "https://attacker.example.net"

        refusal = apply_connection_entry(state)

        assert refusal
        assert state.immich_url == "https://photos.example.com"
        assert state.immich_api_key == "stored-key"

    def test_a_new_url_with_a_new_key_is_applied_together(self):
        state = self._connected()
        state.immich_url_entry = "https://other.example.net"
        state.api_key_entry = "other-key"

        refusal = apply_connection_entry(state)

        assert refusal is None
        assert (state.immich_url, state.immich_api_key) == (
            "https://other.example.net",
            "other-key",
        )

    def test_the_same_url_keeps_the_stored_key(self):
        """A trailing slash or stray space is the same server, not a new one."""
        state = self._connected()
        state.immich_url_entry = " https://photos.example.com/ "

        refusal = apply_connection_entry(state)

        assert refusal is None
        assert state.immich_api_key == "stored-key"

    def test_a_first_url_with_no_stored_key_is_simply_taken(self):
        state = AppState()
        state.immich_url_entry = "https://photos.example.com"

        assert apply_connection_entry(state) is None
        assert state.immich_url == "https://photos.example.com"

    def test_step1_binds_the_url_entry_not_the_live_url(self):
        """Binding the live URL would let a keystroke re-point the stored key."""
        source = _STEP1.read_text()

        assert 'bind_value(state, "immich_url")' not in source
        assert 'bind_value(state, "immich_url_entry")' in source
