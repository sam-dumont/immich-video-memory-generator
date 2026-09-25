<!-- title_people prompt v3 -->
Give the opening title of a personal memory film about people. Language: {lang}.

Facts
Memory type: {memory_type}
People condition (every picture satisfies it): {condition}
{people_facts}
Span: {span}

Rules
- Every name in the title comes from the facts above: a first name, a place, the album the film sits in. A town, a venue, an event or a person no fact names does not belong in the title. Reword the facts; never add to them.
- A place name above is English as the camera recorded it; write it as {lang} would (the English name becomes the name {lang} gives that same place; never a place the facts do not name). That is the only rewriting of a name allowed.
- Say who the film is about the way the family itself would say it: first names, and the role the other people have FOR the subject (her grandparents, his mother, their aunt), worked out from the facts. Never "of the library owner".
- The subject is the person the film follows. A child among adults is the subject.
- Use the words the family uses at home (maman, papa, mamie, papy; mum, dad, grandma, grandpa), never the civil ones (mère, père, mother, father).
- Sentence case: capitalise the first word and proper nouns, nothing else.
- Title: at most 40 characters. Subtitle: at most 50 characters or null; it may only state what a fact above states, so no age, count, place or span the facts are silent about; never a list of full names. Null beats a guess.
- Dates belong in the title only when the span IS the subject: a calendar year, a month, a season, a first year. A whole life so far, or a span that ends today, gets no dates.
- No generic openers such as "Souvenirs de", "Moments avec", "Memories of".

Return ONLY JSON: {"title": "...", "subtitle": "..." or null, "reason": "one sentence"}
