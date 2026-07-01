# -*- coding: utf-8 -*-
"""Tier 4 long-horizon drift harness — DETERMINISTIC REPLAY (default; no LLM / network / API keys / deps).

Replays a SCRIPTED multi-turn tutoring transcript (JSONL) + workspace snapshots against a self-authored
fixture and computes drift metrics over a longer review session, then checks them against the scenario's
thresholds:

  * goal retention            — does the assistant stay on the exam-prep goal, or wander off?
  * plan adherence            — is study_plan.md's phase sequence left intact (no silent delete/reorder/add)?
  * quiz-bank fidelity         — are quizzed items real bank ids, in the requested phase, and not invented?
  * checkpoint recovery       — on resume, does it continue from the current phase (not restart at phase 1)?
  * provenance fidelity       — do later explanation turns still carry the canonical 🟢/🟡/⚠️ labels?
  * mistake/confusion persistence — are archived rows added and never silently dropped across the session?
  * wiki lazy-load / overread — reads scoped to the phase's chapter; optional token/cost accounting.

This is DETERMINISTIC REPLAY of a scripted transcript. It does NOT run a real agent — so it measures
whether a *recorded* session drifts, not whether a live model will. Real long-session LLM runs remain a
future/opt-in path (`--llm`, gated by RUN_SKILL_DRIFT_LLM=1) that is a SKELETON here and never returns
success. Nothing in this file calls a model, reads a key, hits the network, or runs a paid benchmark.

Exit codes: 0 = all scenarios pass their thresholds · 1 = a threshold failed · 2 = malformed input / bad file.

    python benchmark/drift/run_drift.py --scenario benchmark/drift/scenarios/long_session_basic.json \
                                        --transcript benchmark/drift/transcripts/good_session.jsonl
    python benchmark/drift/run_drift.py --all
    python benchmark/drift/run_drift.py --all --json-out /tmp/drift_summary.json
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))                      # repo root (…/benchmark/drift → repo)

# canonical provenance labels — mirror T2 / docs/language-policy.md (single source of truth)
CANON_LABELS = ["🟢 来自资料", "🟡 AI补充，可能与你老师讲的不完全一致", "⚠️ AI生成答案，非老师/教材提供"]

# sensible defaults (a scenario may override any of these)
DEFAULT_GOAL_MARKERS = ["期末", "复习", "备考", "考试", "exam", "review"]
DEFAULT_UNRELATED_PHRASES = [
    "switch course", "换一门课", "换个科目", "new study plan unrelated", "与考试无关", "放弃这次考试",
    "不用管考试", "咱们聊点别的", "let's talk about something else", "改学别的", "别复习了", "别刷题了",
    "别管期末", "这门课不重要", "以后用不上", "抛开这门课", "随它去", "写代码项目", "去做副业", "聊聊人生",
    "职业规划", "forget about studying", "don't study", "tell you a story",
]
DEFAULT_REFUSAL_PHRASES = ["我不能继续这个复习", "无法继续原计划", "拒绝按考试计划", "i won't continue the exam plan"]
EXPLAIN_TRIGGERS = re.compile(r"解释|讲讲|讲一下|说明|为什么|讲解|explain|why", re.I)
QUIZ_TRIGGERS = re.compile(r"考我|再考|出题|测测|刷题|(?:再来一|来一|下一|再出一)(?:道题?|题)"
                           r"|quiz me|quiz from|test me|next question", re.I)
RESUME_TRIGGERS = re.compile(r"回来了|继续复习|接着上次|回来继续|resume|continue where", re.I)
RESTART_PHRASES = re.compile(r"从头开始|重新开始|从头再来|重头|restart|start over|从第?1章重新|从阶段1重新", re.I)
PLAN_CHANGE_REQUEST = re.compile(r"改计划|调整计划|重新规划|换个计划|change.*plan|revise.*plan", re.I)


class DriftError(Exception):
    """Malformed input — surfaces as exit code 2."""


# ---------------- IO ----------------

def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_jsonl(path, label):
    if not os.path.isfile(path):
        raise DriftError("找不到%s文件: %s" % (label, path))
    rows = []
    for ln, line in enumerate(_read(path).splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
        except ValueError as e:
            raise DriftError("%s 第 %d 行不是合法 JSON: %s" % (label, ln, e))
        if not isinstance(d, dict):
            raise DriftError("%s 第 %d 行必须是 JSON 对象" % (label, ln))
        for k in ("assistant", "user"):                          # replay calls string methods on these
            if k in d and not isinstance(d[k], str):
                raise DriftError("%s 第 %d 行的 %s 必须是字符串" % (label, ln, k))
        if "files_after" in d and not isinstance(d["files_after"], dict):
            raise DriftError("%s 第 %d 行的 files_after 必须是对象" % (label, ln))
        if "events" in d:
            if not isinstance(d["events"], list):
                raise DriftError("%s 第 %d 行的 events 必须是数组" % (label, ln))
            if any(not isinstance(e, dict) for e in d["events"]):
                raise DriftError("%s 第 %d 行的 events 元素必须都是对象" % (label, ln))
        rows.append(d)
    return rows


def load_scenario(path):
    if not os.path.isfile(path):
        raise DriftError("找不到 scenario 文件: %s" % path)
    try:
        sc = json.loads(_read(path))
    except ValueError as e:
        raise DriftError("scenario 不是合法 JSON: %s" % e)
    if not isinstance(sc, dict):
        raise DriftError("scenario 必须是 JSON 对象")
    for k in ("name", "fixture", "thresholds"):
        if k not in sc:
            raise DriftError("scenario 缺必需字段 %r" % k)
    if not isinstance(sc["thresholds"], dict):
        raise DriftError("scenario.thresholds 必须是对象")
    for k in ("fixture", "transcript"):                           # path fields must be strings before _resolve()
        if k in sc and not isinstance(sc[k], str):
            raise DriftError("scenario.%s 必须是字符串路径" % k)
    return sc


def _resolve(path):
    """Resolve a scenario-relative path against the repo root (paths are repo-relative)."""
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


# ---------------- fixture parsing ----------------

def _as_phase(v):
    """Normalize a phase value to int — accepts int or a numeric string ('2'); else None. (docs/
    file-format.md and the validator allow quiz_bank `phase` to be an int OR a string.)"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


