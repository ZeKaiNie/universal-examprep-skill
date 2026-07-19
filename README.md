<div align="center">

<img src="assets/exam-panic.png" width="200" alt="Exam Cram Coach" />

# Exam Cram Coach

*Turn your slides, homework, and past papers into a source-aware tutor that remembers your progress.*

English · [中文](README.zh.md)

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
3. Copy this message to the agent:

```text
Use Exam Cram Coach to review D:\Course Materials. I am starting from zero, my exam is tomorrow, reply in English, and begin with Chapter 1. Use lightweight on-demand mode.
```

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

Ordinary features are already on: lightweight page-by-page teaching, visual inspection, full walkthroughs, saved progress, and notes. Extensions are optional. The score below is a practical recommendation, not a quality claim: **5/5 = use whenever the host supports it; 4/5 = enable when the use case matches; 3/5 = only for a specific problem; 1/5 = usually leave off.**

| Extension | Recommendation | Default | Copyable prompt | Inherent limitations |
|---|:---:|---|---|---|
| Full wiki, standard bank, and source-conflict review | **4/5** | Off | Copy prompt ① below | Slower first run; review items must be resolved or reported. Skip it when the exam is tomorrow and you only need a few pages |
| One key question per teaching turn | **4/5** | Off | Copy prompt ② below | Excellent for beginners, but slower; it changes teaching cadence rather than proving mastery |
| Web Study Guide and printable PDF | **4/5** | Off | Copy prompt ③ below | Requires complete content, clean item crops, source checks, and page-by-page visual QA; costs time and disk; unavailable in lightweight mode |
| Native isolated child agent for each answer explanation | **5/5** | **On automatically only when the host proves the required capability** | Copy prompt ④ below | Available only for full v2 Study Guides; hosts without verified clean-context and tool/input restrictions must use the ordinary explanation path |
| Remote MinerU or Docling parsing | **3/5** | Off | Copy prompt ⑤ below | Worth considering for difficult scans or complex layouts; never downloaded or run locally by this project; output still needs review |
| Remote LangGraph orchestration | **1/5** | Off | Copy prompt ⑥ below | Usually unnecessary because the skill already has a persistent state machine; it cannot replace course truth or source receipts |
| Dense + sparse retrieval, RRF, and reranking | **1/5** | Experimental, unavailable | Copy prompt ⑦ below | Current evidence is insufficient; extra latency, size, dependencies, and false retrievals may outweigh any benefit, so BM25 remains the default |

#### Copyable extension prompts

① Full knowledge-base build

```text
Switch to full knowledge-base mode and organize the whole course into a chapter wiki and standard question bank. Before starting, tell me which files will be processed. Report every unresolved review item, missing figure, missing answer, and source conflict; do not silently skip them.
```

② One question per turn

```text
From now on, teach exactly one example per turn. Show the prompt and every required figure, explain what is asked, why the formula applies, each substitution or reasoning step, and why the answer follows. Save the walkthrough to the notebook before waiting for me to continue.
```

③ Visual Study Guide and printable PDF

```text
In full mode, create a visual Study Guide and printable version for the current chapter. Render formulas directly, crop question and answer images to the current item, and inspect every output page for missing figures, bad crops, mojibake, raw LaTeX, and unrelated content. Do not deliver an artifact that fails inspection.
```

④ Native isolated child-agent explanations

```text
Check whether this host officially supports a fresh independent child context and can restrict its input and tools to one item. If it does, enable native isolated child-agent explanations by default for every Study Guide item, passing only the current question, any official answer, target-only crops, language, and fixed explanation instruction. Do not require a separate external API. If the capability cannot be verified, keep ordinary explanations and tell me.
```

⑤ Remote MinerU / Docling

```text
These materials contain difficult scans or complex layouts. Check whether this host already has a remote MinerU or Docling integration; do not download, install, or run either locally. If available, disclose the service, exact files to upload, retention period, and privacy boundary, then wait for my separate consent.
```

⑥ Remote LangGraph

```text
First explain the concrete problem that the existing study state machine cannot solve and how remote LangGraph would solve it. Enable it only if this host already provides the remote service, it will not replace local course truth, and I explicitly consent after seeing the privacy boundary. Do not install LangGraph locally.
```

⑦ Experimental retrieval check

```text
Check whether a frozen real multi-course retrieval benchmark proves that dense plus sparse retrieval, fusion, and reranking pass recall, false-positive, stability, latency, and size gates. If complete evidence is missing, do not enable it and keep the default retriever.
```

**Our recommendation:** start with ordinary lightweight teaching. If you later build a full Study Guide, keep the native isolated child-agent explanation enabled when the host can actually enforce it. Add the full build and visual Guide only when you have enough time; use remote parsing only for genuinely difficult pages; normally leave LangGraph and experimental retrieval off.

If an extension is unavailable, the agent continues with ordinary features and names the limitation. It must never pretend that a remote parser or isolated child ran.

### Isolated explanations, external APIs, and privacy

