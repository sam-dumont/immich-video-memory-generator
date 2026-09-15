"""DOM redaction helpers for screenshot privacy.

These rewrite the few places the hermetic launch still prints something
machine-specific -- a temp path carrying the developer's user name, the port
the fake service happened to get -- before a screenshot is taken.

There is deliberately no name substitution here. The fake service serves one
person, "Fake Person", so there is nothing to anonymise; a list of stand-in
first names only invites relabelling a real person as a fictional one, which is
how a demo ends up starring someone's family under made-up names.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

# RFC 2606 / RFC 6761 reserved names, plus the ones the fixtures already use.
_EXAMPLE_DOMAINS = (
    "@example.com",
    "@example.net",
    "@example.org",
    "@example.test",
    "@example.invalid",
    "@example.localhost",
)

# Input fields to replace with fake values before screenshotting
_INPUT_REDACTIONS = [
    ('input[aria-label="Immich Server URL"]', "https://photos.example.com"),
    ('input[aria-label="API Key"]', "your-api-key-here"),
    ('input[aria-label="Output filename"]', "everyone_june_2024_memories.mp4"),
    ('input[aria-label="Title"]', "Summer Adventures"),
    ('input[aria-label="Subtitle"]', "June 2025"),
]

# Regex patterns (as JS source) → replacement strings for visible text nodes
_TEXT_REDACTIONS = [
    (r"Connected as: .+", "Connected as: user@example.com"),
    # The label has been rendered with a colon and with an em dash; match either,
    # and anything else that separates it from the account.
    (r"Immich Connection[^A-Za-z0-9]+.+", "Immich Connection: user@example.com"),
    # Catch-all: any email address that slipped past the specific patterns above
    (r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "user@example.com"),
    (r"http:\/\/\d+\.\d+\.\d+\.\d+:\d+", "https://photos.example.com"),
    (
        r"\/Users\/\w+\/Videos\/Memories\/.*",
        "/home/user/Videos/Memories/everyone_june_2024_memories.mp4",
    ),
    (r"\/Users\/\w+\/\.immich-memories\/.*", "/home/user/.immich-memories/config.yaml"),
    (
        r"Will be saved to: .*",
        "Will be saved to: /home/user/Videos/Memories/everyone_june_2024_memories.mp4",
    ),
    (r"Saved to: .*", "Saved to: /home/user/Videos/Memories/everyone_june_2024_memories.mp4"),
    (r"Config file: .*", "Config file: /home/user/.immich-memories/config.yaml"),
    (r"Using \w+'s birthday: .+", "Using Fake Person's birthday: June 15, 1995"),
    (r"\d+\.\d{4,},\s*-?\d+\.\d{4,}", "48.8566, 2.3522"),
]


def redact_inputs(page: Page) -> None:
    """Replace sensitive input field values with fake data."""
    for selector, value in _INPUT_REDACTIONS:
        page.evaluate(
            """({sel, val}) => {
                const el = document.querySelector(sel);
                if (el) {
                    el.value = val;
                    // Quasar wraps inputs — also set the inner control's value
                    const native = el.closest('.q-field')?.querySelector('input');
                    if (native && native !== el) native.value = val;
                }
            }""",
            {"sel": selector, "val": value},
        )
    # Catch any remaining IP:port patterns in ALL input elements
    page.evaluate("""() => {
        document.querySelectorAll('input').forEach(el => {
            if (/\\d+\\.\\d+\\.\\d+\\.\\d+/.test(el.value)) {
                el.value = el.value.replace(
                    /https?:\\/\\/\\d+\\.\\d+\\.\\d+\\.\\d+(:\\d+)?/g,
                    'https://photos.example.com'
                );
            }
        });
    }""")


def redact_text_nodes(page: Page) -> None:
    """Walk visible text nodes and replace patterns matching personal data."""
    for pattern, replacement in _TEXT_REDACTIONS:
        page.evaluate(
            """({pattern, repl}) => {
                // WHY the g flag: a node holding two addresses had only its
                // first one rewritten, and the second reached the screenshot.
                const regex = new RegExp(pattern, 'g');
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {
                    const before = node.textContent || '';
                    const after = before.replace(regex, repl);
                    if (after !== before) node.textContent = after;
                }
            }""",
            {"pattern": pattern, "repl": replacement},
        )


def assert_no_real_address(page: Page) -> None:
    """Fail the capture when an address outside the example domains survived redaction.

    The patterns above only cover the places that render one today, and only in
    text nodes -- a page that grows a new one publishes it quietly, which is how
    a real address reached the docs site. Failing here beats reviewing the PNG.
    The addresses themselves are never logged: a CI log is public too.
    """
    rendered = page.evaluate(
        """() => [
            document.body.innerText,
            ...[...document.querySelectorAll('input, textarea')].map(el => el.value),
        ].join('\\n')"""
    )
    leaked = {
        hit
        for hit in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", rendered)
        if not hit.casefold().endswith(_EXAMPLE_DOMAINS)
    }
    assert not leaked, (
        f"{len(leaked)} address(es) outside the example domains reached a screenshot; "
        f"domains seen: {sorted({hit.rpartition('@')[2] for hit in leaked})}"
    )


def redact_page(page: Page) -> None:
    """Apply all redactions (inputs + text nodes), then refuse to publish a leak."""
    redact_inputs(page)
    redact_text_nodes(page)
    assert_no_real_address(page)
