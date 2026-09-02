# Weak-model smoke test

`agent_smoke.py` runs a real coding agent through a four-turn study session on a course folder and scores the transcript. It answers the question the unit tests cannot: does a small model, given only `SKILL.md`, actually run the commands, cite pages, show the figures and keep the honesty labels?

```bash
# Google Antigravity (IDE must be running; find address/token in the language_server.exe command line)
python eval/agent_smoke.py antigravity --model flash_lite --install-skill --materials "D:/EEC 160" \
    --ls-address 127.0.0.1:PORT --csrf-token TOKEN --project-id PROJECT

# Claude Code headless
python eval/agent_smoke.py claude --model claude-haiku-4-5-20251001 --materials "D:/EEC 160"
```

Turns (Chinese by default, `--lang en` for English): 1) “read the skill, my materials are here, teach chapter 1”; 2) “next”; 3) “quiz me on chapter 5”; 4) “show me the reference answer and its figure”.

Score fields:

| field | what it checks |
|---|---|
| `commands_run` / `unknown_commands` | which `coach.py` sub-commands the agent ran; anything not in the CLI is a hallucinated command |
| `ran_setup` … `ran_check` | the loop was followed: setup → next → quiz → check |
| `citations` | `file p.N` references in the replies |
| `labels` | counts of 🟢 / 🟡 / ⚠️ |
| `images_embedded_in_reply`, `image_files_opened_by_tools` | the agent put a cropped figure in front of the student, or at least opened it |
| `reply_is_chinese` | replied in the student's language |

Raw transcripts land in `eval/results/` (git-ignored: they contain course text). Measured results are summarised in `docs/weak-model-test.md`.
