# LLM Title Generation + Trip Classification

**Date:** 2026-03-14
**Status:** Approved

## Problem

Title screens use hardcoded templates ("TWO WEEKS IN X, SUMMER 2025") that are rigid, English-centric, and don't adapt to trip patterns. Trip map mode (intro/outro, excursions, overnight stops) must be selected manually. The LLM already analyzes clips — it should also generate context-aware titles for all memory types.

## Design

### 1. Generic LLM Text Query Utility

**File:** `src/immich_memories/analysis/llm_query.py`

```python
async def query_llm(
    prompt: str,
    llm_config: LLMConfig,
    temperature: float = 0.3,
    max_tokens: int = 500,
    timeout_seconds: int = 30,
) -> str:
```

- Supports Ollama and OpenAI-compatible providers (same as mood analyzer)
- Text-only: no images, no vision
- Reuses existing `LLMConfig` (provider, base_url, model, api_key). The user's omlx (mlx-vlm) handles both vision and text prompts via the same OpenAI-compatible API.
- Returns raw response string; caller parses
- Shorter default timeout (30s) than VLM analysis — text prompts are small
- Raises on failure (caller handles fallback)

### 2. Title Generation with LLM

**File:** `src/immich_memories/titles/llm_titles.py`

**Input context built from:**
- Memory type (trip, person, year, month)
- Date range, duration
- Top 5-10 VLM clip descriptions (`llm_description` from analysis cache)
- Person names (if person memory)
- Trip overnight bases with night counts, cities, countries
- `smartInfo` object labels (e.g., "dog", "beach", "hiking")
- Target locale code (e.g., `"fr"`) — `"auto"` resolved via `detect_system_locale()` before prompt construction, never passed literally to the LLM

**Output:**
```python
TripType = Literal["multi_base", "base_camp", "road_trip", "hiking_trail"]
MapMode = Literal["title_only", "excursions", "overnight_stops"]

@dataclass
class TitleSuggestion:
    title: str              # "Une Semaine en Bretagne"
    subtitle: str | None    # "De Brasparts a la Cote d'Emeraude"
    trip_type: TripType | None
    map_mode: MapMode | None
    map_mode_reason: str | None  # "Two bases with day trips"
```

**Prompt structure** (includes few-shot examples for reliable output from small local models):
```
You are generating a title for a personal memory video.
Language: {locale_full_name}

Memory type: {type}
Dates: {start} to {end} ({duration} days)
Locations: {bases with night counts}
Country: {country}
People: {person names}
Clip descriptions: {top N VLM descriptions}
Objects detected: {smartInfo labels}
Overnight pattern: {N bases, N excursions}

Return ONLY valid JSON with these keys:
- title: short, evocative title (max 60 chars)
- subtitle: optional second line (max 80 chars), null if not needed
- trip_type: one of "multi_base", "base_camp", "road_trip", "hiking_trail", or null
- map_mode: one of "title_only", "excursions", "overnight_stops", or null
- map_mode_reason: one sentence explaining why you chose this map mode

Example for a trip memory:
{"title": "A Week in Bretagne", "subtitle": "From Brasparts to the Emerald Coast", "trip_type": "multi_base", "map_mode": "excursions", "map_mode_reason": "Two bases with day trips from each"}

Example for a person memory:
{"title": "Alice & Emile Through the Years", "subtitle": "2019 - 2025", "trip_type": null, "map_mode": null, "map_mode_reason": null}
```

**Response parsing** (following `mood_analyzer.py` pattern):
- Strip markdown code block wrappers (```json ... ```)
- Parse JSON, validate each field against allowed values
- Reject unrecognized `trip_type`/`map_mode` values (set to `None`)
- Sanitize title/subtitle: strip control chars, cap length at 80/120 chars
- On any parse failure: log warning, return `None` → caller uses template fallback

**Fallback:** If LLM is unavailable or returns invalid JSON, fall back to existing template-based title generation (`generate_title()` in `text_builder.py` for general types, `generate_trip_title()` in `_trip_titles.py` for trips). No error shown to user.

### 3. SmartInfo in Asset Model

**File:** `src/immich_memories/api/models.py`

Add to Asset model:
```python
@dataclass
class SmartInfo:
    objects: list[str] | None = None

@dataclass
class Asset:
    ...
    smart_info: SmartInfo | None = None
```

Immich's `/api/search/metadata` returns `smartInfo` when present. May need `withSmartInfo` parameter depending on Immich version — verify during implementation and update search client if needed.

### 4. Pipeline Integration

**When:** After analysis phase (Step 2 to 3 transition), once VLM descriptions and overnight stops are available.

**Flow:**
1. `SmartPipeline.run()` completes (clips analyzed, overnight stops detected)
2. `generate_title_with_llm()` called with all context
3. `TitleSuggestion` stored in `AppState.title_suggestion: TitleSuggestion | None`
4. Step 3 UI shows generated title + subtitle in editable fields
5. Locale dropdown (en/fr/auto) triggers regeneration
6. "Regenerate" button (uses temperature 0.7 for variety)
7. Step 4 uses the approved title
8. `TitleSuggestion` persists in `AppState` across Step 2↔3 navigation; cleared only when clip selection changes

**Integration with TitleScreenGenerator:**
- Add `title_override: str | None` and `subtitle_override: str | None` to `TitleScreenSettings`
- When set, `TitleScreenGenerator.generate_title_screen()` uses them directly instead of calling `generate_title()`
- Step 4 populates these from the approved `TitleSuggestion`

**What gets replaced:**
- `generate_trip_title()` and `generate_title()` become fallback-only
- LLM title is primary when LLM is configured
- Template path remains for users without LLM

### 5. Locale Support

- Leverages existing `i18n.py` locale system (en, fr, auto)
- `"auto"` resolved to concrete locale via `detect_system_locale()` before prompt
- Locale passed as full language name in prompt ("Language: French")
- UI dropdown in Step 3 to override locale
- LLM generates titles in the target language natively (not translated)

### 6. Clip Distribution by Trip Segment

Once overnight stops divide a trip into segments (bases + excursion days), clip selection should ensure **proportional coverage** across all segments.

**Rule:** Each segment (base or excursion day) gets a clip budget proportional to its duration. A 4-night base gets ~4x the clips of a 1-night excursion. No segment should be unrepresented.

**Implementation:** After `detect_overnight_stops()` returns bases, tag each clip's date to its segment. During `SmartPipeline` refinement phase, enforce minimum 1 clip per segment and distribute remaining budget proportionally by night count.

**Future (photo support):** Same distribution logic applies to photos — each segment gets proportional photo slots.

**Note:** This is a SmartPipeline change, not a title generation change. Included here because it directly depends on the overnight stop detection that also feeds the title LLM. Could be split into a separate spec if preferred.

## Scope Boundaries

**In scope:**
- `query_llm()` utility
- Title generation for all memory types (trip, person, year, month)
- Trip type classification + map mode recommendation
- `smartInfo` parsing in Asset model
- UI: editable title fields, locale dropdown, regenerate button
- Clip distribution by trip segment
- Template fallback

**Out of scope (separate specs):**
- Photo support in memories (plain/crop/Ken Burns)
- Immich tags API integration
- Activity-based memory types
- Additional locales beyond en/fr -> NOT TRUE, WE SHOULD SUPPORT ANY LOCALE BUT AT LEAST THE BIG ONES THAT USERS WILL HAVE SET IN THEIR IMMICH
