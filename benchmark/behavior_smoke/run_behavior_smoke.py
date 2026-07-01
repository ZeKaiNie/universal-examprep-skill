#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tier 2 behavioral smoke — DETERMINISTIC by default, real-LLM smoke OPT-IN only.

This harness tests the skill as a *tutoring workflow*, not just as static files.

  python benchmark/behavior_smoke/run_behavior_smoke.py --check-fixture   # validate the mini-course
  python benchmark/behavior_smoke/run_behavior_smoke.py --mock            # run detectors on mock outputs

The --mock / --check-fixture paths are stdlib-only, no network, no LLM, no API key — safe for CI.
Real-agent smoke is gated behind BOTH a flag and an env opt-in and never runs by default:

  RUN_SKILL_BEHAVIOR_LLM=1 python benchmark/behavior_smoke/run_behavior_smoke.py --llm

It will NOT call any model in CI, never reads API keys, and never runs a paid benchmark.
"""
import os
import re
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))           # repo root
FIXTURE = os.path.join(HERE, "fixtures", "mini_course")
SCENARIOS = os.path.join(HERE, "scenarios.json")
RESULTS_DIR = os.path.join(HERE, "results")             # gitignored output dir

# canonical provenance labels — single source of truth is docs/language-policy.md
CANON_LABELS = [
    "🟢 来自资料",
    "🟡 AI补充，可能与你老师讲的不完全一致",
    "⚠️ AI生成答案，非老师/教材提供",
]


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return f.read()


# ---------------- detectors (deterministic, stdlib-only) ----------------

def load_quiz_bank_ids(workspace):
    data = json.loads(_read(os.path.join(workspace, "references", "quiz_bank.json")))
    return {str(q.get("id")) for q in data if isinstance(q, dict) and q.get("id") is not None}


def load_quiz_bank_map(workspace):
    """{id: {'question', 'chapter', 'phase'}} — used for content-match + chapter/phase-scope checks."""
    data = json.loads(_read(os.path.join(workspace, "references", "quiz_bank.json")))
    return {str(q["id"]): {"question": q.get("question", ""), "chapter": q.get("chapter"),
                           "phase": q.get("phase")}
            for q in data if isinstance(q, dict) and q.get("id") is not None}


def extract_question_ids(text):
    """Quiz outputs mark each drawn item as [#<id>]; pull them all out."""
    return re.findall(r"\[#([^\]\s]+)\]", text or "")


# numbered / Q. / Qn: / 第n题 — always a question item
_NUM_ITEM_RE = re.compile(r"^\s*(?:\d+\s*[.、)）]|[Qq]\d*\s*[.、:：)）]|第\s*[一二三四五六七八九十百零\d]+\s*题\s*[:：]?)")
# an OPTION line — labels are A–D (NOT all of A–Z, so "Q." is a question, not an option)
_OPTION_RE = re.compile(r"^\s*[-*•]?\s*(?:[A-Da-d]|[一二三四甲乙丙丁]|[①②③④⑤⑥])\s*[.、)）.]")


def _is_question_item(ln):
    if _OPTION_RE.match(ln):
        return False
    if _NUM_ITEM_RE.match(ln):
        return True
    # a BULLET counts as a question only if it actually reads like one (ends with ？/?),
    # so a harmless instruction bullet ("- 请直接回复答案") isn't required to carry a tag.
    return bool(re.match(r"^\s*[-*•]\s", ln) and re.search(r"[？?]\s*$", ln))


def assert_quiz_ids_in_bank(text, bank):
    """bank = set of ids (ID/scope check) OR dict {id: question} (also content-match each item).
    Pass a CHAPTER-SCOPED bank to enforce that the quiz draws only from the requested chapter."""
    t = text or ""
    allowed = set(bank)
    qmap = bank if isinstance(bank, dict) else None
    # (a) EVERY [#id] tag anywhere must be in the (scoped) bank — catches invented OR out-of-scope tags.
    ids = extract_question_ids(t)
    if not ids or any(i not in allowed for i in ids):
        return False
    # (b) no malformed multi-tag lines, no UNTAGGED question-item line, and (c) each tag's bank question
    #     must appear in ITS OWN segment (this line + following lines up to the next tag) — so swapping
    #     tag↔content across items (mc_q1's tag on mc_q2's text) is caught, and an invented body fails.
    lines = t.splitlines()
    for idx, ln in enumerate(lines):
        lids = extract_question_ids(ln)
        if len(lids) > 1:
            return False
        if not lids and _is_question_item(ln):
            return False
        if lids and qmap is not None:
            seg = re.sub(r"\[#[^\]]+\]", "", ln)
            for nxt in lines[idx + 1:]:
                if extract_question_ids(nxt):
                    break
                seg += nxt
            seg = re.sub(r"\s+", "", seg)
            b = re.sub(r"\s+", "", qmap.get(lids[0], ""))
            if b:
                k = min(8, len(b))
                # require BOTH ends of the bank question to appear: a prefix-collision or an appended
                # invented body changes the END, so it fails; a middle paraphrase (dropping e.g.
                # "（queue）") keeps both ends, so it passes.
                if b[:k] not in seg or b[-k:] not in seg:
                    return False
    return True


def has_canonical_provenance_labels(text):
    # each canonical label must ANNOTATE content — prefix `label：内容` OR suffix `内容（label）` —
    # not sit in a legend (single- or multi-line) of bare labels with no labelled answer.
    t = text or ""
    for lbl in CANON_LABELS:
        used = False
        for m in re.finditer(re.escape(lbl), t):
            # content must be on the SAME line as the label ([ \t] only — no newline crossing), so a
            # multi-line legend "🟢 …：\n🟡 …：\n答案：…" with an unlabelled answer does NOT count.
            prefix_use = re.match(r"[ \t]*[:：][ \t]*\S", t[m.end():m.end() + 24])       # label：内容
            # 内容（label — the char before （ must be real content, NOT another label's closing ）
            # (so a paren-legend "标签说明（🟢…）（🟡…）（⚠️…）" doesn't read as labelled content)
            suffix_use = re.search(r"[^）)\s][ \t]*[（(][ \t]*$", t[max(0, m.start() - 16):m.start()])
            if prefix_use or suffix_use:
                used = True
                break
        if not used:
            return False
    return True


def _heading_present(text, name):
    """True if `name` is a section HEADING — markdown (## / **bold**), an ordered-list heading
    (`1. 考点拆解`), OR the skill's documented bracket block (`【考点拆解】`) — not an inline mention."""
    n = re.escape(name)
    md = (rf"(?m)^\s{{0,3}}"
          rf"(?:#{{1,4}}\s*|\*\*\s*|[0-9一二三四五六七八九十]+\s*[、.．)）]\s*){{1,3}}{n}")
    # the bracket block must START a line (a heading), not be inline in a checklist like "请包含：【…】【…】"
    bracket = rf"(?m)^\s{{0,3}}[【〖]\s*{n}\s*[】〗]"
    return bool(re.search(md, text or "")) or bool(re.search(bracket, text or ""))


def _heading_has_body(text, name):
    """The section under `name`'s heading must have actual body content (not an empty heading)."""
    n = re.escape(name)
    m = re.search(rf"(?:#{{1,4}}\s*|\*\*\s*|[0-9一二三四五六七八九十]+\s*[、.．)）]\s*|[【〖]\s*)+{n}\s*[】〗]?",
                  text or "")
    if not m:
        return False
    rest = (text or "")[m.end():]
    # cut the body at the next markdown/bracket heading only — NOT at numbered lines, since a section
    # body legitimately contains ordered lists (e.g. "1. 判断结构类型 …").
    nxt = re.search(r"(?m)^\s{0,3}(?:#{1,4}\s|\*\*|[【〖])", rest)
    body = rest[:nxt.start()] if nxt else rest
    return len(re.sub(r"\s+", "", body)) >= 2


def _zb(text, *variants):
    return any(_heading_present(text, v) and _heading_has_body(text, v) for v in variants)


def has_zero_basic_sections(text):
    # each of the four parts must be a real heading AND have actual body content under it
    return (_zb(text, "考点拆解")
            and _zb(text, "标准答题步骤", "标准答题模板")
            and _zb(text, "易错点")
            and _zb(text, "3分钟速记", "三分钟速记"))


def visual_first_asset_display_ok(text, fixture_path=FIXTURE):
    """Smoke-check a visual-required output contract.

    This is structural, not a UI renderer: it requires a labelled question-side Markdown image
    before any prose/prompt/explanation/hint/answer text, rejects non-question images anywhere in
    the prompt block, and rejects path-only or unsafe image targets.
    """
    t = text or ""
    if re.search(r"[/\\][A-Za-z]:", t):
        return False

    question_asset_roles = {"question_context", "figure", "diagram", "table"}
    answer_asset_roles = {"answer_context", "worked_solution"}

    def normalized_safe_asset_target(target):
        p = (target or "").strip()
        if not p or p.startswith(("/", "\\")):
            return None
        if "://" in p or re.match(r"(?i)^[a-z][a-z0-9+.-]*:", p):
            return None
        if any(part == ".." for part in re.split(r"[\\/]+", p)):
            return None
        if not p.startswith("references/assets/"):
            return None
        abs_fixture = os.path.abspath(fixture_path)
        abs_target = os.path.abspath(os.path.join(abs_fixture, *re.split(r"[\\/]+", p)))
        if not (abs_target == abs_fixture or abs_target.startswith(abs_fixture + os.sep)):
            return None
        if not (os.path.isfile(abs_target) and os.access(abs_target, os.R_OK)):
            return None
        return "/".join(re.split(r"[\\/]+", p))

    def expected_assets_for_output():
        ids = extract_question_ids(t)
        if not ids:
            return None, None
        try:
            bank = json.loads(_read(os.path.join(fixture_path, "references", "quiz_bank.json")))
        except Exception:
            return None, None

        by_id = {str(q.get("id")): q for q in bank if isinstance(q, dict) and q.get("id") is not None}
        question_paths, answer_paths = set(), set()
        for qid in dict.fromkeys(ids):
            item = by_id.get(str(qid))
            if item is None:
                return None, None
            for asset in item.get("assets", []) or []:
                if not isinstance(asset, dict):
                    continue
                path = asset.get("path")
                if not isinstance(path, str):
                    continue
                normalized = "/".join(re.split(r"[\\/]+", path.strip()))
                role = asset.get("role")
                if role in question_asset_roles:
                    question_paths.add(normalized)
                elif role in answer_asset_roles:
                    answer_paths.add(normalized)
        return question_paths, answer_paths

    image_re = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    question_side_label = "\u9898\u9762\u56fe / question-side asset"
    answer_side_label = "\u7b54\u6848\u56fe / answer-side asset"

    def prompt_alt_ok(alt):
        alt = alt or ""
        lower_alt = alt.lower()
        return (
            question_side_label in alt
            and answer_side_label not in alt
            and "answer-side asset" not in lower_alt
            and "worked solution" not in lower_alt
            and "\u7b54\u6848\u56fe" not in alt
        )

    def answer_alt_ok(alt):
        alt = alt or ""
        lower_alt = alt.lower()
        return (
            question_side_label not in alt
            and (
                answer_side_label in alt
                or "answer-side asset" in lower_alt
                or "worked solution" in lower_alt
            )
        )

    expected_question_assets, expected_answer_assets = expected_assets_for_output()
    if not expected_question_assets:
        return False

    marker_patterns = (
        "(^|\\n)\\s*" + "\u9898\u76ee",
        "(^|\\n)\\s*" + "\u95ee\u9898[:\uff1a]",
        "(^|\\n)\\s*" + "\u8bf7\u4f5c\u7b54",
        "(^|\\n)\\s*" + "\u8bf7\u56de\u7b54",
        "(^|\\n)\\s*" + "\u8bf7\u5148\u56de\u7b54",
        "(^|\\n)\\s*" + "\u4f5c\u7b54",
        "(^|\\n)\\s*" + "\u63d0\u793a",
        "(^|\\n)\\s*" + "\u89e3\u6790",
        "(^|\\n)\\s*" + "\u7b54\u6848[:\uff1a]",
        r"(^|\n)\s*Question:",
        r"(^|\n)\s*Hint:",
        r"(^|\n)\s*Explanation:",
        r"(^|\n)\s*Answer:",
    )
    positions = [m.start() for pat in marker_patterns for m in [re.search(pat, t, flags=re.MULTILINE)] if m]
    if not positions:
        return False
    first_action = min(positions)

    solution_marker_patterns = (
        "(^|\\n)\\s*" + "\u89e3\u6790",
        "(^|\\n)\\s*" + "\u7b54\u6848[:\uff1a]",
        r"(^|\n)\s*Explanation:",
        r"(^|\n)\s*Answer:",
    )
    solution_positions = [
        m.start()
        for pat in solution_marker_patterns
        for m in [re.search(pat, t, flags=re.MULTILINE)]
        if m and m.start() >= first_action
    ]
    solution_start = min(solution_positions, default=None)

    images = list(image_re.finditer(t))
    displayed_question_assets = set()

    for img in images:
        target = normalized_safe_asset_target(img.group(2))
        if target is None:
            return False
        if img.start() < first_action:
            if not prompt_alt_ok(img.group(1)) or target not in expected_question_assets:
                return False
            displayed_question_assets.add(target)
            continue
        if solution_start is None or img.start() < solution_start:
            return False
        if not answer_alt_ok(img.group(1)) or target not in expected_answer_assets:
            return False

    if not expected_question_assets.issubset(displayed_question_assets):
        return False
    qpos = min(img.start() for img in images if img.start() < first_action)
    if t[:qpos].strip():
        return False

    pre_action_without_images = image_re.sub("", t[:first_action]).strip()
    if pre_action_without_images:
        return False

    return qpos < first_action


def has_hint_skip_offer(text):
    t = (text or "")
    tl = t.lower()
    has_hint = ("提示" in t) or ("hint" in tl)
    has_skip = ("跳过" in t) or ("skip" in tl)
    has_archive = ("错题本" in t) or ("错题档案" in t) or ("归档" in t)
    # reject explicit DENIAL of any escape-hatch option — negation BEFORE the verb/noun
    # ("不会…记录进错题档案") OR AFTER the noun ("错题本暂不记录此题").
    negated = (bool(re.search(
                   r"(没有|不能|不会|不可以|不可|无法|不给|不予|不许|不准|拒绝)[^。\n]{0,10}?(提示|跳过|归档|错题本|错题档案)", t))
               or bool(re.search(r"(错题本|错题档案)[^。\n]{0,6}?(暂不|不记|不写|不归|未记|不予记|不会记|不加入)", t))
               # bare 不 + verb ("不归档" / "不写入" / "不让跳过") and 不把/不将 + verb ("不把…记录")
               or bool(re.search(r"不\s*(归档|写入|记入|记录|存入|加入|放入)|不\s*(给|让|许|准)\s*(提示|跳过)", t))
               or bool(re.search(r"不\s*(把|将)[^。\n]{0,8}?(归档|记录|记入|写入|存入|加入)", t)))
    return has_hint and has_skip and has_archive and not negated


def _section(text, header_keywords):
    """Lines under the first '## ' header containing ANY of header_keywords, until the next '## '."""
    if isinstance(header_keywords, str):
        header_keywords = [header_keywords]
    out, grab = [], False
    for ln in (text or "").splitlines():
        if ln.startswith("## "):
            grab = any(k in ln for k in header_keywords)
            continue
        if grab:
            out.append(ln)
    return "\n".join(out)


def _table_data_rows(section_text):
    """Markdown table data rows in a section (excludes header, separator, and non-table prose)."""
    rows = []
    seen_header = False
    for ln in section_text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):   # separator row ( | --- | --- | )
            continue
        if not seen_header:
            seen_header = True                          # first table row is the header
            continue
        # skip empty-state placeholder rows like `| 暂无错题 | - | - |` or all-blank/dash rows
        joined = "".join(cells)
        if (not joined
                or all(c in ("", "-", "—", "/", "无", "暂无", "空", "N/A", "n/a") for c in cells)
                or re.search(r"暂无|尚无|无记录|无错题|无疑难", joined)):
            continue
        rows.append(cells)
    return rows