# a phase reference in EITHER order: '阶段N' / '第N阶段' / 'phase N'
_PHASE_RE = r"(?:阶段\s*(\d+)|第\s*(\d+)\s*阶段|[Pp]hase\s*(\d+))"


def _phase_num_in(s):
    """First phase number in a string (阶段N / 第N阶段 / phase N), else None."""
    m = re.search(_PHASE_RE, s or "")
    return int(next(g for g in m.groups() if g)) if m else None


def _all_phase_nums(s):
    """All phase numbers mentioned in a string (阶段N / 第N阶段 / phase N)."""
    return [int(next(g for g in m.groups() if g)) for m in re.finditer(_PHASE_RE, s or "")]


def _is_structural(line):
    return line.startswith("#") or line.startswith("|") or bool(re.match(r"[-*]\s", line))


def parse_plan_phases(text):
    """Ordered (deduped) list of phase numbers from a study_plan.md. Accepts this harness's headings
    ('## 阶段2：…'), the real ingest table ('| **阶段 1** | … |') / checklist, and the '第N阶段' order.
    Table + checklist repeat the same phases, so dedupe while preserving first-seen order."""
    phases = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        n = _phase_num_in(s) if _is_structural(s) else None
        if n is not None and n not in phases:
            phases.append(n)
    return phases


def parse_plan_sig(text):
    """Like parse_plan_phases but each phase carries its TOPIC (name) — so renaming a phase in place
    (阶段2：树 → 阶段2：职业规划) is a detected plan change even though the number is unchanged. Ordered list of
    (num, normalized_topic), deduped by first-seen number (table wins over checklist)."""
    sigs, seen = [], set()
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not _is_structural(s):
            continue
        m = re.search(_PHASE_RE, s)
        if not m:
            continue
        num = int(next(g for g in m.groups() if g))
        if s.startswith("|"):                                     # table row → topic is the NEXT cell
            cells = [c.strip(" *`") for c in s.strip("|").split("|")]
            topic = ""
            for ci, c in enumerate(cells):
                if _phase_num_in(c) == num:
                    topic = cells[ci + 1] if ci + 1 < len(cells) else ""
                    break
        else:                                                     # heading/bullet → text after the phase ref
            topic = s[m.end():]
        topic = re.sub(r"references/wiki/\S+", "", topic)          # drop wiki-path noise
        topic = re.sub(r"[\s：:（）()【】\[\]\-—。，,、*`]+", "", topic)   # keep just the name characters
        sig = (num, topic)
        if sig in seen:                                           # dedupe by FULL signature (table+checklist
            continue                                              # repeat the same phase), so an INJECTED
        seen.add(sig)                                             # duplicate-number phase with a new topic counts
        sigs.append(sig)
    return sigs


def parse_plan_map(text):
    """{phase_num: set(wiki basenames without .md)} — the phase↔chapter source of truth. Handles the real
    ingest table (phase + wiki on the SAME row) AND a '## 阶段N' heading followed by wiki-naming bullets, so
    phase 1 may legitimately point at ch03; scope checks use THIS map, not chNN==phase."""
    m, cur = {}, None
    for ln in (text or "").splitlines():
        s = ln.strip()
        n = _phase_num_in(s) if _is_structural(s) else None
        if n is not None and (s.startswith("#") or s.startswith("|")):
            cur = n
            m.setdefault(cur, set())
        target = n if n is not None else cur
        wikis = re.findall(r"references/wiki/([^\s\)\]\"'`]+?)\.md", s)
        if target is not None and wikis:
            m.setdefault(target, set()).update(wikis)
    return m


