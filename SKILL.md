---
name: universal-exam-cram-coach
description: "帮助学生在临考前进行结构化极速复习：解析课程资料/大纲/重点，按章节生成 wiki 知识库与标准题库，组织针对性刷题与判分，并记录复习进度和错题。当用户即将考试、需要快速复习计划、练习题、错题复盘或考前小抄时使用（关键词：期末/备考/复习/刷题/划重点/错题；exam, cram, study plan, quiz, review）。不适用于长期学习规划、与考试无关的写作或编程任务。"
license: MIT
metadata:
  version: "4.2"
  author: ZeKaiNie
---

# Universal Exam Cram Coach — Root Router

This skill turns the agent into a last-minute exam-cram coach whose core memory carrier is a chaptered LLM wiki: it ingests the student's materials into a wiki + standard question bank, teaches chapter by chapter, quizzes and grades from the bank only, replays mistakes, and compiles a pre-exam cheat sheet — with persistent source, readiness, and progress gates.
This root file is a language-neutral ROUTER, not the manual: it only dispatches to the language packs and the control layer below.

## Language dispatch

Read the canonical, language-neutral `study_state.json.language` code and load the matching compatibility entry plus its per-skill wording pack BEFORE emitting any student-visible output:

- `zh` (display choice `中文`) → [`locales/zh/SKILL.md`](locales/zh/SKILL.md) plus the selected sub-skill's zh wording pack under `locales/zh/skills/`
- `en` (display choice `English`) → [`locales/en/SKILL.md`](locales/en/SKILL.md) plus the selected sub-skill's en wording pack under `locales/en/skills/`
- `bilingual` (display choice `双语`) → compose the zh and en wording block by block, with zh first and a `> EN:` mirror for each block (composition rules in [`docs/language-policy.md`](docs/language-policy.md))

`中文`, `English`, and `双语` remain accepted user-facing input aliases. Unset language means this is the first conversation: the merged first ask decides mode × time budget × language, and `exam_start.py confirm` persists the initial three canonical values together with the exact workspace/materials receipt. Later changes use one `update_progress.py set` call; `set --language` switches the pack from the next turn onward. Default English unless the student opened in Chinese; bilingual remains explicit-only.

## Control layer (behavior)

Behavior lives in the modular skill collection, not in this file. Main skill / orchestrator: [`skills/exam-cram/SKILL.md`](skills/exam-cram/SKILL.md). The 9 sub-skills:

| Sub-skill | Role |
|---|---|
| [`exam-ingest`](skills/exam-ingest/SKILL.md) | Build the workspace from materials (wiki + quiz bank + progress) |
| [`exam-tutor`](skills/exam-tutor/SKILL.md) | Lazy per-chapter teaching (seven-step walkthroughs, visual-first gate) |
| [`exam-study-guide`](skills/exam-study-guide/SKILL.md) | Compile persisted chapter sources into formula-readable, self-contained HTML/PDF study material |
| [`exam-quiz`](skills/exam-quiz/SKILL.md) | Select & grade from the bank only (fail-closed on invisible figures) |
| [`exam-review`](skills/exam-review/SKILL.md) | Replay mistakes & confusions |
| [`exam-cheatsheet`](skills/exam-cheatsheet/SKILL.md) | Pre-exam cheat sheet |
| [`exam-audit`](skills/exam-audit/SKILL.md) | Read-only workspace health check |
| [`exam-help`](skills/exam-help/SKILL.md) | Quick reference |
| [`confusion-tracker`](skills/confusion-tracker/SKILL.md) | Concept-confusion tracking |

Generic agents that will not read the full rules: [`AGENTS.md`](AGENTS.md) (one-screen condensed contract).

## Install & run essentials

- Engine scripts live under [`scripts/`](scripts/): first use `exam_start.py status`, then `exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <lang>` after the exact paths and three choices are established. That command writes the confirmation, state, and runtime receipt required by `ingest_course.py` and true Study Guide rendering. `ingest_course.py` is then the normal materials-to-validated-workspace entry; exit 10 means compilation succeeded but content readiness is blocked and must enter the typed `ingest_review.py` workflow. `ingest.py` is the lower-level compiler for an already-built raw payload. `update_progress.py` owns `study_state.json` (the single source of truth — `study_progress.md` is a generated view, never hand-edit it); `select_questions.py` / `select_hard_questions.py` are the official question selectors.
- Workspace file contract (wiki / quiz bank / state / asset metadata): [`docs/file-format.md`](docs/file-format.md).
- Language policy (single-language purity, EN canonical vocabulary, persisted canonical values): [`docs/language-policy.md`](docs/language-policy.md).
- Install in Claude Code at `~/.claude/skills/universal-exam-cram-coach/` or project-local `.claude/skills/universal-exam-cram-coach/`; load matrix for other hosts: [`docs/agent-portability.md`](docs/agent-portability.md).
- PDF capabilities differ by host; use the audited, no-silent-download routing table in [`docs/pdf-capability-adapters.md`](docs/pdf-capability-adapters.md).
- Artifact output is user-controlled: missing/legacy `artifact_mode` defaults to `chat` (normal teaching + state/notebook, no automatic HTML/PDF); only an explicit standing `visual` choice or a one-shot **chapter handout/chapter PDF** request invokes `exam-study-guide`. A cheatsheet PDF routes to `exam-cheatsheet`; if “make a PDF” does not identify the artifact, ask which artifact once. Agents never infer a subscription tier. Persist the standing choice with `update_progress.py set --artifact-mode chat|visual`.
