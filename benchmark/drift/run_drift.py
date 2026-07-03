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
whether a *recorded* session drifts, not whether a live model will. The DEFAULT path calls no model,
reads no key, hits no network, runs no paid benchmark. Real long-session LLM runs are the OPT-IN `--llm`
path (B3): gated by RUN_SKILL_DRIFT_LLM=1, it delegates to `run_live_smoke.py` — drive a real agent
turn-by-turn (token-capped, abort-on-failure, fixture-sandboxed) → record a T5b log → convert to JSONL
→ score with THIS file's `compute_metrics`/thresholds → ledger. CI never runs it (env gate + real agent).

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
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))                      # repo root (…/benchmark/drift → repo)

# A6：复用 scripts/update_progress.py 的 canonical 时间宽裕度归一化——别在这里另建一份会漂的别名表
# （否则「明天考/考前一天/今天」这些被 update_progress 认作 ≤1天 的别名会在 drift 侧被漏判为非紧迫）。
sys.path.insert(0, os.path.join(ROOT, "scripts"))
try:
    from update_progress import (_normalize_tier as _canon_tier, LEARNING_MODES as _LEARN_MODES,
                                 _WINDOW_STATUSES as _WIN_STATUSES, _md_cell as _md_cell)
except Exception:                                                 # 缺文件时退化为内置常量，不让 drift 崩
    _canon_tier = None
    _LEARN_MODES = ("零基础从头讲", "某章起步补弱", "查缺补漏")
    _WIN_STATUSES = ("在窗口", "窗口外", "已实测")

    def _md_cell(v, default="-"):                                # 缺 update_progress 时的等价兜底
        s = str(v) if v not in (None, "") else default
        return re.sub(r"\s*[\r\n]+\s*", " ", s).replace("|", "/").strip()


def _tier_is_urgent(time_budget):
    tb = str(time_budget or "")
    if _canon_tier is not None:
        tb = _canon_tier(tb)[0]                                   # → canonical 档（≤1天/1-3天/…）
    return tb in ("≤1天", "<=1天", "1天", "当天")

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
    for tk in sc["thresholds"]:
        # 未知阈值 key（typo，如 nonsense_max）是坏 scenario——在**加载**即报，让确定性路径与 --llm 预检
        # 都能在判分/付费之前拦下，而不是烧完 token 才在 check_thresholds 炸（Codex OSlL7）
        if tk not in THRESHOLD_RULES:
            raise DriftError("scenario.thresholds 出现未知阈值 %r（可用：%s）"
                             % (tk, ", ".join(sorted(THRESHOLD_RULES))))
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
    return (line.startswith("#") or line.startswith("|") or bool(re.match(r"[-*]\s", line))
            or bool(re.match(r"\d+\s*[.)、）]", line)))   # 有序列表（1. 阶段…）也是计划条目


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


def _window_same_row(prev, cur):
    """prev 行与 cur 行是否同一逻辑窗口条目（**方向性**）：精确相等，或 prev 无章、cur 补了章（backfill）。
    反向——prev 有章 point@N、cur 抹成 point@（擦掉章节身份）——是**丢失**，不算同一行（Codex SGGq）；
    只有 update_progress 真会做的补章节回填算同一行。"""
    if prev == cur:
        return True
    # 键是 point@chapter；point 可能含 @（如 C@语言），章节不含 @——从右切（rpartition）才不会把 point
    # 的一段误当章节（Codex OSZRp）。
    pp, _, pc = prev.rpartition("@")
    cp, _, cc = cur.rpartition("@")
    return pp == cp and pc == "" and cc != ""   # 仅允许 backfill：prev 无章 → cur 有章


