<div align="center">

<img src="assets/exam-panic.jpg" width="200" alt="Exam Cram Coach" />

# Exam Cram Coach

*One night left. You studied nothing. It won't make anything up.*

English · [中文](README.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)
[![agents](https://img.shields.io/badge/works%20with-6%20agents-brightgreen.svg)](docs/agent-portability.md)
[![tokens](https://img.shields.io/badge/tokens-−90%25-orange.svg)](#how-it-works)

**Closed-book <10% → with the skill ~90%+** · tokens −90% · 100% honest abstention · 6 agents

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

Two unrelated open courses, **same model, same question, only "with vs. without materials" changes**. The questions are mined from course transcripts — specifics you can't guess (the professor's own examples, obscure studies he named, exact numbers). Without materials the model runs on priors and mostly collapses.

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_en.svg" width="600" alt="closed-book vs with the skill, correctness" /></div>

Correctness, higher is better (judge: Sonnet):

| Course · Model | Closed-book | Raw files + generic agent | With the skill |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 9% | 96% | **100%** |
| PSYC 110 · Sonnet 4.6 | 7% | 96% | 87% |
| PSYC 110 · Haiku 4.5 | 9% | 89% | **96%** |
| 6.006 · Haiku 4.5 | 31% | 85% | **89%** |

Two domains (humanities fact recall / algorithm reasoning), same result: **without materials the model can't answer; grounding is where the correctness comes from.** The skill matches a "raw files agent" on accuracy but costs less — it pulls only the compressed relevant chapters instead of re-scanning the whole file pile each question.

<details><summary>Cost per question (the skill's real edge: same accuracy, less spend)</summary>

The skill pulls only the compressed relevant chapters; the raw-files agent re-scans the whole pile each question — so at the same accuracy the skill costs less:

| Cost / question | Closed-book | Raw files agent | With the skill |
|---|:--:|:--:|:--:|
| PSYC 110 | $0.033 | $0.117 | **$0.102** |
| 6.006 | $0.034 | $0.066 | **$0.063** |

</details>

Full method, three-arm design, cost, human kappa calibration, limitations → **[test report](benchmark/REPORT.en.md)**.

---

## How it works

A ladder of "don't make it up unless you have to":

1. **Quiz only from the materials** — questions come from a `quiz_bank.json`, never improvised.
2. **Forced source labels** — every claim tagged `🟢 from your materials` / `🟡 AI-supplemented, may differ from your teacher` / `⚠️ AI-generated answer`, never passed off as the textbook.
3. **If it's not in the materials, say so** — abstains honestly on uncovered questions instead of fabricating (100% out-of-scope abstention, measured).
4. **Draw-it questions run the algorithm first** — for binary trees / graph traversal, it runs the real algorithm in the background to get the topology, then renders — no imagining.
5. **Figure-dependent questions fail closed** — a question that needs an image but has none is never served; no unanswerable question handed to the student.
6. **Lazy-loaded wiki** — chapter-sliced, loaded by progress, so long chats don't blow up the context. **Tokens −90%.**

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

> Full load matrix (per-agent support, entry files) in [`docs/agent-portability.md`](docs/agent-portability.md). The behavior source of truth is the Chinese [`SKILL.md`](SKILL.md); [`SKILL.en.md`](SKILL.en.md) is its English rendering.

---

## Sub-skills

The monolith is split into 9 single-purpose sub-skills the agent loads on demand:

| Sub-skill | What it does |
|---|---|
| `exam-cram` | Orchestrator — runs the 4-step workflow + study-mode routing |
| `exam-ingest` | Builds the workspace from your materials (wiki + quiz bank + progress) |
| `exam-tutor` | Lazy per-chapter teaching (7-step walkthroughs, draw-it-runs-algorithm-first) |
| `exam-quiz` | Draws & grades from the bank (6 question types: MC / short / draw / fill / T-F / code) |
| `exam-review` | Mistakes and concept-confusion review |
| `exam-cheatsheet` | Pre-exam cheat sheet |
| `exam-audit` | Read-only workspace health check |
| `exam-help` | One-screen quick reference (workflow / modes / file conventions) |
| `confusion-tracker` | Logs concept questions as you go into a pre-exam blind-spot list |

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

**No Python installed?** Fine. When the agent finds no Python it silently switches to "manual write mode", creating the wiki tree itself — no difference to you.

**Only photos / scanned PDFs / a recording?** First transcribe with any free web multimodal AI ("extract the highlights and questions as plain text, keep the star/underline markers"), paste into a `.txt`, then have the agent build the workspace; the rest is plain-text and smooth. Recordings: transcribe first, then feed.

**Stuck on one quiz question?** Just say "this is too hard / I want to skip" — it files the item to your mistake log, lets you through, and revisits it at the end.

**How is this different from just dropping a folder at an AI?** Similar accuracy, but the skill is cheaper (only the relevant chapters per question, not the whole pile) and helps weaker models more. See the [report](benchmark/REPORT.en.md).

---

## License

[MIT](LICENSE). PRs for more subjects' templates or scripts welcome. Good luck on the cram. 🎓

<div align="center">

[![Star History](https://api.star-history.com/svg?repos=ZeKaiNie/universal-examprep-skill&type=Date)](https://star-history.com/#ZeKaiNie/universal-examprep-skill&Date)

</div>
