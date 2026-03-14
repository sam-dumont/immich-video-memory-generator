# Title Generation Prompt

Generate a title for a personal memory video. Language: {lang}.

## Context
Memory type: {memory_type}
Dates: {start_date} to {end_date} ({duration_days} days)
{context_lines}

## Rules
- Write title and subtitle in {lang}. Be creative, varied, and specific to THIS trip.
- NEVER start with generic formulas like 'Échos de', 'Voyage en', 'Découverte de'.
- Title: max 50 chars. Short, punchy, evocative. Use the region name, not the country.
- Subtitle: max 50 chars. One short phrase adding context. Can be null.
- Good: 'Sous le soleil de Crète', 'La Saxe à pied', 'Oléron, été sans fin'
- Bad: 'Échos de X', 'Voyage en X', 'Une semaine de découverte en X'
- Never use 'weekend' for trips longer than 4 days.
- For non-trip memories (year, person): focus on the people or the time period.

## Trip Pattern Classification
Analyze the daily location clusters to determine the pattern:

**base_camp**: ONE location appears on most days (you return there each night)
  → e.g. Ville Sur Sarre on days 1,2,3,4,5,7,8 with excursions to Cogne, Aosta
  → map_mode: excursions

**multi_base**: 2-3 locations each appear on MULTIPLE consecutive days
  → e.g. Nicosia on days 1-5, then Geroskipou on days 6-10
  → map_mode: overnight_stops

**road_trip**: different location EACH day, covering large distances (>30km/day)
  → e.g. Castiglione day1, Bagnoregio day2, Ancona day3
  → map_mode: overnight_stops

**hiking_trail**: like road_trip but short distances (<30km/day), progressive movement
  → e.g. Hohnstein day1, Bad Schandau day2, Sebnitz day3 (towns 5-15km apart)
  → map_mode: overnight_stops

## Output Format
Return ONLY valid JSON (no explanation, no markdown, no thinking):
{"title": "...", "subtitle": "..." or null, "trip_type": "..." or null, "map_mode": "..." or null, "map_mode_reason": "..." or null}