def _window_diff(prev, cur):
    """一对一方向性匹配统计窗口条目的 (added, lost)——一个 cur 行只能消费一个 prev 行（避免一条 unchaptered
    行同时抵消多条 prev 行掩盖真丢失，Codex R_Xa），且只认精确/backfill 匹配（抹章=丢失，Codex SGGq）。
    specific（有章）prev 先匹配，loose（point@）后，避免抢占。"""
    avail = list(cur)
    lost = 0
    for a in sorted(prev, key=lambda k: k.endswith("@")):
        idx = next((i for i, c in enumerate(avail) if c == a), None)                    # 先精确
        if idx is None:
            idx = next((i for i, c in enumerate(avail) if _window_same_row(a, c)), None)  # 再 backfill
        if idx is None:
            lost += 1
        else:
            avail.pop(idx)
    return len(avail), lost                                  # 剩下没被消费的 cur 行 = 新增


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
    mistake_st, confusion_st, cur_st = [], [], None
    window_keys, window_st, window_nt = [], [], []   # A6 🪟 窗口区：point@chapter 键 + 状态 + 备注（可跨源比对）
    in_window = False
    for ln in t.splitlines():
        h = ln.strip()
        is_heading = bool(re.match(r"^\s{0,3}(#{1,4}\s|\*\*)", ln))
        if is_heading and re.search(r"错题|mistake", h):
            cur, cur_st, in_window = mistake, mistake_st, False   # 进归档区必须清 in_window（窗口区可能在前，OSlL9）
            continue
        if is_heading and re.search(r"疑难|困惑|confusion", h):
            cur, cur_st, in_window = confusion, confusion_st, False
            continue
        if is_heading and re.search(r"🪟|知识点窗口", h):           # A6 知识点窗口区（生成视图）
            cur, cur_st, in_window = None, None, True
            continue
        if re.match(r"^\s{0,3}#{1,4}\s", ln):                      # any OTHER heading ends the section
            cur, cur_st, in_window = None, None, False
            continue
        if in_window:
            # 🪟 表数据行：| 知识点 | 关联章节 | 状态 | 备注 |——结构化成 point@chapter 键 + 状态
            if _TABLE_SEP.match(ln) or _is_table_header(ln) or not h.startswith("|"):
                continue
            cells = [c.strip() for c in h.strip("|").split("|")]
            if not cells or not cells[0] or cells[0] == "-":
                continue
            pt = cells[0]
            ch = cells[1] if len(cells) > 1 and cells[1] not in ("", "-") else ""
            stt = cells[2] if len(cells) > 2 and cells[2] not in ("", "-") else None
            note = cells[3] if len(cells) > 3 and cells[3] not in ("", "-") else ""
            window_keys.append("%s@%s" % (pt, ch))
            window_st.append(stt)
            window_nt.append(note)
            continue
        if cur is None:
            continue
        if re.match(r"^\s*[-*]\s+\S", ln):
            body = re.sub(r"^\s*[-*]\s+", "", h).strip()
            # 占位判定按【整条】——真实笔记里含「（暂无）」字样（如问空集的行）不能被当占位丢掉
            if not _ROW_PLACEHOLDER.fullmatch(body):
                cur.append(re.sub(r"\s+", " ", h))
                if cur_st is not None:
                    cur_st.append(None)                             # bullet 形没有状态列
        elif h.startswith("|") and not _TABLE_SEP.match(ln) and not _is_table_header(ln):
            cells = [c.strip() for c in h.strip("|").split("|")]
            if not any(c and c != "-" for c in cells):
                continue
            # 生成视图的占位行 = 首格是占位词且其余全 '-'（cell 级判定，与迁移解析同口径）
            if cells and _ROW_PLACEHOLDER.fullmatch(cells[0] or "") \
                    and all(c in ("", "-") for c in cells[1:]):
                continue
            cur.append(re.sub(r"\s+", " ", h))                     # a table DATA row with real content
            # 生成表的状态列固定在最后一格（≥4 列才有状态列）——状态是自由文本
            # （已订正/已复盘/已解决…），词表白名单会漏掉合法值让陈旧状态钻空子
            if cur_st is not None:
                cur_st.append(cells[-1] if len(cells) >= 4 and cells[-1] not in ("", "-") else None)
    return {"phase": phase, "mistake_rows": mistake, "confusion_rows": confusion,
            "mistake_status": mistake_st, "confusion_status": confusion_st,
            "window_rows": window_keys, "window_status": window_st, "window_note": window_nt}


def _window_state_map(snap):
    """有序的 [(point@chapter, status, note)] 列表 —— 用于跨源比对 state 与生成视图 md 的窗口面板是否一致。
    用**保留重数**的排序列表而非 dict：md 里同一窗口行出现两次（陈旧/手改）不能被 dict 折叠掉而漏判
    （OSTZO）。带上 note：只改 note 的 state 更新若 md 面板没跟上（备注列陈旧）也要抓（OSZRm）。
    省略 status 归一到渲染默认 在窗口。"""
    keys = snap.get("window_rows", []) or []
    sts = snap.get("window_status", []) or []
    nts = snap.get("window_note", []) or []
    return sorted((k, (sts[i] if i < len(sts) and sts[i] else "在窗口"), (nts[i] if i < len(nts) else ""))
                  for i, k in enumerate(keys))


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

