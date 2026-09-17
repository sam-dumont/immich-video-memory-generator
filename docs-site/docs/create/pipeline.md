---
sidebar_position: 5
title: How a memory gets cut
---

# How a memory gets cut

Every photo app has an automatic memories feature and they all work the same way: rank the pixels
(sharpness, faces, smiles), pick the winners, add music. The result is a highlight reel. Technically
fine, emotionally random, and after the third one you stop watching.

This one reads the period as a story instead. It prepares a description, people, place context and
picture facts for every eligible picture in the range, decides which stories matter and which
distinct moments show them, and only then allocates the film's duration. One
`immich-memories generate` and one **Cut** on the Memory page take the identical route.

```mermaid
flowchart TD
    immich[("Immich")] -->|"every eligible asset,<br/>with its exclusion reason"| prep

    subgraph prep["Preparation: once per picture, then banked"]
        direction LR
        previews["previews"] ~~~ pixels["pixel facts"] ~~~ heads["encoder + six heads"]
        heads ~~~ detectors["two detectors"] ~~~ caption["caption<br/>(full tier only)"]
    end

    prep --> episodes["Reading event evidence: i/n"]
    episodes --> period["Reading the period account: month"]
    period --> cards["Building editorial cards"]
    cards --> edit["Editing the memory"]
    edit --> timing["Validating selected source timing"]
    timing --> render["Render: originals, photos,<br/>title screens, assembly, encode"]
    render --> music["Music"]
    music --> deliver["Upload back to Immich"]

    caption -.->|"a 400 px tile per picture"| captioner(["caption server"])
    episodes -.-> reader(["the reader"])
    period -.-> reader
    edit -.->|"800 px tiles and annotation lines"| reader
```

Solid arrows are this box. The two dotted ones are the only seats that can live somewhere else, and
the only things a picture is ever sent to.

## The rules it obeys

These are constraints, not preferences it weighs.

**Always chronological.** A memory plays in the order things happened. No model may resequence a
cut for drama: chronology is the one thing you can check against your own recollection, and a
reordered memory is subtly a lie about the day. The editorial decisions are what to include and how
long to dwell, never when.

**A favourite wins its moment, not its story's slot.** Where you have flagged a photo, nothing
overrules you with a score: a star leads the rows the pick reads, keeps a slot on the page it sits
on, is never displaced by a replacement, and wins the frame of the moment the pick chooses. What it
does not do is buy the moment. The editor is still asked which moments tell a story, unless that
story offers a single moment and the grant reaches it, because a dense tail of favourites would
otherwise bury a story's beginning. Favourites still help establish a story's importance, subject to
source and audience eligibility, but they do not order which stories a film funds. When a period
holds more stories than the film has slots, a detected trip goes first among the stories of its
weight; then the memory-worthy reading and the number of distinct moments decide, then the order the
reader listed its stories in, then the order things happened. A well starred December cannot push
January out of the year.

**A trip is one story, and it gets room for its length.** The editor runs the app's own trip
detection over the film's pictures, with your `trips` home base, distance, duration and gap, and no
network: a trip is named from the place names its pictures carry. Every day of a detected trip is one
story whatever the reader grouped, and the weighing sees it as a trip, with its days and stops on the
row. A trip weighed as an occasion takes more than one picture where the other stories take their
first: half the film for a trip that is the whole film, the square root of its share of the film's
photographed days below that. A ten-day trip in a five-minute year gets about five, a six-day
holiday in a 90-second year two or three, and never more than the distinct moments it holds. With no
home base configured nothing is away from home, so there are no trip stories, and the run's
`trip-stories` record says so. A trip film is already one journey: its days stay the stories.

**A recurring activity is one thread, in the context of the film.** Four Saturdays at the same
pool are not four occasions in a year's film. After the weighing, stories at the same place on
separate days are put to the reader as a possible thread when its own words link them. Near home
that takes an activity: the same activity in their titles, or an activity word the film uses there
more than anywhere else. Away from home the name the reader gave them is enough, so repeated visits to the same
garden are asked about. The home place itself never holds a thread, because its days are the film,
and a place alone never links two days; neither does a name, a relation, a time of day or a word
like "moments", "life" or "stay". The reader is then asked, with the
film's dates and contract, which of them are one recurring activity and which are steps worth showing
apart, and a confirmed thread becomes one story with the weight of its heaviest day. A film longer
than about 18 months is read in calendar years, and keeps one thread per year, so a child getting
better at swimming still shows the progress. Trip films and subject memories ask nothing: their days
are already their stories.