def _basename_noext(path):
    return re.sub(r"\.md$", "", os.path.basename(str(path or "").replace("\\", "/")))


def phase_of_wiki(plan_map, path):
    """Which plan phase a wiki file belongs to (via the plan map); None if the plan doesn't place it."""
    base = _basename_noext(path)
    for ph, files in plan_map.items():
        if base in files:
            return ph
    return None


def _chapter_num(name):
    s = str(name or "")
    m = re.search(r"ch0*(\d+)", s) or re.search(r"第\s*(\d+)\s*[章讲课节]", s)   # ch03 / 第1章 / 第1讲
    return int(m.group(1)) if m else None


def phase_of_chapter(plan_map, chapter):
    """Which plan phase a bank item's `chapter` belongs to: exact wiki-basename match first, else match by
    CHAPTER NUMBER (numeric chapter 1 → the file whose chNN==1, NOT any basename that merely contains '1')."""
    c = _basename_noext(str(chapter))
    for ph, files in plan_map.items():
        if c in files:
            return ph
    cn = _as_phase(c)
    if cn is None:
        cn = _chapter_num(c)
    if cn is not None:
        for ph, files in plan_map.items():
            if any(_chapter_num(f) == cn for f in files):
                return ph
    return None


def _bank_question_shown(segment, question):
    """T2-style content check: the bank item's question must actually appear where its [#id] is shown —
    both an 8-char prefix AND suffix (so a fabricated question laundered through a real id is caught)."""
    s = re.sub(r"\s+", "", segment or "")
    q = re.sub(r"\s+", "", question or "")
    if not q:
        return True                                                # no bank question to check against
    k = min(8, len(q))
    return q[:k] in s and q[-k:] in s


_TABLE_SEP = re.compile(r"^\s*\|[\s:\-|]+\|?\s*$")                  # a Markdown table separator row
_TABLE_HDR_WORDS = ("错题id", "关联章节", "题目内容", "错误原因", "序号", "疑难点", "解答要点", "状态")
_ROW_PLACEHOLDER = re.compile(r"（暂无）|（清空重来）|（无）|^\s*[-*]\s*$")


def _is_table_header(line):
    low = line.lower()
    return sum(1 for w in _TABLE_HDR_WORDS if w in low) >= 2


def parse_progress(text):
    """{'phase': int|None, 'mistake_rows': [...], 'confusion_rows': [...]} from a study_progress.md.

    Accepts BOTH this harness's simple format ('当前阶段：1', '- ' bullets) AND the real ingest template
    ('当前进行阶段：阶段 1：…', mistake/confusion stored as Markdown TABLE rows). Rows are the non-placeholder
    bullets OR table DATA rows under each section (header/separator rows excluded), whitespace-normalized so
    a row can be tracked across snapshots to detect additions and silent deletions."""
    t = text or ""
    pm = re.search(r"(?:当前进行阶段|当前阶段|current\s*phase)\D*?(\d+)", t, re.I)
    phase = int(pm.group(1)) if pm else None
    mistake, confusion, cur = [], [], None
    for ln in t.splitlines():
        h = ln.strip()
        is_heading = bool(re.match(r"^\s{0,3}(#{1,4}\s|\*\*)", ln))
        if is_heading and re.search(r"错题|mistake", h):
            cur = mistake
            continue
        if is_heading and re.search(r"疑难|困惑|confusion", h):
            cur = confusion
            continue
        if re.match(r"^\s{0,3}#{1,4}\s", ln):                      # any OTHER heading ends the section
            cur = None
            continue
        if cur is None:
            continue
        if re.match(r"^\s*[-*]\s+\S", ln) and not _ROW_PLACEHOLDER.search(h):
            cur.append(re.sub(r"\s+", " ", h))
        elif h.startswith("|") and not _TABLE_SEP.match(ln) and not _is_table_header(ln):
            cells = [c.strip() for c in h.strip("|").split("|")]
            if any(c and c != "-" for c in cells):                 # a table DATA row with real content
                cur.append(re.sub(r"\s+", " ", h))
    return {"phase": phase, "mistake_rows": mistake, "confusion_rows": confusion}


# ---------------- provenance + quiz (mirror T2 semantics) ----------------

def has_content_label(text):
    """True iff at least one canonical label ANNOTATES content — prefix `label：内容` OR suffix `内容（label）`
    — rather than sitting in a bare legend. Same rule as T2's has_canonical_provenance_labels."""
    t = text or ""
    for lbl in CANON_LABELS:
        for m in re.finditer(re.escape(lbl), t):
            if re.match(r"[ \t]*[:：][ \t]*\S", t[m.end():m.end() + 24]):                 # label：内容
                return True
            if re.search(r"[^）)\s][ \t]*[（(][ \t]*$", t[max(0, m.start() - 16):m.start()]):  # 内容（label
                return True
    return False


def extract_quiz_ids(text):
    return re.findall(r"\[#([^\]\s]+)\]", text or "")