def parse_state_json(text, plan_phases=None):
    """A4: parse a study_state.json snapshot into the same shape parse_progress returns. Rows are
    rendered to "[#id] note" strings so persistence tracking (_row_key) works identically.
    传入 plan_phases 时阶段还必须落在计划内——validator/update 工具拒收的 99 号断点，
    T4 也不能让它把 expected_phase 带进沟里。"""
    if text is not None and not isinstance(text, str):
        # 手写 JSONL 把快照写成 JSON 对象而不是文件内容字符串——按畸形输入 fail-loud，
        # 不能让 json.loads 的 TypeError 炸成未处理堆栈
        raise DriftError("files_after 里的 study_state.json 必须是文件内容字符串，实际是 %s"
                         % type(text).__name__)
    try:
        st = json.loads(text or "")
    except ValueError as e:
        raise DriftError("files_after 里的 study_state.json 不是合法 JSON: %s" % e)
    if not isinstance(st, dict):
        raise DriftError("files_after 里的 study_state.json 顶层必须是对象")
    cp = st.get("current_phase")
    # 与 validator/update 工具同一 schema——坏 phase（"2"/0/缺失）是坏输入要 fail-loud，
    # 静默当 None/照单全收会让断点与阶段限定指标从错误阶段继续算，掩盖坏写入
    if not (isinstance(cp, int) and not isinstance(cp, bool) and cp >= 1):
        raise DriftError("study_state.json 快照的 current_phase 必须是 ≥1 的整数，当前 %r" % cp)
    if plan_phases and cp not in plan_phases:
        raise DriftError("study_state.json 快照的 current_phase=%d 不在计划阶段 %s 中（断点不可恢复）"
                         % (cp, sorted(plan_phases)))
    phase = cp

    def _rows(field):
        v = st.get(field)
        if v is None:
            v = []
        if not isinstance(v, list):   # 标量/对象直接迭代会 TypeError 崩溃——按畸形输入统一报 DriftError
            raise DriftError("files_after 里的 study_state.json 字段 %s 必须是数组，实际 %s"
                             % (field, type(v).__name__))
        out, sts = [], []
        for r in v:
            # 与 validator/update 工具同一行 schema——字符串元素/缺 note 的行是坏写入，
            # 静默跳过会让坏 state 以「0 行」通过行持久化指标
            if not isinstance(r, dict) or not isinstance(r.get("note"), str) or not r["note"].strip():
                raise DriftError("study_state.json 快照的 %s 行必须是含非空 note 的对象: %r" % (field, r))
            for k in ("id", "status"):
                if r.get(k) is not None and not isinstance(r[k], str):
                    # 非字符串 id 被 str() 硬转会让行持久化在伪键下计算——坏写入必须 fail-loud
                    raise DriftError("study_state.json 快照的 %s 行 %s 必须是字符串或省略: %r"
                                     % (field, k, r))
            if r.get("id"):
                prefix = "[#%s] " % r["id"]
            elif r.get("chapter") not in (None, ""):
                prefix = "[ch:%s] " % r["chapter"]     # 无 id 的行拿章节当判别符——同 note 不同章不折叠
            else:
                prefix = ""
            out.append((prefix + r["note"]).strip())
            sts.append(r["status"] if isinstance(r.get("status"), str) else None)
        stat[field] = sts
        return out
    stat = {}
    m_rows, c_rows = _rows("mistake_archive"), _rows("confusion_log")

    # A6 知识点窗口行：point 必填、chapter 选填、status 选填——键为 point@chapter（同名不同章不折叠），
    # 与 update_progress 的窗口 schema 同口径；坏行（缺 point / 非字符串）按畸形输入 fail-loud。
    wv = st.get("knowledge_window")
    if wv is None:
        wv = []
    if not isinstance(wv, list):
        raise DriftError("files_after 里的 study_state.json 字段 knowledge_window 必须是数组，实际 %s"
                         % type(wv).__name__)
    window_rows, window_status, window_note = [], [], []
    for r in wv:
        if not isinstance(r, dict) or not isinstance(r.get("point"), str) or not r["point"].strip():
            raise DriftError("study_state.json 快照的 knowledge_window 行必须是含非空 point 的对象: %r" % r)
        ch = r.get("chapter")
        if ch is not None and not isinstance(ch, (str, int)) or isinstance(ch, bool):
            raise DriftError("study_state.json 快照的 knowledge_window 行 chapter 必须是字符串/整数或省略: %r" % r)
        stt = r.get("status")
        if stt is not None and stt not in _WIN_STATUSES:
            # update_progress 只接受 在窗口/窗口外/已实测——非 canonical（typo/任意串）是坏写入，
            # 静默收下会让「状态迁移」指标把乱码变化也当成真窗口进出（Codex R_Xd）
            raise DriftError("study_state.json 快照的 knowledge_window 行 status 必须是 %s 或省略: %r"
                             % ("/".join(_WIN_STATUSES), r))
        # 键按 _md_cell 归一（| → /、换行折叠），与 render_md 写进生成视图的单元格一致——否则含 | 的
        # point 名（DFS|BFS → DFS/BFS）会让正确重渲染的 md 被误判为陈旧面板（Codex OSTZP）
        window_rows.append("%s@%s" % (_md_cell(r["point"]), "" if ch in (None, "") else _md_cell(ch)))
        # 省略 status 归一到渲染默认「在窗口」——update_progress.render_md 也把缺省 status 渲成 在窗口，
        # 否则 {point,chapter} 无 status 的合法行会让 state 图（None）与 md 图（在窗口）误判不一致（SGGn）
        window_status.append(stt if isinstance(stt, str) else "在窗口")
        # 备注也按 _md_cell 归一（与 render_md 的备注列一致），省略 → ""，用于生成视图一致性比对（OSZRm）
        wn = r.get("note")
        window_note.append(_md_cell(wn, default="") if wn not in (None, "") else "")

    if len(set(window_rows)) != len(window_rows):
        # 同一 point@chapter 出现多次 = 追加而非更新的坏写入（update_progress 的 window-add 会去重）；
        # 静默收下会让 _window_diff 把重复当"新增"、迁移循环把它当迁移、生成视图折叠掉——矛盾的重复行
        # 却报 lost=0（Codex OSL86）。按畸形输入 fail-loud。
        dups = sorted({k for k in window_rows if window_rows.count(k) > 1})
        raise DriftError("study_state.json 快照的 knowledge_window 有重复条目（同一 point@chapter 多次出现，"
                         "窗口进出应更新同一行而非追加）: %s" % dups)
    return {"phase": phase, "mistake_rows": m_rows, "confusion_rows": c_rows,
            "mistake_status": stat["mistake_archive"], "confusion_status": stat["confusion_log"],
            "window_rows": window_rows, "window_status": window_status, "window_note": window_note}


def _snap_text(fa, name):
    """files_after 快照值必须是文件内容字符串——对象/数组直接喂给 json.loads/正则会 TypeError
    崩栈，按畸形输入统一 DriftError（文档承诺的 exit-2 路径）。"""
    v = fa[name]
    if not isinstance(v, str):
        raise DriftError("files_after 里的 %s 必须是文件内容字符串，实际是 %s"
                         % (name, type(v).__name__))
    return v


def _state_note(row):
    """state 行串去掉 [#id]/[ch:N] 前缀后的 note 本体。"""
    return re.sub(r"^\[(?:#|ch:)[^\]]*\]\s*", "", row)


def _contain_squash(txt):
    """跨源包含比对的归一化：镜像 _md_cell 的 换行→空格、|→/，再压掉全部空白。"""
    return re.sub(r"\s+", "", re.sub(r"\s+", " ", txt).replace("|", "/"))


