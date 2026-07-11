<div align="center">

<img src="assets/exam-panic.png" width="200" alt="Exam Cram Coach" />

# Exam Cram Coach

*One night left. You studied nothing. It won't make anything up.*

English · [中文](README.zh.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**Never fabricates: 100% honest abstention** · in-your-materials-not-the-model's-head 11% → ~99% · context −90% · 6 agents

</div>

You know him. Night before the exam, hair a mess, eyes wide open, hasn't read a single page of the course. This skill is for him — it doesn't pour in more "knowledge" that it isn't sure about; it teaches only what's actually in *your* materials, and says "not in the materials" for everything else.

**30-second start** — clone the repo, then say one line to your agent:

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
# In Claude Code / Cursor, say: "use this skill to set up my exam-prep space", then drop in your materials
```

---

## Before / after

**With the skill** — every claim carries its source, so you can check it:

> **[#vis_q1]** In the figure, which set relation does the shaded region show?
> **The intersection of A and B.**
> `Question: hw02.pdf p.3 | Answer: hw02_sol.pdf | 🟢 from your materials`

**Closed-book / plain agent** — sounds just as confident, but you can't tell if it's true:

> The shaded region is the **union**. <sub>(It's actually the intersection; no source label, nothing to check against — this is where hallucination happens.)</sub>

The difference isn't tone. It's whether each claim lands back in your materials.

---

## Numbers

The skill's value is **grounding**: connecting what's in your materials but not in the model's head — **accurately**, and **never fabricated**. Two real measurements (judge: Sonnet):

**① In your materials, not in the model — the skill goes from 11% up to 100%.** Details mined from course transcripts (the professor's examples, obscure studies, exact numbers) that world knowledge can't answer; closed-book collapses, hand the materials back and it returns:

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_en.svg" width="600" alt="materials-specific: closed-book vs with the skill" /></div>

| Course · Model | Closed-book | Raw files + generic agent | With the skill |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 11% | 98% | **100%** |
| PSYC 110 · Sonnet 4.6 | 13% | 100% | **100%** |
| PSYC 110 · Haiku 4.5 | 11% | 98% | **100%** |
| 6.006 · Haiku 4.5 | 45% | 89% | **91%** |

**② Not in the materials at all — the skill says "not covered" 100% of the time.** On out-of-scope probes, with the skill (and raw files) **all three models, both courses, abstain honestly 100%**; closed-book only 60%–90% (it fabricates a plausible answer). This is the most direct anti-hallucination measure.

<div align="center"><img src="benchmark/docs/img/oos_psyc_abst_en.svg" width="560" alt="out-of-scope probes: honest abstention rate" /></div>

The skill matches a "raw files agent" on accuracy but costs less — it pulls only the compressed relevant chapters instead of re-scanning the whole file pile each question:

<details><summary>Cost per question (same accuracy, less spend)</summary>

| Cost / question | Closed-book | Raw files agent | With the skill |
|---|:--:|:--:|:--:|
| PSYC 110 | $0.033 | $0.117 | **$0.102** |
| 6.006 | $0.034 | $0.066 | **$0.063** |

</details>

Full method, three-arm design, judge calibration, cost, limitations → **[test report](benchmark/REPORT.en.md)**.

---

## How it works

A ladder of "don't make it up unless you have to":

1. **Quiz only from the materials** — questions come from a `quiz_bank.json`, never improvised.
2. **Forced source labels** — every claim tagged `🟢 from your materials` / `🟡 AI-supplemented, may differ from your teacher` / `⚠️ AI-generated answer`, never passed off as the textbook.
3. **If it's not in the materials, say so** — abstains honestly on uncovered questions instead of fabricating (100% out-of-scope abstention, measured).
4. **Draw-it questions run the algorithm first** — for binary trees / graph traversal, it runs the real algorithm in the background to get the topology, then renders — no imagining.
5. **Figure-dependent questions won't be served without the figure** — no unanswerable question handed to the student.
6. **Chapter-sliced knowledge base, loaded on demand** — sliced by chapter, loaded by progress, so long chats don't blow up the context. **Context −90%.**

---

## Study modes · time budget · preferences

The skill adapts how deep it teaches, how fast, and whether it asks you questions — all kept in `study_state.json`, persistent across chats.

**3 study modes** (how it teaches):

| Mode | For |
|---|---|
| **Teach from scratch** | Haven't studied at all — walk every chapter from zero, 7-step walkthrough per key question |
| **Start mid-course, shore up weak spots** | Know some — start from a chapter you name, target the weak parts |
| **Fill the gaps** | Mostly covered — just quiz to find blind spots, mistakes first |

**4 time budgets** (how fast):

| Budget | Behavior |
|---|---|
| **≤ 1 day** | All-out sprint — **never asks you anything**, silently infers defaults (teach-from-scratch), goes straight in |
| **1–3 days** | Hits the essentials, compresses the rest |
| **3–7 days** | Normal pace, asks which chapters you're solid on |
| **> 7 days** | Relaxed — for chapters you say you know, it **quizzes to verify** rather than taking your word |

**Preferences** (remembers your habits): whether walkthroughs append the "common mistakes" / "3-minute recap" closing blocks, reply language (Chinese / English / bilingual), and per-chapter mastery windows (`window-add` / `window-set-status`) — all persisted, changed by a single line anytime. See [`docs/language-policy.md`](docs/language-policy.md) and [`docs/skill-architecture.md`](docs/skill-architecture.md).

---

## Install

### Claude Code

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

Works from a project-local `.claude/skills/` or global `~/.claude/skills/`.

### Codex / Cursor / Windsurf / Antigravity

Clone the repo; have the agent read `AGENTS.md` (a one-screen fallback contract) or load `skills/`. These tools write files and run scripts directly.

### Web (ChatGPT / DeepSeek / Gemini)

Can't write local files — use the drop-in prompt instead: copy [`prompts/web_prompt.en.md`](prompts/web_prompt.en.md) and send it, then paste your materials.

> Full load matrix (per-agent support, entry files) in [`docs/agent-portability.md`](docs/agent-portability.md). The behavior source of truth is [`SKILL.md`](SKILL.md); [`SKILL.en.md`](SKILL.en.md) is its English rendering.

---

## Sub-skills

The monolith is split into 9 single-purpose sub-skills the agent loads on demand:

| Sub-skill | What it does |
|---|---|
| `exam-cram` | Orchestrator — runs the 4-step workflow + study-mode routing |
| `exam-ingest` | Builds the workspace from your materials (knowledge base + quiz bank + progress) |
| `exam-tutor` | Lazy per-chapter teaching (7-step walkthroughs, draw-it-runs-algorithm-first) |
| `exam-quiz` | Draws & grades from the bank (6 question types: MC / short / draw / fill / T-F / code) |
| `exam-review` | Mistakes and concept-confusion review |
| `exam-cheatsheet` | Pre-exam cheat sheet |
| `exam-audit` | Read-only workspace health check |
| `exam-help` | One-screen quick reference (workflow / modes / file conventions) |
| `confusion-tracker` | Logs concept questions as you go into a pre-exam blind-spot list |

All nine live under [`skills/`](skills/) (e.g. [`skills/confusion-tracker/SKILL.md`](skills/confusion-tracker/SKILL.md)), loaded on demand.

---

## Development

Zero-cost structured checks you can run often (no API spend):

```bash
python -m unittest discover -s tests -v          # unit tests (pure stdlib, in CI)
python scripts/validate_workspace.py path/to/ws  # validate a built exam-prep workspace
```

The real paid benchmark is expensive (tens of dollars / hours per matrix), run manually only — see [`benchmark/docs/running-real-runs.md`](benchmark/docs/running-real-runs.md) and the tiering in [`benchmark/docs/test_tiers.md`](benchmark/docs/test_tiers.md). Workspace file format: [`docs/file-format.md`](docs/file-format.md).

---

## FAQ

**No Python installed?** Fine. When the agent finds no Python it silently switches to "manual write mode", creating the knowledge-base tree itself — no difference to you.

**Only photos / scanned PDFs / a recording?** First transcribe with any free web multimodal AI ("extract the highlights and questions as plain text, keep the star/underline markers"), paste into a `.txt`, then have the agent build the workspace; the rest is plain-text and smooth. Recordings: transcribe first, then feed.

**Stuck on one quiz question?** Just say "this is too hard / I want to skip" — it files the item to your mistake log, lets you through, and revisits it at the end.

**How is this different from just dropping a folder at an AI?** Similar accuracy, but the skill is cheaper (only the relevant chapters per question, not the whole pile) and helps weaker models more. See the [report](benchmark/REPORT.en.md).

---

## License

[MIT](LICENSE). PRs for more subjects' templates or scripts welcome. Good luck on the cram. 🎓

<div align="center">

<a href="https://www.star-history.com/?repos=ZeKaiNie%2Funiversal-examprep-skill&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&theme=dark&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
 </picture>
</a>

</div>