def _row_matches(rows, expect):
    # token-boundary match (join cells with a separator) so `mc_q2` doesn't match `mc_q20`
    pat = re.compile(rf"(?<![0-9A-Za-z_]){re.escape(expect)}(?![0-9A-Za-z_])")
    return any(pat.search(" | ".join(r)) for r in rows)


_ARCHIVE_NEG = re.compile(r"未\s*归档|未\s*记录|未\s*存|未\s*加入|待\s*归档|待\s*记录|暂不|尚未")


def progress_has_mistake_archive(progress_text, expect=None):
    # standard template section is "## ❌ 错题档案记录"; mini/legacy wording uses "错题本" — accept both.
    # a row must match `expect` (if given) AND not be marked 未归档/待归档 (i.e. actually archived).
    rows = _table_data_rows(_section(progress_text, ["错题档案", "错题本"]))

    def archived(r):
        joined = " | ".join(r)
        if expect and not re.search(rf"(?<![0-9A-Za-z_]){re.escape(expect)}(?![0-9A-Za-z_])", joined):
            return False
        return not _ARCHIVE_NEG.search(joined)

    return any(archived(r) for r in rows)


def progress_has_confusion_row(progress_text, expect=None):
    rows = _table_data_rows(_section(progress_text, ["疑难", "confusion"]))
    if not rows:
        return False
    return _row_matches(rows, expect) if expect else True


