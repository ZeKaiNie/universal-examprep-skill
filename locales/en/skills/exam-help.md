# exam-help — en student-facing pack

> Wording only. Behavior lives in [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md).

## Student-facing Output

### Four-step map

1. Start in lightweight on-demand processing (recommended), or explicitly choose full ingestion when a complete knowledge base is worth the extra setup.
2. `exam-tutor`: teach one current page/chapter slice at a time and persist full explanations; only full mode enters typed Guide authoring.
3. `exam-quiz`: select and grade bank questions only.
4. `exam-review` + `exam-cheatsheet`: revisit mistakes/confusions; render a printable sheet only when authorized.

### Choices and files

- Modes: from scratch, start from a chapter, or fill gaps. Time tiers: ≤1 day, 1–3, 3–7, or >7 days. Only an explicit no-questions request suppresses interaction and caps completion at `covered_unverified`.
- `processing_mode=lightweight` is the default/recommended choice: inspect only current-phase PDF pages or one definitely single-frame PNG/JPEG/BMP, with at most eight primary pages and one active batch. Contact sheets group up to four pages at roughly 768 px/tile and are overview-only. Exact external answer pages use `register-answer-dependency`; dependency pages are locator/detail context, while only source-qualified target-answer crops enter solution calls. All canonical evidence is PNG under `.lightweight/assets/`, and figure prompt/answer crops stay distinct. Only receipt-backed `abandon --reason` closes an unfinished batch; `replace-taught --reason` retains a taught predecessor/event as superseded history and plans its exact-slice successor. Keep the state machines and generate no Study Guide/PDF. Explanations remain detailed. Explicit `full` opens the complete ingestion/review route.
- `artifact_mode=chat|visual` is separate. Full processing does not silently request a PDF; only explicit full + visual enters typed Guide → render → receipt → all-page QA. A saved visual preference is dormant/effectively chat in lightweight. Never guess a subscription.
- `answer_explanation_mode=ordinary|isolated` is separate too. Default `ordinary` still gives every Guide item a detailed beginner-first answer explanation, but makes no second Provider call and claims no context isolation. `isolated` is an off-by-default, full-v2-only extension with two consents: Provider/API-billing and retention/privacy disclosure before no-upload planning, then the exact plan's item/image scope and count plus a current-pricing estimate before upload. A GPT model, subscription, API key, full, or visual never enables it automatically.
- `preferences.interaction_style=batch|step_by_step` is an optional full-teaching cadence, not another startup choice. Stored step mode is effective only in full with `no_questions=false`; otherwise it is retained but dormant and effective cadence is batch. “Continue” is navigation, while completion needs the marked walkthrough plus its atomic `record-taught-example` binding. Older unbound teaching IDs remain valid batch history; bound IDs remain live-checked after cadence changes.
- `.lightweight/session.json` is on-demand page-batch truth; `.ingest/` is full-mode build/review truth; `study_state.json` is progress truth and `study_progress.md` its generated view; `references/quiz_bank.json` is the only scored-quiz source; `notebook/` is durable teaching.
- Ingestion exit `0` means ready or usable-with-named-gaps, `10` means content blocked, and other nonzero means an operation failure.
- Lightweight completion requires every current unsuperseded batch taught with notebook/progress bindings and can reach `covered_unverified`; `verified` requires two revision-bound checkpoints (one pass) from the unchanged quiz bank that pre-existed initialization. Startup records only an immutable stat baseline; absent-at-init, drifted, or legacy-unbound banks/checkpoints do not qualify. Routine health checks use metadata/physical identity only; exact hashes run at transitions/completion or explicit `status --verify-live`. Older taught history is `unchecked_historical` until that phase resumes.
- MinerU, Docling, and LangGraph are explicit-named-request and remote/cloud-only; never download, install, or execute them locally.

### Quiz and source card

Types: `choice`, `subjective`, `diagram`, `fill_blank`, `true_false`, `code`.

- 🟢 From your materials
- 🟡 AI-supplemented — may differ from what your teacher taught
- ⚠️ AI-generated answer — not from your teacher or textbook

Never invent a checkpoint or disguise AI content as course evidence. See [language policy](../../../docs/language-policy.md).
