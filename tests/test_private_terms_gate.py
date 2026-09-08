"""Behavioral tests for the private-terms gate.

The denylist itself never appears here: every fixture term is a fake word
(`zorblax`, `quuxley`) chosen only for its shape, never a real name. Git-backed
tests use a real temp repo instead of mocking `git` -- it is the actual read
boundary this script has, the same way other tests in this repo exercise real
FFmpeg rather than stubbing it.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from private_terms_gate import (  # noqa: E402
    DEFAULT_TERMS_ENV_VAR,
    EXIT_HITS_FOUND,
    EXIT_MISCONFIGURED,
    EXIT_OK,
    Term,
    added_lines,
    load_terms,
    main,
    mask,
    scan_diff,
    scan_text,
)


def _terms(tmp_path: Path, content: str) -> list[Term]:
    path = tmp_path / "terms.txt"
    path.write_text(content)
    return load_terms(terms_file=str(path))


def _clean_git_env() -> dict[str, str]:
    """Strip inherited GIT_DIR/GIT_INDEX_FILE/etc.

    These tests spin up a throwaway repo under tmp_path. Run under this
    project's own git hooks (as `make ci`'s pre-commit check does), the
    outer `git commit` sets those vars for its own repo; git subprocess
    calls inherit them by default and would git-add into the OUTER
    worktree's index instead of the tmp_path repo they're meant for.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, env=_clean_git_env(), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _run(["git", "init", "-q"], path)
    _run(["git", "config", "user.email", "test@example.com"], path)
    _run(["git", "config", "user.name", "Test Bot"], path)


# --- mask() ------------------------------------------------------------------


def test_mask_returns_first_character_plus_stars() -> None:
    assert mask("Zorblax") == "Z***"


def test_mask_of_empty_string_is_just_stars() -> None:
    assert mask("") == "***"


# --- scan_text() ---------------------------------------------------------------


def test_scan_text_matches_whole_word_case_insensitively(tmp_path: Path) -> None:
    terms = _terms(tmp_path, "zorblax\n")

    hits = scan_text("ZORBLAX showed up in the log", terms)

    assert len(hits) == 1
    assert hits[0].masked == "Z***"


def test_scan_text_does_not_match_a_partial_word(tmp_path: Path) -> None:
    terms = _terms(tmp_path, "zorblax\n")

    hits = scan_text("zorblaxian visitors arrived", terms)

    assert hits == []


def test_scan_text_regex_term_matches_a_birth_date_shape(tmp_path: Path) -> None:
    terms = _terms(tmp_path, r"re:\b19[0-9]{2}-[0-9]{2}-[0-9]{2}\b" + "\n")

    hits = scan_text("born 1985-03-21 somewhere", terms)

    assert len(hits) == 1
    assert hits[0].masked == "1***"


def test_scan_text_regex_term_matches_a_coordinate_shape(tmp_path: Path) -> None:
    terms = _terms(tmp_path, r"re:\b5[01]\.[0-9]{5,}\b" + "\n")

    hits = scan_text("saw them near 50.84671, 4.35208", terms)

    assert len(hits) == 1


def test_scan_text_masked_output_never_contains_the_original_term(tmp_path: Path) -> None:
    terms = _terms(tmp_path, "quuxley\n")

    hits = scan_text("QuuXley appears twice: quuxley and QUUXLEY", terms)

    assert len(hits) == 3
    for hit in hits:
        assert "quuxley" not in hit.masked.lower()


# --- added_lines() -------------------------------------------------------------

_DIFF_TWO_FILES = """diff --git a/alpha.txt b/alpha.txt
index 111..222 100644
--- a/alpha.txt
+++ b/alpha.txt
@@ -1,2 +1,3 @@
 unchanged context line
-removed line
+added line in alpha
diff --git a/beta.txt b/beta.txt
index 333..444 100644
--- a/beta.txt
+++ b/beta.txt
@@ -1 +1,2 @@
+added line in beta
"""


def test_added_lines_ignores_removed_context_and_header_lines() -> None:
    lines = [line for _, line in added_lines(_DIFF_TWO_FILES)]

    assert "removed line" not in lines
    assert "unchanged context line" not in lines
    assert all(not line.startswith(("---", "+++")) for line in lines)


def test_added_lines_attributes_hits_to_the_right_file_across_multiple_files() -> None:
    pairs = list(added_lines(_DIFF_TWO_FILES))

    assert ("alpha.txt", "added line in alpha") in pairs
    assert ("beta.txt", "added line in beta") in pairs


def test_added_line_starting_with_plus_plus_is_not_mistaken_for_a_diff_header(
    tmp_path: Path,
) -> None:
    """A real added line whose content starts with `++` (e.g. `++zorblax;`) is itself
    prefixed by the diff's own `+` marker, so the raw line reads `+++zorblax;` -- three
    literal `+` characters, same as a `+++ ` file header's prefix. Only the header has
    a space right after those three, so that's what must distinguish them."""
    terms = _terms(tmp_path, "zorblax\n")
    diff_text = (
        "diff --git a/counter.py b/counter.py\n"
        "--- a/counter.py\n"
        "+++ b/counter.py\n"
        "@@ -1 +1 @@\n"
        "-old = 1\n"
        "+++zorblax\n"
    )

    hits = scan_diff(diff_text, terms)

    assert len(hits) == 1
    assert hits[0].location == "counter.py (added line)"


# --- scan_diff() -----------------------------------------------------------------


