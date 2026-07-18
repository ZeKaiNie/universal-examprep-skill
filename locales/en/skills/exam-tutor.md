# exam-tutor — en student-facing pack

> Wording only; behavior lives in [skills/exam-tutor/SKILL.md](../../../skills/exam-tutor/SKILL.md).

## Student-facing Output

Use this exact seven-block order; fill every block with concrete, persisted content:

```text
Current stage: Stage N | Template: seven-step walkthrough
① Question figure: [render every Question-side asset, or state that the item has no figure]
② What's being asked: [target and tested point]
③ What to read off the figure: [given quantities/evidence]
④ Core formula: [formula or framework]
⑤ Step-by-step solution: [numbered work]
⑥ Answer self-check: [boundary/unit/logic check]
⑦ Source trace: [chapter · wiki · clickable original location]
Question source: … | Answer source: … | 🟢 From your materials
```

The full trailing label is one of: 🟢 From your materials; 🟡 AI-supplemented — may differ from what your teacher taught; ⚠️ AI-generated answer — not from your teacher or textbook. Unknown metadata stays `Source unknown` or `Source page unknown`.

Default output stops at the source block. Add Common pitfalls / 3-minute mnemonic / Your turn only when requested or stored. In the liberal-arts variant, block ③ names source concepts, ④ the framework, and ⑤ the scoring points; numbering stays fixed. Without a material answer, put the full AI-generated-answer warning in block ⑤ and the source line.

Persist to `notebook/chNN.md` first, update the same item in place, then return a short digest ending `Full walkthrough: notebook/chNN.md#<anchor> | Index: notebook/index.md`. If writing fails, say so and provide the complete walkthrough.

## One-question continuation prompt

```text
Current progress: X of Y key questions covered.

Reply "Continue" for the next key question. If you want this one explained again, name the step that needs more detail.
```
