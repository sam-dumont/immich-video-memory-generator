Give the opening title of a personal memory film about people. Language: {lang}.

Facts
Memory type: {memory_type}
People condition (every picture satisfies it): {condition}
{people_facts}
Span: {span}

Rules
- Say who the film is about the way the family itself would say it: first names, and the role the other people have FOR the subject (her grandparents, his mother, their aunt), worked out from the facts. Never "of the library owner".
- The subject is the person the film follows. A child among adults is the subject.
- Use the words the family uses at home (maman, papa, mamie, papy; mum, dad, grandma, grandpa), never the civil ones (mère, père, mother, father).
- Title: at most 40 characters. Subtitle: at most 50 characters or null; never a list of full names; never an age, a count or a span the facts do not state. Null beats a guess.
- Dates belong in the title only when the span IS the subject: a calendar year, a month, a season, a first year. A whole life so far, or a span that ends today, gets no dates.
- No generic openers such as "Souvenirs de", "Moments avec", "Memories of".
- The only proper nouns allowed are first names, place names, and the album's name when the album is what the occasion was called.

Return ONLY JSON: {"title": "...", "subtitle": "..." or null, "reason": "one sentence"}
