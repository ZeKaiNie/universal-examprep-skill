---
name: universal-exam-cram-coach
description: "帮助学生在临考前进行结构化极速复习：解析课程资料/大纲/重点，按章节生成 wiki 知识库与标准题库，组织针对性刷题与判分，并记录复习进度和错题。当用户即将考试、需要快速复习计划、练习题、错题复盘或考前小抄时使用（关键词：期末/备考/复习/刷题/划重点/错题；exam, cram, study plan, quiz, review）。不适用于长期学习规划、与考试无关的写作或编程任务。"
license: MIT
metadata:
  version: "3.0"
  author: ZeKaiNie
---

# Universal Exam Cram Coach — Root Router

This skill turns the agent into a last-minute exam-cram coach whose core memory carrier is a chaptered LLM wiki: it ingests the student's materials into a wiki + standard question bank, teaches chapter by chapter, quizzes and grades from the bank only, replays mistakes, and compiles a pre-exam cheat sheet — with physical file locking against hallucination.
This root file is a language-neutral ROUTER, not the manual: it only dispatches to the language packs and the control layer below.

## Language dispatch

Read `study_state.json.language` and load the matching full-entry pack BEFORE emitting any student-visible output:

- `zh` → [`locales/zh/SKILL.md`](locales/zh/SKILL.md) (plus the per-skill zh packs under `locales/zh/skills/`)
- `en` → [`locales/en/SKILL.md`](locales/en/SKILL.md) (plus the per-skill en packs under `locales/en/skills/`)
- `bilingual` → compose from the zh pack with a `> EN:` mirror line per block (composition rules in [`docs/language-policy.md`](docs/language-policy.md))

Unset language → this is the first conversation: the merged first-ask (mode × time budget × language, persisted in one `update_progress.py set` call) decides it; default en unless the student opened in Chinese. `set --language` can switch mid-session; from the next turn on, load the new pack.

## Control layer (behavior)

Behavior lives in the modular skill collection, not in this file. Main skill / orchestrator: [`skills/exam-cram/SKILL.md`](skills/exam-cram/SKILL.md). The 8 sub-skills:

| Sub-skill | Role |
|---|---|
| [`exam-ingest`](skills/exam-ingest/SKILL.md) | Build the workspace from materials (wiki + quiz bank + progress) |
| [`exam-tutor`](skills/exam-tutor/SKILL.md) | Lazy per-chapter teaching (seven-step walkthroughs, visual-first gate) |
| [`exam-quiz`](skills/exam-quiz/SKILL.md) | Select & grade from the bank only (fail-closed on invisible figures) |
| [`exam-review`](skills/exam-review/SKILL.md) | Replay mistakes & confusions |
| [`exam-cheatsheet`](skills/exam-cheatsheet/SKILL.md) | Pre-exam cheat sheet |
| [`exam-audit`](skills/exam-audit/SKILL.md) | Read-only workspace health check |
| [`exam-help`](skills/exam-help/SKILL.md) | Quick reference |
| [`confusion-tracker`](skills/confusion-tracker/SKILL.md) | Concept-confusion tracking |

Generic agents that will not read the full rules: [`AGENTS.md`](AGENTS.md) (one-screen condensed contract).

## Install & run essentials

- Engine scripts live under [`scripts/`](scripts/) (Python standard library only, no pip): `ingest.py` builds the workspace one-shot; `update_progress.py` owns `study_state.json` (the single source of truth — `study_progress.md` is a generated view, never hand-edit it); `select_questions.py` / `select_hard_questions.py` are the official question selectors.
- Workspace file contract (wiki / quiz bank / state / asset metadata): [`docs/file-format.md`](docs/file-format.md).
- Language policy (single-language purity, EN canonical vocabulary, persisted canonical values): [`docs/language-policy.md`](docs/language-policy.md).
- Install in Claude Code at `~/.claude/skills/universal-exam-cram-coach/` or project-local `.claude/skills/universal-exam-cram-coach/`; load matrix for other hosts: [`docs/agent-portability.md`](docs/agent-portability.md).
