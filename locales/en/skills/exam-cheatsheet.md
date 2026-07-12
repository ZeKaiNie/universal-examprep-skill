# exam-cheatsheet — en student-facing pack
> This file is the en language pack for student-visible wording; behavior logic lives in [skills/exam-cheatsheet/SKILL.md](../../../skills/exam-cheatsheet/SKILL.md) (the control layer, single source of truth).

The last-hour-before-the-exam quick-recall cheat sheet: four fixed sections, repeated per chapter (concise and practical; AI-supplemented or AI-generated lines are labeled inline):

```text
[Must-memorize conclusions & formulas]
- ...
- ... (🟡 AI-supplemented — may differ from what your teacher taught — label only lines the materials did not cover and the AI added)

[Worked example] (one hard worked example per key knowledge point; a figure-dependent item must actually show its question figure first — if it cannot be shown, swap to a self-contained item)
- Example: ...
  ![Question-side asset](references/assets/chNN_pXX_fig.png)

[Worked solution] (substitute the values into the formula: intermediate arithmetic may be skipped, but the base process must stay — which formula, what gets substituted, what comes out)
- ... (when the teacher/materials give no answer, label ⚠️ AI-generated answer — not from your teacher or textbook)

[Takeaway] (how to handle same-type or similar-stem questions: recognize the cue first, then apply the matching answer framework)
- ...
```

The code block above is only a **layout example** — when writing the real `cheatsheet.md`, image lines must be actual Markdown images (workspace-relative paths, so the student sees the figure the moment the md is opened); writing the path as plain text does not count as showing it, and if the figure cannot be embedded, swap to a self-contained item. Every bullet ends with a source arrow (e.g. `[→](mistakes/ch02.md#q13-venn-diagram-shading)`) that jumps back to the mistake notebook / notebook / wiki original — the anchor must be the REAL one printed by the `notebook.py add-entry` receipt (it includes the title slug; a bare `#q13` is a dead anchor the validator rejects).

Delivery phrasing (after compile + render):

> Your cheat sheet is compiled: `cheatsheet.md` (every bullet carries a source link back to your mistake notebook / notebook for verification).
> The print version is at your requested page count: `cheatsheet.pdf` (2 pages, margins pre-set to printer-safe distances).
> Say "squeeze it to 1 page" or "relax it to 3 pages" and I will re-fit the layout.

Degradation phrasing when no local browser is available:

> Generated `cheatsheet.html` — open it in a browser and press Ctrl+P, then "Save as PDF" (page count and margins are already set in the page).
