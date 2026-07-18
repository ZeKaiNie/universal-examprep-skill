<div align="center">

<img src="assets/exam-panic.png" width="200" alt="Exam Cram Coach" />

# Exam Cram Coach

*One night left. You studied nothing. Every answer should show where it came from.*

English · [中文](README.zh.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**Grounded to your course materials** · bank-only quizzes · source-labeled AI supplements · chapter-sliced retrieval

</div>

You know him. Night before the exam, hair a mess, eyes wide open, hasn't read a single page of the course. This skill is for him: it grounds teaching in *your* materials, labels any AI supplement, and says when the materials do not support an answer.

**30-second start** — clone the repo, then say one line to your agent:

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
# In Claude Code / Cursor, say: "use this skill to set up my exam-prep space", then drop in your materials
```

---

## Before / after

**With the skill** — answers and key walkthroughs carry provenance, so you can check them:

> **[#vis_q1]** In the figure, which set relation does the shaded region show?
> **The intersection of A and B.**
> `Question source: hw02.pdf p.3 | Answer source: hw02_sol.pdf | 🟢 From your materials`

**Closed-book / plain agent** — sounds just as confident, but you can't tell if it's true:

> The shaded region is the **union**. <sub>(It's actually the intersection; no source label, nothing to check against — this is where hallucination happens.)</sub>

The difference isn't tone. It's whether the answer exposes the evidence and any AI-added part.

---

## Numbers

The skill's value is **grounding**: connecting what's in your materials but not in the model's head, while making unsupported answers visible. The figures below are results from the named benchmark courses/models, not a guarantee for every subject or host (judge: Sonnet):

**① Material-specific retrieval improved on these runs.** Details mined from course transcripts (the professor's examples, obscure studies, exact numbers) are difficult to answer from world knowledge alone; the table reports the measured result for each listed course/model:

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_en.svg" width="600" alt="materials-specific: closed-book vs with the skill" /></div>

| Course · Model | Closed-book | Raw files + generic agent | With the skill |
|---|:--:|:--:|:--:|
| PSYC 110 · Opus 4.8 | 11% | 98% | **100%** |
| PSYC 110 · Sonnet 4.6 | 13% | 100% | **100%** |
| PSYC 110 · Haiku 4.5 | 11% | 98% | **100%** |
| 6.006 · Haiku 4.5 | 45% | 89% | **91%** |

**② Out-of-scope abstention reached 100% in this benchmark slice.** Across the tested probes, three models and two courses abstained on every out-of-scope item with the skill (and with raw files); closed-book measured 60%–90%. See the report for sample design and limitations.

<div align="center"><img src="benchmark/docs/img/oos_psyc_abst_en.svg" width="560" alt="out-of-scope probes: honest abstention rate" /></div>

In these runs, chapter retrieval had similar accuracy to the raw-files arm with the per-question costs shown below. The mechanism retrieves relevant chapter slices instead of re-scanning the whole pile:

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

1. **Quiz only from the prebuilt bank** — questions come from the provenance-labeled `quiz_bank.json`, never improvised. Bank entries may be material-sourced or explicitly AI-generated; the label is never hidden.
2. **Forced source labels** — every claim tagged `🟢 From your materials` / `🟡 AI-supplemented — may differ from what your teacher taught` / `⚠️ AI-generated answer — not from your teacher or textbook`, never passed off as the textbook.
3. **If it's not in the materials, say so** — abstains honestly on unsupported questions instead of forcing an answer.
4. **Draw-it questions run the algorithm first** — for binary trees / graph traversal, it runs the real algorithm in the background to get the topology, then renders — no imagining.
5. **Figure-dependent questions won't be served without the figure** — no unanswerable question handed to the student.
6. **Chapter-sliced knowledge base, loaded on demand** — reads the current chapter slice instead of loading the full course into every turn.

The local core ingest path accepts PDF, DOCX, PPTX, XLSX, common standalone raster images, plain text, and Markdown. XLSX and raster files use dedicated standard-library routes: worksheets retain ordered cells, formulas/cached values, table metadata, and supported embedded raster images without requiring Excel; a standalone image retains its dimensions, digest, and local asset, then creates an OCR/vision review task when it has no usable UTF-8 sidecar. `scripts/ingest_course.py` is the single regular orchestrator: it builds structured content units and location anchors, compiles the wiki/bank, initializes state, and validates the result. It returns `0` for `ready` or `usable_with_gaps`; return code `10` means the build ran but unresolved review work blocks teaching. The agent then works through the typed review queue and append-only patch ledger before rebuilding and validating again—never by silently editing a generated wiki or pretending a warning disappeared. Large reviewed queues can use `ingest_review.py apply-batch --patch-list <json>`: every issue still keeps an independently validated patch and ledger entry, while expensive derivatives compile once per batch instead of once per issue.

An ingestion-v2 workspace also records one `.ingest/parser_receipts.json` entry for every discovered source. Each receipt binds the exact source digest and media type to the selected parser/version/config, the enumerated output anchors, and a local-only policy (`network=false`, `upload=false`, `install=false`); missing, stale, or inconsistent receipts fail validation. These booleans are validated configuration declarations: the bundled adapter does not perform those actions, but it neither sandboxes nor attests a host-supplied runner, whose operator must enforce the policy. Location IDs are deliberately not content-derived: `source_id` comes from the canonical relative path, while `unit_id` comes from source ID + location/bbox + kind + ordinal; source and unit hashes bind the exact revision. A DOCX `page` is only a logical segment split at an explicit page break—not a physical Word page. PPTX uses slide order, XLSX uses worksheet order, and a standalone raster uses one page-equivalent anchor.

The v2 fact layer derives exact duplicate groups and explicit cross-source conflicts without rewriting source occurrences or unit IDs. Near matches remain review candidates; similar formulas or visual variants at different locations inside one source are not treated as evidence that the locations assert the same claim. Source priority is evidence metadata, never a silent winner, and every unresolved cross-source conflict fails closed. An ingestion-v2 Study Guide must also pass the exact-location claim gate: each covered material assertion carries a strict `claim_id` on the same source reference and `source_unit_id`, with a compatible concept/formula/question/answer role. The typed-guide validator recomputes the `location_only` receipt against the canonical strict-JSON guide hash and the live source/content/group/conflict/claim hashes. It covers directly material-backed knowledge-point explanations in the rendered source language, every formula, printed prompt text that is not replaced by a full-prompt image, and every answer language labelled `material`; AI translations or supplements are never disguised as material claims. Unreferenced sidecar records are absent from the verified ID list, although the receipt still binds the full sidecar hash. This proves guide membership/text identity plus location/revision—not that the quote entails or semantically supports the authored claim. Legacy ingestion-v1 guides remain on their compatibility path rather than fabricating v2 evidence.

The answer-time retriever remains the zero-dependency BM25 path. Dense + sparse retrieval, RRF, and reranking are experimental candidates only; they may become opt-in only after a sufficient frozen **real, multi-course** recall Gold Set passes the documented safety/resource gate. The committed synthetic sample is intentionally insufficient evidence. Likewise, [`scripts/host_adapters/langgraph_exam.py`](scripts/host_adapters/langgraph_exam.py) is an optional adapter for hosts that already use LangGraph, not the workflow truth: local commands, `study_state.json`, `.ingest/`, and their receipts remain authoritative. See [workspace formats](docs/file-format.md), [retrieval evaluation](docs/retrieval-evaluation.md), and the [LangGraph host boundary](docs/langgraph-host-adapter.md).

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
| **≤ 1 day** | All-out sprint — skips opening clarification/preference questions and reflective follow-ups, silently infers defaults, and goes straight in; standard-bank drills/checkpoints may still verify mastery |
| **1–3 days** | Hits the essentials, compresses the rest |
| **3–7 days** | Normal pace, asks which chapters you're solid on |
| **> 7 days** | Relaxed — for chapters you say you know, it **quizzes to verify** rather than taking your word |

**Preferences** (remembers your habits): whether walkthroughs append the "common mistakes" / "3-minute recap" closing blocks, reply language (Chinese / English / bilingual), an explicit `no_questions` request (which suppresses all interactive questions and caps a phase at `covered_unverified`), and per-chapter mastery windows (`window-add` / `window-set-status`) — all persisted, changed by a single line anytime. See [`docs/language-policy.md`](docs/language-policy.md) and [`docs/skill-architecture.md`](docs/skill-architecture.md).

**Optional ordinary teaching cadence:** `batch` is the default for new and legacy state. In explicit full mode, choose `step_by_step` to receive one complete seven-step walkthrough per turn, in `teaching_examples.json` manifest order:

```bash
python scripts/update_progress.py --workspace <ws> set --interaction-style step_by_step
# Or pass --interaction-style step_by_step to exam_start.py confirm.
```

This preference is optional, not a fourth startup question. It is effective only on the full route with `no_questions=false`; otherwise the saved choice remains visible but dormant and effective cadence is `batch`. This release predates the explicit processing selector, so an absent `processing_mode` means its historical implicit full route; an explicitly present non-`full` value remains dormant. Under the ≤1-day tier, an agent does not ask for it. The selector covers full-mode teaching examples only—not the chapter bank, typed question units, or page batches—and reads manifest, state, baseline, and notebook from one locked snapshot. `Continue` is not completion evidence. One `record-taught-example` save binds a marked walkthrough to `{id, notebook_ref, notebook_block_sha256, manifest_item_sha256}` and to the current first-pending manifest item. Quiz/teaching/notebook/Guide IDs share one safe-Unicode 1–200-character contract; whitespace, Markdown/path separators, control/format/surrogate/replacement characters, and Unicode noncharacters are rejected. Unbound IDs remain valid batch history; a bound event must keep passing live notebook and manifest-revision checks after cadence changes, and `notebook_ref` cannot be shared by two bindings. Missing entries plus anchor/marker/hash/revision drift become pending with bounded machine-readable diagnostics. Reparse points, unsafe file types/escapes, invalid UTF-8, unterminated fences, malformed blocks, duplicate evidence, and out-of-roster evidence fail closed. A completed full phase with only structurally sound new-roster or recoverable-stale pending items mounts as `usable_with_gaps` for ordered repair, while Guide validation and phase completion remain strict and must be rebuilt/recompleted. Every retained baseline ID must have a current teaching snapshot in the same canonical chapter under exact `policy=append_only`—an item left only in the quiz bank does not count. All normal phase gates still apply, and `teaching_example_roster_exhausted=true` is not chapter completion, including when the roster is empty. Changing reply language does not automatically schedule an already evidenced item for reteaching; request a reteach explicitly when needed.

---

## Install

### Claude Code

**Recommended — the runtime bundle** (just the runtime skill, without benchmarks and development tests):

Download `universal-exam-cram-coach.zip` from the [latest release](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest) and unzip it into `.claude/skills/universal-exam-cram-coach/` (project-local or global `~/.claude/skills/`).

Basic TXT/Markdown/DOCX/PPTX/XLSX and standalone-raster metadata/sidecar ingestion uses the standard library. Before the first build, the agent runs the bundled dependency preflight (`scripts/check_deps.py`); if the selected PDF or visual route needs an optional package, it asks before installing it. Unsupported, encrypted, damaged, or scan-only content is reported into the review workflow instead of being silently skipped.

**Or clone the repo** (developer path — includes benchmarks, tests, and maintainer documentation):

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

### Codex / Cursor / Windsurf / Antigravity

Clone the repo; have the agent read `AGENTS.md` (a one-screen fallback contract) or load `skills/`. These tools write files and run scripts directly.

### Web (ChatGPT / DeepSeek / Gemini)

Can't write local files — use the drop-in prompt instead: copy [`prompts/web_prompt.en.md`](prompts/web_prompt.en.md) and send it, then paste your materials.

> Full load matrix (per-agent support, entry files) in [`docs/agent-portability.md`](docs/agent-portability.md). The trigger entry is [`SKILL.md`](SKILL.md), a language-neutral router. It loads the shared control rules under [`skills/`](skills/) plus the compact English compatibility/wording entry at [`locales/en/SKILL.md`](locales/en/SKILL.md); neither locale is a second behavior manual.

---

## Sub-skills

The monolith is split into 10 single-purpose skills the agent loads on demand:

| Sub-skill | What it does |
|---|---|
| `exam-cram` | Orchestrator — runs the 4-step workflow + study-mode routing |
| `exam-ingest` | Orchestrates PDF/DOCX/PPTX/XLSX/raster/text ingestion, typed AI review, compilation, and readiness validation |
| `exam-tutor` | Lazy per-chapter teaching (7-step walkthroughs, draw-it-runs-algorithm-first) |
| `exam-study-guide` | Compiles one chapter into formula-readable, self-contained HTML and optional visually checked PDF |
| `exam-quiz` | Draws & grades from the bank (6 question types: MC / short / draw / fill / T-F / code) |
| `exam-review` | Mistakes and concept-confusion review |
| `exam-cheatsheet` | Pre-exam cheat sheet |
| `exam-audit` | Read-only workspace health check |
| `exam-help` | One-screen quick reference (workflow / modes / file conventions) |
| [`confusion-tracker`](skills/confusion-tracker/SKILL.md) | Logs concept questions as you go into a pre-exam blind-spot list |

All ten live under [`skills/`](skills/) (e.g. [`skills/exam-study-guide/SKILL.md`](skills/exam-study-guide/SKILL.md)), loaded on demand. PDF tooling is routed per host without silent downloads; see [`docs/pdf-capability-adapters.md`](docs/pdf-capability-adapters.md).

Chapter artifacts are opt-in. The default `chat` output mode keeps teaching in the conversation (plus the normal progress/notebook files) and does not automatically build HTML/PDF. Say “save tokens / chat only” or set `--artifact-mode chat`; say “visual study guide / I want printable PDFs” or set `--artifact-mode visual` for automatic chapter HTML + visually checked PDF. An agent must never guess this from your subscription plan, and a one-off PDF request does not silently change the saved preference.

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

**No Python installed?** After confirming the interpreter truly cannot start, the core workspace can use a disclosed manual-write fallback with reduced validation. A script/data error while Python runs must be fixed or reported; it must not trigger that fallback. The MathML HTML/PDF renderer requires Python and fails loudly with the missing prerequisite.

**On a limited plan?** `artifact_mode=chat` is the safe default, so normal tutoring does not spend extra generation effort on chapter HTML/PDF. Switch to `visual` only when you want printable chapter artifacts; PDF rendering itself is local, but the richer material workflow can consume more context and generation.

**Only photos / scanned PDFs / a recording?** Give the original files to the ingest workflow. It renders/reads PDF pages where supported and puts each scanned, skipped, or review-needed item into a typed queue for AI takeover; the agent must claim and resolve it or report the exact file and reason. Audio still needs a transcript before ingestion.

**Stuck on one quiz question?** Just say "this is too hard / I want to skip" — it files the item to your mistake log, lets you through, and revisits it at the end.

**How is this different from just dropping a folder at an AI?** It adds a persistent state, chapter retrieval, a standard question bank, provenance labels, and fail-closed visual checks. The benchmark compares accuracy/cost for its tested courses; see the [report](benchmark/REPORT.en.md).

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