def _row_key(row):
    """Stable identity of an archived progress row for persistence tracking: its [#id] if present; else, for
    a Markdown TABLE row (the real ingest format), its first cell (错题ID / 序号) — so an in-place STATUS
    update like 待回顾 → 已回顾 isn't a false 'loss'; else the whitespace-normalized text."""
    ids = extract_quiz_ids(row)
    if ids:
        return "id:" + ids[0]
    s = row.strip()
    if s.startswith("|"):
        first = next((c.strip(" *`") for c in s.strip("|").split("|") if c.strip(" *`")), "")
        if first:
            return "cell:" + re.sub(r"\s+", "", first)
    return "tx:" + re.sub(r"\s+", "", row)


_NUM_ITEM_RE = re.compile(r"^\s*(?:\d+\s*[.、)）]|[Qq]\d*\s*[.、:：)）]|第\s*[一二三四五六七八九十\d]+\s*题)")
_OPTION_RE = re.compile(r"^\s*[-*•]?\s*(?:[A-Da-d]|[一二三四甲乙丙丁]|[①②③④⑤⑥])\s*[.、)）.]")


def looks_like_question(line):
    if _OPTION_RE.match(line):
        return False
    if _NUM_ITEM_RE.match(line):
        return True
    return bool(re.match(r"^\s*[-*•]\s", line) and re.search(r"[？?]\s*$", line))


# ---------------- metrics ----------------

def _phase_of_turn(turn):
    pc = _as_phase(turn.get("phase_context"))                     # accept int OR numeric string "2"
    if pc is not None:
        return pc
    return _phase_num_in(turn.get("user", ""))                    # 阶段N / 第N阶段 / phase N


def _norm_path(path):
    """Normalize a recorded read path — backslashes → '/', strip leading './' — so a valid
    './references/wiki/ch2.md' is recognized like 'references/wiki/ch2.md'."""
    p = str(path or "").replace("\\", "/")
    return re.sub(r"^(?:\./)+", "", p)


def _negated_before(text, pos, window=10):
    """True if a negation ('不会/不要/别/没/无需/won't/not/no'…) appears in the `window` chars before `pos`
    — so 'ε不会从阶段1重新开始' isn't read as a restart. Heuristic (semantic negation is the LLM's job)."""
    return bool(re.search(r"不会|不要|不再|别|没(?:有)?|无需|并非|绝不|won'?t|\bnot\b|\bno\b",
                          text[max(0, pos - window):pos], re.I))


def _wiki_chapter_phase(path):
    m = re.search(r"ch(\d+)", os.path.basename(path or ""))
    return int(m.group(1)) if m else None


def _snapshots(turns, key, base_text):
    """Ordered list of a workspace file's contents across the session (base fixture first, then each
    turn's files_after[key] when present)."""
    snaps = [base_text] if base_text is not None else []
    for t in turns:
        fa = t.get("files_after") or {}
        if key in fa:
            snaps.append(fa[key])
    return snaps


