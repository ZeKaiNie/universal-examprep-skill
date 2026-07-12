# exam-tutor — en student-facing pack

> This file is the en language pack for the skill's student-visible wording; behavior logic lives in [skills/exam-tutor/SKILL.md](../../../skills/exam-tutor/SKILL.md) (the control layer, single source of truth).

## Student-facing Output
Key-question walkthroughs use the seven-step template in compact, exam-oriented English (concrete, no padding or long-winded prose). All seven numbered blocks ①-⑦ must appear, in this exact order, none skipped or reordered. Same numbered blocks as the zh pack ([`../../zh/skills/exam-tutor.md`](../../zh/skills/exam-tutor.md)), zero CJK outside code spans, fixed phrasing verbatim from the EN canonical vocabulary in [`docs/language-policy.md`](../../../docs/language-policy.md):

```text
Current stage: Stage 2 (Linear Lists) | Template: seven-step walkthrough (stored as a preference — change anytime)

① Question figure:
![Question-side asset](references/assets/ch02_p12_fig.png)
(For a no-figure item this block reads: This question has no figure — read the given conditions.)

② What's being asked:
You are given a sequential list and a linked list; the question asks which structure gives faster random access to the i-th element, and why. The tested point is the lookup cost of the two storage schemes.

③ What to read off the figure:
List length n and the target index i; in the linked-list figure, count how many hops it takes from the head node to the i-th node.

④ Core formula:
Sequential-list lookup: address = base address + i × element size → O(1); linked-list lookup: walk i steps from the head → O(i).

⑤ Step-by-step solution:
1. Sequential list: one multiply-add computes the address directly — 1 step, done.
2. Linked list: with i=5 it takes 5 next hops.
3. Conclusion: random access is O(1) on the sequential list vs O(n) on the linked list — the sequential list is faster.

⑥ Answer self-check:
Plug the boundary i=0 back in: the sequential list still takes 1 step, the linked list 0 steps — the ordering is unchanged, so the conclusion holds.

⑦ Source trace:
Chapter 2 (Linear Lists) · references/wiki/ch02_linear_list.md · original [lecture03.pdf p.12](../lecture03.pdf#page=12)

Question source: hw02.pdf p.3 (homework) | Answer source: hw02_sol.pdf p.1 | 🟢 From your materials
```

- **Default output stops at the source block.** The three closers Common pitfalls / 3-minute mnemonic / Your turn are OFF by default — emit them only when the student explicitly asks (e.g. "any common pitfalls?", "give me a mnemonic", "quiz me") or a stored ⚙️ preference (e.g. `收尾块=易错点+3分钟速记`) requests them; when emitted, use those three canonical EN names verbatim.
- **Source-block line**: ASCII `|` separators; the trailing label is the FULL text of one of the three EN provenance sentences — 🟢 From your materials / 🟡 AI-supplemented — may differ from what your teacher taught / ⚠️ AI-generated answer — not from your teacher or textbook — never the emoji alone. Missing source metadata → write Source unknown (file known but page missing → Source page unknown); never invent a filename or page number.
- **Liberal-arts variant** (stored preference value `文科变体`): ③ → the key sentences/concepts to read in the material, ④ → the core concept / theoretical framework, ⑤ → expand each scoring point one by one, and an optional line after ⑦: Possible exam focus: …. Numbering and the other blocks are unchanged.
- **No teacher/textbook answer**: ⑤'s title becomes `⑤ Step-by-step solution (⚠️ AI-generated answer — not from your teacher or textbook)`, and the source block's trailing label is the FULL sentence ⚠️ AI-generated answer — not from your teacher or textbook.
- Zero-basic key-question mode walks every key question through this same seven-step template (the legacy exam-point-breakdown / standard-answer-steps blocks are folded into ②/④⑤ and never emitted separately).
- Stage references render in English (Stage N); a resume opens with: Resuming from Stage 2.
- **Persist first, then digest**: every seven-step walkthrough is written to the notebook `notebook/chNN.md` before the reply (re-teaching the same item updates its entry in place — no duplicates); the chat reply is a 3-5 line digest ending with the fixed line: Full walkthrough: `notebook/ch02.md#q13` | Index: `notebook/index.md`. If the notebook write fails, that is stated plainly and the complete walkthrough is delivered in chat instead — content is never silently dropped.
