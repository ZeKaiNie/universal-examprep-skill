# Contributing

Small, focused pull requests are welcome. Please keep these three things true:

1. **A student can start in one command.** `coach.py setup` must stay fast (seconds) and must never require the agent to author JSON or run more than one command before teaching starts.
2. **The materials are the only source of course facts.** Anything the tool prints as 🟢 must be traceable to `file p.N`. Do not add features that generate questions or answers and present them as material.
3. **No mandatory dependencies.** The core runs on the standard library; `pypdf` stays optional. New heavy dependencies need a clear student-facing reason and a graceful fallback.

Before opening a PR:

```bash
python -m unittest discover -s tests -v
python coach.py setup samples/zh-data-structures && python coach.py next
```

Good contributions: parser fixes with a small fixture (see `tests/helpers.py` for DOCX/PPTX/PDF builders), better chapter or question detection on a real course layout you can describe, wording improvements in `SKILL.md`, and translations of the CLI strings in `coach/cli.py`.
