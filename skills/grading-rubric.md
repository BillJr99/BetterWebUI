---
name: Grading Rubric Helper
description: When the user wants to draft, refine, or apply a grading rubric for an assignment, paper, or project.
---

When this skill is loaded, help the user with grading rubrics:

1. If they're drafting a new rubric, ask 3-4 short questions to anchor it:
   - Course level (intro / upper-level / graduate)
   - Assignment type (essay, project, exam, presentation, …)
   - Total points or weight
   - 2-3 learning outcomes the assignment targets

2. Produce a rubric as a markdown table with these columns by default:
   `Criterion | Excellent | Proficient | Developing | Needs Work | Weight`.
   Use plain language, not jargon. Keep each cell to one or two sentences.

3. If the user pastes a student submission, score it against the rubric and
   produce: a short narrative, the table with circled levels, and a numeric
   total. Always note 1-2 specific strengths and 1-2 specific revisions.

4. Offer to save the rubric. If they say yes, write it to a file using the
   `write_file` tool (ask for the path or default to `./rubrics/<slug>.md`).
