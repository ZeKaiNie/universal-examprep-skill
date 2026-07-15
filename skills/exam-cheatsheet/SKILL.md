---
name: exam-cheatsheet
description: >
  全员通关后把 错题本+笔记本+知识点窗口+wiki 编译成考前速记小抄 cheatsheet.md（每条要点带可溯源
  锚点），并在视觉产物模式或用户明确要求 PDF/打印版时按指定页数渲染成打印级 PDF：按「必背结论/公式
  → 有难度例题（必要时含题面图）→ 例题解答（代入公式、保留基础过程）→ 要点解释（同类题怎么办）」
  四段组织。当复习收尾、用户要「考前小抄/速记/总结/打印版」时使用。
license: MIT
---

# exam-cheatsheet — pre-exam cheatsheet compiler

## Purpose
Compile, rather than free-generate, mastered content into workspace-root `cheatsheet.md`. Every top-level bullet must link into `notebook/`, `mistakes/`, or `references/wiki/`. Do not teach new material or invent questions. Render the requested-page-count PDF only for standing `visual` mode or an explicit PDF/print request. Never write the retired `walkthrough.md`; leave an existing copy untouched.

## Activation
Trigger on an explicit request for 「考前小抄 / 速记 / 总复习」, or when review is wrapping up after all phases and persisted `artifact_mode=visual`. Automatic final review under `chat` stays a conversational `exam-review` summary.

## Inputs
- Weak-spot source: `study_state.json` (`mistake_archive`, `confusion_log`, and `phase_checklist`) when it exists; otherwise the possibly stale generated `study_progress.md`. Read these first, then `mistakes/index.md` and `notebook/index.md` when present; their full entries provide preferred ready-made anchors.
- Rank `knowledge_window` status `out_window` above `in_window` and `verified` (codes are defined by `scripts/i18n.py`).
- Read core conclusions and formulas from every mastered chapter in `references/wiki/`, derived from `study_state.json`'s `current_phase`/`phase_checklist` when it exists, otherwise `study_progress.md`, checked against `study_plan.md`. Lazy-load one chapter at a time.
- Use `references/quiz_bank.json` for teacher-flagged items and answer frameworks. Resolve `scripts/select_hard_questions.py` from `${CLAUDE_SKILL_DIR}`, never the student workspace; it returns a flat ranked list which the agent groups by knowledge point.

## Workflow
1. **Gate artifacts.** Read `study_state.json.artifact_mode`; missing, legacy, or unknown means `chat`. Never infer a subscription tier or add a fourth required first-contact question. Automatic `chat` review creates no sheet; an explicit sheet request may create Markdown. Only standing `visual` or an explicit one-shot PDF/print request authorizes rendering. A one-shot request does not modify the persisted value. Never install dependencies or skills silently.
2. **Build the skeleton.** Weak spots come first. Per chapter retain only high-frequency or high-scoring formulas, conclusions, and one-sentence definitions.
3. **Select one hard example per key point.** For each mastered chapter run `python "${CLAUDE_SKILL_DIR}/scripts/select_hard_questions.py" --workspace <ws> --chapter <N> --mode 查缺补漏 -n <M> --json`. Both `--chapter` and `--mode` are required: they avoid a missing-range failure in `某章起步补弱` and override easy-first `零基础从头讲`. Set `<M>` at least to the bank length so the default top ten cannot starve later points. Group the flat result, prioritize points linked to mistakes/confusions, and choose the hardest candidate per point. With no linked bank item, emit 「无题库例题」 and only the 「必背结论/公式」 and 「要点解释」 sections; never invent a replacement.
4. **Fail closed on prompt assets.** For `requires_assets=true` or `maybe_requires_assets=true`, embed every `question_context`, `figure`, `diagram`, and `table` as workspace-relative `references/assets/` links, labeled `题面图` for `zh`/`bilingual` or `Question-side asset` for `en`. Missing or unusable assets require a self-contained alternative. A `stub` or `page_reference` item likewise needs its original-page render or replacement by a `full` item. Never include an example whose prompt figure/page is invisible.
5. **Write the four sections.** The worked solution states the formula, substituted values, and result; only intermediate arithmetic may be omitted. The takeaway starts with the recognition cue and then the answer framework. Material-backed lines may remain unlabeled; AI supplements require 🟡 AI补充，可能与你老师讲的不完全一致, AI answers require ⚠️ AI生成答案，非老师/教材提供, and missing/unknown bank answer provenance requires 「来源未知」. Do not let uncertain content inherit the material default; see [`docs/language-policy.md`](../../docs/language-policy.md).
6. **Attach traceability.** End every top-level `- ` bullet with `[→](notebook/chNN.md#<anchor>)`, `[→](mistakes/chNN.md#<anchor>)`, or `[→](references/wiki/<file>.md)`, preferring notebook/mistake evidence. Run `python "${CLAUDE_SKILL_DIR}/scripts/validate_workspace.py" <ws>` and fix every untraced or dead link before delivery.
7. **Write only when authorized.** Create workspace-root `cheatsheet.md` with the four sections for every mastered chapter and a refreshed progress panel. Under `chat`, this requires an explicit sheet request.
8. **Render only when authorized.** For standing `visual` or explicit one-shot PDF/print, ask for the page count if omitted (default 2), then run `python "${CLAUDE_SKILL_DIR}/scripts/cheatsheet_render.py" --workspace <ws> --pages <N>`. Exit 0 must produce exactly N print-safe pages with margins ≥12 mm. Exit 3 returns `cheatsheet.html` plus the emitted print instruction. Visually inspect the result; adjust `--font-size`, not margins, until it fits N pages and the last page has at most about 15% blank. Under ordinary `chat`, stop after validated Markdown and do not ask for page count.
9. Never invent teacher emphasis; only material-flagged points may be described that way.

## Output Contract
- `cheatsheet.md` uses active-language headings per mastered chapter: `zh` uses 「必背结论/公式」→「例题」→「例题解答」→「要点解释」; `en` uses Must-memorize conclusions & formulas → Worked example → Worked solution → Takeaway. Every bullet is traced and validation passes.
- An explicit `chat` request delivers Markdown only unless it also requests print/PDF. Authorized rendering delivers exact-page-count `cheatsheet.pdf`, or `cheatsheet.html` plus print instructions on the no-browser path.
- Student-facing output defaults to English (Simplified Chinese if the student opened in Chinese). Persisted `language` values `zh`, `en`, and `bilingual` select single-language or mirrored output per [`docs/language-policy.md`](../../docs/language-policy.md).

## Language packs

- `中文` → [`../../locales/zh/skills/exam-cheatsheet.md`](../../locales/zh/skills/exam-cheatsheet.md)
- `English` → [`../../locales/en/skills/exam-cheatsheet.md`](../../locales/en/skills/exam-cheatsheet.md)
- `双语` → compose both blockwise, zh then `> EN:`, under [`docs/language-policy.md`](../../docs/language-policy.md)

Display aliases are normalized to `zh`, `en`, or `bilingual`.

## Boundaries
- Unsupported content needs the applicable 🟡 or ⚠️ label. The sheet compresses completed review; it never bypasses source labels or the `quiz_bank`-only rule.
