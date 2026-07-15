# exam-help — en student-facing pack

> Wording only. Behavior lives in [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md).

## Student-facing Output

### Four-step map

1. `exam-ingest`: build and validate source records, wiki, bank, and state; resolve blocked review items first.
2. `exam-tutor`: teach one chapter at a time and persist explanations; before structured completion, validate/import the full typed Guide.
3. `exam-quiz`: select and grade bank questions only.
4. `exam-review` + `exam-cheatsheet`: revisit mistakes/confusions; render a printable sheet only when authorized.

### Choices and files

- Modes: from scratch, start from a chapter, or fill gaps. Time tiers: ≤1 day, 1–3, 3–7, or >7 days. Only an explicit no-questions request suppresses interaction and caps completion at `covered_unverified`.
- `artifact_mode=chat` is the safe default: keep conversation/state/notebook work and **do not automatically build chapter HTML/PDF**. Explicit `visual` requests typed Guide → render → receipt → all-page QA; a one-shot request does not change the stored preference. Never guess a subscription or install silently.
- `.ingest/` is build/review truth; `study_state.json` is progress truth and `study_progress.md` its generated view; `references/wiki/chN_*.md` is the chapter source; `references/quiz_bank.json` is the only quiz source; `notebook/` is durable teaching; `study_guide/` contains gated derivatives.
- Ingestion exit `0` means ready or usable-with-named-gaps, `10` means content blocked, and other nonzero means an operation failure.

### Quiz and source card

Types: `choice`, `subjective`, `diagram`, `fill_blank`, `true_false`, `code`.

- 🟢 From your materials
- 🟡 AI-supplemented — may differ from what your teacher taught
- ⚠️ AI-generated answer — not from your teacher or textbook

Never invent a checkpoint or disguise AI content as course evidence. See [language policy](../../../docs/language-policy.md).
