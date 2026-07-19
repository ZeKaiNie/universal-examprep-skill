# exam-cram — en student-facing pack

> Wording only; behavior lives in [skills/exam-cram/SKILL.md](../../../skills/exam-cram/SKILL.md).

## Student-facing Output

Use the canonical vocabulary from [language policy](../../../docs/language-policy.md): Current stage / What this tests / Standard answer steps / Common pitfalls / 3-minute mnemonic / Your turn / Recorded to the mistake archive / Must-memorize / Worked example / Worked solution / Takeaway / Mistake replay / Confusion restate / Prep workspace initialized.

- 🟢 From your materials
- 🟡 AI-supplemented — may differ from what your teacher taught
- ⚠️ AI-generated answer — not from your teacher or textbook

English is English-only. For `bilingual`, compose each Chinese block followed immediately by its pure-English `> EN:` mirror; do not invent a third template or omit either side. Source blocks, receipts, notebook, and Guide content follow the same rule.

Persist substantive work to `notebook/` first, mirror wrong items to `mistakes/`, then reply with a 3–5-line digest and `Full walkthrough: notebook/chNN.md#<anchor> | Index: notebook/index.md`. Only the progress panel, help card, and one-shot hint are chat-only. Web clients use a text breakpoint; failed writes are reported and the full content stays in chat.

At opening, confirm the registered course and show the absolute workspace path; never create a hidden default workspace.

Then show one material-processing choice: “Lightweight on-demand (recommended)
or full knowledge-base build?” Default to lightweight when the learner accepts the
recommendation, is urgent, or gives no answer. Explain once that lightweight reads
only the current-phase slice (at most eight pages), keeps distinct figure prompt /
answer crops and progress state, and creates no Study Guide. An unfinished batch
may be abandoned with a reason and replanned; taught progress cannot be abandoned.
Full performs the complete structured build. Do not mix this choice with chat/visual
artifact output; a saved visual preference is dormant/effectively chat in lightweight.

Do not make answer-explanation isolation an ordinary startup question. At full-v2
Guide entry, first perform a host-capability handshake. If official host capabilities
verifiably provide a fresh independent child context and can restrict both input and
tools to one exact item, use the internal isolated subagent by default after one notice
that it consumes extra model quota and time; it needs no API key and makes no external
upload. If any capability is incomplete, including tool restriction, or cannot be
confirmed, use `ordinary` and explain why. An external Provider is a fallback only
when the learner explicitly names it; retain the two consents for Provider/API billing,
retention/privacy, exact item/image scope, call count, current pricing, and upload.

Do not add teaching cadence to the required opening question. If the learner asks
for one-question pacing, explain once that the stored choice is `step_by_step` but
is effective only in full mode while questions are allowed; otherwise it is retained
as dormant and teaching uses batch cadence. “Continue” advances routing only and is
never evidence that an item was taught.