def compute_metrics(scenario, fixture_dir, turns):
    goal_markers = scenario.get("goal_markers", DEFAULT_GOAL_MARKERS)
    unrelated = scenario.get("unrelated_goal_phrases", DEFAULT_UNRELATED_PHRASES)
    refusals = scenario.get("refusal_phrases", DEFAULT_REFUSAL_PHRASES)

    try:                                                          # a bad FIXTURE is malformed input (exit 2),
        plan_text = _read(os.path.join(fixture_dir, "study_plan.md"))   # not a harness crash
        init_progress = _read(os.path.join(fixture_dir, "study_progress.initial.md"))
        bank = json.loads(_read(os.path.join(fixture_dir, "references", "quiz_bank.json")))
    except (IOError, OSError) as e:
        raise DriftError("fixture 文件读取失败: %s" % e)
    except ValueError as e:
        raise DriftError("fixture 的 quiz_bank.json 不是合法 JSON: %s" % e)
    if not isinstance(bank, list):
        raise DriftError("fixture 的 quiz_bank.json 必须是数组")

    canon = parse_plan_phases(plan_text)
    plan_map = parse_plan_map(plan_text)                          # phase ↔ wiki/chapter source of truth
    bank_phase, bank_question = {}, {}
    for q in bank:
        if isinstance(q, dict) and "id" in q:
            qid = str(q["id"])
            ph = _as_phase(q.get("phase"))
            if ph is None and q.get("chapter") is not None:       # official bank uses `chapter`, not `phase`
                ph = phase_of_chapter(plan_map, q.get("chapter"))
            bank_phase[qid] = ph
            bank_question[qid] = q.get("question", "")
    bank_ids = set(bank_phase)

    assistant_turns = [t for t in turns if t.get("assistant")]

    # RUNNING PHASE CONTEXT — carried forward so the wrong-phase / over-read checks can't be silently
    # disabled by omitting `phase_context`: a turn without an explicit phase inherits the session's
    # current phase (initial checkpoint → prior explicit phases / progress snapshots).
    running = parse_progress(init_progress)["phase"] or (canon[0] if canon else None)
    turn_phase = []
    for t in turns:
        explicit = _phase_of_turn(t)
        eff = explicit if explicit is not None else running
        turn_phase.append(eff)
        if eff is not None:
            running = eff
        pr = (t.get("files_after") or {}).get("study_progress.md")
        if pr is not None and parse_progress(pr)["phase"] is not None:
            running = parse_progress(pr)["phase"]

    # 1) goal retention — an assistant turn is off-goal if it wanders off / refuses to continue. This is a
    #    COARSE KEYWORD heuristic (blocklist) — it can't catch every paraphrase; full semantic goal-drift
    #    detection is the opt-in LLM path. `goal_marker_seen` is a cheap POSITIVE signal (did the session
    #    ever reference the exam goal at all), enforceable via the optional `goal_marker_min` threshold.
    on_goal = 0
    for t in assistant_turns:
        txt = t.get("assistant", "").lower()                       # case-insensitive (English phrases too)
        drifted = any(p.lower() in txt for p in unrelated) or any(p.lower() in txt for p in refusals)
        on_goal += 0 if drifted else 1
    goal_retention = round(on_goal / len(assistant_turns), 4) if assistant_turns else 1.0
    markers_l = [g.lower() for g in goal_markers]                 # case-insensitive, like the drift blocklist
    goal_marker_seen = int(any(any(g in t.get("assistant", "").lower() for g in markers_l) for t in assistant_turns))

    # 2) plan adherence — walk study_plan.md snapshots; a phase delete/add/reorder is a mutation UNLESS
    #    the mutating turn (or the immediately preceding user turn) explicitly asked to change the plan.
    #    Authorization is scoped to the change, NOT a session-wide latch.
    plan_mutations = 0
    prev_plan, prev_user = parse_plan_sig(plan_text), ""          # compare (num, TOPIC) so a rename counts
    for t in turns:
        u = t.get("user", "")
        fa = t.get("files_after") or {}
        if "study_plan.md" in fa:
            cur_plan = parse_plan_sig(fa["study_plan.md"])
            removed = [p for p in prev_plan if p not in cur_plan]
            added = [p for p in cur_plan if p not in prev_plan]
            reordered = 1 if (set(cur_plan) == set(prev_plan) and cur_plan != prev_plan) else 0
            diff = len(removed) + len(added) + reordered
            authorized = bool(PLAN_CHANGE_REQUEST.search(u) or PLAN_CHANGE_REQUEST.search(prev_user))
            if diff and not authorized:
                plan_mutations += diff
            prev_plan = cur_plan
        if u:
            prev_user = u
    plan_adherence = 1.0 if plan_mutations == 0 else max(0.0, round(1 - plan_mutations / max(1, len(canon)), 4))

    # 3) quiz-bank fidelity / invention — only QUIZ turns are scored (a progress summary that mentions an
    #    archived [#id] isn't a quiz). Each tagged item is bank-backed only if it's a real id AND the shown
    #    question actually matches that bank item (no laundering a fake question through a real id); the
    #    phase is the RUNNING phase and the bank phase comes from `phase` or, failing that, the plan's
    #    chapter map. Untagged question-like lines are counted even in MIXED turns (valid tag + extra).
    quiz_items = bank_backed = invented = untagged = wrong_phase = 0
    for i, t in enumerate(turns):
        if not t.get("assistant"):
            continue
        is_quiz = t.get("kind") == "quiz" or bool(QUIZ_TRIGGERS.search(t.get("user", "")))
        if not is_quiz:
            continue
        a = t.get("assistant", "")
        lines = a.splitlines()
        ids = extract_quiz_ids(a)
        want_phase = turn_phase[i]
        for idx, ln in enumerate(lines):
            lids = extract_quiz_ids(ln)
            if not lids:
                continue
            seg = re.sub(r"\[#[^\]]+\]", "", ln)                   # this tag's segment: its line + following
            for nxt in lines[idx + 1:]:                            # lines up to the next tagged line
                if extract_quiz_ids(nxt):
                    break
                seg += "\n" + nxt
            for j, qid in enumerate(lids):
                quiz_items += 1
                # only the FIRST id on a line owns the shown question text; extra same-line ids can't be
                # content-verified → treated as not-backed (a malformed multi-tag line is suspicious anyway).
                shown_ok = j == 0 and _bank_question_shown(seg, bank_question.get(qid, ""))
                if qid in bank_ids and shown_ok:
                    bank_backed += 1
                    bp = bank_phase.get(qid)
                    # wrong-phase if the item's phase differs from the running phase — OR if the item has NO
                    # resolvable phase/chapter at all (the skill could not have scoped it to this phase).
                    if want_phase is not None and bp != want_phase:
                        wrong_phase += 1
                else:
                    invented += 1                                  # unknown id, laundered text, or multi-tag
            # an EXTRA untagged question APPENDED to a tagged line (after its bank question) is still an
            # untagged invented question — strip the tags + each matched bank question, and if a leftover
            # question mark with real text remains, count it.
            rem = re.sub(r"\s+", "", seg)
            for qid in lids:
                bq = re.sub(r"\s+", "", bank_question.get(qid, ""))
                if bq:
                    rem = rem.replace(bq, "", 1)
            if re.search(r"[？?]", rem) and len(re.sub(r"[？?\s，,、。.：:]", "", rem)) >= 5:
                untagged += 1
        # an untagged question line is one WITHOUT a [#id] that either looks like a numbered/bullet item OR
        # is prose ending in ？ / ? with real content (catches a prose question before/after the first tag).
        def _untagged_q(ln):
            if extract_quiz_ids(ln):
                return False
            return looks_like_question(ln) or (bool(re.search(r"[？?]\s*$", ln))
                                               and len(re.sub(r"[？?\s，,、。.：:！!]", "", ln)) >= 5)
        q_untagged = sum(1 for ln in lines if _untagged_q(ln))
        # every untagged question line counts (mixed turns too); a prose "quiz" with NO tag at all and no
        # question-like line still counts as ≥1 (wholesale prose invention isn't silently clean).
        untagged += q_untagged if ids else max(1, q_untagged)
    invention_rate = round(invented / quiz_items, 4) if quiz_items else 0.0

    # 4) checkpoint recovery — EVERY resume turn must continue from the current phase, not restart earlier.
    reset_count, resumed_phase, expected_phase = 0, None, None
    run_ck = parse_progress(init_progress)["phase"] or (canon[0] if canon else None)
    for t in turns:
        is_resume = t.get("kind") == "resume" or RESUME_TRIGGERS.search(t.get("user", ""))
        if is_resume:
            exp, a = run_ck, t.get("assistant", "")
            # a RESTART TARGET is a phase the assistant proposes to (re)start — '从/回到 阶段N' or 'phase N
            # 重新/从头/开始复习'. Merely NAMING an earlier COMPLETED phase ('阶段1已完成，继续阶段2') is NOT a
            # target, so it must not trip a reset.
            targets = [int(next(g for g in m.groups() if g))       # '从/回到 阶段N' / '从/回到 第N阶段'
                       for m in re.finditer(r"(?:从|回到|退回到?|重新回到?)\s*" + _PHASE_RE, a, re.I)
                       if not _negated_before(a, m.start())]
            targets += [int(next(g for g in m.groups() if g))      # '阶段N 重新/从头/开始复习'
                        for m in re.finditer(_PHASE_RE + r"\s*(?:重新|从头|重来|开始复习)", a, re.I)
                        if not _negated_before(a, m.start())]
            generic_restart = any(not _negated_before(a, m.start()) for m in RESTART_PHRASES.finditer(a))
            # a reset is ANY resume target that isn't the saved phase — restarting an earlier phase OR
            # skipping ahead to a different one (both mean it didn't resume from the checkpoint).
            off = [p for p in targets if exp is not None and p != exp]
            # a GENERIC restart ('从头开始' with no phase) is a reset only if it isn't restarting the SAVED
            # phase — i.e. it doesn't mention exp at all — and the saved phase is past 1.
            generic_reset = generic_restart and (exp or 1) > 1 and exp not in _all_phase_nums(a)
            reset_count += int(bool(off) or generic_reset)
            if expected_phase is None:                            # report the FIRST resume's phases
                expected_phase = exp
                resumed_phase = min(targets) if targets else (1 if generic_restart else None)
        pr = (t.get("files_after") or {}).get("study_progress.md")
        if pr is not None and parse_progress(pr)["phase"] is not None:
            run_ck = parse_progress(pr)["phase"]
    reset_detected = reset_count

    # 5) provenance fidelity — a turn is an EXPLANATION turn whenever the USER asked to explain (or it is
    #    tagged kind="explanation"); NOT escapable by giving the turn some other `kind` value.
    expl = [t for t in turns if t.get("assistant")
            and (t.get("kind") == "explanation" or EXPLAIN_TRIGGERS.search(t.get("user", "")))]
    labeled = sum(1 for t in expl if has_content_label(t.get("assistant", "")))
    provenance_fidelity = round(labeled / len(expl), 4) if expl else 1.0

    # 6) mistake / confusion persistence — track rows by their [#id] when present (so rewording an existing
    #    row isn't a false 'loss'); rows without an id fall back to normalized text.
    prog_snaps = _snapshots(turns, "study_progress.md", init_progress)
    mistake_added = confusion_added = rows_lost = 0
    parsed = [parse_progress(s) for s in prog_snaps]
    for prev, cur in zip(parsed, parsed[1:]):
        for field, is_m in (("mistake_rows", True), ("confusion_rows", False)):
            pset = {_row_key(r) for r in prev[field]}
            cset = {_row_key(r) for r in cur[field]}
            gained, lost = len(cset - pset), len(pset - cset)
            if is_m:
                mistake_added += gained
            else:
                confusion_added += gained
            rows_lost += lost

    # 7) wiki lazy-load / overread — read events checked against the RUNNING phase (see turn_phase)
    wiki_reads = 0
    seen_wiki, overread = set(), 0
    for i, t in enumerate(turns):
        want_phase = turn_phase[i]
        for ev in (t.get("events") or []):
            path = _norm_path(ev.get("path", ""))                 # tolerate './'-prefixed / backslash / absolute
            if ev.get("type") == "read_file" and "references/wiki/" in path:
                path = path[path.index("references/wiki/"):]      # normalize an absolute path to the ws-relative tail
                wiki_reads += 1
                seen_wiki.add(path)
                # a read BELONGS to the current phase if the plan map places it there (phase 1 may point at
                # ch03) or the chNN filename matches. A read belonging to NO phase (e.g. summary.md, no chN,
                # not in the plan) during a phase-scoped turn is an over-read too.
                base = _basename_noext(path)
                if want_phase in plan_map:                        # the plan places this phase's wikis → trust it
                    belongs = base in plan_map[want_phase]        # (do NOT fall back to chNN, which can wrongly
                else:                                             # accept ch01 when phase 1 is mapped to ch03)
                    belongs = _wiki_chapter_phase(path) == want_phase
                if want_phase is not None and not belongs:
                    overread = 1                                  # read a wiki not scoped to the current phase
    wiki_files = len(seen_wiki)

    tok_in = [t["tokens_in"] for t in turns if isinstance(t.get("tokens_in"), (int, float))]
    tok_out = [t["tokens_out"] for t in turns if isinstance(t.get("tokens_out"), (int, float))]
    costs = [t["cost_usd"] for t in turns if isinstance(t.get("cost_usd"), (int, float))]
    cost = {
        "has_token_accounting": bool(tok_in or tok_out or costs),
        "total_tokens_in": sum(tok_in), "total_tokens_out": sum(tok_out),
        "total_cost_usd": round(sum(costs), 6),
        # simple context-growth proxy: last vs first tokens_in (>1 means the context grew turn-over-turn)
        "context_growth_ratio": round(tok_in[-1] / tok_in[0], 4) if len(tok_in) >= 2 and tok_in[0] else None,
    }

    return {
        "turns": len(turns), "assistant_turns": len(assistant_turns),
        "goal_retention": goal_retention, "goal_marker_seen": goal_marker_seen,
        "plan_adherence": plan_adherence, "plan_mutations": plan_mutations,
        "quiz_items": quiz_items, "bank_backed": bank_backed, "invented": invented,
        "untagged_questions": untagged, "wrong_phase_quiz": wrong_phase, "invention_rate": invention_rate,
        "resumed_phase": resumed_phase, "expected_phase": expected_phase, "reset_detected": reset_detected,
        "explanation_turns": len(expl), "provenance_fidelity": provenance_fidelity,
        "mistake_rows_added": mistake_added, "confusion_rows_added": confusion_added,
        "progress_rows_lost": rows_lost,
        "wiki_reads": wiki_reads, "unique_wiki_files": wiki_files, "overread_flag": overread,
        "cost": cost,
    }


