---
sidebar_position: 7
title: Title screens, maps and music
---

# Title screens, maps and music

An intro card, a divider at every month change, a satellite fly-over for a trip, location cards
between segments, an ending. This is the structure around the clips, and it is the difference
between a memory and an FFmpeg concat of your footage.

- **Intro card**: a content-backed background (a blurred, darkened frame from your footage), white
  title text with an entrance animation, optional subtitle. 3.5 seconds. Title and subtitle are
  stacked by the heights they actually wrap to and shrink together when the pair would pass 80 % of
  the frame height, and a layout whose two blocks would still share a row is refused and drawn
  again smaller.
- **Month dividers**: at every month change, for any single-year range spanning four or more months.
  The first month's divider is skipped because the intro card already said it.
  `month_divider_threshold` does not gate insertion, it sizes a budget cap, and when the cap binds it
  keeps the *first* N month changes chronologically. So a one-clip January can get a card while a
  dense November loses one.
- **Trip map animation**: a satellite fly-over from home to destination, replacing the generic intro.
- **Location cards**: city name and map thumbnail between trip segments.
- **Ending**: a fade-to-white card. No text: every ending path renders with an empty title.

By default a title screen's background is the first second of the first clip, blurred and darkened,
so every card belongs to the video it introduces rather than to a generic gradient. How hard it is
blurred and dimmed depends on the renderer and neither is a config key. The GPU path animates that
background in slow motion, interpolating between decoded frames with Catmull-Rom and a cubic
ease-in, falling back to a single blurred frame when the clip cannot be decoded or yields fewer than
five frames.

