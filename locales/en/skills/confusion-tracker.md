# confusion-tracker — en student-facing pack

> This file is the en language pack for student-visible wording; behavior lives in [skills/confusion-tracker/SKILL.md](../../../skills/confusion-tracker/SKILL.md) (the control layer, single source of truth).

Table format in the progress file (the `序号` column auto-increments over existing records):

```text
## 💡 概念疑难点记录

| 序号 | 关联章节 | 疑难点 | 解答要点 | 状态 |
|:---|:---|:---|:---|:---|
| 1 | Crystal structures | Why is FCC ABC stacking? | Third layer lands in the C hollows → FCC; lands over A → HCP | 待回顾 |
```

The table skeleton is persisted canonical vocabulary and stays in Chinese in every language mode — the `## 💡 概念疑难点记录` heading, the column headers (`序号`/`关联章节`/`疑难点`/`解答要点`/`状态`), and the `状态` values `待回顾`/`已回顾` (see [`docs/language-policy.md`](../../../docs/language-policy.md)); only the recorded note text follows the session language.

After recording, give one short receipt line (e.g. "Recorded to the confusion log") without breaking the teaching flow.

The full explanation itself is also written into the notebook `notebook/chNN.md` (the state row stays in the progress state as usual — the two index each other), and the receipt may add one line: Full explanation: `notebook/ch04.md#fcc-abc` | Index: `notebook/index.md`.