The preferred isolated route stays inside the agent host you are already using. For each question, a fresh child agent receives only the fixed explanation instruction, that question, its official answer when one exists, the requested language, and target-only question/answer crops. It must not receive the main chat, the course wiki, other questions, or general workspace access. No separate API key is required, but the call still consumes the current host's model allowance and remains covered by that host's privacy policy.

Current official documentation supports a suitable native child-agent route in Codex, Claude Code, Gemini CLI, and Antigravity. Cursor documents a clean child context but also says child agents inherit parent tools, so it is enabled only when the running host can close that tool boundary. Windsurf does not currently document a general-purpose clean-context child agent for this job, so the extension stays off there unless a future capability check proves otherwise.

Calling a separate external model provider is only a fallback for a user who explicitly requests it. It remains off and requires two approvals: first a local, no-upload plan showing exact items, images, and call count; then exact-plan upload approval after current pricing and the provider's retention/privacy boundary are disclosed. Keep API keys in the host or operating system's secret storage, never in course files, logs, receipts, screenshots, chat, or Git. See [the optional external-model adapter guide](docs/openai-study-guide-adapter.md).

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

If your agent can use a terminal and the internet, it can do the installation for you. Copy this first; the agent may ask you to approve network access or writing outside the current project:

```text
Fetch the latest release from https://github.com/ZeKaiNie/universal-examprep-skill and install or safely update the Agent Skill named universal-exam-cram-coach in this host's officially supported user-level skills directory. Prefer the lightweight runtime release asset; if that route is unavailable, clone the repository. Back up an existing version before replacement, verify SKILL.md, and report the actual install path and version. Do not merely download it without loading it.
```

Platform-specific copy-and-paste options follow.

### Codex

```text
Install the latest version of universal-exam-cram-coach from https://github.com/ZeKaiNie/universal-examprep-skill into my Codex skills directory. Back up any older copy before replacement. After installation, verify SKILL.md, report the installed path and version, and tell me whether I need to start a new task for the skill to appear.
```

### Claude Code

```text
Install or update https://github.com/ZeKaiNie/universal-examprep-skill in ~/.claude/skills/universal-exam-cram-coach. Use the terminal and ask for permission when needed, preserve local changes, and ask before overwriting files. Then verify SKILL.md and report the installed version.
```

### Cursor

```text
Fetch the latest https://github.com/ZeKaiNie/universal-examprep-skill and install it as universal-exam-cram-coach in my Cursor user skills directory (~/.cursor/skills/ or ~/.agents/skills/). Back up an older copy before replacement, verify that Cursor discovers SKILL.md, and report the installed path and version.
```

### Windsurf

```text
Fetch the latest https://github.com/ZeKaiNie/universal-examprep-skill and install it as universal-exam-cram-coach in ~/.codeium/windsurf/skills/universal-exam-cram-coach. Ask before downloading, moving, or replacing files; back up an older copy. Then verify that Cascade discovers SKILL.md and report the version.
```

### Antigravity

```text
Fetch the latest https://github.com/ZeKaiNie/universal-examprep-skill and install it globally as universal-exam-cram-coach in ~/.gemini/config/skills/universal-exam-cram-coach. Request permission for network, terminal, or outside-workspace writes. Rescan skills, verify SKILL.md, and report the installed version.
```

### Gemini CLI

Gemini CLI has a native Git installer:

```bash
gemini skills install https://github.com/ZeKaiNie/universal-examprep-skill.git
```

Or ask it to do the same work:

```text
Install the latest Agent Skill from https://github.com/ZeKaiNie/universal-examprep-skill as universal-exam-cram-coach with Gemini CLI's built-in skills installer. Ask for permission if required, rescan skills, and report the installed version.
```

### Manual and web fallbacks

