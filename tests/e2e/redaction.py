"""DOM redaction helpers for screenshot privacy.

Ported from docs-site/scripts/take-screenshots.ts.
All functions call page.evaluate() with JavaScript to modify the DOM
before screenshots are captured.
"""

from __future__ import annotations

from playwright.sync_api import Page

# Input fields to replace with fake values before screenshotting
_INPUT_REDACTIONS = [
    ('input[aria-label="Immich Server URL"]', "https://photos.example.com"),
    ('input[aria-label="API Key"]', "your-api-key-here"),
    ('input[aria-label="Output filename"]', "alice_2025_memories.mp4"),
]

# Regex patterns (as JS source) → replacement strings for visible text nodes
_TEXT_REDACTIONS = [
    (r"Connected as: .+", "Connected as: user@example.com"),
    (r"http:\/\/\d+\.\d+\.\d+\.\d+:\d+", "https://photos.example.com"),
    (r"\/Users\/\w+\/Videos\/Memories\/.*", "/home/user/Videos/Memories/alice_2025_memories.mp4"),
    (r"\/Users\/\w+\/\.immich-memories\/.*", "/home/user/.immich-memories/config.yaml"),
    (
        r"Will be saved to: .*",
        "Will be saved to: /home/user/Videos/Memories/alice_2025_memories.mp4",
    ),
    (r"Saved to: .*", "Saved to: /home/user/Videos/Memories/alice_2025_memories.mp4"),
    (r"Config file: .*", "Config file: /home/user/.immich-memories/config.yaml"),
    (r"Using \w+'s birthday: .+", "Using Alice's birthday: June 15, 1995"),
    (r"\d+\.\d{4,},\s*-?\d+\.\d{4,}", "48.8566, 2.3522"),
]

_GENERIC_NAMES = [
    "All people",
    "Alice",
    "Bob",
    "Carol",
    "David",
    "Emma",
    "Frank",
    "Grace",
    "Henry",
    "Iris",
    "Jack",
    "Kate",
    "Liam",
    "Mia",
    "Noah",
    "Olivia",
    "Paul",
    "Quinn",
    "Rose",
    "Sam",
    "Tina",
    "Uma",
    "Victor",
    "Wendy",
    "Xander",
    "Yara",
    "Zane",
    "Amy",
    "Ben",
    "Chloe",
    "Dylan",
    "Ella",
    "Finn",
    "Gina",
    "Hugo",
    "Ivy",
    "Jules",
    "Kira",
    "Leo",
    "Nora",
    "Owen",
]


def redact_inputs(page: Page) -> None:
    """Replace sensitive input field values with fake data."""
    for selector, value in _INPUT_REDACTIONS:
        page.evaluate(
            """({sel, val}) => {
                const el = document.querySelector(sel);
                if (el) el.value = val;
            }""",
            {"sel": selector, "val": value},
        )


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


def redact_person_names(page: Page) -> None:
    """Replace person names in Quasar dropdowns using CSS ::after trick.

    WHY: Direct DOM text changes get overwritten by Vue/Quasar reactivity.
    The CSS approach hides real text (font-size: 0) and overlays fake names
    via ::after pseudo-elements, which Vue cannot clobber.
    """
    page.evaluate(
        """(names) => {
            let css = '';
            const options = document.querySelectorAll('[role="option"]');
            options.forEach((opt, i) => {
                if (i >= names.length) return;
                opt.setAttribute('data-redact-idx', String(i));
                const div = opt.querySelector('div');
                if (div) div.setAttribute('data-redact-idx', String(i));
            });
            names.forEach((name, i) => {
                css += `[role="option"][data-redact-idx="${i}"] > div {
                    font-size: 0 !important;
                    line-height: normal !important;
                }
                [role="option"][data-redact-idx="${i}"] > div::after {
                    content: "${name}" !important;
                    font-size: 14px !important;
                }
                `;
            });
            const style = document.createElement('style');
            style.textContent = css;
            document.head.appendChild(style);
        }""",
        _GENERIC_NAMES,
    )
    page.wait_for_timeout(100)


def redact_page(page: Page) -> None:
    """Apply all redactions (inputs + text nodes)."""
    redact_inputs(page)
    redact_text_nodes(page)
