---
name: universal-exam-cram-coach
description: "帮助学生在临考前进行结构化极速复习：解析课程资料/大纲/重点，按章节生成 wiki 知识库与标准题库，组织针对性刷题与判分，并记录复习进度和错题。当用户即将考试、需要快速复习计划、练习题、错题复盘或考前小抄时使用（关键词：期末/备考/复习/刷题/划重点/错题；exam, cram, study plan, quiz, review）。不适用于长期学习规划、与考试无关的写作或编程任务。"
license: MIT
metadata:
  version: "4.2"
  author: ZeKaiNie
---

# Universal Exam Cram Coach — Root Router

This language-neutral router dispatches last-minute exam prep to the chapter-wiki, bank-only, persistent control layer and its wording packs; it is not a duplicate manual.

## Language dispatch

Read the canonical, language-neutral `study_state.json.language` code and load the matching compatibility entry plus its per-skill wording pack BEFORE emitting any student-visible output:

- `zh` (display choice `中文`) → [`locales/zh/SKILL.md`](locales/zh/SKILL.md) plus the selected sub-skill's zh wording pack under `locales/zh/skills/`
- `en` (display choice `English`) → [`locales/en/SKILL.md`](locales/en/SKILL.md) plus the selected sub-skill's en wording pack under `locales/en/skills/`
- `bilingual` (display choice `双语`) → compose the zh and en wording block by block, with zh first and a `> EN:` mirror for each block (composition rules in [`docs/language-policy.md`](docs/language-policy.md))

`中文`, `English`, and `双语` remain accepted user-facing input aliases. On first contact one combined ask sets mode, budget, and language; `exam_start.py confirm` persists them with the exact workspace/materials receipt. Later `update_progress.py set --language` applies next turn. Default English unless the student opened in Chinese; bilingual is explicit-only.

## Control layer (behavior)

Behavior lives in [`skills/exam-cram/SKILL.md`](skills/exam-cram/SKILL.md) and these subskills:

| Sub-skill | Role |
|---|---|
| [`exam-ingest`](skills/exam-ingest/SKILL.md) | Build/validate workspace |
| [`exam-tutor`](skills/exam-tutor/SKILL.md) | Lazy chapter teaching |
| [`exam-study-guide`](skills/exam-study-guide/SKILL.md) | Typed guide and visual artifact gate |
| [`exam-quiz`](skills/exam-quiz/SKILL.md) | Bank-only selection/grading |
| [`exam-review`](skills/exam-review/SKILL.md) | Replay mistakes/confusions |
| [`exam-cheatsheet`](skills/exam-cheatsheet/SKILL.md) | Final handout |
| [`exam-audit`](skills/exam-audit/SKILL.md) | Read-only workspace health check |
| [`exam-help`](skills/exam-help/SKILL.md) | Quick reference |
| [`confusion-tracker`](skills/confusion-tracker/SKILL.md) | Concept-confusion tracking |

Generic-agent fallback: [`AGENTS.md`](AGENTS.md).

## Install & run essentials

- Under [`scripts/`](scripts/), use `exam_start.py status`, then `exam_start.py confirm --course <name> --materials <dir> --workspace <ws> --mode <mode> --time-budget <tier> --language <lang>`. It writes the confirmation/state/runtime receipt; `ingest_course.py` is the normal build entry, exit 10 routes to typed `ingest_review.py`, and `ingest.py` is lower-level only. `update_progress.py` owns `study_state.json`; `study_progress.md` is generated. Official selectors are `select_questions.py` / `select_hard_questions.py`.
- Workspace file contract (wiki / quiz bank / state / asset metadata): [`docs/file-format.md`](docs/file-format.md).
- Language policy (single-language purity, EN canonical vocabulary, persisted canonical values): [`docs/language-policy.md`](docs/language-policy.md).
- Host loading: [`docs/agent-portability.md`](docs/agent-portability.md).
- PDF capabilities differ by host; use the audited, no-silent-download routing table in [`docs/pdf-capability-adapters.md`](docs/pdf-capability-adapters.md).
- Missing/legacy `artifact_mode` is `chat`; explicit standing `visual` or a one-shot chapter artifact invokes `exam-study-guide`, while cheat-sheet PDF uses `exam-cheatsheet`. An ambiguous PDF request asks which once. Never infer subscription. Persist with `update_progress.py set --artifact-mode chat|visual`.