**The audience is FAMILY.** A shirtless baby is ordinary family content and can be included, and so
is a parent holding a baby in a pool or a baby's swimming lesson: swimming is not bathing. Eight
findings are not, at any audience, and a carrier that draws one is replaced rather than shown:
breastfeeding or expressing milk, bathing, toileting or changing, intimate hygiene, graphic medical
procedures, identifying records, sexual content, adult changing. The model is told that newborn care
is ordinary family content, which keeps it from filing a bath as something worse, and the code holds
all eight out of the cut regardless of what the model was told. The gate judges the finished cut
rather than every picture the editor considered: one verdict per carrier, plus one for each
replacement a refusal pulls in from the same moment.

**A day's title claims only what the evidence shows.** A special day's title is checked against the
evidence lines it was written from, and an unsupported claim is dropped rather than printed. Trip
titles are a different path, written from dates and place names, with no such check.

**An episode reading names nothing the facts do not name.** Words on a banner, a shirt, a sign, a
screen or a poster name the thing they are printed on, never the day, the place or the event: a
festival poster in the background of one picture does not make the weekend that festival. A name
has to come from a fact line, and the Immich albums holding an episode's pictures are one of those
lines. If your album calls that weekend "Summer Festival 2022", the reading may call it that too,
and everything downstream (the period thesis, the story titles, the film title) inherits a name you
typed rather than one the model read off a banner.

**Refuse over fake.** A day the model could not name does not get a generic "Memories of June 12th"
card: it does not render. An empty special-days catalogue produces instructions for building one,
not an invented occasion.

