# exam-cram — en student-facing pack

> This file is the en language pack for the skill's student-visible wording; behavior logic lives in [skills/exam-cram/SKILL.md](../../../skills/exam-cram/SKILL.md) (the control layer, single source of truth).

## Student-facing Output

In `English` mode (and the `> EN:` side of `双语`) use the EN canonical vocabulary on the student side (Current stage / What this tests / Standard answer steps / Common pitfalls / 3-minute mnemonic / Your turn / Recorded to the mistake archive / Must-memorize / Worked example / Worked solution / Takeaway / Mistake replay / Confusion restate / Prep workspace initialized), pinned verbatim in [`docs/language-policy.md`](../../../docs/language-policy.md); in `中文` mode use the zh canonical vocabulary (zh pack: [`../../zh/skills/exam-cram.md`](../../zh/skills/exam-cram.md)). In `English` mode the provenance markers appear verbatim as:

- 🟢 **From your materials**: sourced directly from what the student uploaded; high confidence.
- 🟡 **AI-supplemented**: content the materials do not cover, filled in from the AI's own knowledge — each labelled "🟡 AI-supplemented — may differ from what your teacher taught" (the teacher prevails).
- ⚠️ **AI-generated answer**: the teacher marked the question but gave no answer, so the AI answered — each labelled "⚠️ AI-generated answer — not from your teacher or textbook".

Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); the persisted `language` switches it per the control layer's Output Contract dispatch rule (each mode single-language pure).

Bilingual composition rule (`language=bilingual`; display alias `双语`): NEVER a third template set — compose zh+en per block: the zh unit first (pure Chinese, zh canonical forms), an `> EN:` mirror line immediately after (pure English, EN canonical vocabulary); each side stays single-language pure and each anchor appears once per side. The progress panel, receipts, and source blocks mirror line-by-line the same way. In the `≤1天` tier each side may use shorter wording, but no bilingual block may lose either language; durable notebook and Study Guide content remains complete zh+en.

**Persist first, then digest**: substantive replies (seven-step walkthroughs, grading feedback, confusion explanations, review conclusions, including casual concept answers) are written into the workspace notebook `notebook/` first (wrong items mirrored into `mistakes/`), and the chat reply is a 3-5 line digest plus one full-text link line (e.g. Full walkthrough: `notebook/ch02.md#q13` | Index: `notebook/index.md`); only the progress panel, the quick-reference card, and one-shot escape-hatch hints may stay chat-only. Pure web clients (no file I/O) keep the existing chat-only + text-breakpoint mode. If a notebook write fails, that is stated plainly and the full content is delivered in chat.

**Workspace placement**: on first activation the registry is consulted — no course registered yet means you are asked where your materials folder is (with a 30-second usage tour on offer); with registered courses you are asked which one to continue. No folder is ever created silently in a place you do not know about, and every session-opening progress panel carries one line with the workspace's absolute path, so you always know where your files are.