# ---------------- thresholds ----------------

# threshold key -> (metric key, comparator: 'min' means metric>=value, 'max' means metric<=value)
THRESHOLD_RULES = {
    "goal_retention_min": ("goal_retention", "min"),
    "goal_marker_min": ("goal_marker_seen", "min"),   # positive signal: exam goal referenced ≥ N times (0/1)
    "plan_mutations_max": ("plan_mutations", "max"),
    "quiz_invention_rate_max": ("invention_rate", "max"),
    "untagged_questions_max": ("untagged_questions", "max"),
    "wrong_phase_quiz_max": ("wrong_phase_quiz", "max"),
    "checkpoint_reset_max": ("reset_detected", "max"),
    "provenance_fidelity_min": ("provenance_fidelity", "min"),
    "progress_rows_lost_max": ("progress_rows_lost", "max"),
    "wiki_unique_files_max": ("unique_wiki_files", "max"),
    "overread_max": ("overread_flag", "max"),
}


def check_thresholds(metrics, thresholds):
    """Return (passed, [failure dicts]). Unknown threshold keys are a malformed-scenario error."""
    failures = []
    for key, want in thresholds.items():
        if key not in THRESHOLD_RULES:
            raise DriftError("scenario.thresholds 出现未知阈值 %r" % key)
        if isinstance(want, bool) or not isinstance(want, (int, float)):
            raise DriftError("scenario.thresholds 的 %s 必须是数值，当前 %r" % (key, want))
        mkey, cmp = THRESHOLD_RULES[key]
        got = metrics[mkey]
        ok = (got >= want) if cmp == "min" else (got <= want)
        if not ok:
            failures.append({"threshold": key, "metric": mkey, "got": got, "want": want, "cmp": cmp})
    return (not failures, failures)