**Emergent, not queried.** Nothing searches your library for "beach" or "dog". The
[special days catalogue](./cli/prepare.md#discover-days) is built by looking at what your days actually
contain and asking whether anything happened, which is how it finds the day that mattered with 30
photos. A day has to clear 20 photos and six active hours before the question is worth a model call.

## Judging content, not pixels

Descriptions do the discriminating that scores cannot. Two clips of the same cake a couple of
minutes apart are one moment: keep the better one. Two toasts at the same party are two moments.
Perceptual hashing cannot tell those apart; a sentence about each can. Time on its own settles
nothing: the five-minute `photos.burst_window_seconds` groups a burst, it does not say two things an
hour apart are separate moments.

The story reading comes before the duration allocation, so a short and a long memory can share an
understanding of the period while showing different amounts of it. More time lets an important story
show more distinct moments (arrival, the main activity, people together, how the day ended) rather
than making every extra frame a new event. Matching facts are reused: descriptions and model
decisions are cached by producer and input.

**Videos lead the pick.** This is a film. When a story chooses its moments, the ones a video
carries, or a Live Photo whose motion passed the check, are listed ahead of the stills, each with a
sentence saying what happens in it, and the reader is told to choose the video over a still of the
same moment. That sentence is written once per video at preparation by the caption server, from
three keyframes, so the reader never sees a video frame. On `no_captions` and `metadata_only` the
row carries the plain facts instead: how long the clip is, and how much a Live Photo moves. The pick is asked in two orders; when they disagree, the moment that moves
wins. That holds at every length: a 90-second memory gives most stories one picture, and that one
slot is still a choice between a video and a still. Only the question is reordered; the film still
plays in the order things happened. A chosen video holds about 6 seconds where a photo holds 4,
longer when someone is mid-sentence at the cut. When the videos push the cut past its length, the
longest holds are shaved half a second at a time, never below 3.5 seconds or through a sentence; a
video is never dropped for being long.

**Motion reaches the pick.** Before the reader is asked anything, a story samples the moments its
grant can reach, and that sample used to be where the motion went. Three things carry it through
now. A moment that holds something playable is taken ahead of a still that would otherwise fill its
place. When the sample fills up anyway, on a story dense in favourites, it reaches for as many more
playable moments as the story has slots, appended rather than swapped in, so no favourite loses its
place and the spread of times and people already chosen is left alone. And when a capture group
holds both a still and a video of the same instant, the video takes the frame unless a favourite
claims it: a video carries no sharpness measurement, so on a tie of everything else it used to lose
to any still in the group.

Coverage is checked, not assumed. Required source and annotation coverage is verified before
selection, and an incomplete run is never reported as complete.

Large annual memories are read in batches. Each story-weighting batch keeps the period's account
and main-story context, with at most 60 stories in its initial request. If the possible main stories
would crowd out a batch, they are compared first; every story is still evaluated afterward. A batch
that returns missing decisions or repeatedly truncated text is split into smaller groups while
keeping parts of the same occasion together. All batches must finish before the decisions are used.

The shipped design (the source model, the annotation store and its banks, the six stages, the two
readings, the structure and story planners, carriers and durable attempts) is written up in
[Story-first selection](https://github.com/sam-dumont/immich-video-memory-generator/blob/main/docs/designs/2026-09-10-story-first-selection.md)
in the repository.

## Twins and near-duplicates

You held the shutter down. You imported the same clip twice. You shot the cake from two steps left.
None of that should cost three slots in a two-minute film. Sameness is decided in four places,
cheapest first, and only the first one is free of model calls.

**1. Bursts, on capture time and pixels.** Photos within `photos.burst_window_seconds` of each other
**and** within `photos.burst_hash_threshold` bits on an average hash of their Immich previews are
one burst, and only the best frame survives it.

```yaml
photos:
  burst_window_seconds: 300   # 0 disables burst de-duplication
  burst_hash_threshold: 8     # hash bits two frames may differ by
```

Both conditions are required: time alone would collapse a busy minute at a party, similarity alone
would merge the same kitchen photographed a month apart. A photo whose preview never arrived is
always kept, because redundancy is measured and never assumed. Measured on one real June library, 64
of 303 photos went, 21 % of the pool, in groups of up to five. It saves no model calls (captions,
heads and detectors are paid for the whole eligible period first); what it saves is a cut with five
near-identical frames in it.

**2. Neighbours, asked as a pair.** Two pictures side by side, two numbered tiles, judged in both
arrangements: only the two orders agreeing counts as one picture, so a verdict bought once is a
cache hit everywhere. Perceptual distance is the second vote, never the only one. Measured on 653
real pairs the model contradicted itself on 39, and only 4 of those were pixel-close: its
uncertainty lives on pixel-*distant* pairs. At a corroboration distance of 10 the rule reproduced
all 653 decisions exactly while removing 30 % of the calls, and the first changed decision appears
at 12. That 10 is a constant in the code, not a setting.

**3. Inside a story, while the cut can still change.** A story's second or third picture is kept
only if it does not look like one the story already holds. Trips and long stays are where this
matters: three one-day stories of a weekend away used to ship three near-identical selfies. The
question is step 4's repetition question, asked in both arrangements: a pair from the same 90-minute
episode is asked exactly as step 4 asks it, so the answer is shared, and a pair days apart is asked
with a premise that says so. A refused picture frees its slot for the story's next distinct moment,
or for the next story in line. A favourite is never refused for looking like a picture you did not
star, and when nothing else can take the slot the refused picture comes back, so this check alone
never makes a film short. A picture is compared with the frames its story
already holds around it: its own moment's, and the kept frame just before and just after it in time,
which is where a repetition lives. The check asks at most twice as many pairs as the film has slots
and records every refusal in the run's `story-selection` record.

The same question lets a dense occasion fill its film. Five minutes of spacing inside a capture group
keeps a burst from taking several slots, and on a busy afternoon it also kept every moment after the
first of each group out of the cut: a 90-second special day with 27 usable pictures shipped four. A
film that is still short after every selection pass now spends its free slots inside the moments its
stories already show, in funding order: the moments the inventory found and no pick took, alternating
between capture groups, then up to three frames of each chosen moment. A frame gets in only when the
question confirms it looks different from the kept frames of its moment and the ones just before and
after it. Nothing unasked and no look-alike fills a slot this way, so a film of one repeated scene
stays short.

**4. The final film, over what actually shipped.** The pictures in the cut are checked against each
other again. A pair is nominated when any of three signals fires: hashes within 10 bits,
descriptions that read as the same thing (Jaccard over words of four letters or more, at 0.60), or
the same capture episode within the 90-minute window. That 0.60 is the knee of a measured curve over
1,124,250 real pairs from the cache: 0.60 collapses 33 pairs, 0.55 collapses 74, 0.50 collapses 135.
The count triples per step below it, which is where genuinely different shots start merging.
Nominated pairs are then asked as pairs, by the same question as step 2.

The episode signal is there because the other two miss the obvious case. Two frames of the same
minute on a dark bus, shot from slightly different angles, have distant hashes and get two
unrelated descriptions, so nothing ever put them side by side and both shipped. A pair that only
its episode nominated is asked a different question, whether the two show similar content so that
keeping one avoids repetition, and it always needs both arrangements to agree: the corroboration
distance of step 2 was measured on the same-picture question, so it buys nothing here. Pair work
stays inside the same fixed bound of twice the number of pictures in the cut, and a comparison the
bound cut short keeps both pictures and says so in the record.

Which one survives, in order: protected carriers, favourites, pictures with a known quality figure,
quality itself, capture time, then asset id. A favourited copy wins even at a lower resolution: you
flagged that one on purpose. Each removal writes which picture went, which kept its slot, the
hamming distance, and which signal nominated the pair.

There is no `duplicate_hash_threshold`. That key belonged to the retired clip scorer, and a config
file naming it starts normally and logs one warning instead of keeping a setting that quietly does
nothing.

## Editing without a language model

Set `advanced.editorial.reader: rules` (or leave it on `auto` with no `llm.model`) and the editor
runs the same six stages with a rule answering each question a model would answer. Same stages, same
records, same storyboard. It costs nothing in API fees and runs on a 4-core NAS.

| Question | Rule |
|---|---|
| Is this happening memory-worthy? | Remarkable when the day clears four times the median photographed day or the 75th percentile, whichever is higher, or when its pictures are away from the usual cities (or more than 10 km from `trips.homebase_*`). An album product is remarkable outright. Maybe when a favourite, a close family member, a video, or the only happening in a required partition is in it. Background otherwise |
| How do days group into stories? | A run of consecutive photographed days is a story; a day splits into two episodes when more than 90 minutes pass and the dominant place changes |
| What is the story called? | Templated from facts: the activity at the place, or the place, or the date. Never retitled, never joined |
| How much does a story weigh? | From the gate: remarkable seeds `minor`, maybe seeds `glimpse`, background gets nothing; three favourites raise a story to `major`. `dominant` comes from the thesis pass that names a central story, the same one the model path uses |
| Which pictures show a moment? | A capture group is a moment; the favourite wins it, then sharpness, then capture order. Thumbnail hashes collapse near-identical frames inside a group; they never merge two groups into one moment |
| Does a picture stand on its own? | From the facts on its line: a favourite stands; a document, a sensitive-content hit, a blurry, dark or blown-out frame is weak; a picture with people, an activity or a real venue stands |
| Who may see it? | Any flag from the detectors keeps a picture at family-only viewing. Nothing clears a flag except you, on the pool page |

Every answer stays inside the vocabulary the model path uses, so the planners downstream do not know
which reader spoke.

What you lose: the thesis (the page hides the quote rather than showing a templated one), an
editor's sentence under each picture (you get `<story>: <n> pictures at <place>` instead), moments
merged by content across capture groups, sampled duplicate review, the choice to play a Live Photo's
motion, and the ability to clear a flagged-but-innocent picture for sending.

Measured against the model editor's reference cut over the same periods, the rules reader kept 100 %
of the known occasions for a special day, on-this-day and album, 94 % for a person, 86 % for several
people, 67 % for a trip, and between 43 % and 62 % for a month, a season or a year. The per-type
table is on [Running modes](../deploy/running-modes.md#what-the-rules-cut-keeps-per-memory-type).

Rules need nothing beyond the app on `tier: metadata_only`. With `tier: no_captions` and
`immich-memories models fetch`, the two detectors and six context heads give the standing and
audience rules something to read. The Memory page's note under the title is keyed to the tier rather
than the reader, so a rules cut on `full` gets none; on `no_captions` it reads *Edited without
descriptions: picture content was classified, not read.*

Rules are a degraded mode, not an equal-quality alternative. Prefiltered requests (a person, an
album, one event, a trip) survive it well; broad recaps are where the model earns its cost.

## The stages, and what each one costs

The stage names are what the run reports: a row on the Memory page, a line in the terminal.

| Stage | What runs | Where it can run |
|---|---|---|
| **Reading dates, places and people** | The source model, then preparation per producer: previews, pixel facts, the encoder with six context heads, the two detectors, and on `full` one caption per picture and one motion sentence per video. Nothing banked is produced twice | previews over the network; captions remotable; heads, detectors and pixels on this box or the [inference service](../deploy/installation/inference-service.md) |
| **Reading event evidence: i/n** | Paged episode reading over the annotation lines, the cull asked inside each episode, with an `Albums:` fact line naming the Immich albums that hold the episode. Banked per group and evidence key | the reader |
| **Reading the period account** | The banked episode readings placed into day episodes, one page per calendar month, then one thesis over all of them. One bounded repair if malformed. Banked | the reader |
| **Building editorial cards** | One card per moment, rendered into the wall the planner reads | this box, cheap |
| **Editing the memory** | The structure and story planners: trip detection over the film's pictures, the memory-worthy gate, story weighing, the recurring-activity question, moment picks, standing gate, audience checks. Each a banked question, the gates asked in two orders. The moment inventory reads only the capture groups a funded story can spend a slot on, standing is asked in two packed rounds and banked per picture, and picture facts are observed for the cut. The pick reads each video's banked motion sentence; motion is measured for the chosen Live carriers | the reader; motion locally |
| **Validating selected source timing** | Intervals bound to their sources, duration realised | this box, cheap |

If the reader stops answering, the Editing stage reports *Waiting for the reader at host:port* and
retries three times before failing.

A cold cut pays for every picture never read and every reading of a period nobody has cut. A warm cut
over the same period is mostly the render: nothing in the period reading carries between calendar
months, so the bank answers a month it has already read, and a monthly cut after a yearly one asks
nothing again for that month. Measured on one reader and config, selection only: a 60-second
February 2024 cost 55 model calls and 3.7 minutes cold, and 40 seconds warm; a 10-minute year over
13,500 assets cost about 990 calls and about 70 minutes cold, and 11 minutes warm. A warm run asks
the model nothing at all, and what is left of it is the video work after the cut, playback
downloads, motion measurement and picture review. There is no depth knob and no shortlist at the
source: every eligible picture is prepared, because a picture the editor never saw is one it cannot
weigh.
The levers are putting the caption server and the reader where they are fast, preparing a library
ahead with [`prepare`](./cli/prepare.md), and keeping the cache. If the render is
the slow part none of that helps: that is decode, scale, blend and encode, and the levers are a
hardware encoder, a lower resolution and fewer clips.

### What overlaps, and what cannot

Reading is mostly a queue of one, and every pick below reads the stages above. Three places hold
independent questions: the period account is read one calendar month per page and no month sees
another, the moment inventory of one event knows nothing about the next event's, and the worthiness
and standing gates ask in blocks of twelve that do not see each other. Those are what
`advanced.llm.reader_concurrency` overlaps, and nothing else in the reading can be made to overlap
by raising it.

```mermaid
flowchart TB
    packs["Event evidence, pack by pack"]
    packs --> pages["The period account, one page per calendar month:<br/>no page sees another"]
    pages --> synthesis["The synthesis: one thesis over every episode"]

    synthesis --> worthy
    subgraph worthy["Memory-worthy gate: happenings in blocks of 12, each block asked in two orders"]
        direction LR
        w1["block 1"] ~~~ w2["block 2"] ~~~ wn["block n"]
    end

    worthy --> weigh["Story weighing"]
    weigh --> inventories
    subgraph inventories["Moment inventories: one job per event"]
        direction LR
        i1["event 1"] ~~~ i2["event 2"] ~~~ iN["event n"]
    end

    inventories --> standing
    subgraph standing["Standing gate: pictures in blocks of 12, two orders again"]
        direction LR
        s1["block 1"] ~~~ sn["block n"]
    end

    standing --> picks["Picture picks, audience checks, duration"]
```

Unset, the concurrency limit is 1 for a model on your own machine or network and 4 for a public
host, because a local server is one process in front of one accelerator and four requests there
queue instead of overlapping.

## Render

`generate_memory()` takes over from the plan under a file lock.

- **Originals** of the selected sources are downloaded (3 workers by default,
  `analysis.download_workers`) and each interval trimmed with FFmpeg. A video always plays. A Live
  Photo plays its video only when its measured motion reached 1.5; below that its photograph is
  held, and the pick was offered it as a still. Live companions of different sizes
  are fitted to a common frame without stretching or changing their selected timing.
  ProRes MOV clips keep their original video, HDR metadata and audio during trimming.
- **Photos** render frame by frame in Python: Ken Burns is a `cv2.warpAffine` per frame at 30 fps
  for the seconds granted, two of them on the blurred-background path. HEIC decode and gain-map HDR
  happen here, and sources are capped at 1.5x the output size.
- **Title screens** render on the GPU when the kernel library initialises, PIL otherwise, and encode
  with the final video's encoder.
- **Assembly and encode** stream: one FFmpeg decode per clip at a time, crossfades blended into one
  preallocated buffer, raw frames piped into one encode process. Memory stays flat with clip count,
  which is what makes 4K output possible.
- **The finished film is decoded once**, just before it gets its final name. It stays under its
  `.assembling` name through the music mix. After the encode and after the mix, `ffprobe` reads
  container, codec, pixel format, colour and duration without decoding and compares them with the
  encoding plan. Then FFmpeg decodes every video frame of the mixed film, and a decode error fails
  the run. That decode may take as long as the encode did (15 minutes at least) and logs its
  position once a minute. The upload reads the metadata again and reuses the decode unless the
  file changed since. A film that fails stays on disk, and the error starts with its path.

Encoder selection is a real probe: NVIDIA, Apple, QSV, VAAPI, each having to encode one 256x256
frame before it is used. A hardware encoder that fails mid-run is retried once in software with the
same codec. Assembly uses a hardware **encoder** and a software **decoder**: a GPU speeds up the
write side, not the read side. See [Hardware acceleration](../deploy/hardware.md).

Music resolution walks a fixed chain: an explicit file, then a generator if one is enabled, then a
bundled track chosen by mood. A failed generator falls through and the run is told. Delivery, the
optional upload back to Immich, is non-fatal on failure: the video stays on disk, the run is marked
delivery-pending, and secrets are scrubbed from the logged error.

## What a cut leaves behind

Every attempt is durable under `<cache>/editorial-runs/<key>/attempts/<id>/`, with
`latest-attempt.private.json` pointing at the newest. Inside: the status (stage, request, outcome,
duration realisation, and a lease that tells an interrupted run from a slow one), the plan (thesis,
stories with weights, every carrier with its reason, which is what `runs story` reads), the render
projection, the selection trace that `runs why` reads, every model request and answer, and the
evidence hashes per episode.

The status also counts the run's model calls per stage family, under `calls_by_stage`, with how many
of them the bank answered and how long each family took, so `jq .calls_by_stage status.private.json`
says where one cut spent its calls. Every carrier the editor cut carries the reason it was cut, the
timing trim included, and the selection sheet prints it.

| Cache | Location | Holds |
|---|---|---|
| Annotation store | `~/.immich-memories/cache/annotations.sqlite` | every fact per picture and producer; the episode, period, cull and judgment banks |
| Structure banks | `~/.immich-memories/cache/structure-banks/` | the memory-worthy and standing votes, thumbnail hashes, demanded motion |
| Attempts | `~/.immich-memories/cache/editorial-runs/` | one directory per cut |
| Downloaded videos | `~/.immich-memories/cache/video-cache` | 10 GB, 7 days |
| Immich previews | `~/.immich-memories/cache/thumbnails` | 10 GB |
| Clip previews | `~/.immich-memories/cache/preview-cache` | 2 GB |
| Run database | `~/.immich-memories/cache.db` | run history |

Facts are keyed by producer version, so changing a version names a new fact generation and the next
cut produces it. Readings are keyed by the exact request, prompt included.

The cull bank is the one that follows a picture out of the memory it was judged in: a photographed
receipt is a receipt in every cut that could reach it, so the verdict is remembered per picture
rather than per cut. It holds what the reading kept as well as what it rejected, and it is keyed by
that reading, so a release that changes the episode prompt retires the old verdicts instead of
piling them on the new ones. A picture the reading never looked at, because its episode failed to
read, is remembered as neither. When a standing verdict removes a picture the current reading would
have kept, the trace says so by name, and the newer answer replaces the old one for the next cut.
A star still outranks anything in the bank, and [`runs why`](./cli/runs.md) prints the reason.
