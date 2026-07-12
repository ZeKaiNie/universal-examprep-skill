# exam-help — en student-facing pack

> This file is the en language pack for student-visible wording; behavior lives in [skills/exam-help/SKILL.md](../../../skills/exam-help/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
One screen to understand this exam-prep skill suite. Detailed rules live in the root `SKILL.md` and each subskill.

### Four-step workflow
1. **Build the library** (`exam-ingest`): upload your materials → auto-build the wiki + quiz bank + progress file.
2. **Teach** (`exam-tutor`): lazy-load chapter by chapter; metaphor-first concept teaching / key-problem walkthroughs / run the algorithm before drawing.
3. **Quiz** (`exam-quiz`): draw questions from the quiz bank and grade; after two misses you get a hint / skip / archive.
4. **Review + cheat sheet** (`exam-review` / `exam-cheatsheet`): clear out mistakes and confusion points, then produce a pre-exam quick-recall sheet.

### Learning mode × time budget (settled in the first conversation)
- **3 learning modes**: `零基础从头讲` (teach from scratch: every knowledge point in order, linked problems easy to hard) · `某章起步补弱` (start from a chapter to patch weak spots: skim the chapters you already know, expand the ones you don't) · `查缺补漏` (gap-hunting: one harder problem per knowledge point across all chapters, expanding only where you're confused).
- **4 time budgets** (stacked on top): `≤1天` (≤1 day — asking the student questions is forbidden) · `1-3天` (1-3 days — randomly ask back about confusion points) · `3-7天` (3-7 days — knowledge-point window system: assumed retained inside the window, asked back outside it) · `>7天` (>7 days — outside-window points are tested with hard problems).
- Legacy `normal/sprint/panic/mock` are deprecated; `set --mode` auto-migrates with a warning (panic→`零基础从头讲`+`≤1天`, sprint→`查缺补漏`+`1-3天`, normal/mock→`查缺补漏`).

### Workspace files
- `references/wiki/chN_*.md` per-chapter knowledge base (the only knowledge source; read on demand) · `references/quiz_bank.json` canonical quiz bank (the only answer source)
- `study_plan.md` stage plan · `study_progress.md` progress + mistakes + 💡 confusion points (updated every round; read first after a restart)

### 6 quiz types
`choice` multiple choice · `subjective` subjective/calculation · `diagram` diagram drawing · `fill_blank` fill in the blank · `true_false` true/false · `code` code.

### Anti-hallucination & source labeling
- Teaching and grading stay within the wiki/quiz-bank scope; if the materials don't cover it, the coach honestly declines to answer.
- 🟢 From your materials · 🟡 AI-supplemented — may differ from what your teacher taught · ⚠️ AI-generated answer — not from your teacher or textbook.
- No made-up questions when the quiz bank has a relevant one; AI-generated content is never disguised as teacher-provided.

### When to use each subskill
`exam-ingest` build the library · `exam-tutor` teach · `exam-quiz` quiz · `exam-review` review · `exam-cheatsheet` cheat sheet · `exam-audit` read-only checkup · `exam-cram` overall orchestrator.

### Language
Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese); a persisted `language` (`中文` / `English` / `双语`) switches it per the dispatch rule, and control instructions stay English / precise. See [`docs/language-policy.md`](../../../docs/language-policy.md).
