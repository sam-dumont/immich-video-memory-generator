Give the opening title of a personal memory film. Language: {lang}.

Facts
Memory type: {memory_type}
Span: {span}
{occasion_facts}

Rules
- Place names below are English as the camera recorded them; write them as {lang} would (Cyprus → Chypre, Brussels → Bruxelles).
- A special day is named by what happened that day; an album by its name, reworded only when the name is a date or a code; a holiday by the holiday and the family; a month, a season or a year by what ran through it.
- People: first names and the words the family uses at home (maman, papa, mamie, papy; mum, dad), never civil terms (mère, père). Name people only when they are the point of the film.
- Title: at most 40 characters. Subtitle: at most 50 characters or null; never a list of names; never a fact that is not given. Null beats a guess.
- Dates in the title only when the span IS the subject (a calendar year, a month, a season). A single day gets no dates.
- No generic openers such as "Souvenirs de", "Moments avec", "Voyage en", "Échos de", "Memories of".

Return ONLY JSON: {"title": "...", "subtitle": "..." or null, "reason": "one sentence"}
