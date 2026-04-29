---
name: Research Citations
description: When the user needs help formatting citations, building a reference list, or converting between citation styles (APA, MLA, Chicago, IEEE).
---

When this skill is loaded:

1. Ask which style the user needs (APA 7, MLA 9, Chicago author-date,
   Chicago notes-bibliography, IEEE) if not already clear.

2. Accept input in any form: pasted text, DOIs, URLs, BibTeX, or a rough
   list. For DOIs/URLs, do NOT fabricate — if you're unsure of the
   metadata, say so and ask the user to confirm or paste the source.

3. Produce two outputs: (a) a properly formatted reference list, (b) a
   matching list of in-text citations the user can drop into prose.

4. Watch for common mistakes and call them out plainly:
   - Missing DOI or URL where the style requires one.
   - Hanging-indent vs. flat list (most styles want hanging indent).
   - "et al." misuse (varies by style and author count).

5. Offer to save the result. If yes, ask for a path and use `write_file`.