def _dual_key(row):
    """双写比对用的行键：idless 生成表行首列是占位 '-'，_row_key 会把所有此类行折叠成同一个
    cell:- 键、同数替换互相隐身——这里降级为【去状态列】的内容键（最后一列是状态，官方
    set-*-status 只改它、state 行串不含状态，不能因此误报）。其余行沿用 _row_key。"""
    k = _row_key(row)
    if k != "cell:-":
        return k
    cells = row.strip().strip("|").split("|")
    return "tx:" + re.sub(r"\s+", "", "|".join(cells[:-1] if len(cells) > 1 else cells))


def _session_snapshots(turns, state_established=False, plan_phases=None, init_dual=None):
    """Per-turn parsed progress snapshot (None = no usable snapshot that turn), honoring the A4
    source-of-truth contract: within a turn study_state.json wins; and once state has appeared
    (fixture or any turn), a LATER md-only write is a hand-edit of the generated view — it must
    NOT advance checkpoint/row metrics（那正是 A4 要抓的漂移）. md fallback is for legacy sessions."""
    out, stale_md, prev_dual = [], 0, init_dual
    plan_phases = set(plan_phases) if plan_phases else None
    for t in turns:
        fa = t.get("files_after") or {}
        if "study_plan.md" in fa:
            # 会话内获授权改计划后，state 的新阶段要对照【最新】计划快照——拿初始计划卡会把
            # 合法改计划的转写误判成坏输入
            newp = set(parse_plan_phases(_snap_text(fa, "study_plan.md")))
            if newp:
                plan_phases = newp
        # 违规判定连事件一起看：直接手写 JSONL 可以只给 write_file 事件、不带快照——
        # 光看 files_after 会漏掉正是要抓的 A4 手改
        evs = {str(e.get("path", "")).replace(chr(92), "/").rsplit("/", 1)[-1]
               for e in (t.get("events") or []) if e.get("type") == "write_file"}
        md_touch = "study_progress.md" in fa or "study_progress.md" in evs
        state_touch = "study_state.json" in fa or "study_state.json" in evs
        if "study_progress.md" in fa and "study_state.json" in evs \
                and "study_state.json" not in fa:
            # 带 md 快照却只给 state 的 write_file 事件、不给 state 快照——生成视图是否手改
            # 无从核对，光靠事件豁免 md_write_after_state 正好放走要抓的回归；按畸形输入拒收
            raise DriftError("转写第 %s 回合有 study_progress.md 快照但 study_state.json 只有 "
                             "write_file 事件没有快照——无法核对 A4 事实源，请补 state 快照"
                             % t.get("turn", "?"))
        if "study_state.json" in fa and "study_progress.md" in evs \
                and "study_progress.md" not in fa:
            # 反向同理：写了生成视图（write_file 事件）却不给 md 快照——手改无从核对，
            # 光靠 state_touch 豁免计数正好放走要抓的回归
            raise DriftError("转写第 %s 回合写了 study_progress.md（write_file 事件）但没给 md 快照，"
                             "而同回合有 state 快照——生成视图无从核对，请补 md 快照"
                             % t.get("turn", "?"))
        if "study_state.json" in fa:
            state_established = True
            snap = parse_state_json(_snap_text(fa, "study_state.json"), plan_phases)
            out.append(snap)
            if "study_progress.md" in fa:
                # 双写却手改生成视图的两种形态都要抓：① md 归档行数超过事实源（新增行）；
                # ② md 行相对上一次双写发生变化而 state 纹丝不动（同数替换/改写）。
                # 不直接比 md↔state 行键——跨源无 id 行键格式不可比会误报；
                # 同源对比（md↔md、state↔state）没有这个问题
                md_snap = parse_progress(_snap_text(fa, "study_progress.md"))
                md_keys = ([_dual_key(r) for r in md_snap["mistake_rows"]],
                           [_dual_key(r) for r in md_snap["confusion_rows"]])
                st_keys = ([_dual_key(r) for r in snap["mistake_rows"]],
                           [_dual_key(r) for r in snap["confusion_rows"]])
                if md_snap["phase"] is not None and md_snap["phase"] != snap["phase"]:
                    # 断点面板陈旧：state 已到新阶段而生成视图还停在旧阶段——官方更新每次写
                    # state 都重渲染 md，双快照阶段必然一致
                    stale_md += 1
                elif (len(md_snap["mistake_rows"]) + len(md_snap["confusion_rows"])
                        != len(snap["mistake_rows"]) + len(snap["confusion_rows"])):
                    # 双向都算：md 多行=手加，md 少行=state 进了新行而给学生看的生成视图没跟上
                    #（官方更新每次写 state 都重渲染 md，双快照行数必然一致）
                    stale_md += 1
                elif _window_state_map(md_snap) != _window_state_map(snap):
                    # A6：生成视图的 🪟 区必须与事实源一致——不只行数，连每条 point@chapter 的状态都要匹配。
                    # state 把某条从 在窗口 迁到 窗口外 而 md 面板还停在旧状态（漏渲染/陈旧面板）→ 计入
                    # md_write_after_state（学生看到错的窗口状态，Codex R5LE + R_XY）
                    stale_md += 1
                elif prev_dual is not None and md_keys != prev_dual[0] \
                        and st_keys == prev_dual[1]:
                    stale_md += 1
                elif ([k for k in md_keys[0] if k.startswith("id:")],
                      [k for k in md_keys[1] if k.startswith("id:")]) != \
                        ([k for k in st_keys[0] if k.startswith("id:")],
                         [k for k in st_keys[1] if k.startswith("id:")]):
                    # 带 id 的行键跨源可比（md 表格/bullet 的 [#id] ↔ state 行的 [#id]）——
                    # 同数但 id 序列不同（state 进了 q1、生成视图却显示 q9）也是手改/陈旧面板；
                    # 无 id 行仍只走同源比对，不误报
                    stale_md += 1
                elif any(
                        _contain_squash(_state_note(r)) and all(
                            _contain_squash(_state_note(r)) not in _contain_squash(m)
                            for m in md_sec)
                        for st_sec, md_sec in ((snap["mistake_rows"], md_snap["mistake_rows"]),
                                               (snap["confusion_rows"], md_snap["confusion_rows"]))
                        for r in st_sec):
                    # 无 id 行同数同状态但 note 背离：事实源的 note 必须原文出现在同节的某条
                    # 生成行里（官方渲染逐字写入 note，仅换行/竖线被 _md_cell 归一）——
                    # add-confusion 这类无 id 路径的手改/陈旧面板由此现形
                    stale_md += 1
                elif any(sm is not None and ss is not None and sm != ss for sm, ss in
                         list(zip(md_snap["mistake_status"], snap["mistake_status"]))
                         + list(zip(md_snap["confusion_status"], snap["confusion_status"]))):
                    # 状态-only 更新的陈旧面板：state 已改状态而生成视图还挂旧状态——
                    # 官方 set-*-status 会重渲染 md，两侧状态必然一致；按节对位比对
                    stale_md += 1
                prev_dual = (md_keys, st_keys)
        elif "study_state.json" in evs:
            state_established = True   # 只有 write_file 事件、没带快照——事实源同样已确立，
            out.append(None)           # 后续 md-only 不能再当 legacy 来源
        elif "study_progress.md" in fa and not state_established:
            out.append(parse_progress(_snap_text(fa, "study_progress.md")))
        else:
            out.append(None)
        if state_established and md_touch and not state_touch:
            stale_md += 1            # A4 违规：state 确立后只动生成视图 md（官方更新会两个都写）
    return out, stale_md


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