Only download `universal-exam-cram-coach.zip` yourself from the [latest release](https://github.com/ZeKaiNie/universal-examprep-skill/releases/latest) when the agent cannot access the network or run terminal commands. A web-only client that cannot write local files can use the [English web prompt](prompts/web_prompt.en.md), but it cannot fully reproduce persistent local state, strict item cropping, or the verified publication chain.

**For PDF-, formula-, and image-heavy studying, use the agent's desktop app or IDE whenever one exists.** A terminal is fine for installing and debugging, but terminal chat often cannot display local images, rich formulas, and clickable file links as clearly as a graphical client. Claude Desktop Code, Codex desktop, Cursor, Windsurf, and Antigravity are therefore better study surfaces than their terminal-only routes.

See [agent portability](docs/agent-portability.md) for official paths, capability boundaries, and source links. The compact English entry is [`locales/en/SKILL.md`](locales/en/SKILL.md).

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

See [workspace file formats](docs/file-format.en.md) for the full contract.

## Numbers

The skill's value is **grounding**: connecting what is in your materials but not in the model's prior knowledge, while making unsupported answers visible. The figures below are results from the named benchmark courses and models, not a guarantee for every subject or host (judge: Sonnet).

**① Material-specific retrieval improved on these runs.** Details mined from course transcripts—such as the professor's examples, obscure studies, and exact numbers—are difficult to answer from general knowledge alone:

<div align="center"><img src="benchmark/docs/img/hard_psyc_correct_en.svg" width="600" alt="materials-specific: closed-book vs with the skill" /></div>

| Course · Model | Closed-book | Raw files + generic agent | With the skill |
|---|:---:|:---:|:---:|
| PSYC 110 · Opus 4.8 | 11% | 98% | **100%** |
| PSYC 110 · Sonnet 4.6 | 13% | 100% | **100%** |
| PSYC 110 · Haiku 4.5 | 11% | 98% | **100%** |
| 6.006 · Haiku 4.5 | 45% | 89% | **91%** |

**② Out-of-scope abstention reached 100% in this benchmark slice.** Across the tested probes, three models and two courses abstained on every out-of-scope item with the skill and with raw files; closed-book measured 60%–90%. See the report for sample design and limitations.

<div align="center"><img src="benchmark/docs/img/oos_psyc_abst_en.svg" width="560" alt="out-of-scope probes: honest abstention rate" /></div>

In these runs, chapter retrieval had similar accuracy to the raw-files arm with the per-question costs shown below. The mechanism retrieves relevant chapter slices instead of rescanning the whole pile:

<details><summary>Cost per question</summary>

| Cost / question | Closed-book | Raw files agent | With the skill |
|---|:---:|:---:|:---:|
| PSYC 110 | $0.033 | $0.117 | **$0.102** |
| 6.006 | $0.034 | $0.066 | **$0.063** |

</details>

Full method, three-arm design, judge calibration, costs, and limitations → **[benchmark report](benchmark/REPORT.en.md)**. The benchmark files were never removed from the repository; v4.3 had only hidden this detailed README presentation during a rewrite.

## FAQ

**Why are there no images in Chapter 1?** Check that the question-side image is actually visible, not merely printed as a file path. Lightweight mode must inspect the current pages; full mode must have the relevant visual index/crops and no unresolved review issue. A chapter with missing required figures must not be called complete.

**Why do formulas still look like raw LaTeX?** Chat teaching should unpack formulas in plain language. A visual Study Guide must render formulas into readable notation. Raw formula strings may remain as machine evidence, but they cannot be the only student-facing display.

**What if I only have scans or photos?** Lightweight mode can inspect the current image or rendered PDF page. Full mode puts uncertain recognition into a review queue instead of silently skipping it. Audio still needs a transcript first.

**What if Python is unavailable?** Use the reduced manual fallback only after confirming that the interpreter truly cannot start. A script error is not the same as having no Python; fix or report the actual error.

**Can I skip a hard question?** Yes. Say that you want to skip it; the item is saved to the mistake record, teaching continues, and the item returns during review.

**How do I audit an existing workspace?** Use [`exam-audit`](skills/exam-audit/SKILL.md) for a read-only check of material revisions, missing visuals, review issues, bank coverage, and learning state.

## For developers and maintainers

The root [`SKILL.md`](SKILL.md) routes activation. Shared behavior starts at [`skills/exam-cram/SKILL.md`](skills/exam-cram/SKILL.md), while [`locales/en/SKILL.md`](locales/en/SKILL.md) is the compact English compatibility and wording entry. The sub-skills cover orchestration, ingestion, tutoring, quizzes, review, Study Guides, cheat sheets, audits, help, and [concept-confusion tracking](locales/en/skills/confusion-tracker.md).

Useful checks:

```bash
python -m unittest discover -s tests -v
python scripts/validate_workspace.py path/to/workspace
python scripts/build_dist.py
```

See [skill architecture](docs/skill-architecture.en.md), [agent portability](docs/agent-portability.md), [PDF capability adapters](docs/pdf-capability-adapters.en.md), and [language policy](docs/language-policy.md). Release history belongs in the [English changelog](CHANGELOG.en.md).

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

The corresponding intent and coverage live in the [behavior-smoke coverage matrix](benchmark/docs/coverage-matrix.en.md).

</details>

## Contributing

Read the [contribution guide](CONTRIBUTING.md) before opening a PR. Debugging and reliability come first, followed by real-course teaching evidence and maintenance, then focused new features. Small PRs are welcome, and most can be merged after ordinary review and reasonable verification.

## License

[MIT](LICENSE). Contributions of subject templates, parser adapters, and real-course regression samples are welcome. Good luck with your exam. 🎓

<div align="center">

<a href="https://www.star-history.com/?repos=ZeKaiNie%2Funiversal-examprep-skill&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&theme=dark&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=ZeKaiNie/universal-examprep-skill&type=date&legend=top-left&sealed_token=q2eC20GmpWMHMen634RnHHNopx3dtYK6mzpbK0tB8B7sBn_LT0IKz-TYsaaWMY5xLJ6i7bsHedSzBxs4DU6cD5vZ8HFc-ZD2XAlqm5MnqBbf-ZbEq8zr2A" />
 </picture>
</a>

</div>
