"""What the episode reader asks, and the facts a reading may take a name from."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from immich_memories.analysis.text_episode_answers import _EpisodeRequestScope

TEXT_EPISODE_PROMPT_VERSION = "episode-prompt-v2-names-from-facts"

AlbumNames = Callable[[Sequence[str]], tuple[str, ...]]

_PROMPT = """Read these episodes from one family's photo library. Every asset line contains
all banked annotations for that asset. Use only those lines; do not invent names, places,
relationships, or events.

Text read off a banner, a shirt, a sign, a screen or a poster names the thing it is written
on. It never names the day, the place or the event. Name an event, a venue or an organisation
only when a fact line of that episode names it.

For every episode return what happened in at most 25 words, one to three representatives
covering distinct situations, and only genuine Cull rejects. Prefer a starred action frame,
video, or qualifying Live Photo when it earns the place. Cull buckets are notes (screens,
documents, receipts), failed (the picture did not come out), and foreign (saved imagery not
from this life). Similar or merely ordinary pictures are not Cull rejects.

Return JSON only:
{{"schema_version":"episode-reading-text-v1","episodes":[{{"episode":1,
"what_happened":"plain factual sentence","representatives":[{{"asset":1,
"reason":"short reason"}}],"cull":[{{"asset":2,"bucket":"notes"}}]}}]}}

{episodes}"""


@dataclass(frozen=True, slots=True)
class EpisodePromptFacts:
    """Everything an episode block is rendered from besides the scope itself."""

    lines: Mapping[str, str]
    album_names: AlbumNames | None = None

    def albums_line(self, asset_ids: Sequence[str]) -> str:
        """The `Albums:` fact line, or nothing when no album holds these assets."""
        if self.album_names is None:
            return ""
        names = self.album_names(asset_ids)
        return f"  Albums: {'; '.join(names)}\n" if names else ""


def episode_prompt(
    scopes: Sequence[_EpisodeRequestScope],
    facts: EpisodePromptFacts,
) -> str:
    """Render one request: its rules, then a block of facts per episode."""
    blocks = []
    for episode_alias, scope in enumerate(scopes, start=1):
        asset_lines = "\n".join(
            f"  asset {asset_alias} | {facts.lines[asset_id]}"
            for asset_alias, asset_id in enumerate(scope.page_asset_ids, start=1)
        )
        page = f"  page {scope.page_number} of {scope.page_count}\n" if scope.page_count > 1 else ""
        # The album holds the whole episode, not just this page of it.
        albums = facts.albums_line(scope.full_asset_ids)
        blocks.append(f"episode {episode_alias}\n{albums}{page}{asset_lines}")
    return _PROMPT.format(episodes="\n\n".join(blocks))
