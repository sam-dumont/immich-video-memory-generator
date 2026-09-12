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

from playwright.sync_api import Page

# Input fields to replace with fake values before screenshotting
_INPUT_REDACTIONS = [
    ('input[aria-label="Immich Server URL"]', "https://photos.example.com"),
    ('input[aria-label="API Key"]', "your-api-key-here"),
    ('input[aria-label="Output filename"]', "everyone_2025_memories.mp4"),
    ('input[aria-label="Title"]', "Summer Adventures"),
    ('input[aria-label="Subtitle"]', "June 2025"),
]

# Regex patterns (as JS source) → replacement strings for visible text nodes
_TEXT_REDACTIONS = [
    (r"Connected as: .+", "Connected as: user@example.com"),
    (r"Immich Connection: .+", "Immich Connection: user@example.com"),
    # Catch-all: any email address that slipped past the specific patterns above
    (r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "user@example.com"),
    (r"http:\/\/\d+\.\d+\.\d+\.\d+:\d+", "https://photos.example.com"),
    (
        r"\/Users\/\w+\/Videos\/Memories\/.*",
        "/home/user/Videos/Memories/everyone_2025_memories.mp4",
    ),
    (r"\/Users\/\w+\/\.immich-memories\/.*", "/home/user/.immich-memories/config.yaml"),
    (
        r"Will be saved to: .*",
        "Will be saved to: /home/user/Videos/Memories/everyone_2025_memories.mp4",
    ),
    (r"Saved to: .*", "Saved to: /home/user/Videos/Memories/everyone_2025_memories.mp4"),
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
                const regex = new RegExp(pattern);
                const walker = document.createTreeWalker(
                    document.body, NodeFilter.SHOW_TEXT
                );
                let node;
                while ((node = walker.nextNode())) {
                    if (regex.test(node.textContent || '')) {
                        node.textContent = (node.textContent || '').replace(regex, repl);
                    }
                }
            }""",
            {"pattern": pattern, "repl": replacement},
        )


def redact_page(page: Page) -> None:
    """Apply all redactions (inputs + text nodes)."""
    redact_inputs(page)
    redact_text_nodes(page)