# A6：面向学生的澄清/偏好问句线索（≤1天档严禁——每问一次都在浪费复习时间）。
# 这是 benchmark/behavior_smoke/run_behavior_smoke.py 的 asks_student_question 的**逐字等价副本**
# （两文件互不 import，各自独立、无网络/依赖）；tests/test_drift_harness 的 parity 测试锁二者一致，
# 改一处必须同步改另一处。
_STUDENT_ASK_CUE = re.compile(
    r"你想|你要|你希望|你打算|你更|你觉得|你认为|你选|你决定|你来定|由你|你自己|你先|"
    r"你(?:还)?记得|你会(?:不会)?|你能(?:不能)?|你有(?:没有)?|你是(?:不是|否)|你(?:比较)?熟|"
    r"你复习到|你学到|要不要|想不想|请问|需要我|哪一?章|从哪|"
    r"(?:先讲|先复习|先看|先做|先学|讲|复习|看|做|来)[^，。？?！!\n]{0,8}还是|"
    r"需(?:不需)?要(?:我|先)|要(?:不要)?先|用不用(?:先|我)|该(?:先|不该)|哪个先|先哪|先(?:讲|复习|看|做|学|过)(?:什么|哪|谁)|"
    r"还有(?:什么|没有)?问题|有没有(?:什么)?问题|有问题吗|还有(?:不懂|不会|疑问)|哪里不(?:懂|会|清楚)|"
    r"可以吗|可不可以|行吗|行不行|好吗|好不好|方便吗|需要吗|要吗|"
    r"接下来(?:怎么|怎样|如何|想|要|需要)|下一步(?:怎么|想|要)|怎么安排|如何安排|怎么(?:样)?进行|"
    r"do you\b|would you\b|are you\b|have you\b|can you\b|which chapter\b|what.*\byou\b|"
    r"should i\b|shall i\b|want me to\b|which.*first\b|any questions\b",
    re.I)
_RHETORICAL_PREFACE = re.compile(
    r"[你您]可能(?:会)?问|[你您]也许(?:会)?问|[你您](?:有没有|是不是|会不会)想过|[你您]是不是(?:觉得|以为)|"
    r"[你您]也许(?:会)?好奇|[你您]可能(?:会)?好奇|试想|想象一下|不妨想")
_SELF_ANSWER = re.compile(
    r"^(?:因为|答案|其实|这是因为|这(?:正)?是|正是|原因|答[:：]|because|the answer|it'?s because|"
    r"here'?s why)", re.I)


def _asks_student_question(txt):
    t = re.sub(r"[ \t]*\n[ \t]*", " ", txt or "").strip()
    sents = [s.strip() for s in re.split(r"(?<=[？?。！!；;])\s*", t) if s.strip()]
    if not sents:
        return False
    last = sents[-1]
    if (last.endswith("？") or last.endswith("?")) and not _RHETORICAL_PREFACE.search(last):
        return True
    for i, s in enumerate(sents):
        if not (s.endswith("？") or s.endswith("?")):
            continue
        if _RHETORICAL_PREFACE.search(s):
            continue
        nxt = sents[i + 1] if i + 1 < len(sents) else ""
        if _SELF_ANSWER.match(nxt):
            continue
        if _STUDENT_ASK_CUE.search(s):
            return True
    return False


