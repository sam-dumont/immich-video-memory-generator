"""What the episode reader asks, and the facts a reading may take a name from."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from immich_memories.analysis.text_episode_answers import _EpisodeRequestScope

_PROMPT_NAME = "episode-prompt-v4-notable-bar"

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

Also return notable_moments, separately from the account and its representatives: a picture
that is the record of something that happens once, which the 25-word account would lose. The
bar is absolute: judge each episode on its own lines, never against the other episodes here.
It passes only when a line shows the once-only thing itself: an arrival (a birth, a new pet, a
new home), a milestone with its occasion visible (a named birthday or anniversary, a first day,
a graduation, a wedding, a trophy or a certificate), a change you can see (a new haircut, a cast,
a finished build), or text naming the occasion. A small object can carry one when it is the
record itself (a ring in its box, a hospital bracelet, the keys to a new home). An outing, a
view, a meal, a pose, a game, a nice portrait or an animal is not a record, however good the
picture. Do not infer a first, a relationship, a diagnosis or a feeling from the order things
happened in. Name the asset and the observable reason. Return [] when no line shows such a
record; that is the usual answer.

Return JSON only:
{{"schema_version":"episode-reading-text-v1","episodes":[{{"episode":1,
"what_happened":"plain factual sentence","representatives":[{{"asset":1,
"reason":"short reason"}}],"cull":[{{"asset":2,"bucket":"notes"}}],
"notable_moments":[{{"asset":1,"reason":"what this is a record of"}}]}}]}}

{episodes}"""

_LEAN_PROMPT_NAME = "episode-prompt-v4-lean"

# What a film's on-demand reading asks: the account's sentence and the records, with the one
# representative a reading needs to be bankable. The full prompt's other asks (up to three
# representatives with reasons, every Cull reject) were 62 % of the reader's output on the
# measured cold year, and nothing a film reads on demand uses them.
_LEAN_PROMPT = """Read these episodes from one family's photo library. Every asset line contains
all banked annotations for that asset. Use only those lines; do not invent names, places,
relationships, or events.

Text read off a banner, a shirt, a sign, a screen or a poster names the thing it is written
on. It never names the day, the place or the event. Name an event, a venue or an organisation
only when a fact line of that episode names it.

For every episode return what happened in at most 25 words, and the one representative that
best shows it, with a reason of at most six words. Prefer a starred action frame, video, or
qualifying Live Photo when it earns the place.

Also return notable_moments, separately from the account and its representative: a picture
that is the record of something that happens once, which the 25-word account would lose. The
bar is absolute: judge each episode on its own lines, never against the other episodes here.
It passes only when a line shows the once-only thing itself: an arrival (a birth, a new pet, a
new home), a milestone with its occasion visible (a named birthday or anniversary, a first day,
a graduation, a wedding, a trophy or a certificate), a change you can see (a new haircut, a cast,
a finished build), or text naming the occasion. A small object can carry one when it is the
record itself (a ring in its box, a hospital bracelet, the keys to a new home). An outing, a
view, a meal, a pose, a game, a nice portrait or an animal is not a record, however good the
picture. Do not infer a first, a relationship, a diagnosis or a feeling from the order things
happened in. Name the asset and the observable reason. Return [] when no line shows such a
record; that is the usual answer.

Return JSON only:
{{"schema_version":"episode-reading-text-v1","episodes":[{{"episode":1,
"what_happened":"plain factual sentence","representatives":[{{"asset":1,
"reason":"short reason"}}],
"notable_moments":[{{"asset":1,"reason":"what this is a record of"}}]}}]}}

{episodes}"""

# A banked answer is ground truth for the question that was asked. Hashing the prompt into the
# producer expires every reading the moment its wording changes, whether or not the name above
# was bumped with it.
TEXT_EPISODE_PROMPT_VERSION = f"{_PROMPT_NAME}/{sha256(_PROMPT.encode()).hexdigest()[:16]}"
TEXT_EPISODE_LEAN_PROMPT_VERSION = (
    f"{_LEAN_PROMPT_NAME}/{sha256(_LEAN_PROMPT.encode()).hexdigest()[:16]}"
)


@dataclass(frozen=True, slots=True)
class EpisodePromptFacts:
    """Everything an episode block is rendered from besides the scope itself."""

    lines: Mapping[str, str]
    album_names: AlbumNames | None = None
    # The film's on-demand question: no Cull, one representative.
    lean: bool = False

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
    template = _LEAN_PROMPT if facts.lean else _PROMPT
    return template.format(episodes="\n\n".join(blocks))
