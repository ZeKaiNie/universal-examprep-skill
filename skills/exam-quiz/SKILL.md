---
name: exam-quiz
description: >
  从标准题库 references/quiz_bank.json 抽取本章题目对学生测验并判分，支持 6 大题型（选择、主观、
  画图、填空、判断、代码）。主观题用「要点检索制」对照 keywords 判分，连续答错两次给提示/跳过/归档。
  禁止现场编题。当某一阶段学完需要刷题检验、或用户要求测验/模考时使用。
license: MIT
---

# exam-quiz — 抽题判分

Quiz the student from the question bank and grade against stored answers. Never invent questions or answers on the fly.

## Purpose
Pull chapter/phase-scoped items from `references/quiz_bank.json`, present one item at a time across the six quiz types, grade each answer, run the escape hatch on repeated failures, and archive skipped/wrong items to `study_progress.md`. Hand control back to `exam-cram` after the checkpoint.

## Activation
- Trigger after a phase is studied and needs a checkpoint quiz, or when the user asks 「测一下 / 来几道题 / 模考」.

## Inputs
- `references/quiz_bank.json` — the question bank. Each item carries `type`, `answer`, `explanation`, `source`, and a `chapter` OR `phase` tag; subjective items carry `keywords`. Filter selection by `chapter` or `phase`. An item with neither tag cannot be selected for a chapter quiz.
- Current chapter number — select only items whose `chapter` (or matching `phase`) equals it.

> If `exam-ingest` produced the bank, require every item to carry `chapter`/`phase`. Without it, the chapter quiz reports "no items found" even when the bank holds matching items.

## Workflow
1. **Select items**: filter by matching `chapter` OR `phase` (the bank uses both fields; filtering on `chapter` alone drops items tagged only with `phase`). If the bank contains relevant items, never write new questions.
2. **Grade by the six quiz types**:
   - `choice` — compare against the `answer` option.
   - `subjective` — keyword-coverage grading: pass if the answer covers the item's `keywords` and key steps; accept equivalent wording; report coverage feedback.
   - `fill_blank` — compare against the standard fill (accept synonyms).
   - `true_false` — compare the verdict and require a one-line reason.
   - `code` — check the key edits/output against `answer`.
   - `diagram` — do not judge the figure from memory: follow `render_hint` to run the standard algorithm first, derive the structure, then compare against the student's drawing; state that the instructor's drawing method takes precedence.
3. **Escape hatch**: on a wrong answer, give the logic gap + the item's `explanation` + a hint. On the 2nd consecutive wrong answer, offer three choices — view hint / skip and archive the wrong item / continue — and proceed per the choice.
4. **Archive**: write skipped or wrong items into the `study_progress.md` wrong-item archive.
5. **Source honesty**: when an item's or answer's `source` is `ai_generated`, flag it at grading time with 「⚠️ AI生成答案，非老师/教材提供」 (reference only, verify against the instructor/textbook).

## Output Contract
- Present one item at a time; grade as pass/not-pass plus key-point feedback; refresh the progress panel at the end.
- Update the `study_progress.md` check-in log and wrong-item archive, then hand control back to `exam-cram`.
- Student-facing output defaults to Simplified Chinese unless the user asks otherwise. (See [`docs/language-policy.md`](../../docs/language-policy.md).)
- Provenance labels in feedback are verbatim student-facing markers: 🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供.

## Student-facing Output
判分反馈用简短、具体的中文，先点考点再给改进：

- **答对**：✅ 对了。这题考什么：……（一句点考点）。顺手记个易错点：……。
- **部分对**：🟡 思路对了一半——你答到了「……」，但漏了「……」这一步，补上就满分。
- **答错**：❌ 这里错了：……（指出逻辑漏洞）。标准答题步骤：1.… 2.…。再看一眼原题解析。
- **连错两次**：要不要 ① 查看提示　② 跳过并归档错题　③ 再想想？选 ② 我就「已记录到错题本」，考前再扫雷。
- **题/答为 AI 生成**：⚠️ AI生成答案，非老师/教材提供，仅供参考，请和老师/教材核对。

## Boundaries
- When the bank holds relevant items, do not write your own. With no stored answer, do not force a verdict — mark ⚠️ or state the limitation plainly.
- Do not judge diagram items from memory — the algorithm-derived standard structure is the reference.
