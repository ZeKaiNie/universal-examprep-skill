<div align="center">

<img src="assets/exam-panic.png" width="200" alt="Exam Cram Coach" />

# Exam Cram Coach

*Turn your slides, homework, and past papers into a source-aware tutor that remembers your progress.*

English · [Chinese](README.zh.md)

[![stars](https://img.shields.io/github/stars/ZeKaiNie/universal-examprep-skill?style=flat&color=blue)](https://github.com/ZeKaiNie/universal-examprep-skill/stargazers)
[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

**Teach from your materials · Show figures before solving · Explain key questions step by step · Keep progress across chats**

</div>

Give the agent your course folder, then say how soon the exam is, where you want to start, and which reply language you want. It teaches each concept beside the matching examples from lectures, homework, quizzes, and practice exams. It explains which formula applies, how the values fit, and why the answer follows.

Its most important feature is visible provenance:

- 🟢 From your materials: traceable to the original file and location;
- 🟡 AI-supplemented — may differ from what your teacher taught;
- ⚠️ AI-generated answer — not from your teacher or textbook.

When the materials do not support a conclusion, the agent should say so instead of pretending to know.

## Start studying in one minute

1. [Install the skill](#install).
2. Put your slides, notes, homework, solutions, quizzes, and practice exams in one materials folder.
3. Tell the agent:

> Use Exam Cram Coach to review `D:\Course Materials`. I am starting from zero, my exam is tomorrow, reply in English, and begin with Chapter 1. Use lightweight on-demand mode.

4. The agent shows the absolute materials/workspace path, study mode, time budget, reply language, and processing choice before teaching.

You do not need to learn any commands first. Commands below are only for automation or troubleshooting.

## Choose a processing mode

The skill asks once. If you are unsure, choose lightweight on-demand.

| Mode | What it does | Best for | Limitations |
|---|---|---|---|
| **Lightweight on-demand (default, recommended)** | Processes only the pages you are studying now, inspects their visuals, teaches in chat, and saves progress and notes | An exam tomorrow, a large folder, or anyone who wants to start quickly | Does not organize the whole course in advance; one active batch has at most 8 primary pages; does not create a complete Study Guide or printable PDF; without an unchanged standard bank that existed before initialization, a chapter is capped at `covered_unverified` |
| **Full knowledge-base build** | Organizes the whole course into a chapter wiki, standard question bank, and review queue; can later produce chapter Study Guides | Large or messy courses, systematic review, and students willing to wait longer | Initial processing is slower and uses more disk; scans, complex layouts, and question-answer pairing can still need AI or human review; a completed build does not prove every detail was recognized correctly |

Lightweight mode saves the cost of processing the whole course up front. It does not shorten the explanation shown to the student.

Processing and output are separate choices:

- Chat is the default output: teaching appears in the conversation and notebook, without automatic HTML/PDF work.
- Visual output creates a web Study Guide and can produce a printable PDF.
- In lightweight mode, a saved visual preference stays dormant. Switch explicitly to full mode before building a Study Guide.

## How it teaches a chapter

Each chapter continues until its concepts and matching examples have been covered:

1. Explain the concept in everyday language without assuming prior knowledge.
2. Show only the relevant question text and figures, not a full page of unrelated items.
3. State what the problem asks and what values can be read from the figure.
4. Explain why the formula or rule applies.
5. Substitute values and work through every step.
6. Explain in beginner-friendly language why the answer follows.
7. Link the concept, question, and answer back to their original file and location.

If a question or solution depends on an image, the image must be visibly rendered before it is used. Questions with missing required figures are not served. Tree, traversal, state-machine, and similar problems are computed deterministically before a diagram is drawn.

Quizzes come only from the workspace's standard question bank; temporary AI-written questions are never passed off as course quizzes. Wrong answers, skipped items, and “why/how” questions are saved for review.

## Ordinary features and extensions

Ordinary features cover what most students need. Extensions are off by default because they take longer, use more storage, or depend on an external service and separate permission.

| Feature | Default | How to enable | Inherent limitations |
|---|---|---|---|
| Lightweight teaching, visual inspection, detailed walkthroughs, progress, and notes | On | Choose lightweight on-demand, or accept the default | Processes only the current pages and does not prebuild the whole course |
| Full wiki, standard bank, and source-conflict review | Off | Explicitly choose a full knowledge-base build | Slower first run; unresolved review items must be fixed or clearly reported |
| One key question per teaching turn | Off | In full mode, ask for one question at a time or set `--interaction-style step_by_step` | Changes teaching cadence only; it does not prove quiz mastery or chapter completion, and concurrent tutors can still select the same pending item |
| Web Study Guide and printable PDF | Off | In full mode, explicitly request a visual Study Guide or printable version | Requires complete content, item-specific crops, source checks, and page-by-page visual QA; costs time and disk; unavailable in lightweight mode |
| A separate external-model call for each answer explanation | Off | First approve local-only planning; after reviewing exact items, images, call count, privacy boundary, and a current-price estimate, approve the exact upload plan | Not every agent host can make fresh, stateless, tool-disabled calls; a ChatGPT or Codex subscription is separate from API billing; N items require at least N calls; listed questions, answers, or crops are sent to the external provider |
| Remote MinerU or Docling parsing | Off | The user must name it and the host must already provide a remote integration; confirm upload terms separately | This project never downloads or runs either parser on the student's computer; the service may be unavailable, expands the privacy boundary, and still needs review |
| Remote LangGraph orchestration | Off | The user must name it and the host must already provide a remote integration | It can arrange steps but cannot replace local course state or source records; no heavy local dependency is installed automatically |
| Dense + sparse retrieval, RRF, and reranking | Experimental, off | Consider only after real multi-course recall testing shows a stable advantage | Current evidence is insufficient; it can increase latency, size, dependencies, and false retrievals, so BM25 remains the default |

If an extension is unavailable, the agent must continue with ordinary features and state the limitation. It must not invent a remote-parse or isolated-call receipt.

### External model calls and privacy

Ordinary features do not start a second provider API call by themselves. Your materials are still handled under the privacy policy of the agent host you are already using; this is not a promise that the entire session is offline.

The per-item external-call extension requires two separate approvals:

1. A planning approval lists the exact questions, images, call count, and expected output locally without uploading course content.
2. After you review that scope, current pricing assumptions, and the provider's retention/privacy boundary, upload approval must bind the exact plan ID and call count.

Keep API keys in the host or operating system's secret storage, never in a course workspace, receipt, log, or Git. A git-ignored `.env.local` is only a plaintext fallback, not an encrypted vault. Setup and failure behavior are documented in [the external-model adapter guide](docs/openai-study-guide-adapter.md).

## Study mode, time budget, and language

The skill remembers these choices across chats:

| Choice | Options | Effect |
|---|---|---|
| Starting point | Teach from scratch / Start mid-course and shore up weak spots / Fill the gaps | Controls explanation depth and the starting chapter |
| Time before the exam | ≤ 1 day / 1–3 days / 3–7 days / > 7 days | Controls pace and how often earlier material is checked |
| Reply language | Chinese / English / bilingual | Controls agent-authored explanations; source evidence stays in its original language |

With ≤ 1 day, the agent skips unnecessary opening and reflective questions and starts with the essentials; **standard-bank drills/checkpoints may still verify mastery**. If you explicitly ask for no questions, it saves `no_questions=true`; the chapter is then capped at `covered_unverified` rather than pretending that a checkpoint was passed.

Bilingual replies must be chosen explicitly. English mode does not mechanically append Chinese to every paragraph, and Chinese mode does not mechanically append English. Bilingual mode supplies both languages for each complete explanation block.

## Install

### Claude Code

Download `universal-exam-cram-coach.zip` from the [latest release](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest), then unzip it into either location:

- Project-local: `.claude/skills/universal-exam-cram-coach/`
- Global: `~/.claude/skills/universal-exam-cram-coach/`

You can also clone the complete repository. It includes tests, benchmark data, and maintainer documentation:

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill .claude/skills/universal-exam-cram-coach
```

### Codex, Cursor, Windsurf, and Antigravity

Clone the repository and have the agent read [`AGENTS.md`](AGENTS.md) or load [`skills/`](skills/). These hosts can usually work with local files and scripts directly.

### ChatGPT, DeepSeek, Gemini, and other web clients

If the client cannot write local files, copy the [English web prompt](prompts/web_prompt.en.md), send it, and then provide your materials. This is a portable fallback; it cannot fully reproduce persistent local state, strict item cropping, or the verified publication chain.

See [agent portability](docs/agent-portability.md) for the support matrix. The compact English entry is [`locales/en/SKILL.md`](locales/en/SKILL.md), and the English web fallback is [`prompts/web_prompt.en.md`](prompts/web_prompt.en.md).

## What appears in the study workspace

Students usually need to recognize only these paths:

| Path | Purpose |
|---|---|
| `study_state.json` | Source of truth for learning progress, modes, and preferences; do not edit it by hand |
| `study_progress.md` | Human-readable progress summary generated from the state |
| `notebook/` | Taught material, mistakes, and chapter notes |
| `references/wiki/` | Chapter knowledge base created by full mode |
| `references/quiz_bank.json` | Standard question bank with source records |
| `.ingest/` | Full-build parsing records, review items, and provenance evidence; mainly for the agent and audit tools |
| `.lightweight/` | Current-page images and processing records for lightweight mode |

See [workspace file formats](docs/file-format.md) for the full contract.

## How to read the benchmark

The published benchmark compares three primary arms: `closedbook`, `rawfiles`, and `skill`. A material/dump-all route appears only as a legacy stress footnote, not as the primary fair comparison. On the named courses, models, and question sets, course-specific accuracy improved and unsupported questions were more likely to receive an honest abstention.

Those results describe only the reported courses, models, prompts, judge, and datasets. They do not guarantee the same improvement for every subject or agent host. The goal is traceable answers and visible gaps, not a claim that the skill is always correct. See the [full benchmark report](benchmark/REPORT.en.md) for the exact results, costs, method, and limitations.

## FAQ

**Why are there no images in Chapter 1?** Check that the question-side image is actually visible, not merely printed as a file path. Lightweight mode must inspect the current pages; full mode must have the relevant visual index/crops and no unresolved review issue. A chapter with missing required figures must not be called complete.

**Why do formulas still look like raw LaTeX?** Chat teaching should unpack formulas in plain language. A visual Study Guide must render formulas into readable notation. Raw formula strings may remain as machine evidence, but they cannot be the only student-facing display.

**What if I only have scans or photos?** Lightweight mode can inspect the current image or rendered PDF page. Full mode puts uncertain recognition into a review queue instead of silently skipping it. Audio still needs a transcript first.

**What if Python is unavailable?** Use the reduced manual fallback only after confirming that the interpreter truly cannot start. A script error is not the same as having no Python; fix or report the actual error.

**Can I skip a hard question?** Yes. Say that you want to skip it; the item is saved to the mistake record, teaching continues, and the item returns during review.

**How do I audit an existing workspace?** Use [`skills/exam-audit/`](skills/exam-audit/) for a read-only check of material revisions, missing visuals, review issues, bank coverage, and learning state.

## For developers and maintainers

The root [`SKILL.md`](SKILL.md) routes activation. Shared behavior lives under [`skills/`](skills/), while [`locales/`](locales/) contains compact language compatibility and wording entries. The sub-skills cover orchestration, ingestion, tutoring, quizzes, review, Study Guides, cheat sheets, audits, help, and concept-confusion tracking at [`skills/confusion-tracker`](skills/confusion-tracker/SKILL.md).

Useful checks:

```bash
python -m unittest discover -s tests -v
python scripts/validate_workspace.py path/to/workspace
python scripts/build_dist.py
```

See [skill architecture](docs/skill-architecture.md), [agent portability](docs/agent-portability.md), [PDF capability adapters](docs/pdf-capability-adapters.md), and [language policy](docs/language-policy.md). Release history belongs in [`CHANGELOG.md`](CHANGELOG.md).

<details>
<summary>Maintainer-only behavior-smoke scenario registry</summary>

These fixture identifiers are kept here so documentation coverage cannot silently drift from the deterministic smoke suite:

- `quiz_bank_only`
- `provenance_labels`
- `hint_skip_mistake_archive`
- `confusion_tracking`
- `checkpoint_recovery`
- `no_python_fallback`
- `zero_basic_key_question`
- `time_budget_no_questions`
- `knowledge_window_recheck`
- `teaching_template`
- `visual_first_assets`
- `lazy_load_best_effort`
- `scope_override`
- `language_first_ask`
- `artifact_mode_routing`
- `notebook_persist_ok`
- `workspace_confirm_ok`

The corresponding intent and coverage live in the [behavior-smoke coverage matrix](benchmark/docs/coverage-matrix.md).

</details>

## License

[MIT](LICENSE). Contributions of subject templates, parser adapters, and real-course regression samples are welcome. Good luck with your exam. 🎓