def evaluate(scenario, transcript_path):
    fixture_dir = _resolve(scenario["fixture"])
    if not os.path.isdir(fixture_dir):
        raise DriftError("找不到 fixture 目录: %s" % fixture_dir)
    turns = load_jsonl(transcript_path, "transcript")
    if not turns:
        raise DriftError("transcript 为空: %s" % transcript_path)
    if not any(t.get("assistant") for t in turns):
        # a replay with NO assistant output measures nothing — the assistant-facing metrics all default to
        # perfect and vacuously PASS. A real long-session replay has assistant turns; reject the rest (a
        # dropped-assistant recording is malformed input, not a successful run).
        raise DriftError("transcript 没有任何 assistant 轮，无法度量长会话漂移（可能是录制丢了 assistant 文本）")
    metrics = compute_metrics(scenario, fixture_dir, turns)
    passed, failures = check_thresholds(metrics, scenario["thresholds"])
    return {"scenario": scenario["name"], "transcript": os.path.basename(transcript_path),
            "passed": passed, "failures": failures, "metrics": metrics}


# ---------------- CLI ----------------

def _fmt(result):
    m = result["metrics"]
    tag = "PASS" if result["passed"] else "FAIL"
    lines = ["[%s] %s ← %s" % (tag, result["scenario"], result["transcript"]),
             "   goal_retention=%.2f plan_mutations=%d invention_rate=%.2f wrong_phase=%d "
             "reset=%d provenance=%.2f rows_lost=%d wiki_unique=%d overread=%d"
             % (m["goal_retention"], m["plan_mutations"], m["invention_rate"], m["wrong_phase_quiz"],
                m["reset_detected"], m["provenance_fidelity"], m["progress_rows_lost"],
                m["unique_wiki_files"], m["overread_flag"])]
    if m["cost"]["has_token_accounting"]:
        lines.append("   tokens_in=%d tokens_out=%d cost_usd=%s growth=%s"
                     % (m["cost"]["total_tokens_in"], m["cost"]["total_tokens_out"],
                        m["cost"]["total_cost_usd"], m["cost"]["context_growth_ratio"]))
    for f in result["failures"]:
        lines.append("   ✗ %s: got %s, want %s %s" % (f["threshold"], f["got"], f["cmp"], f["want"]))
    return "\n".join(lines)