def compute_metrics(scenario, fixture_dir, turns):
    goal_markers = scenario.get("goal_markers", DEFAULT_GOAL_MARKERS)
    unrelated = scenario.get("unrelated_goal_phrases", DEFAULT_UNRELATED_PHRASES)
    refusals = scenario.get("refusal_phrases", DEFAULT_REFUSAL_PHRASES)

    try:                                                          # a bad FIXTURE is malformed input (exit 2),
        plan_text = _read(os.path.join(fixture_dir, "study_plan.md"))   # not a harness crash
        init_md_path = os.path.join(fixture_dir, "study_progress.initial.md")
        init_progress = _read(init_md_path) if os.path.isfile(init_md_path) else None
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

    # A4 source-of-truth aware per-turn snapshots — computed ONCE and shared by the checkpoint /
    # row-persistence sections so the md-only-after-state rejection can't diverge between them
    state_init = os.path.join(fixture_dir, "study_state.json")
    # requires_state 的 A4 场景即使 fixture 没带 state 文件也从「事实源已确立」起步——
    # 纯 md 手改转写不能在 state 场景白拿 md_write_after_state=0（那正是要抓的回归）
    require_state = bool(scenario.get("requires_state")) or os.path.isfile(state_init)
    # 指标种子：fixture 自带 state 时，初始阶段/行都从 JSON 事实源来——
    # 生成视图 md 过期/不一致时不能拿它当会话起点
    try:
        if os.path.isfile(state_init):
            init_snap = parse_state_json(_read(state_init), set(canon))
        elif init_progress is not None:
            init_snap = parse_progress(init_progress)
        else:
            raise DriftError("fixture 需要 study_state.json 或 study_progress.initial.md 之一作为初始断点")
    except UnicodeDecodeError as e:
        # UnicodeDecodeError 是 ValueError 不是 OSError——单列，坏编码同样走畸形输入 exit-2
        raise DriftError("fixture 的 study_state.json 不是 UTF-8: %s" % e)
    except (IOError, OSError) as e:
        raise DriftError("fixture 的 study_state.json 读取失败: %s" % e)

    # 首个双写快照的同数手改也要有比对基线：fixture 同时给了 state 与初始 md 时，用它们
    # 做 prev_dual 种子（同源比对——md 基线配 md、state 基线配 state；只有一侧就没有基线，
    # 首回合仍靠行数/阶段背离兜底）
    init_dual = None
    if os.path.isfile(state_init) and init_progress is not None:
        md0 = parse_progress(init_progress)
        # 基线 md 必须与基线 state 一致（阶段/行数/id 序列）才可用作同源比对种子——
        # fixture 自带的初始 md 若本就陈旧，首回合官方 render 修复会被误判成手改
        consistent = (md0["phase"] in (None, init_snap["phase"])
                      and len(md0["mistake_rows"]) + len(md0["confusion_rows"])
                      == len(init_snap["mistake_rows"]) + len(init_snap["confusion_rows"])
                      and [k for k in (_dual_key(r) for r in md0["mistake_rows"])
                           if k.startswith("id:")]
                      == [k for k in (_dual_key(r) for r in init_snap["mistake_rows"])
                          if k.startswith("id:")]
                      and [k for k in (_dual_key(r) for r in md0["confusion_rows"])
                           if k.startswith("id:")]
                      == [k for k in (_dual_key(r) for r in init_snap["confusion_rows"])
                          if k.startswith("id:")]
                      # 无 id 行同样要证明一致：state 每条 note 都能在基线 md 同节找到——
                      # 行数/id 序列都对但 idless note 背离的陈旧基线不做种子，
                      # 否则首回合官方 render 修复会被误判成手改
                      and all(not _contain_squash(_state_note(r))
                              or any(_contain_squash(_state_note(r)) in _contain_squash(m)
                                     for m in md_sec)
                              for st_sec, md_sec in
                              ((init_snap["mistake_rows"], md0["mistake_rows"]),
                               (init_snap["confusion_rows"], md0["confusion_rows"]))
                              for r in st_sec))
        if consistent:
            init_dual = (([_dual_key(r) for r in md0["mistake_rows"]],
                          [_dual_key(r) for r in md0["confusion_rows"]]),
                         ([_dual_key(r) for r in init_snap["mistake_rows"]],
                          [_dual_key(r) for r in init_snap["confusion_rows"]]))
    snaps, md_after_state = _session_snapshots(turns, require_state, set(canon), init_dual)

    # RUNNING PHASE CONTEXT — carried forward so the wrong-phase / over-read checks can't be silently
    # disabled by omitting `phase_context`: a turn without an explicit phase inherits the session's
    # current phase (initial checkpoint → prior explicit phases / progress snapshots).
    running = init_snap["phase"] or (canon[0] if canon else None)
    turn_phase = []
    for i, t in enumerate(turns):
        explicit = _phase_of_turn(t)
        eff = explicit if explicit is not None else running
        turn_phase.append(eff)
        if eff is not None:
            running = eff
        snap = snaps[i]
        if snap is not None and snap["phase"] is not None:
            running = snap["phase"]

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

    # A6 模式漂移：≤1天时间宽裕度下，任何向学生抛出的澄清/偏好问句都算漂移（浪费复习时间）。
    # 时间宽裕度从 scenario.time_budget 读；非 ≤1天档本指标恒为 0（不适用）。
    urgent = _tier_is_urgent(scenario.get("time_budget"))        # canonical 归一后判定，别名（明天考/今天…）也算
    urgent_mode_questions = (sum(1 for t in assistant_turns if _asks_student_question(t.get("assistant", "")))
                             if urgent else 0)
    # A6：紧迫开场必须"推断并**持久化**模式/时间"——扫最后一个 study_state.json 快照，校验落盘了 canonical
    # 学习模式 + 紧迫时间档（默认 零基础从头讲 + ≤1天）。非紧迫档本指标不适用，恒为 1（不施压）。
    urgent_mode_persisted = 1
    if urgent:
        last_state = None
        for t in turns:
            sj = (t.get("files_after") or {}).get("study_state.json")
            if isinstance(sj, str):
                try:
                    last_state = json.loads(sj)
                except ValueError:
                    last_state = None
        urgent_mode_persisted = int(
            isinstance(last_state, dict) and last_state.get("mode") in _LEARN_MODES
            and _tier_is_urgent(last_state.get("time_budget")))

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
    run_ck = init_snap["phase"] or (canon[0] if canon else None)
    for i, t in enumerate(turns):
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
        snap = snaps[i]
        if snap is not None and snap["phase"] is not None:
            run_ck = snap["phase"]
    reset_detected = reset_count

    # 5) provenance fidelity — a turn is an EXPLANATION turn whenever the USER asked to explain (or it is
    #    tagged kind="explanation"); NOT escapable by giving the turn some other `kind` value.
    expl = [t for t in turns if t.get("assistant")
            and (t.get("kind") == "explanation" or EXPLAIN_TRIGGERS.search(t.get("user", "")))]
    labeled = sum(1 for t in expl if has_content_label(t.get("assistant", "")))
    provenance_fidelity = round(labeled / len(expl), 4) if expl else 1.0

    # 6) mistake / confusion persistence — track rows by their [#id] when present (so rewording an existing
    #    row isn't a false 'loss'); rows without an id fall back to normalized text.
    parsed = [init_snap]
    for snap in snaps:
        if snap is not None:
            parsed.append(snap)
    mistake_added = confusion_added = rows_lost = 0
    window_added = window_lost = window_status_migrations = 0
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
        # A6 知识点窗口持久化：新增可以（讲了新点），但**丢失**（静默掉行）不行。身份用 compat 判定
        # ——null-章节键 point@ 与 point@N 相容（工具补章节回填是同一行），补章节不算 丢+加（R5LA）。
        pw, cw = prev.get("window_rows", []), cur.get("window_rows", [])
        ps, cs = prev.get("window_status", []), cur.get("window_status", [])
        _add, _lost = _window_diff(pw, cw)
        window_added += _add
        window_lost += _lost
        # 状态迁移：同一（相容）条目状态变了（在窗口→窗口外→已实测）计一次——只保留行不迁移状态
        # 的转写不算真的窗口进出（R5LB）。
        pstat = list(zip(pw, ps))
        for c, st in zip(cw, cs):
            if st is None:
                continue
            for a, pst in pstat:
                if _window_same_row(a, c) and pst is not None and pst != st:
                    window_status_migrations += 1
                    break

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
        "urgent_mode_questions": urgent_mode_questions, "urgent_mode_persisted": urgent_mode_persisted,
        "plan_adherence": plan_adherence, "plan_mutations": plan_mutations,
        "quiz_items": quiz_items, "bank_backed": bank_backed, "invented": invented,
        "untagged_questions": untagged, "wrong_phase_quiz": wrong_phase, "invention_rate": invention_rate,
        "resumed_phase": resumed_phase, "expected_phase": expected_phase, "reset_detected": reset_detected,
        "explanation_turns": len(expl), "provenance_fidelity": provenance_fidelity,
        "mistake_rows_added": mistake_added, "confusion_rows_added": confusion_added,
        "progress_rows_lost": rows_lost, "md_write_after_state": md_after_state,
        "window_rows_added": window_added, "window_rows_lost": window_lost,
        "window_status_migrations": window_status_migrations,
        "wiki_reads": wiki_reads, "unique_wiki_files": wiki_files, "overread_flag": overread,
        "cost": cost,
    }