Two rendering backends, chosen automatically with nothing to configure: the GPU kernels where the
library initialises (Metal, CUDA or Vulkan) and PIL where it does not, which gives static gradients
and clean text instead of bokeh particles. `--no-animated-background` is a different switch that
stays on whichever backend you have and turns off gradient rotation, colour pulse and vignette
pulse; on the shipped default it changes nothing, because a content-backed title zeroes all three
anyway. Which renderer your machine gets is on
[Title kernels](../deploy/hardware.md#title-kernels).

## Styles

All styles use dark cinematic palettes with white text. Every title is Montserrat: there is no font
choice, and the `preferred_fonts` list in the style definitions is read by nothing.

| Style | Palette | Character |
|-------|---------|-----------|
| `modern_warm` | Warm charcoal/stone | Bold, semibold. Amber accents |
| `elegant_minimal` | Deep navy/black | Clean, medium weight. Cyan accents |
| `vintage_charm` | Warm charcoal | Nostalgic. Amber/gold accents |
| `playful_bright` | Deep teal | Energetic, semibold. Teal accents |
| `soft_romantic` | Dark amber-tinted | Gentle scale-in. Warm amber accents |

On `style_mode: auto`, the default, the style comes from the memory's detected mood: happy,
nostalgic and romantic get `warm_dark`; calm and peaceful get `deep_teal`; energetic and playful get
`midnight`; exciting gets `cinematic_dark`. `style_mode: random` picks a named style at random, and
`--style elegant_minimal` on the titles CLI forces one.

`title_screens.enabled` turns the whole thing off. The fly-over has a switch of its own,
`network.map_tiles`, and it is off in a fresh install: see
[The map fly-over](#the-map-fly-over). The rest of the keys are in the
[config reference](../reference/config-reference.md#title-screens).

Preview a card without running a pipeline:

```bash
immich-memories titles test --year 2025 --style elegant_minimal
immich-memories titles test --birthday-age 3 --person "Emma" --year 2025
immich-memories titles test --month 6 --year 2025 --type month
immich-memories titles test --year 2025 --locale fr --orientation portrait
```

Before you spend time on `titles fonts`: titles are Montserrat, and Outfit is used only for the
date and place captions burned onto clips. Raleway, Josefin Sans and Quicksand ship and are never
selected. All five families are OFL-1.1 and live inside the package, so titles render on a fresh
install with no network. `--download` mirrors the same files from the Fontsource CDN into
`~/.immich-memories/fonts/`; it is there for inspecting what ships, not for making titles work and
not for replacing them, because for any of the five known families the bundled file wins before the
cache is consulted. The listing reads the cache directory only, so on a fresh install it says **Not
downloaded** for all five while titles render perfectly from the bundle.

## Date and place captions

`generate --add-date --add-place` burns small translucent context onto clips: 48 px on a 1080p frame
at 85 % opacity, date and place appearing independently as each one changes. Missing metadata on a
clip does not restart a caption already running, captions stay clear of dissolves so outgoing and
incoming labels cannot overlap, and a clip too short to hold a window keeps its caption for its whole
length instead of losing it.

Places you are at constantly stay unlabelled, because the name of your own town over every third clip
is noise. Familiarity is measured on GPS observations within **250 m of the asset**, across the
accessible library's history, and a place qualifies on either pattern: 12 distinct visit weeks across
six months in each of two years, or 3 distinct visit weeks across three months in each of five years.
Several photos in one week count as one visit, so an annual summer holiday never qualifies on its
own. These are conservative recurrence rules, not proof of residence, and they never suppress an
entire city: the 250 m circle is the unit.

`trips.homebase_latitude` and `trips.homebase_longitude` mark the home circle as well, and its GPS
history supplies the home country, which is then left out of domestic captions while foreign
countries stay visible. A picture with no GPS cannot be matched to a familiar place by city name
alone, and maps keep the original location data either way.

The first render with place captions reads library metadata and downloads no photos for it. The
answer is cached under `cache.directory/familiar-places/`, keyed per server and credential scope, for
seven days; delete that directory to pick up metadata edits sooner. Privacy mode, and any render
without place captions, skips the scan.

## The map fly-over

The fly-over needs satellite tiles from a third party, so it waits for your permission:

```yaml
network:
  map_tiles: true
```

Without it a trip memory opens on the ordinary title card carrying the trip title, and location
cards keep their text on the style's own background. Nothing is requested, and nothing about where
you went leaves the machine. What follows is what the switch buys.

A trip memory opens on your home location at city-level zoom, flies out and across to the
destinations, and settles at a zoom that shows every pin. It runs for `title_duration`, 3.5 seconds
by default. Each endpoint gets a red pin with a white outline and a city label, and the title text
fades in over the lower third where it does not block the map.

Two animations, picked on the distance between departure and destination. **Van Wijk zoom** for long
distances: the d3 `interpolateZoom` algorithm, which picks the smoothest path through zoom-space
rather than interpolating linearly, so the further apart the two points the further the camera pulls
out mid-flight. **Linear pan** for short hops, when the mid-transit zoom would stay at level 10 or
above.

Imagery is ArcGIS World Imagery at `server.arcgisonline.com`, no API key, cached in memory during
rendering. One fly-over is hundreds of tiles covering the trip area and your home base. A host that
cannot reach it renders grey frames rather than failing the run. The static map
renderer also knows `osm` and `topo` for the pin-and-label frames, but both are internal today:
`map_style` is a parameter on the renderer functions in `titles/map_renderer.py`, not a
`title_screens` key, so a generate run always gets satellite.

### Place names and the film's language

Immich geocodes with GeoNames and stores English, so every city and country it hands over is
English. Country names are translated offline (CLDR, through babel) wherever a viewer reads one:
the trip title, the clip overlays, the map pin labels and the location cards. A French film says
`DEUX SEMAINES À ESPAGNE`, not `À SPAIN`.

The islands and regions a trip is named after have a short offline table too (`Crète`,
`Pouilles`, `Majorque`, `Saxe`), and two regions are joined in the film's language
(`Utah et Nevada`). City names have no offline table. They stay as Immich stored them unless
`network.geocoding: true` lets Nominatim answer in the film's language, one request per distinct
place on the cut. See
[Network & Privacy](../deploy/configuration/network-and-privacy.md#geocoding-and-maps).

### Trip titles and classification

A template gives you "TWO WEEKS IN SPAIN, SUMMER 2025". The model gives you "Sous les falaises de
grès". English and French are the two locales the app ships.

The model never sees coordinates. The selected material's GPS points are clustered greedily within
5 km, each cluster is reverse-geocoded to a city name, and the prompt is one line per day: the place
names and how many of the selected pictures fell at each. Back come a title, an optional subtitle, a
classification with a one-line reason, and a map mode. All of it is editable on the Generation
Options page, and the regenerate button asks again over the same GPS data.

| Pattern | Classification | Example |
|---------|---------------|---------|
| Same spot every night, excursions during the day | `base_camp` | A week in a mountain village, day hikes into two valleys |
| 2-3 spots, each for several consecutive days | `multi_base` | An island: 5 nights in the capital, 5 on the coast |
| Different town each day, big distances | `road_trip` | Two weeks driving through three regions |
| Daily moves but short distances, progressive | `hiking_trail` | A hut-to-hut trail |

The `map_mode` that comes back with it (`excursions`, `overnight_stops`, `title_only`) is read by
nothing in the renderer: today it is a badge in the UI and changes no pixels.

### People and occasion titles

The template opens a film about three people on a date span with three full names stacked
underneath. The model gets the facts instead and writes "Ada and her grandparents".

What it is sent, and nothing else: how many people are in the film, one line per person with their
birth date and their age at each end of the span, one line per ordered pair saying what your people
file records about them (`grandparent-of`, `cousin-of`, "no recorded relation"), the people
condition the selection ran on, and what the span IS rather than only where it ends: "starts on
Ada's birth date", "ends today (open-ended)", "the calendar year 2025". First names and the words a
family uses at home (maman, papa, mamie, papy; mum, dad) are asked for; a span that is somebody's
whole life so far gets no dates. The relationships come from your people file, so the roles are only
as good as what you confirmed in **People**; without that file the model still gets the names, the
birth dates Immich holds and the span.

An occasion (a special day, an album, a holiday, a month, a season, a year, an On This Day) gets
the same treatment from what the occasion already is: the name the special-day catalogue gave it,
the album's own name, the holiday, the places by day. Any memory whose material mostly sits in one
Immich album is also told that album's name: a family day is often called nothing else, and reading
a name somebody typed is not inventing one. Where an album name and the catalogue's own words
disagree, the prompt says the album wins: somebody typed it, the catalogue guessed it. Both prompts
are told that place names arrive in English as the camera recorded them and should be written in
your locale, which is what turns "Cyprus et Grèce" into "Chypre et Grèce".

The app asks Immich which albums hold each picture of the cut, and the bar comes from the cut's own
shape rather than a fixed share: the leading album has to hold more of the cut than the pictures no
album holds at all. A day filed across two albums still learns the bigger one's name; two pictures
out of ten in some catch-all learn nothing. Between albums holding as much of the cut as each other
the smaller one wins, because a collection that swallows the day names it less well than the day's
own album. Trips get the album name too.

Only an album made for a film like this one can name it. The phone's catch-all (Recents, in
whatever language the phone speaks) holds most of every cut, so it would name every film. The app
knows no album names; it looks at proportions Immich already reports. An album with more than twice
as many pictures as the film had to choose from is mostly other films, and an album whose dates run
mostly (over a quarter of its span) outside the film's window was filed around another time. A year
in review that sits 121 of 127 pictures in a 38,000-picture catch-all gets its normal year title.
A person film since birth covers a catch-all's whole span, so there the size bar decides on its own.
A picture that only a catch-all holds counts as filed nowhere.

Neither prompt sees the film's own reading of the period. Those readings promote names off banners
and shopfronts (a stage banner once became "the X festival"), and a title may not invent. The only
proper nouns a title can use are the catalogue's words, the place names, the album name and the
people's first names, and that is checked rather than only asked for. A title with a capitalised word that
appears in none of the facts the prompt carried is refused and the template names the memory
instead; the same check drops a subtitle that names something unrecorded and keeps the title. A
place written the way your locale spells it (Ghent → Gent, Brussels → Bruxelles) still counts as the
recorded name, so the locale rule and the check do not fight. Refusing costs a plainer title and
nothing else, which is why the check leans towards refusing.

The model names people and occasion memories by default as soon as a reader is configured, in the
wizard and on the CLI. `--title` still wins, the template is the fallback whenever the model fails
or no reader is set, and a special day whose catalogue entry already carries a title keeps it. A row
the scan described but never named is a different case: words like "an outdoor music festival with
multiple performances" are a fact about the day, not a title, so they go to the prompt as one and
the reader still gets asked. A special day the catalogue never found is named here too, from the
day's own facts and with no catalogue entry to read, which is what lets a day the scan missed be
filmed at all. Without a reader either day falls back to the catalogue's words, or to its own date,
which names no event.
`--llm-title` extends the same treatment to trips; `--no-llm-title` pins the template everywhere,
which is what a contact-sheet matrix wants so runs months apart stay comparable.

Two known limits. A single grandparent can come back plural, because the people file records no
gender. Four or five children in one condition is enough for the model to start inventing roles.

## Music

Three stages: a mood for the memory, a track for the mood, and ducking so the music drops under the
clips' own sound. Ducking is a sidechain compressor keyed on the clip's audio and does not know what
the sound is, so speech, laughter, wind and traffic all duck it.

The mood comes from the same text model the editor uses, reading the saved cut's thesis, story
labels and prepared captions for kept pictures. No new images go out, the answer is reused for
identical text and model settings, and `runs why` reports its source. Without usable text or a model
the local defaults apply; a failed text call never switches to vision. The answer carries a full
judgment, not one word: a primary mood, an energy level, a tempo and up to five genre yields, and the
generation step uses all of it rather than collapsing every memory to "upbeat" or "calm".

| Where | Switch | Effect |
|---|---|---|
| Config | `ace_step.enabled`, `musicgen.enabled` | When either is on, a track is generated (ACE-Step first when both are) |
| CLI | `--music PATH`, `--no-music`, `--music-volume 0.0-1.0` (default 0.5) | Your file, no music, or the level |
| UI, Generation Options | **Background music**: None, Upload file, Bundled, AI Generated, plus the volume slider | Same choices per run |

The chain is fixed: an explicit file wins, otherwise a generator if one is enabled, otherwise a
bundled track. A generator that fails falls through to the next and then to the bundle, and the
substitution comes back as a warning on the finished video and in the nightly notification, so a
dead backend does not sound like working music forever.

### The bundled tracks

A plain install renders silent videos unless you supply a file. The `music` extra ships 28
royalty-free tracks and the Docker image and the `all` extra include it:

```bash
pip install "immich-memories[music]"
```

Five moods (calm, energetic, happy, nostalgic, tender) in acoustic and electronic styles, about 30 s
each, looped with a crossfade to fill longer videos. They were generated locally with ACE-Step 1.5
from nothing sampled, so there is no attribution requirement; each track's tempo, key and seed are in
`LICENSE-MUSIC` inside the package.

When a memory holds photos, a bundled track whose measured beat lands within 0.2 beats of the photo
cadence is preferred, so cuts land with the pulse. The window is loose on purpose: the detector
quantises the beat period to 23 ms frames and reads half or double time often enough that a tighter
window would throw away tracks that fit.

### ACE-Step

ACE-Step 1.5 takes explicit musical parameters (BPM, key, time signature) as structured fields. In
`api` mode, the default, it is HTTP to an ACE-Step server polled every 3 s, and the server owns the
loaded models. In `lib` mode the model runs locally: MLX on Apple Silicon, CUDA on NVIDIA,
PyTorch CPU otherwise, Python 3.12 or earlier, falling back to `api` when the package is missing.

A worked example, not the defaults. Shipped, `enabled` is false, `mode` is `api`, `model_variant` is
`turbo` and `lm_model_size` is `1.7B`:

```yaml
advanced:
  ace_step:
    enabled: true
    mode: lib
    model_variant: acestep-v15-xl-turbo   # 4B, 8 steps
    lm_model_size: 4B
    use_lm: false
    num_versions: 1
  musicgen:
    enabled: false                     # use local Demucs for stems
```

The variants are `turbo` and `base` (2B, 8 and 50 steps), `acestep-v15-xl-turbo` (4B, 8 steps), and
`acestep-v15-xl-sft` and `-base` (4B, 50 steps). On v0.1.8 the non-turbo XL models inherit DCW on and
can produce garbled audio on Apple Silicon, so use `xl-turbo` for automation. `use_lm` is off by
default: with it on, ACE-Step's language model rewrites the caption before the audio model sees it,
pulls instrumental briefs off target, and takes a 60 s track from about 17 s to 45 s.

When the memory holds photos the requested tempo is nudged so a photo lasts a whole number of beats,
measured against the interval between visible cuts, within the genre's tempo range and within 15 % of
the mood's tempo. Videos are never re-timed.

Auto mode checks each generated track before it ships. A metronomically repetitive take, the "tic
tac" from #1007, is measured on the mastered full mix and, when flagged, replaced by up to
`audio.max_regenerations` (default 2) more takes, keeping the best-scored one. The check reads how
much of the onset energy sits at a single repeat lag, so a stuck loop or a flat grid reads high while
a varied track reads low. Music is never dropped, only re-rolled, so a run still ends with a track
even when every take is flagged.

A video longer than `audio.music_block_seconds` (default 120) is not one long generation. Auto mode
generates up to `audio.max_music_blocks` (default 3) distinct takes of the same caption and joins
them with crossfades, then loops the sequence to fill the rest. One long take reads as a metronomic
ramble, and one short phrase on repeat is its own kind of monotony; a chain of a few distinct takes
is neither, and stems are then separated once on the assembled mix.

`lib` mode checks free memory against the weights the profile keeps resident and refuses with a named
shortfall rather than letting macOS kill the process mid-render: about 29 GB for XL with the 4B
planner, 21 GB for XL without it, 11 GB and 7 GB for the 2B profiles. A refusal is a normal backend
failure, so MusicGen is next and then a bundled track. The MLX buffer cache is capped at 4 GiB and
the DiT copy runs in bf16 (7.8 GB instead of 15.5 GB for XL); `IMMICH_MEMORIES_ACESTEP_MLX_DIT_FP32=1`
keeps fp32. Models are dropped after each batch.

#### Install locally on a Mac

Use the source checkout's Python 3.12 environment for local ACE-Step. It is not included in
`uv tool install` or the `all-mac` extra. From a fresh checkout:

```bash
brew install uv ffmpeg
git clone https://github.com/sam-dumont/immich-video-memory-generator.git
cd immich-video-memory-generator
make dev-mac
make install-acestep
```

`make install-acestep` installs ACE-Step **v0.1.8**, its inference dependencies and Demucs together.
It checks the real ACE-Step handler, its language-model imports and a torchvision operator.
ACE-Step runs automatically in a sibling `.venv-acestep` environment because its Transformers
version requires an older Hugging Face library than the editor. No server is needed. PyTorch,
torchvision and torchaudio are pinned together. Training and the upstream web UI are excluded.

Add the configuration above to `~/.immich-memories/config.yaml`, then verify actual generation:

```bash
make check-local-audio
# Optional: test the larger XL-turbo model with its 4B planner
make check-local-audio AUDIO_CHECK_ARGS="--quality high"
uv run immich-memories ui
```

The check generates 15 seconds with 2B turbo and its 0.6B planner, runs local Demucs, and checks
the duration and samples of the track and all four stems. It prints the files so you can listen.
It fails if local generation fails; a remote server or bundled track cannot pass this check.
The first run downloads missing weights. Existing `~/.cache/ace-step/checkpoints` and
`~/.cache/torch/hub/checkpoints` caches are reused. See the memory requirements above before using XL.

Run the app with `uv run immich-memories` from this checkout so it uses the environment you just
installed into. `make dev`, `make dev-mac` and `make ensure-dev` leave the audio environment alone.
A bare `uv sync` also leaves it intact, but can remove Demucs from the editor's environment;
`make install-acestep` restores both. Rerun the installer after moving the checkout or changing
the app version. It also repairs `operator torchvision::nms does not exist` from an older install.

#### Containers and GPU access

A separate ACE-Step container can expose its API to the editor with `advanced.ace_step.mode: api`
and `advanced.ace_step.api_url` pointing at that service. Its model cache belongs on a persistent
volume. This is useful on a [Linux NVIDIA host](../deploy/common-setups/linux-nvidia.md).

On macOS, a normal Docker container cannot use ACE-Step's Metal/MLX backend; it runs on CPU.
[Docker Desktop's container GPU support](https://docs.docker.com/desktop/features/gpu/) covers
Windows with WSL2. Use the native installer above for GPU music generation on an Apple Silicon Mac.
An editor running in Docker can still call a native ACE-Step API server through
`http://host.docker.internal:8000`.

### MusicGen

Meta's MusicGen through a remote server, for text-to-music and for Demucs stem separation. With
ACE-Step enabled it is the fallback; with ACE-Step disabled it generates alone.

```yaml
musicgen:
  enabled: true
  base_url: "http://localhost:8000"
  timeout_seconds: 10800
  num_versions: 3
```

### Ducking and stems

Generated music is mastered before [Demucs](https://github.com/facebookresearch/demucs) separates
it. The CLI retains all four stems for the final mix: vocals duck most under original audio, bass
and other instruments duck less, and drums keep their rhythm. Detected music in the original
footage lowers every generated stem. Speech-safe video cuts remain part of selection.

`--music-volume` maps 0.0 to 1.0 onto -20 dB to 0 dB before ducking. The CLI uses threshold 0.02,
ratio 4.0, 100 ms attack, 2.5 s release, a 2 s fade in and a 3 s fade out. If four stems are
unavailable, it ducks the full track with the same settings. Bundled and explicitly supplied tracks
also use this full-track path. The web UI's preview mixer has its own controls.

`make install-acestep` includes local Demucs, which uses Metal on Apple Silicon. For Demucs alone,
install `immich-memories[demucs]`. Its htdemucs model is about 80 MB on first use. With
`advanced.musicgen.enabled: true`, the MusicGen server's `/separate` endpoint takes priority over
local Demucs.

None of the ducking constants has a config key. `audio:` holds only `local_music_dir`
(`~/Music/Memories`).

### The music commands

```bash
immich-memories music search --mood happy --genre acoustic --limit 5
immich-memories music analyze ~/Videos/vacation.mp4
immich-memories music add compilation.mp4 output.mp4 --music ~/Music/track.mp3
immich-memories music add compilation.mp4 output.mp4 --fade-in 3 --fade-out 5 --mood energetic
```

`music search` reads `audio.local_music_dir` and matches on mood or on any word in a track's title,
artist or folder name. Two things its flags do not tell you: `--tempo` is accepted and does not
filter local results, and the search hard-codes a 10-minute maximum with no flag to raise it.

`music analyze` extracts keyframes from a video file and asks the configured vision model what it
sounds like. That is an explicit request to send frames. (`--ollama-url` overrides the configured
reader's base URL whatever provider it is set to; the name is historical.)

`music add` mixes a track under a video that already exists, ducking it when the clip's own audio
comes up, which is the way to get custom fades or an exact dB level. Without `--music` it searches
your local library at mood `calm` and sends no frames; `--analyze-frames` asks the vision provider to
judge sampled frames instead, and a supplied `--mood` beats both.

| Model | Location | Size |
|---|---|---|
| ACE-Step 2B (turbo, base) | `~/.cache/ace-step/checkpoints/` | about 4.5 GB each |
| ACE-Step XL-turbo (4B) | same | about 19 GB |
| ACE-Step planners (0.6B, 1.7B, 4B) | same | 1.2, 3.4, 7.8 GB |
| Shared VAE and embedding | same | about 1.4 GB |
| Demucs htdemucs | `~/.cache/torch/hub/` | about 80 MB |

The XL production profile with the 4B planner is about 28 GB on disk. Old checkpoints are not removed
automatically.