def test_scan_diff_combines_added_lines_with_term_matches(tmp_path: Path) -> None:
    terms = _terms(tmp_path, "quuxley\n")
    diff_text = (
        "diff --git a/notes.txt b/notes.txt\n"
        "--- a/notes.txt\n"
        "+++ b/notes.txt\n"
        "@@ -1 +1 @@\n"
        "-old line\n"
        "+quuxley was mentioned here\n"
    )

    hits = scan_diff(diff_text, terms)

    assert len(hits) == 1
    assert hits[0].location == "notes.txt (added line)"
    assert "quuxley" not in hits[0].masked


# --- load_terms() ----------------------------------------------------------------


def test_load_terms_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    terms = _terms(tmp_path, "\n# a comment\nzorblax\n\n   \n# another comment\nquuxley\n")

    assert [t.index for t in terms] == [1, 2]
    assert len(scan_text("zorblax and quuxley both appear", terms)) == 2


def test_load_terms_reads_newline_separated_content_from_a_named_env_var(monkeypatch) -> None:
    monkeypatch.setenv("PRIVATE_TERMS_TEST_VAR", "zorblax\nquuxley\n")

    terms = load_terms(terms_env="PRIVATE_TERMS_TEST_VAR")

    assert len(terms) == 2


def test_load_terms_returns_empty_when_nothing_is_configured(tmp_path: Path, monkeypatch) -> None:
    # No file, no env var, no default path -- HOME is redirected so a real
    # denylist that happens to exist on the machine running this test can't leak in.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(DEFAULT_TERMS_ENV_VAR, raising=False)

    assert load_terms() == []


# --- main() ------------------------------------------------------------------------


def test_main_reports_a_skip_notice_and_exits_clean_with_no_denylist(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(DEFAULT_TERMS_ENV_VAR, raising=False)

    exit_code = main([])

    assert exit_code == EXIT_OK
    assert "no denylist found, skipping" in capsys.readouterr().out


def test_main_require_terms_without_a_denylist_exits_with_configuration_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(DEFAULT_TERMS_ENV_VAR, raising=False)

    assert main(["--require-terms"]) == EXIT_MISCONFIGURED


def test_main_reports_a_clear_error_when_terms_file_is_missing(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "does-not-exist.txt"

    exit_code = main(["--terms-file", str(missing)])

    assert exit_code == EXIT_MISCONFIGURED
    assert "could not read --terms-file" in capsys.readouterr().out


def test_main_commit_msg_file_hit_exits_with_hits_found(tmp_path: Path, capsys) -> None:
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("zorblax\n")
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text("feat: mentions zorblax by mistake\n")

    exit_code = main(["--commit-msg", str(msg_file), "--terms-file", str(terms_file)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_HITS_FOUND
    assert "zorblax" not in out
    assert "commit message 0" in out


def test_main_text_file_stdin_reads_and_exits_clean_when_nothing_matches(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("zorblax\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("nothing sensitive here\n"))

    exit_code = main(["--text-file", "-", "--terms-file", str(terms_file)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_OK
    assert "0 hit(s) found" in out


def test_main_staged_reports_a_masked_hit_from_the_cached_diff(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("hello\n")
    _run(["git", "add", "scratch.txt"], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    (tmp_path / "scratch.txt").write_text("hello\nquuxley was here\n")
    _run(["git", "add", "scratch.txt"], tmp_path)
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("quuxley\n")
    monkeypatch.chdir(tmp_path)

    exit_code = main(["--staged", "--terms-file", str(terms_file)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_HITS_FOUND
    assert "scratch.txt (added line)" in out
    assert "quuxley" not in out
    assert "q***" in out


def test_main_range_scans_both_the_diff_and_the_commit_messages(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("base\n")
    _run(["git", "add", "a.txt"], tmp_path)
    _run(["git", "commit", "-q", "-m", "base"], tmp_path)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        env=_clean_git_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (tmp_path / "a.txt").write_text("base\nzorblax lives here\n")
    _run(["git", "add", "a.txt"], tmp_path)
    _run(["git", "commit", "-q", "-m", "mentions zorblax in the message"], tmp_path)
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("zorblax\n")
    monkeypatch.chdir(tmp_path)

    exit_code = main(["--range", f"{base_sha}..HEAD", "--terms-file", str(terms_file)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_HITS_FOUND
    assert "zorblax" not in out
    assert "a.txt (added line)" in out
    assert "commit message" in out
    assert "2 hit(s) found" in out


def test_main_staged_is_not_redirected_by_an_inherited_git_dir(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Regression: this project's own `private-terms` pre-commit hook runs this
    script from inside a `git commit`, which sets GIT_DIR/GIT_WORK_TREE for ITS
    repo. A nested git call that inherits those gets redirected there instead of
    the repo `--staged` is actually meant to scan."""
    outer_git_dir = subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"],
        cwd=Path(__file__).resolve().parent,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _init_repo(tmp_path)
    (tmp_path / "scratch.txt").write_text("hello\n")
    _run(["git", "add", "scratch.txt"], tmp_path)
    _run(["git", "commit", "-q", "-m", "init"], tmp_path)
    (tmp_path / "scratch.txt").write_text("hello\nquuxley was here\n")
    _run(["git", "add", "scratch.txt"], tmp_path)
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("quuxley\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_DIR", outer_git_dir)

    exit_code = main(["--staged", "--terms-file", str(terms_file)])

    out = capsys.readouterr().out
    assert exit_code == EXIT_HITS_FOUND
    assert "scratch.txt (added line)" in out