# ---------------- thresholds ----------------

# threshold key -> (metric key, comparator: 'min' means metric>=value, 'max' means metric<=value)
THRESHOLD_RULES = {
    "goal_retention_min": ("goal_retention", "min"),
    "goal_marker_min": ("goal_marker_seen", "min"),   # positive signal: exam goal referenced ≥ N times (0/1)
    "urgent_mode_questions_max": ("urgent_mode_questions", "max"),  # A6：≤1天档向学生提问的次数上限
    "urgent_mode_persisted_min": ("urgent_mode_persisted", "min"),  # A6：紧迫开场须把 mode/time 落盘（0/1）
    "plan_mutations_max": ("plan_mutations", "max"),
    "quiz_invention_rate_max": ("invention_rate", "max"),
    "untagged_questions_max": ("untagged_questions", "max"),
    "wrong_phase_quiz_max": ("wrong_phase_quiz", "max"),
    "checkpoint_reset_max": ("reset_detected", "max"),
    "provenance_fidelity_min": ("provenance_fidelity", "min"),
    "progress_rows_lost_max": ("progress_rows_lost", "max"),
    "window_rows_added_min": ("window_rows_added", "min"),   # A6：长会话里至少登记过 N 个窗口条目
    "window_rows_lost_max": ("window_rows_lost", "max"),     # A6：窗口条目不得静默丢失（进出是状态迁移）
    "window_status_migrations_min": ("window_status_migrations", "min"),  # A6：窗口条目须真的迁移状态
    "md_write_after_state_max": ("md_write_after_state", "max"),   # A4: state 确立后手改生成视图的次数
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


def run_llm(argv):
    """B3：opt-in 真 agent 长会话漂移测量——不再是 skeleton。委托给 run_live_smoke.py 的真管线
    （驱动真 agent 逐回合 → 录 T5b 会话日志 → 转 JSONL → 本文件的 compute_metrics/阈值判分 → 记账），
    它已实现 token 上限、失败中止、fixture 沙箱、opt-in env 门控。用 `--turns <spec>`（内含 fixture/
    scenario/turns）指定回合脚本，`--agent-cmd`/`--out-dir`/`--max-*` 等透传。CI 绝不跑（env 门控 + 需真 agent）。"""
    if os.environ.get("RUN_SKILL_DRIFT_LLM") != "1":
        sys.stderr.write("run_drift: --llm 需 RUN_SKILL_DRIFT_LLM=1 显式开启（真 agent 长会话，会产生真实"
                         "调用成本，opt-in、CI 绝不运行）\n")
        return 2
    live = os.path.join(HERE, "run_live_smoke.py")
    if not os.path.isfile(live):
        sys.stderr.write("run_drift: 缺 run_live_smoke.py，无法运行真 agent 长会话\n")
        return 3
    # 透传 --llm 之外的所有参数给 live runner（--agent-cmd/--out-dir/--turns/--max-* 由它校验）
    passthrough = [a for a in (argv or []) if a != "--llm"]

    def _flag_val(flag):
        for i, a in enumerate(passthrough):
            if a == flag and i + 1 < len(passthrough):
                return passthrough[i + 1]
            if a.startswith(flag + "="):
                return a.split("=", 1)[1]
        return None

    def _absolutize(flag):
        # --turns/--out-dir 与 run_live_smoke 一样是 **CWD 相对**——在此绝对化并回写 passthrough，
        # 让本处预检与委托的 live runner（及它内部再调本 harness 判分）用同一份绝对路径，
        # 避免路径口径不一致导致预检漏判、或付费跑完才在判分处「找不到 transcript」（Codex OSfRR/OSfRS）。
        for i, a in enumerate(passthrough):
            if a == flag and i + 1 < len(passthrough):
                passthrough[i + 1] = os.path.abspath(passthrough[i + 1])
                return passthrough[i + 1]
            if a.startswith(flag + "="):
                v = os.path.abspath(a.split("=", 1)[1])
                passthrough[i] = flag + "=" + v
                return v
        return None

    # run_live_smoke 的 --turns 有默认值（短 smoke live_smoke_basic）——委托时不带 --turns 会静默跑成
    # 短 smoke 而非调用者想要的长会话漂移探针。这里强制要求显式 --turns（Codex R_Xg）。
    if not _flag_val("--turns"):
        sys.stderr.write("run_drift: --llm 长会话漂移必须显式指定 --turns <回合脚本>（否则委托的 live "
                         "runner 会默认跑短 smoke 而非长会话漂移）\n")
        return 2
    turns_abs = _absolutize("--turns")
    _absolutize("--out-dir")

    # 预检：坏 turns / 坏 scenario / 依赖 state 的 scenario 都在**付费 agent 循环之前**拦下——
    # run_live_smoke 只在进 agent 循环前查 scenario 文件是否存在、不校验其内容，坏阈值会烧完 token 才在
    # 判分处炸（Codex OSfRP）。
    try:
        spec = json.loads(_read(turns_abs))
    except (IOError, OSError, ValueError):
        # turns 文件缺失/坏 JSON——交给 live runner 报（它在进 agent 循环前就会校验并 _die，不烧 token）
        return subprocess.run([sys.executable, live] + passthrough).returncode
    scen_ref = spec.get("scenario") if isinstance(spec, dict) else None
    sc = None
    if isinstance(scen_ref, str) and scen_ref:
        try:
            sc = load_scenario(_resolve(scen_ref))           # scenario ref 是 repo-root 相对（同 live runner）
        except DriftError as e:
            sys.stderr.write("run_drift: --llm 的 scenario 无法解析/校验（%s），拒绝在付费 agent 循环之前"
                             "委托：%s\n" % (scen_ref, e))
            return 2
    # run_live_smoke 只录对话 + 阶段切换时的合成 study_progress.md 快照，**不录 agent 写的 study_state.json**。
    # 因此依赖 state 快照的 scenario（requires_state / fixture 自带 study_state.json / state·窗口阈值）在 live
    # 判分里会看不到 state 写入而恒 0、谎报成 agent 失败——付费前显式拒绝（Codex OSL85/OSTZM/OSZRj）。
    # 只拒真正需 study_state.json 的：窗口行只来自 state、urgent_mode_persisted 读 state；checkpoint/
    # md_write_after/progress_rows_lost 由合成 md 快照支持，不该把默认 live_smoke_basic 也挡下。
    _STATE_THRESH = {"window_rows_added_min", "window_rows_lost_max", "window_status_migrations_min",
                     "urgent_mode_persisted_min"}
    fixture_has_state = (sc is not None and isinstance(sc.get("fixture"), str)
                         and os.path.isfile(os.path.join(_resolve(sc["fixture"]), "study_state.json")))
    if sc is not None and (sc.get("requires_state") or fixture_has_state
                           or (set(sc.get("thresholds", {})) & _STATE_THRESH)):
        sys.stderr.write("run_drift: --llm 暂不支持依赖 study_state.json 的 scenario（%s：requires_state / "
                         "fixture 自带 study_state.json / state·窗口阈值）——run_live_smoke 只录对话+合成 md、"
                         "不录 agent 写的 state 快照，会看不到 state/窗口写入而误判。请用无 state 的 scenario "
                         "跑 live，或走确定性 replay（--scenario ... --transcript ...）。捕获真实 state 快照是"
                         "后续工作。\n" % sc.get("name"))
        return 2
    return subprocess.run([sys.executable, live] + passthrough).returncode


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
    ap.add_argument("--llm", action="store_true",
                    help="opt-in 真 agent 长会话（委托 run_live_smoke.py 真管线；需 RUN_SKILL_DRIFT_LLM=1，CI 绝不跑）")
    # --llm 下把未知参数（--agent-cmd/--turns/--out-dir/--max-* 等）透传给 live runner；
    # 确定性 replay 路径仍严格解析——未识别参数报错，防 typo 静默吞掉
    args, extra = ap.parse_known_args(argv)
    if args.llm:
        return run_llm(argv if argv is not None else sys.argv[1:])
    if extra:
        ap.error("未识别的参数: %s" % " ".join(extra))

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
