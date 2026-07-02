# Live-Agent Pilot Runbook

This runbook is for tiny, controlled live-agent calibration pilots that produce a replayable transcript for
`benchmark/drift/run_drift.py`. It does not replace the deterministic T4 replay tests, and it is not a full
benchmark.

## What A Live-Agent Pilot Means

A live-agent pilot is one short tutoring session run against a known, self-authored fixture. The goal is to
capture what the agent actually said and what files/tools it appeared to use, then convert that record into
T4 JSONL for deterministic scoring.

Keep it small:

- one session
- roughly 5-10 turns
- self-authored fixtures only
- no paid/full benchmark
- no external API calls unless a future task explicitly approves them
- stop immediately if the session is clearly broken

> **Automated mode (T5c)**: `run_live_smoke.py` runs this whole pilot as ONE command
> (drive → record → convert → score); this runbook stays the manual/fallback path. See README §T5c.

## Three Modes

| Mode | What Runs | What It Proves |
| :-- | :-- | :-- |
| Deterministic replay | Existing JSONL under `benchmark/drift/transcripts/` | The harness catches known drift patterns in scripted transcripts. |
| Codex self-run structured pilot | Codex writes a small session log from its own controlled tutor responses | The workflow can be manually exercised, but it is not an independent black-box model run. |
| True black-box agent pilot | A separate live agent is prompted and recorded | The external agent's observed behavior on that small session. Still not statistical proof. |

## Recommended Fixture

Start with:

```text
benchmark/drift/fixtures/mini_course_long
benchmark/drift/scenarios/long_session_basic.json
```

This fixture is self-authored and already aligned with the T4 drift scenario. Do not use private course
material, EEC160 material, or copyrighted lecture content for committed fixtures.

## Fill The Markdown Session Log

Use the template:

```bash
python benchmark/drift/convert_session_log.py --template \
  benchmark/drift/templates/live_session_template.md > /tmp/live_session.md
```

On Windows, prefer opening `benchmark/drift/templates/live_session_template.md` in a UTF-8 capable editor and
saving a copy under `%TEMP%`, for example:

```powershell
Copy-Item benchmark\drift\templates\live_session_template.md $env:TEMP\live_session.md
```

Each turn uses this shape:

````markdown
## Turn 1
kind: resume
phase_context: 1

### User
我回来了，继续上次的复习。

### Assistant
🟢 来自资料：当前阶段是阶段1，继续栈/队列复习。

### Events
- read_file: references/wiki/ch1_stack_queue.md
- write_file: study_progress.md

### Files After: study_progress.md
```text
# 复习进度（study progress）

当前阶段：1
```
````

Supported turn fields:

- `kind` optional; common values are `resume`, `quiz`, `explanation`, `confusion`
- `phase_context` optional integer
- `tokens_in`, `tokens_out`, `cost_usd` optional non-negative accounting fields; `cost_usd` must be finite

Supported sections:

- `### User` required
- `### Assistant` required
- `### Events` optional, with lines like `- read_file: references/wiki/ch1_stack_queue.md`
- `### Files After: path` optional, with a fenced code block containing the full file snapshot

If user or assistant text needs to show this adapter syntax literally, put the example inside a fenced code
block so reserved headings such as `### Events` stay message content.
If a `Files After` snapshot itself contains a fenced code block, use a longer outer fence such as four
backticks so the inner triple-backtick block stays part of the snapshot.

Supported event types are deliberately narrow: `read_file` and `write_file`. Typos such as `readfile` are
malformed and the converter exits with code `2`.

Tracked writes must include snapshots in the same turn. If `### Events` records `- write_file:
study_progress.md` or `- write_file: study_plan.md`, include the matching `### Files After:
study_progress.md` or `### Files After: study_plan.md` block. Without that snapshot, T4 cannot score
progress-row persistence or plan drift accurately, so the converter rejects the log.
For these tracked files, `./` prefixes and Windows-style backslashes are normalized to the canonical
`study_progress.md` / `study_plan.md` keys before JSONL is emitted.

## Convert To T4 JSONL

Validate only:

```bash
python benchmark/drift/convert_session_log.py \
  --in /tmp/live_session.md \
  --check
```

Convert:

```bash
python benchmark/drift/convert_session_log.py \
  --in /tmp/live_session.md \
  --out /tmp/live_session.jsonl
```

The converter reads and writes UTF-8 explicitly, preserves Chinese text and emoji provenance labels, and exits
with code `2` for malformed input.

## Run T4 Scoring

```bash
python benchmark/drift/run_drift.py \
  --scenario benchmark/drift/scenarios/long_session_basic.json \
  --transcript /tmp/live_session.jsonl \
  --json-out /tmp/live_metrics.json
```

Do not lower drift thresholds just to make a tiny converter fixture pass. If a tiny log is only meant to test
the adapter, test loader compatibility and document that it is not a full drift pass.

## Windows And UTF-8

Avoid legacy shell encodings when the log contains Chinese or emoji labels such as `🟢 来自资料` and
`🟡 AI补充，可能与你老师讲的不完全一致`.

Safer habits:

- use explicit `--out` files instead of shell redirection for JSONL output
- use a UTF-8 capable editor
- avoid piping emoji/Chinese through legacy Windows PowerShell encodings
- if piping in PowerShell is unavoidable, set UTF-8 first:

```powershell
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
```

## Token And Spend Caps

Before running a true black-box pilot, decide the cap in writing:

- maximum one session
- maximum 10 turns unless a task explicitly says otherwise
- no model variants
- no full benchmark matrix
- no private materials
- no API keys in logs, prompts, or transcripts

If the environment cannot run a separate black-box agent, say so and record the run as a Codex self-run
structured pilot rather than pretending it was independent.

## What Not To Commit

Do not commit:

- private live-run transcripts
- copyrighted course material
- EEC160 materials
- raw external model outputs from private sessions
- API keys, account identifiers, or secrets
- generated metrics from ad hoc live pilots unless explicitly approved

Small self-authored fixture logs under `benchmark/drift/fixtures/live_logs/` are okay when they are designed
for tests and contain no private material.
