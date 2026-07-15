# exam-quiz — en student-facing pack

> Student wording only; [skills/exam-quiz/SKILL.md](../../../skills/exam-quiz/SKILL.md) is the behavioral source of truth.

## Student-facing Output

- **Correct:** ✅ Correct. What this tests: … One pitfall: ….
- **Partly correct:** 🟡 Halfway there—you covered “…”, but missed “…”.
- **Wrong:** ❌ Here is the logic gap: …. Standard answer steps: 1. … 2. ….
- **Two wrong in a row:** Would you like to ① view a hint ② skip and archive ③ try again? On ②: Recorded to the mistake archive—we will review it before the exam.
- End every graded item with `Question source: hw02.pdf p.3 (homework) | Answer source: hw02_sol.pdf p.1 | 🟢 From your materials`. The final label is one full canonical sentence: 🟢 From your materials / 🟡 AI-supplemented — may differ from what your teacher taught / ⚠️ AI-generated answer — not from your teacher or textbook. An AI answer also carries the full ⚠️ sentence in its answer/explanation heading. Missing metadata says Source unknown or Source page unknown; never invent it.
- Before an out-of-scope item: ⚠️ Temporarily overriding your <scope> scope preference.
- Persist full feedback first; wrong/skipped feedback also mirrors to `mistakes/`. End the digest with `Full feedback: notebook/ch03.md#q21 | Index: notebook/index.md`. If writing fails, say so and give the full feedback in chat.
