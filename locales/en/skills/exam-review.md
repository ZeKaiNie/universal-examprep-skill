# exam-review — en student-facing pack
> This file is the en language pack for student-visible wording; behavior logic lives in [skills/exam-review/SKILL.md](../../../skills/exam-review/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
- **Mistake replay**: Last time you missed this one on "…". Do the exact same question again — this time keep your eye on …. Get it right and I cross it off the mistake archive (status set to `已订正`, corrected).
- **Confusion restate**: You previously got stuck on the concept "…". Explain it in your own words: what it is, and why it works that way. Explain it clearly → marked `已回顾` (reviewed); still fuzzy → I explain it once more and keep it `待回顾` (to review).
- **Gap summary**: Still not nailed down — mistakes: …; confusion points: …. These items make `exam-cheatsheet` prioritize their knowledge points when picking the hard worked examples.
- **Review conclusions persist**: the not-yet-mastered list and each replay's conclusion are written into the notebook `notebook/chNN.md` first — they never live only in chat; the reply is a digest ending with the fixed line: Full review: `notebook/ch05.md#review-01` | Index: `notebook/index.md`. If the notebook write fails, that is stated plainly and the full list is delivered in chat instead.
