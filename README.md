<div align="center">

<img src="assets/exam-panic.png" width="160" alt="Exam Cram Coach" />

# Exam Cram Coach

*Drop in your course folder. Get a tutor that teaches from it, quizzes from it, and remembers where you stopped.*

English · [中文](README.zh.md)

[![MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/ZeKaiNie/universal-examprep-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/ZeKaiNie/universal-examprep-skill/actions)

</div>

An [Agent Skill](SKILL.md) plus one small Python tool. Give any coding agent (Claude Code, Codex, Cursor, Gemini CLI, …) your slides, notes, homework and past papers. It:

- reads PDF / PPTX / DOCX / Markdown / TXT / HTML and splits them into chapters,
- crops the figures out of the lecture notes and the printed questions/solutions, so the tutor can show them in chat,
- pulls real questions and reference answers out of homework and exams (matching `hw2.pdf` with `hw2solutions.pdf`, `Problem 1.3.10` with its solution),
- teaches chapter by chapter, citing `file p.N` for every claim,
- quizzes you only from those questions, keeps mistakes and notes,
- and labels every sentence: 🟢 from your materials · 🟡 AI supplement · ⚠️ AI-generated answer.

Setup takes about a second, even for a whole course. Everything lives in one `exam-cram/` folder next to your files.

## Try it in two minutes

```bash
git clone https://github.com/ZeKaiNie/universal-examprep-skill exam-cram-coach
cd exam-cram-coach
pip install pypdfium2      # optional: PDF text + figure cropping (pypdf also works for text only)
python coach.py setup samples/zh-data-structures --days 2
python coach.py next       # first teaching slice, with sources
python coach.py quiz       # questions from the homework and mock exam
```

Then tell your agent:

```text
Use the Exam Cram Coach skill. My materials are in D:\Courses\Algorithms, the exam is in 3 days, start from chapter 1.
```

The agent runs `setup`, shows you the chapter list, and starts teaching from `next`.

Real open-course data to play with: `python samples/fetch.py` downloads MIT 6.006 lecture notes with Quiz 1 and its official solutions, and four Open Yale PSYC 110 lecture transcripts (both CC BY-NC-SA, see [samples/README.md](samples/README.md)).

## What the agent does with it

| You say | It runs | You get |
|---|---|---|
| “start” / “next” | `coach.py next` | The next slice of your material, explained in plain words with `[lec2.pdf p.3]` sources, plus the figures cropped from those pages |
| a question | `coach.py ask "…"` | An answer built only from matching passages, or “the materials do not cover this” |
| “quiz me” | `coach.py quiz` → `check` → `answer` | Homework/exam questions with their printed figure, graded against the reference answer (with its diagram), mistakes remembered |
| “I’m done with this chapter” | `coach.py note --type summary` + `done` | Chapter marked *verified* (you answered a material question right) or *covered* |
| “cheat sheet” | `coach.py cheatsheet` | `cheatsheet.md` built from your summaries, confusions and mistakes |
| new chat, days later | `coach.py status` | Exactly where you stopped |

## Install as a skill

Copy or clone this folder into your agent's skills directory (for example `~/.claude/skills/exam-cram-coach`, `~/.codex/skills/…`, `~/.cursor/skills/…`, `~/.gemini/antigravity/skills/…`) and make sure `python` is on the PATH. `pip install pypdfium2` is the only optional dependency: it gives PDF text and figure cropping (`pypdf` alone gives text). Without either, PDFs are listed as “needs pypdfium2” and DOCX/PPTX/Markdown still work. Scanned or handwritten pages (for example your own submitted homework) are detected and skipped, never shown as answers.

Small or local models: run `setup --slice 2000` so each teaching slice fits a short context; every command ends with the next command to run, so the model never has to remember the CLI. The whole skill text is about 1,100 words.

## Files in `exam-cram/`

| File | Purpose |
|---|---|
| `study_state.json` | progress, current chapter, results, mistakes, notes (the only state) |
| `progress.md`, `notebook.md` | human-readable views of the state |
| `chapters/chNN_title.md` | chapter text with `[file p.N]` anchors |
| `quiz_bank.json` | questions with answers, sources, chapter and figure paths |
| `figures/`, `figures.json` | PNG crops: lecture figures per page, question figures, answer figures |
| `index.json` | retrieval chunks for `ask` |
| `cheatsheet.md` | output of `cheatsheet` |

## Commands

```text
setup <folder> [--days N] [--lang zh|en] [--name X] [--start N] [--slice CHARS] [--fresh]
status | next [--repeat|--back] | chapter N [--part K] | goto N [--restart]
ask "keywords" [-k 5] [--chapter N]
quiz [-n 3] [--chapter N] [--all] | check <id> | answer <id> right|wrong|skip [--note …]
done [--chapter N] | note "…" [--type summary|confusion|note] | mistakes [--answers]
cheatsheet [--out FILE] | figures [--chapter N] [--file F] [--page P] | figure <file> <page> [--crop x0,y0,x1,y1]
doctor | help
```

`--workspace PATH` (or `EXAM_CRAM_WORKSPACE`) selects a workspace; otherwise the last one used is remembered.

## Development

```bash
python -m unittest discover -s tests -v     # ~1 s, stdlib only (PDF tests skipped without pypdfium2)
python eval/agent_smoke.py claude --model claude-haiku-4-5-20251001 --materials <folder>   # drive a real agent, score the transcript
```

Version 5 is a ground-up rewrite: the previous 144k-line runtime (receipts, generation ledgers, crop reviews, benchmark harness, three-layer language dispatch) was replaced by ~1.8k lines that do the student-facing work. See [CHANGELOG.md](CHANGELOG.md), [docs/v5-refactor.md](docs/v5-refactor.md) (measured before/after) and [docs/feature-audit.md](docs/feature-audit.md) (which v4 features exist in v5).

## License

[MIT](LICENSE). Sample courses keep their own licenses (see `samples/README.md`).
