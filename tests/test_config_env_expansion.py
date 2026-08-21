"""A secret containing `$` must survive being loaded.

`expand_env_vars` expanded bare `$NAME` as well as `${NAME}`, over `api_key`,
`password` and `client_secret`. A password like `pa$USERword` silently became
`pa<login>word`, so auth failed with no indication why -- the user sees "wrong
password" and has no path to the cause. Silent corruption of a credential is
worse than a parse error.

Only the delimited `${VAR}` form expands now. It is unambiguous, it is what
every documented example uses, and a `$` inside a secret cannot collide with it.
"""

from __future__ import annotations

from immich_memories.config_models import expand_env_vars


def test_a_password_containing_a_dollar_is_left_alone(monkeypatch):
    """`$USER` is set on every login shell, so this is not an exotic password.

    The corruption needs the name to end at a non-word character or at the end
    of the string; `pa$USERword` happens to survive because the greedy match
    swallows `word` and `USERword` is unset. These do not survive.
    """
    monkeypatch.setenv("USER", "sam")

    assert expand_env_vars("pa$USER-word") == "pa$USER-word"
    assert expand_env_vars("S3cret$USER!") == "S3cret$USER!"
    assert expand_env_vars("pa$USER") == "pa$USER"


def test_a_path_like_secret_is_left_alone(monkeypatch):
    """`$HOME/...` expanded to a real path inside a client secret."""
    monkeypatch.setenv("HOME", "/Users/sam")

    assert expand_env_vars("a$HOME/b") == "a$HOME/b"


def test_the_delimited_form_still_expands(monkeypatch):
    monkeypatch.setenv("IMMICH_API_KEY", "sk-secret")

    assert expand_env_vars("${IMMICH_API_KEY}") == "sk-secret"


def test_an_unset_variable_stays_literal(monkeypatch):
    """Leaving the reference intact is what lets Save write it back."""
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)

    assert expand_env_vars("${NOT_SET_ANYWHERE}") == "${NOT_SET_ANYWHERE}"


def test_a_bare_reference_is_reported_rather_than_silently_ignored(monkeypatch, caplog):
    """Dropping a documented form quietly would trade one silent surprise for
    another: someone relying on `$VAR` deserves to be told why it stopped."""
    monkeypatch.setenv("MY_TOKEN", "value")

    with caplog.at_level("WARNING"):
        result = expand_env_vars("$MY_TOKEN")

    assert result == "$MY_TOKEN"
    assert "MY_TOKEN" in caplog.text
    assert "${" in caplog.text


def test_a_dollar_that_matches_nothing_is_silent(monkeypatch, caplog):
    """A `$` in a password is normal and must not produce noise."""
    monkeypatch.delenv("USERword", raising=False)
    monkeypatch.delenv("NOTAVAR", raising=False)

    with caplog.at_level("WARNING"):
        expand_env_vars("pa$NOTAVARword")

    assert caplog.text == ""


def test_mixed_forms_expand_only_the_delimited_one(monkeypatch):
    monkeypatch.setenv("A", "1")
    monkeypatch.setenv("B", "2")

    assert expand_env_vars("${A}-$B") == "1-$B"
