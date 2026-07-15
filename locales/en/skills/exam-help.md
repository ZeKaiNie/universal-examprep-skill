# exam-help — en student-facing pack

> This file is the en language pack for student-visible wording; behavior lives in [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
One screen to understand this exam-prep skill suite. Detailed rules live in the root `SKILL.md` and each subskill.

### Four-step workflow
1. **Build and validate the library** (`exam-ingest`): upload your materials → run the one-command course ingest → build structured source records, the wiki, quiz bank, and progress state → review any blocked items before studying.
2. **Teach** (`exam-tutor`): lazy-load chapter by chapter; metaphor-first concept teaching / key-problem walkthroughs / run the algorithm before drawing.
   Before completing a structured chapter, use `exam-study-guide` to validate/import its `profile=full` typed guide. The default `chat` mode stops there without HTML/PDF; standing `visual` or a one-shot request continues through the requested rendering and QA path.
3. **Quiz** (`exam-quiz`): draw questions from the quiz bank and grade; after two misses you get a hint / skip / archive.
4. **Review + cheat sheet** (`exam-review` / `exam-cheatsheet`): clear out mistakes and confusion points. An automatically reached final review under `chat` stays conversational; an explicit cheat-sheet request may compile `cheatsheet.md`, while PDF rendering requires `visual` or an explicit PDF/print request.

### Learning mode × time budget (settled in the first conversation)
- **3 learning modes**: `零基础从头讲` (teach from scratch: every knowledge point in order, linked problems easy to hard) · `某章起步补弱` (start from a chapter to patch weak spots: skim the chapters you already know, expand the ones you don't) · `查缺补漏` (gap-hunting: one harder problem per knowledge point across all chapters, expanding only where you're confused).
- **4 time budgets** (stacked on top): `≤1天` (≤1 day — skip opening clarification, preference-confirmation, and reflective follow-up questions; teach immediately, while standard-bank drills/checkpoints remain available to verify mastery) · `1-3天` (1-3 days — randomly ask back about confusion points) · `3-7天` (3-7 days — knowledge-point window system: assumed retained inside the window, asked back outside it) · `>7天` (>7 days — outside-window points are tested with hard problems). Only an explicit “no questions” request sets `no_questions=true`, suppresses all interactive questions, and caps the phase at `covered_unverified`.
- Legacy `normal/sprint/panic/mock` are deprecated; `set --mode` auto-migrates with a warning (panic→`零基础从头讲`+`≤1天`, sprint→`查缺补漏`+`1-3天`, normal/mock→`查缺补漏`).

### Artifact output mode (not a fourth required first-contact question)
The workspace field is `artifact_mode`, with only the canonical values `chat` / `visual`.
- **`chat` (economical conversation, default)**: missing legacy state and unknown values also resolve here. Teach in the conversation and keep normal notebook/state persistence; do not automatically build chapter HTML/PDF or a cheat-sheet PDF.
- **`visual` (visual study guide)**: persist it only after the student explicitly chooses it, via `update_progress.py set --artifact-mode visual`. It requests the typed manifest → HTML/PDF → receipt → all-page QA pipeline; a chapter is deliverable and phase-completable only after `artifact_ready=ready`. A failed route stays blocked/degraded. The final cheat sheet may also receive a printable PDF. Dependencies or external skills still must never be installed silently.
- An explicit one-shot HTML/PDF/print request may temporarily override `chat` without modifying the stored choice; `set --artifact-mode chat` returns to the standing economical path. The agent never reads or guesses the student's subscription plan and never switches based on a presumed quota.

### Workspace files
- `.ingest/` is the build/review truth: source versions, structured content units, typed review issues, replayable patches, and integrity hashes. Do not edit it by hand.
- `study_state.json` is progress truth and is read first after a restart; `study_progress.md` is its generated compatibility view. If that renderer retains canonical Chinese state vocabulary, the coach restates it in English instead of pasting it as English teaching prose.
- `references/wiki/chN_*.md` is the compiled per-chapter concept-teaching source · `references/quiz_bank.json` is the only quiz/grading source · `notebook/chNN.md` stores full explanations · `notebook/chNN.guide.json` is the validated typed chapter source · `study_guide/chNN.html|pdf`, `chNN.receipt.json`, and `qa/` are gated derived reading/QA artifacts.

### Ingestion readiness
The normal entry is `scripts/ingest_course.py`. Exit 10 means the files were compiled but content readiness is blocked; the coach must resolve the typed review queue and validate again before teaching or quizzing. `usable_with_gaps` is usable only after the remaining warnings are named; `ready` has no current validator errors or warnings.

### 6 quiz types
`choice` multiple choice · `subjective` subjective/calculation · `diagram` diagram drawing · `fill_blank` fill in the blank · `true_false` true/false · `code` code.

### Anti-hallucination & source labeling
- Teaching and grading stay within the wiki/quiz-bank scope; any explicit AI supplement is labeled, and an unsupported answer is declined rather than presented as course fact.
- 🟢 From your materials · 🟡 AI-supplemented — may differ from what your teacher taught · ⚠️ AI-generated answer — not from your teacher or textbook.
- No made-up questions when the quiz bank has a relevant one; AI-generated content is never disguised as teacher-provided.

### When to use each subskill
`exam-ingest` build the library · `exam-tutor` teach · `exam-study-guide` compile visual material · `exam-quiz` quiz · `exam-review` review · `exam-cheatsheet` cheat sheet · `exam-audit` read-only checkup · `exam-cram` overall orchestrator.

### Language
Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); a persisted `language` code (`zh` / `en` / `bilingual`) switches it per the dispatch rule, and control instructions stay English / precise. See [`docs/language-policy.md`](../../../docs/language-policy.md).