def progress_current_phase(progress_text):
    # anchor to the CURRENT-phase marker (当前阶段 / 当前进行阶段) so a "已完成：阶段 1" line listed
    # BEFORE the active checkpoint can't be misread as the current phase.
    m = re.search(r"当前(?:进行)?阶段[^#\n]{0,8}?(\d+)", progress_text or "")
    return int(m.group(1)) if m else None


# restart-at-1 / from-scratch language a resume message must NOT contain.
# NB: no \b after the digit — between a digit and a CJK char there is no word boundary, so
# "从阶段1开始" (no space) would otherwise slip; (?!\d) guards against matching 阶段 10/11.
_RESTART_RE = re.compile(
    r"从\s*头\s*开始|从\s*阶段\s*1(?!\d)|从\s*第\s*1\s*阶段|重新\s*开始|重头\s*开始|从头(重新)?来")


def resume_refers_to_phase(resume_text, phase):
    """Resume must point at the CURRENT phase AND not restart at phase 1 / from scratch.

    Accepts spacing/word-order variants: 阶段 2 / 阶段2 / 第2阶段 / 第 2 阶段.
    """
    t = resume_text or ""
    mentions = bool(re.search(rf"阶段\s*{phase}(?!\d)|第\s*{phase}\s*阶段", t))
    # reject negation of the current phase, both 阶段2 and 第2阶段 forms
    negated = bool(re.search(rf"(?:不是|不在)\s*(?:第\s*{phase}\s*阶段|第?\s*阶段\s*{phase})", t))
    # reject restarting from a NON-current phase, both word orders: 阶段2 / 第2阶段
    rm = re.search(r"从\s*(?:第\s*(\d+)\s*阶段|第?\s*阶段\s*(\d+))\s*(?:阶段)?\s*(?:开始|起|做起|学起|重新|重头)", t)
    if rm:
        rn = rm.group(1) or rm.group(2)
        if rn != str(phase):
            return False
    return mentions and not negated and not _RESTART_RE.search(t)


