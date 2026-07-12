# exam-quiz — en student-facing pack

> This file is the en language pack for the skill's student-visible wording; behavior logic lives in [skills/exam-quiz/SKILL.md](../../../skills/exam-quiz/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
Grading feedback is short, specific English — name the tested point first, then the improvement. Same numbered blocks as the zh pack ([`../../zh/skills/exam-quiz.md`](../../zh/skills/exam-quiz.md)), zero CJK outside code spans, fixed phrasing verbatim from the EN canonical vocabulary in [`docs/language-policy.md`](../../../docs/language-policy.md):

- **Correct**: ✅ Correct. What this tests: … (one sentence). One pitfall worth noting: ….
- **Every graded item's feedback ends with the fixed source line** (ASCII `|`): `Question source: hw02.pdf p.3 (homework) | Answer source: hw02_sol.pdf p.1 | 🟢 From your materials` — the trailing label is the FULL text of one of 🟢 From your materials / 🟡 AI-supplemented — may differ from what your teacher taught / ⚠️ AI-generated answer — not from your teacher or textbook, never the emoji alone. When the answer is AI-supplied, the trailing label is the FULL sentence ⚠️ AI-generated answer — not from your teacher or textbook (never the emoji alone) and the answer/explanation block title carries the same ⚠️ sentence. Missing source metadata → write Source unknown (or Source page unknown), never a fabricated filename or page.
- **Partially correct**: 🟡 Halfway there — you covered "…", but missed the "…" step; add it for full marks.
- **Wrong**: ❌ Here is the error: … (point out the logic gap). Standard answer steps: 1. … 2. …. Re-read the item's explanation.
- **Two wrong in a row**: Would you like to ① view a hint ② skip and archive the wrong item ③ think again? On ② reply with the receipt: Recorded to the mistake archive — we sweep it again before the exam.
- **AI-generated item/answer**: ⚠️ AI-generated answer — not from your teacher or textbook; reference only, verify against your teacher or textbook.
- **Scope override**: emit the verbatim line ⚠️ Temporarily overriding your <scope> scope preference BEFORE the first out-of-scope item (substitute the active scope name).
- **Feedback persists first**: each graded item's full feedback is written to the notebook `notebook/chNN.md` before the reply; wrong or skipped items are mirrored into `mistakes/chNN.md` as well (the progress-state row is still recorded as usual). The chat receipt ends with the fixed line: Full feedback: `notebook/ch03.md#q21` | Index: `notebook/index.md`. If the notebook write fails, that is stated plainly and the full feedback is delivered in chat instead.