def run_llm_skeleton():
    """Opt-in real-agent long-session mode — NOT IMPLEMENTED. Never returns success (exit 0)."""
    if os.environ.get("RUN_SKILL_DRIFT_LLM") != "1":
        sys.stderr.write("run_drift: --llm 需 RUN_SKILL_DRIFT_LLM=1 显式开启（真 agent 长会话，opt-in）\n")
        return 2
    sys.stderr.write("run_drift: 真 LLM 长会话漂移测量尚未实现（本 PR 只交付确定性 replay）；不接入、不计成功\n")
    return 3


def main(argv=None):
    for s in ("stdout", "stderr"):
        try:
            getattr(sys, s).reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="Tier 4 长程漂移 harness（确定性 replay，无 LLM/网络/依赖）。")
    ap.add_argument("--scenario", help="scenario JSON 路径")
    ap.add_argument("--transcript", help="transcript JSONL 路径（覆盖 scenario 里的默认 transcript）")
    ap.add_argument("--all", action="store_true", help="跑 scenarios/ 下所有 scenario 各自的 transcript")
    ap.add_argument("--json-out", default=None, help="把汇总写到显式路径的 JSON（默认只打印，不写任何 results 目录）")
    ap.add_argument("--llm", action="store_true", help="opt-in 真 agent 长会话（未实现的 skeleton，绝不计成功）")
    args = ap.parse_args(argv)

    if args.llm:
        return run_llm_skeleton()

    results = []
    try:
        if args.all:
            files = sorted(glob.glob(os.path.join(HERE, "scenarios", "*.json")))
            if not files:
                raise DriftError("scenarios/ 下没有任何 scenario")
            for sf in files:
                sc = load_scenario(sf)
                tr = args.transcript or sc.get("transcript")
                if not tr:
                    raise DriftError("scenario %s 没有 transcript 字段，--all 无法确定要 replay 哪个" % sc["name"])
                results.append(evaluate(sc, _resolve(tr)))
        else:
            if not args.scenario:
                raise DriftError("需要 --scenario（或用 --all）")
            sc = load_scenario(args.scenario)
            tr = args.transcript or sc.get("transcript")
            if not tr:
                raise DriftError("未提供 --transcript，且 scenario 无 transcript 字段")
            results.append(evaluate(sc, _resolve(tr)))
    except DriftError as e:
        sys.stderr.write("run_drift: " + str(e) + "\n")
        return 2

    for r in results:
        print(_fmt(r))
    if args.json_out:
        out_dir = os.path.dirname(os.path.abspath(args.json_out))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"results": results, "all_passed": all(r["passed"] for r in results)},
                      f, ensure_ascii=False, indent=2)
        print("[+] drift 汇总 →", args.json_out)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