def count_wiki_reads(transcript_text):
    """best-effort: count read_file events that touch references/wiki/*.md in a JSONL transcript."""
    n = 0
    for line in (transcript_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("tool") == "read_file" and "references/wiki/" in str(ev.get("path", "")):
            n += 1
    return n


def validate_fixture_workspace(path):
    """Run the Tier-1 validator on a workspace. Returns (ok, errors, warnings, stats)."""
    spath = os.path.join(ROOT, "scripts")
    if spath not in sys.path:
        sys.path.insert(0, spath)
    import validate_workspace as V
    errors, warnings, stats = V.validate(path)
    return V._exit_code(errors) == 0, errors, warnings, stats


# ---------------- scenario runner (mock = deterministic) ----------------

def load_scenarios():
    return json.loads(_read(SCENARIOS))


def _p(rel):
    return os.path.join(HERE, rel)


def check_scenario_mock(name, sc, fixture_path=FIXTURE):
    """Return (ok, detail) for one scenario using only mock artifacts — no LLM."""
    if name == "quiz_bank_only":
        qmap = load_quiz_bank_map(fixture_path)
        ch = sc.get("chapter")
        scoped = {i: v["question"] for i, v in qmap.items()
                  if ch is None or str(v.get("chapter")) == str(ch) or str(v.get("phase")) == str(ch)}
        min_q = sc.get("min_questions", 1)
        good_txt = _read(_p(sc["mock_output"]))
        n_good = len(set(extract_question_ids(good_txt)))
        good = assert_quiz_ids_in_bank(good_txt, scoped) and n_good >= min_q
        bad = assert_quiz_ids_in_bank(_read(_p(sc["mock_negative"])), scoped)
        return (good and not bad), f"good={good} n={n_good}>={min_q} invented/oos_caught={not bad} ch/phase={ch}"
    if name == "provenance_labels":
        ok = has_canonical_provenance_labels(_read(_p(sc["mock_output"])))
        return ok, f"all_canonical_labels={ok}"
    if name == "hint_skip_mistake_archive":
        offer = has_hint_skip_offer(_read(_p(sc["mock_output"])))
        arch = progress_has_mistake_archive(_read(_p(sc["progress_after"])), sc.get("expect_archive"))
        return (offer and arch), f"hint_skip_offer={offer} mistake_archived={arch}"
    if name == "confusion_tracking":
        ok = progress_has_confusion_row(_read(_p(sc["mock_output"])), sc.get("expect_confusion"))
        return ok, f"confusion_row_written={ok}"
    if name == "checkpoint_recovery":
        ph = progress_current_phase(_read(os.path.join(fixture_path, "study_progress.md")))
        resume = _read(_p(sc["mock_output"]))
        refers = resume_refers_to_phase(resume, sc["expected_phase"])
        return (ph == sc["expected_phase"] and refers), f"current_phase={ph} resume_refers_current={refers}"
    if name == "no_python_fallback":
        ok = validate_fixture_workspace(_p(sc["fallback_workspace"]))[0]
        return ok, f"hand_authored_workspace_valid={ok}"
    if name == "zero_basic_key_question":
        ok = has_zero_basic_sections(_read(_p(sc["mock_output"])))
        return ok, f"required_sections_present={ok}"
    if name == "visual_first_assets":
        good = visual_first_asset_display_ok(_read(_p(sc["mock_output"])), fixture_path)
        answer_first = visual_first_asset_display_ok(_read(_p(sc["mock_negative"])), fixture_path)
        answer_before_prompt = visual_first_asset_display_ok(_read(_p(sc["mock_negative_leak"])), fixture_path)
        unlabeled_answer = visual_first_asset_display_ok(_read(_p(sc["mock_negative_unlabeled"])), fixture_path)
        prose_before = visual_first_asset_display_ok(_read(_p(sc["mock_negative_prose"])), fixture_path)
        answer_after_prompt = visual_first_asset_display_ok(_read(_p(sc["mock_negative_after_prompt"])), fixture_path)
        unsafe_path = visual_first_asset_display_ok(_read(_p(sc["mock_negative_unsafe_path"])), fixture_path)
        question_label_late = visual_first_asset_display_ok(
            _read(_p(sc["mock_negative_question_label_late"])), fixture_path)
        missing_asset = visual_first_asset_display_ok(_read(_p(sc["mock_negative_missing_asset"])), fixture_path)
        answer_text = visual_first_asset_display_ok(_read(_p(sc["mock_negative_answer_text"])), fixture_path)
        path_only = visual_first_asset_display_ok(_read(_p(sc["mock_negative_path"])), fixture_path)
        return (
            good and not answer_first and not answer_before_prompt and not unlabeled_answer
            and not prose_before and not answer_after_prompt and not unsafe_path
            and not question_label_late and not missing_asset and not answer_text and not path_only
        ), (
            f"good={good} answer_side_first_caught={not answer_first} "
            f"answer_before_prompt_caught={not answer_before_prompt} "
            f"unlabeled_answer_caught={not unlabeled_answer} prose_before_caught={not prose_before} "
            f"answer_after_prompt_caught={not answer_after_prompt} unsafe_path_caught={not unsafe_path} "
            f"question_label_late_caught={not question_label_late} missing_asset_caught={not missing_asset} "
            f"answer_text_caught={not answer_text} "
            f"path_only_caught={not path_only}")
    return False, "unknown scenario"


def run_mock(verbose=True):
    spec = load_scenarios()
    fixture_path = os.path.join(HERE, spec.get("fixture", "fixtures/mini_course"))
    results = []   # (name, detail, status) where status ∈ {PASS, FAIL, SKIP}
    for sc in spec["scenarios"]:
        name = sc["name"]
        if sc.get("best_effort"):
            # best-effort scenarios are NOT asserted in deterministic mode — report as SKIP,
            # never as PASS, so the conclusion doesn't overstate what was verified.
            results.append((name, "best-effort（需 LLM/transcript，--mock 不断言）", "SKIP"))
            continue
        ok, detail = check_scenario_mock(name, sc, fixture_path)
        results.append((name, detail, "PASS" if ok else "FAIL"))
    n_pass = sum(1 for _, _, s in results if s == "PASS")
    n_skip = sum(1 for _, _, s in results if s == "SKIP")
    n_fail = sum(1 for _, _, s in results if s == "FAIL")
    if verbose:
        for name, detail, status in results:
            print(f"  [{status}] {name}: {detail}")
        print(f"  ({n_pass} passed, {n_skip} skipped[best-effort, 未断言], {n_fail} failed)")
    return n_fail == 0, results


def check_fixture(verbose=True):
    ok, errors, warnings, stats = validate_fixture_workspace(FIXTURE)
    # the fixture is documented as 0-error AND 0-warning — a warning means a recommended field
    # (keywords / diagram_type / code tests …) was lost, which would weaken the six-type smoke.
    clean = ok and not warnings
    if verbose:
        print(f"fixture: {FIXTURE}")
        print(f"  valid={ok}  warnings={len(warnings)}  stats={stats}")
        for e in errors:
            print(f"  [error] {e['msg']}")
        for w in warnings:
            print(f"  [warning] {w['msg']}")
    return clean


def run_llm():
    """OPT-IN skeleton: real `claude -p` smoke. Never runs in CI, never reads API keys."""
    if os.environ.get("RUN_SKILL_BEHAVIOR_LLM") != "1":
        print("LLM behavioral smoke is OPT-IN and disabled by default.")
        print("To enable you must set env RUN_SKILL_BEHAVIOR_LLM=1 AND pass --llm. Refusing to run.")
        return 2
    # Skeleton only — T2 ships the harness, not the paid runs. The real path would, per scenario:
    #   1) copy FIXTURE into a tempdir, 2) run `claude -p <scenario.prompt>` (subscription, no API key),
    #   3) capture output/files into RESULTS_DIR, 4) apply the SAME deterministic detectors as --mock.
    print("RUN_SKILL_BEHAVIOR_LLM=1 detected, but the real-agent smoke is NOT YET WIRED in this PR.")
    print("This is a skeleton: it does not run `claude`, capture output, or apply any detector.")
    print("Scenarios it WILL drive once implemented:",
          ", ".join(s["name"] for s in load_scenarios()["scenarios"]))
    return 3   # NOT 0 — a no-op must not be recorded as a passing smoke run


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Tier 2 behavioral smoke — deterministic by default, LLM smoke opt-in")
    ap.add_argument("--mock", action="store_true",
                    help="run deterministic detectors on mock outputs (no LLM, no network)")
    ap.add_argument("--check-fixture", action="store_true",
                    help="validate the mini-course fixture workspace (Tier 1)")
    ap.add_argument("--llm", action="store_true",
                    help="real claude -p smoke; requires RUN_SKILL_BEHAVIOR_LLM=1 (off in CI)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.llm:
        return run_llm()
    if args.check_fixture:
        return 0 if check_fixture() else 1
    if args.mock:
        ok, _ = run_mock()
        print("结论:", "✓ 确定性行为冒烟全部通过（best-effort 项已跳过、未断言）" if ok else "✗ 有失败")
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
