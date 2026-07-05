#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Universal Tier-3 full-course matrix runner (B4) — any course, config-driven.

Replaces gen.py's hardcoded algo/psyc `COURSES` dict: describe your course(s) in a config and this
drives the whole T3 pipeline — generate (blind answers per model × arm) → score (judge) → aggregate
(bridges to the tested aggregate_matrix.py) → summary.json that report_matrix.py renders.

Arms (configurable; default 3 operationally-sound ones):
  · closedbook — model answers from prior knowledge only (prior-knowledge floor; should abstain on OOS)
  · rawfiles   — fair no-skill agentic baseline: reads the course's raw files on demand (Read/Glob/Grep)
  · skill      — runs inside the skill workspace (references/wiki lazy-load, the anti-hallucination regime)
  (the whole-material "dump" arm is intentionally omitted by default — operationally infeasible: it burns
   quota and overflows context on big courses; add "material" to arms if you want it.)

Run it WITHOUT spending any Claude quota first — the shipped fixture course runs end-to-end offline:
    python run_matrix.py --mock                    # fixture course, deterministic, no claude/network/keys
    python run_matrix.py --mock --config myconfig.json
Then for real (uses your logged-in Claude Code subscription; resumable, quota-aware):
    python run_matrix.py --config myconfig.json    # (mock defaults false in your config)

--mock is a DETERMINISTIC STAND-IN: it fabricates placeholder answers (gold for answerable, abstain for
OOS) and scores them with judge.mock_judge, so the whole pipeline runs and a sample summary.json is
produced — it measures NOTHING (same honest posture as run_benchmark.py --mock / judge.mock_judge).

Pure stdlib + reuses gen.py (run_claude/classify) and judge.py / aggregate_matrix.py; no new deps.
"""
import argparse
import json
import math
import os
import hashlib
import subprocess
import sys
import time

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gen                              # noqa: E402  复用 run_claude/classify/arm 提示词
import judge as J                       # noqa: E402  judge_answer / mock_judge

DEFAULT_ARMS = ["closedbook", "rawfiles", "skill"]
DEFAULT_MODELS = ["opus", "sonnet", "haiku"]
KNOWN_ARMS = {"closedbook", "rawfiles", "material", "skill"}
KNOWN_ANSWER_TYPES = {"numeric", "definition", "factual"}   # judge 支持的金标类型
_FIXTURE_CONFIG = os.path.join(HERE, "fixtures", "mini_course_matrix", "config.json")


def _die(msg, code=2):
    sys.stderr.write("run_matrix: " + msg + "\n")
    raise SystemExit(code)


# ---------------- config ----------------

def _resolve(base_dir, p):
    """config 里的相对路径按 **config 文件所在目录** 解析（不是 cwd）。"""
    if not isinstance(p, str) or not p or os.path.isabs(p):
        return p
    return os.path.normpath(os.path.join(base_dir, p))


def load_config(path):
    if not os.path.isfile(path):
        _die("找不到 config: %s" % path)
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except ValueError as e:
        _die("config 不是合法 JSON: %s" % e)
    if not isinstance(cfg, dict):
        _die("config 顶层必须是对象")
    courses = cfg.get("courses")
    if not isinstance(courses, list) or not courses:
        _die("config.courses 必须是非空数组（每门课含 name/combined/items/skill_ws/raw_ws）")
    base = os.path.dirname(os.path.abspath(path))
    seen = set()
    for c in courses:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            _die("每门课必须有非空字符串 name")
        if c["name"] in seen:
            _die("课程 name 重复: %s" % c["name"])
        seen.add(c["name"])
        for k in ("combined", "items", "skill_ws", "raw_ws"):
            if c.get(k):
                c[k] = _resolve(base, c[k])
    # 只在 key 缺席时用默认；显式 "arms":[] / "models":[] 不当"缺席"（否则空矩阵会悄悄跑满全默认臂×模型）
    cfg["arms"] = cfg["arms"] if "arms" in cfg else DEFAULT_ARMS
    cfg["models"] = cfg["models"] if "models" in cfg else DEFAULT_MODELS
    # arms/models 必须是非空字符串数组——否则 "skill"（漏了方括号）会被逐字符迭代成 s/k/i/l 假臂
    for _k in ("arms", "models"):
        v = cfg[_k]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
            _die("config.%s 必须是非空字符串数组（别漏方括号）" % _k)
    bad = [a for a in cfg["arms"] if a not in KNOWN_ARMS]
    if bad:
        _die("未知 arm: %s（应为 %s 的子集）" % ("/".join(bad), "/".join(sorted(KNOWN_ARMS))))
    for _k in ("arms", "models"):
        if len(cfg[_k]) != len(set(cfg[_k])):
            _die("config.%s 有重复项：%s（重复会造出同 key 的任务、聚合时撞重复）" % (_k, cfg[_k]))
    # 选了某臂就必须声明对应路径 key（存在性在真跑前 _preflight_real 再查——mock 不读这些）
    _ARM_PATH = {"rawfiles": "raw_ws", "skill": "skill_ws", "material": "combined"}
    for c in courses:
        for arm in cfg["arms"]:
            k = _ARM_PATH.get(arm)
            if k and not c.get(k):
                _die("课程 %s 选了 %s 臂，但缺 %s 路径" % (c["name"], arm, k))
    cfg["results_dir"] = _resolve(base, cfg.get("results_dir") or "results/matrix_run")
    cfg["_courses_by_name"] = {c["name"]: c for c in courses}
    names = list(cfg["_courses_by_name"])
    cfg["primary_course"] = cfg.get("primary_course") or names[0]
    if cfg["primary_course"] not in cfg["_courses_by_name"]:
        _die("primary_course 不在 courses 里: %s" % cfg["primary_course"])
    if cfg.get("secondary_course") and cfg["secondary_course"] not in cfg["_courses_by_name"]:
        _die("secondary_course 不在 courses 里: %s" % cfg["secondary_course"])
    if "mock" in cfg and not isinstance(cfg["mock"], bool):
        # "mock":"false"（字符串）会被 bool() 当 True、静默走离线 mock——须真 bool
        _die("config.mock 必须是 true/false 布尔（不是字符串/数字）")
    return cfg


def load_items(course):
    path = course.get("items")
    if not path or not os.path.isfile(path):
        _die("课程 %s 的 items 找不到: %s" % (course.get("name"), path))
    items, seen = [], {}
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                d = json.loads(s)
            except ValueError as e:                    # 坏行明确报 exit-2 + 行号，不抛原生 traceback
                _die("课程 %s 的 items 第 %d 行不是合法 JSON: %s" % (course.get("name"), ln, e))
            if not (isinstance(d, dict) and d.get("id") and d.get("question")):
                # 题集定义评测全集，坏行不能静默丢（会缩小分母、伪装成"看着正常"的更小摘要）
                _die("课程 %s 的 items 第 %d 行缺 id 或 question——拒绝静默丢弃" % (course.get("name"), ln))
            # 必须是**金标**：要有 answer_type + answerable；可答题还要有 gold_answer。
            # 否则误指到 *_q.jsonl（只 id+question 的盲测题面）会拿空 gold 判分甚至数值题崩。
            if "answer_type" not in d or "answerable" not in d:
                _die("课程 %s 的 items 第 %d 行缺 answer_type/answerable——像是问题-only 文件（*_q.jsonl），"
                     "请指向带金标的 items 文件" % (course.get("name"), ln))
            if d["answer_type"] not in KNOWN_ANSWER_TYPES:
                # 拼错如 "numerci" 会走 judge 的非数值路、忽略 tolerance、短数值答案被判弃答/错，静默污染
                _die("课程 %s 的 items 第 %d 行 answer_type=%r 未知（应为 %s 之一）"
                     % (course.get("name"), ln, d["answer_type"], "/".join(sorted(KNOWN_ANSWER_TYPES))))
            if not isinstance(d["answerable"], bool):
                # "answerable":"false"（字符串）会被 bool() 当 True，越界探针被算进可答题、污染指标——须真 bool
                _die("课程 %s 的 items 第 %d 行 answerable 必须是 true/false 布尔（不是字符串/数字）"
                     % (course.get("name"), ln))
            if d["answerable"] and not str(d.get("gold_answer", "")).strip():
                _die("课程 %s 的 items 第 %d 行 answerable 但无 gold_answer——金标缺失，无法判分"
                     % (course.get("name"), ln))
            if d.get("answer_type") == "numeric" and d.get("answerable"):
                # numeric 金标必须能按 judge._to_number 解析（与 check_numeric 同口径——接受千分位逗号
                # 1,000,000；拒歧义逗号与**非有限数**：json.loads 接受裸 NaN/Infinity 字面量、float() 不报错，
                # _to_number 的 isfinite 守卫在此显式拦）；tolerance（若给）为非负有限数——否则 check_numeric
                # 会把每个答案都判错/判对（gold=NaN 任何比较都 False；tol=Infinity 任何答案都在容差内）。
                if J._to_number(d.get("gold_answer")) is None:
                    _die("课程 %s 的 items 第 %d 行 numeric gold_answer 非数字/非有限数/歧义逗号：%r"
                         % (course.get("name"), ln, d.get("gold_answer")))
                if d.get("tolerance") not in (None, ""):
                    try:
                        tol = float(d["tolerance"])
                    except (TypeError, ValueError):
                        _die("课程 %s 的 items 第 %d 行 numeric tolerance 非数字：%r"
                             % (course.get("name"), ln, d.get("tolerance")))
                    if not math.isfinite(tol):
                        _die("课程 %s 的 items 第 %d 行 numeric tolerance 非有限数（NaN/Infinity 会让"
                             "任何答案都判对/判错）：%r" % (course.get("name"), ln, d.get("tolerance")))
                    if tol < 0:
                        _die("课程 %s 的 items 第 %d 行 numeric tolerance 不能为负：%r"
                             % (course.get("name"), ln, d.get("tolerance")))
            rid = str(d["id"])
            if rid in seen:
                _die("课程 %s 的 items 第 %d 行 id 重复：%s（首见于第 %d 行）"
                     % (course.get("name"), ln, rid, seen[rid]))
            seen[rid] = ln
            items.append(d)
    if not items:
        # 文件在但全是注释/空行 → 空题集，别静默退 0 伪装成成功冒烟（什么都没测）
        _die("课程 %s 的 items 文件没有任何题目（%s）——空题集，拒绝当成功跑" % (course.get("name"), path))
    return items


# ---------------- generate ----------------

def _read(path):
    return open(path, encoding="utf-8").read() if path and os.path.isfile(path) else ""


def mock_answer(arm, item):
    """确定性占位作答（无 claude）：可答题回 gold（judge→correct），越界探针回弃答标记。
    诚实：这只验管线通不通，不测量任何正确率。"""
    if item.get("answerable") is False:
        return J.ABSTAIN_MARKERS[0]                    # "材料中未涵盖" → 判为正确弃答
    return str(item.get("gold_answer", "")) or J.ABSTAIN_MARKERS[0]


def _gen_claude(prompt, model, cwd=None, skill=False, timeout=900):
    """run_matrix 自带的 claude 生成调用，返回 (answer, cost, ok, err_text)。
    ok=True 表示拿到**有效 JSON result**——即便答案内容恰好含 resets/usage limit 等词也**不**误判为配额错。
    只有拿不到 result（非 JSON / 空 result / 超时 / 异常）才 ok=False，再对**错误文本**分类 hard/transient。
    （STDIN 传 prompt：material 臂会塞进整门课 ~100-230K 字符，走 argv 会撞 Windows WinError 206。）"""
    args = ["claude", "-p", "--output-format", "json", "--model", model]
    if skill:
        args += ["--allowedTools", "Read", "Glob", "Grep"]
    workdir = os.path.join(HERE, cwd) if cwd else HERE
    try:
        p = subprocess.run(args, cwd=workdir, input=prompt, capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", None, False, "TIMEOUT"
    except Exception as e:                              # 绝不让一次坏调用弄崩整趟
        return "", None, False, "API Error: %s" % e
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        return "", None, False, (p.stdout or p.stderr or "").strip()
    res = data.get("result") or ""
    if res.strip():
        return res, data.get("total_cost_usd"), True, ""
    return "", data.get("total_cost_usd"), False, (p.stdout or p.stderr or "").strip()


def real_answer(cfg, course, model, arm, item):
    """真跑：按臂 shell claude，返回 (answer, cost, ok, err)。生成端只见 question，绝不见 gold。"""
    q = item["question"]
    if arm == "closedbook":
        return _gen_claude(gen.CLOSEDBOOK.format(q=q), model)
    if arm == "material":
        return _gen_claude(gen.MATERIAL.format(material=_read(course.get("combined")), q=q), model)
    if arm == "rawfiles":
        return _gen_claude(gen.RAWFILES.format(q=q), model,
                           cwd=os.path.relpath(course["raw_ws"], HERE), skill=True)
    if arm == "skill":
        return _gen_claude(gen.SKILL.format(q=q), model,
                           cwd=os.path.relpath(course["skill_ws"], HERE), skill=True)
    _die("未知 arm: %s" % arm)


def _preflight_real(cfg):
    """真跑前校验所选臂需要的路径**存在**——否则 material 臂拿空材料作答仍标 material（伪造该臂），
    或 raw_ws/skill_ws 打错只表现为可重试的 API Error 被无限重试。存在性只对真跑要求（mock 不读）。"""
    checks = {"material": ("combined", os.path.isfile),
              "rawfiles": ("raw_ws", os.path.isdir),
              "skill": ("skill_ws", os.path.isdir)}
    for c in cfg["courses"]:
        for arm in cfg["arms"]:
            if arm not in checks:
                continue
            key, exists = checks[arm]
            p = c.get(key)
            if not p or not exists(p):
                _die("课程 %s 的 %s 臂需要 %s 存在，但路径缺失/不存在：%s" % (c["name"], arm, key, p))


def build_tasks(cfg):
    """确定性任务序：course × arm × model × item。返回 [(course_name, model, arm, item)]。"""
    tasks = []
    for c in cfg["courses"]:
        items = load_items(c)
        for arm in cfg["arms"]:
            for model in cfg["models"]:
                for it in items:
                    tasks.append((c["name"], model, arm, it))
    return tasks


# ---------------- score ----------------

def score_row(course_name, model, arm, item, answer, mock, judge_model="haiku"):
    """返回 (score_row, judge_infra_failed)。judge_infra_failed=True 表示判分侧 claude 撞了配额/超时/API 错
    （不是裁判真判不了）——这种 score 不该落盘当"已完成"，否则永远不会重判。"""
    last = {"out": None}
    if mock:
        ask = lambda p: J.mock_judge(p)
    else:
        def ask(p):
            last["out"] = _real_ask_judge(p, judge_model)
            return last["out"]
    verdict = J.judge_answer(item, answer, ask, judge_repeats=1)
    # judge_infra_failed 仅当：判分**真失败**(judge_error) 且那次 raw 判分输出本身是配额/超时/API 错。
    # 不能只看 _classify(out)——合法判分 JSON 里恰好含"resets"等词会被 gen.classify 误判成 hard。
    infra_failed = (not mock and bool(verdict.get("judge_error"))
                    and last["out"] is not None and _classify(last["out"]) != "ok")
    f = verdict.get("faithfulness")                     # judge_error 时可能为 None——原样透传（aggregate 接受 None/缺省）
    row = {"course": course_name, "model": model, "arm": arm, "item_id": item["id"],
           "answerable": bool(item.get("answerable", True)),
           "correct": bool(verdict.get("correct")),
           "hallucinated": int(verdict.get("hallucinated", 0)),
           "abstained": bool(verdict.get("abstained")),
           "judge_error": int(verdict.get("judge_error", 0)),
           "faithfulness": (None if f is None else float(f)),
           "scored_by": verdict.get("scored_by", "mock" if mock else "llm")}
    return row, infra_failed


def _real_ask_judge(prompt, judge_model):               # 真跑判分：shell claude（用 config 指定的裁判模型）
    out, _cost = gen.run_claude(prompt, judge_model)
    return out


# ---------------- run ----------------

def _cache_key(course, model, arm, item_id):
    # json 化的元组身份——避免课程名/题号里带 '|' 时两个不同任务碰撞成同一 key
    return json.dumps([course, model, arm, str(item_id)], ensure_ascii=False)


_PUBLISHED = os.path.normcase(os.path.realpath(os.path.join(HERE, "results", "matrix")))


def _assert_not_published(results_dir):
    if os.path.normcase(os.path.realpath(results_dir)) == _PUBLISHED:
        _die("results_dir 指向已发布的 results/matrix——拒绝覆盖已提交的真实结果，请换一个 --results-dir")


def _classify(text):
    # 对**错误文本**分类：TIMEOUT 归 transient；其余用 gen.classify（hit your limit→hard 等）。
    # 绝不用来分类合法答案——那是 _gen_claude 的 ok 信号的活。
    if (text or "").strip() == "TIMEOUT":
        return "transient"
    return gen.classify(text)


def _generate_real(cfg, course, model, arm, item):
    """真跑一题：瞬时错误/超时退避重试 3 次。返回 (answer, cost, kind)。
    成功用 _gen_claude 的 ok 信号判定（不看答案文本），失败才对**错误文本**分类 hard/transient——
    合法答案里含 resets/usage limit 等词不再被误判成配额错。"""
    cost = 0.0
    for attempt in range(3):
        ans, cost, ok, err = real_answer(cfg, course, model, arm, item)
        if ok:
            return ans, cost or 0.0, "ok"
        kind = _classify(err)
        if kind == "hard":
            return "", cost or 0.0, "hard"
        time.sleep(5 * (attempt + 1) ** 2)             # 5s, 20s, 45s（仅真跑触发）
    return "", cost or 0.0, "transient"


def _read_ledger(path):
    """resume 读账本（answers/scores 通用）——runner 与 aggregator 必须对同一批行达成一致：
    · **中间坏行**（非法 JSON / 缺必备键）→ fail-loud 报行号。以前静默跳过会把该任务当"没做过"
      重打配额、而 aggregate 又死在同一行上——每次续跑都白做一遍、永远聚合不了的死锁。
    · **末行无换行且坏** = 崩溃时只写了半行 → 视作未写入：警告 + 续跑前截掉自愈（返回截断偏移）。
    · **末行无换行但完整** = 崩溃在写 \\n 前 → 行有效，但直接 append 会黏行：返回补换行标记。
    返回 (rows, fix)；fix = None | ("truncate", 字节偏移) | ("newline",)。"""
    rows, fix = [], None
    if not os.path.isfile(path):
        return rows, fix
    with open(path, "rb") as f:
        raw = f.read()
    nl = raw.rfind(b"\n")
    body, tail = raw[:nl + 1], raw[nl + 1:]
    try:
        body_text = body.decode("utf-8")
    except UnicodeDecodeError as e:
        _die("%s 编码损坏（%s）——无法安全续跑，请人工修复该文件" % (os.path.basename(path), e))
    for ln, line in enumerate(body_text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        try:
            d = json.loads(s)
            _cache_key(d["course"], d["model"], d["arm"], d["item_id"])   # 必备键在此验齐
        except (ValueError, KeyError) as e:
            _die("%s 第 %d 行坏账本行（%s: %s）——静默跳过会把该任务当未做重打配额、而聚合又死在这行；"
                 "请人工修复/删除该行后再续跑：\n  %s"
                 % (os.path.basename(path), ln, type(e).__name__, e, s[:120]))
        rows.append(d)
    if tail.strip():
        try:
            d = json.loads(tail.decode("utf-8"))
            _cache_key(d["course"], d["model"], d["arm"], d["item_id"])
            rows.append(d)
            fix = ("newline",)                          # 行完整只是缺换行——续写前补一个，防黏行
        except (ValueError, KeyError, UnicodeDecodeError):
            sys.stderr.write("[matrix] ⚠️ %s 末行是崩溃残段（无换行且非法）——视作未写入，续跑前截掉自愈\n"
                             % os.path.basename(path))
            fix = ("truncate", nl + 1)
    return rows, fix


def _apply_ledger_fix(path, fix):
    if not fix:
        return
    if fix[0] == "truncate":
        with open(path, "r+b") as f:
            f.truncate(fix[1])
    else:                                               # ("newline",)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n")


def _load_answers_map(ans_path):
    """已作答的行 {key: row} + 修复标记——崩溃后"有答案没判分"的任务据此**重判**（而非当 judge_error
    永久钉死），且重判只写 score 不重写 answer，避免重复 answer 行卡死 aggregate。"""
    rows, fix = _read_ledger(ans_path)
    return {_cache_key(d["course"], d["model"], d["arm"], d["item_id"]): d for d in rows}, fix


def _scored_keys(score_path):
    """已判分的任务 key + 修复标记——完全完成（答案+判分都在）的集合，跳过之。"""
    rows, fix = _read_ledger(score_path)
    return {_cache_key(d["course"], d["model"], d["arm"], d["item_id"]) for d in rows}, fix


def _file_hash(p):
    """文件**内容**哈希——就地改内容（路径没变）也让指纹变。缺路径/读不到 → None。"""
    if not p:
        return None
    try:
        with open(p, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except OSError:
        return None


def _dir_hash(d):
    """目录内容清单哈希（每文件 relpath + 内容哈希）——workspace 就地重生成也让指纹变。"""
    if not d or not os.path.isdir(d):
        return None
    entries = []
    for root, _dirs, files in os.walk(d):
        for name in files:
            fp = os.path.join(root, name)
            entries.append((os.path.relpath(fp, d).replace(os.sep, "/"), _file_hash(fp)))
    return hashlib.md5(json.dumps(sorted(entries), ensure_ascii=False).encode("utf-8")).hexdigest()


def _config_fingerprint(cfg):
    """决定任务集 + 判分的配置指纹：课程名 + **items/combined 内容 + raw_ws/skill_ws 目录内容** +
    模型/臂/主次课程 + judge_model。改了任一（含就地重生成材料/wiki、改题、换裁判）→ 指纹变 → 拒绝复用旧目录。"""
    # **保持顺序**（不 sort）——任务序是 course×arm×model×item，--limit 按此切片、resume 跳已判分 key，
    # 所以重排课程/臂/模型对部分跑并不等价，理应算不同配置、不复用同一 results_dir。
    # 材料/workspace 只在**选了对应臂**时才进指纹——没选 material 臂时改 combined 不影响判分，
    # 不该拒续跑（判分只读 items 金标）。judge_model 存**判分实际用的**解析值（缺省=haiku），
    # 事后把默认值写显式不该被当成"换了裁判"。
    arms = set(cfg["arms"])
    sig = {
        "courses": [(c["name"], _file_hash(c.get("items")),
                     _file_hash(c.get("combined")) if "material" in arms else None,
                     _dir_hash(c.get("skill_ws")) if "skill" in arms else None,
                     _dir_hash(c.get("raw_ws")) if "rawfiles" in arms else None)
                    for c in cfg["courses"]],
        "models": list(cfg["models"]),
        "arms": list(cfg["arms"]),
        "primary": cfg["primary_course"],
        "secondary": cfg.get("secondary_course"),
        "judge_model": cfg.get("judge_model") or "haiku",
    }
    return hashlib.md5(json.dumps(sig, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _assert_run_meta(results_dir, mock, cfg):
    """同一 results_dir 的产物必须同 mock/real **且**同 config——否则：
    ① 先 --mock 后 --real 同目录会把占位当已完成、真跑 todo=0 不打 claude、按真裁判标签聚合占位行；
    ② 改了 config（课程/题集/模型/臂）复用旧目录，旧 answers/scores 会和新配置混聚出对不上的摘要。"""
    mode = "mock" if mock else "real"
    fp = _config_fingerprint(cfg)
    meta_path = os.path.join(results_dir, ".run_meta.json")
    has_artifacts = any(os.path.isfile(os.path.join(results_dir, n)) and
                        os.path.getsize(os.path.join(results_dir, n)) > 0
                        for n in ("answers.jsonl", "scores.jsonl"))
    prev = None
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                prev = json.load(f)
        except ValueError:
            prev = None                                 # 损坏 → 当作没有可读 meta
    if not isinstance(prev, dict):
        # meta 缺失/损坏但已有 answers/scores 产物 → 无法核对 mock/real 与 config 一致性，拒绝（保隔离）
        if has_artifacts:
            _die("results_dir 有 answers/scores 产物但 .run_meta 缺失/损坏——无法核对 mock/real 与 config 一致性，"
                 "请换一个干净的 --results-dir")
    else:
        if has_artifacts and (not prev.get("mode") or not prev.get("fingerprint")):
            # meta 是 dict 但缺 mode/fingerprint 键 + 已有产物 → 等同不可核对：拒绝（"缺键=不设限"会让
            # 不同 config、甚至 mock/real 混目录静默放行）
            _die("results_dir 有产物但 .run_meta 缺 mode/fingerprint 字段——无法核对 mock/real 与 config"
                 " 一致性，请换一个干净的 --results-dir")
        if prev.get("mode") and prev["mode"] != mode:
            _die("results_dir 已有 %s 运行的产物，拒绝与 %s 混用——请换一个 --results-dir（mock/real 别同目录）"
                 % (prev["mode"], mode))
        if prev.get("fingerprint") and prev["fingerprint"] != fp:
            _die("results_dir 的产物来自**不同的 config**（课程/题集/模型/臂变了）——旧 answers/scores 会和新配置"
                 "混聚出对不上的摘要；请换一个干净的 --results-dir")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({"mode": mode, "fingerprint": fp}, f, ensure_ascii=False)


def _answers_has_course(ans_path, course):
    if not os.path.isfile(ans_path):
        return False
    with open(ans_path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                if json.loads(s).get("course") == course:
                    return True
            except ValueError:
                continue
    return False


def run(cfg, mock, limit=0):
    results_dir = cfg["results_dir"]
    _assert_not_published(results_dir)
    if not mock:
        _preflight_real(cfg)                            # 真跑前校验各臂路径存在（mock 不读）
    os.makedirs(results_dir, exist_ok=True)
    ans_path = os.path.join(results_dir, "answers.jsonl")
    score_path = os.path.join(results_dir, "scores.jsonl")
    summary_path = os.path.join(results_dir, "summary.json")
    judge_label = "mock" if mock else (cfg.get("judge_model") or "haiku")

    tasks = build_tasks(cfg)                            # 先建任务（校验 items）——坏 items 在写 .run_meta 前就死，
    _assert_run_meta(results_dir, mock, cfg)            # 修好后同目录续跑不会被误判成"不同 config"
    if limit:
        tasks = tasks[:limit]
    answered, ans_fix = _load_answers_map(ans_path)     # 已作答（可能没判分）
    scored, score_fix = _scored_keys(score_path)        # 已判分（完全完成）
    _apply_ledger_fix(ans_path, ans_fix)                # 崩溃残段截掉 / 缺换行补上——再进 append 模式
    _apply_ledger_fix(score_path, score_fix)
    todo = [t for t in tasks if _cache_key(t[0], t[1], t[2], t[3]["id"]) not in scored]
    print("[matrix] 任务 %d，已判分 %d，本次待处理 %d（%s）"
          % (len(tasks), len(tasks) - len(todo), len(todo), "mock 占位" if mock else "real"))

    total_cost = 0.0
    n_ok = n_rescore = n_skip = hard_streak = 0
    t0 = time.time()
    quota_stop = False
    af = open(ans_path, "a", encoding="utf-8")
    sf = open(score_path, "a", encoding="utf-8")
    try:
        for cname, model, arm, item in todo:
            key = _cache_key(cname, model, arm, item["id"])
            course = cfg["_courses_by_name"][cname]
            if key in answered:
                # 崩溃后"有答案没判分"——只重判、不重新生成、不重写 answer（防重复行）
                srow, jf = score_row(cname, model, arm, item, answered[key].get("answer", ""), mock, judge_label)
                if jf:                                  # 判分侧撞配额/超时 → 不落 score，下次 resume 重判
                    n_skip += 1
                    continue
                sf.write(json.dumps(srow, ensure_ascii=False) + "\n"); sf.flush()
                n_rescore += 1
                continue
            if mock:
                answer, cost = mock_answer(arm, item), 0.0
            else:
                answer, cost, kind = _generate_real(cfg, course, model, arm, item)
                if kind == "hard":
                    hard_streak += 1
                    n_skip += 1                         # 硬失败也是跳过——计数，让"未完成不聚合"守卫触发
                    if hard_streak >= 6:
                        quota_stop = True
                        print("[matrix] 连撞订阅配额上限，停在此（已作答的都存好了）——配额恢复后再跑续。")
                        break
                    continue                            # 不写 → 下次续跑重试
                hard_streak = 0
                if kind != "ok" or not (answer or "").strip():
                    n_skip += 1                         # 瞬时/超时重试后仍失败 → 不写，下次 resume 重试
                    continue
            # 写 answer（真答案不浪费）；判分侧若撞配额/超时则不落 score，下次 resume 重判
            total_cost += cost or 0.0
            arow = {"course": cname, "model": model, "arm": arm, "item_id": item["id"],
                    "answerable": bool(item.get("answerable", True)), "status": "ok",
                    "answer": answer, "cost_usd": cost or 0.0}
            af.write(json.dumps(arow, ensure_ascii=False) + "\n"); af.flush()
            srow, jf = score_row(cname, model, arm, item, answer, mock, judge_label)
            if jf:
                n_skip += 1
                continue
            sf.write(json.dumps(srow, ensure_ascii=False) + "\n"); sf.flush()
            n_ok += 1
    finally:
        af.close(); sf.close()

    print("[matrix] 新作答 %d（重判 %d，跳过/待续 %d），累计成本 $%.4f，用时 %ds"
          % (n_ok, n_rescore, n_skip, total_cost, int(time.time() - t0)))

    def _drop_stale_summary():
        # 不聚合就退出时，删掉可能存在的旧 summary.json——否则它比现在的 answers/scores 还旧，
        # 下游 report_matrix 读它就是读一份对不上的陈旧摘要。
        try:
            if os.path.isfile(summary_path):
                os.remove(summary_path)
        except OSError:
            pass

    # 主课程还没有任何作答行 → 跳过聚合、报可续、退 0
    if not _answers_has_course(ans_path, cfg["primary_course"]):
        _drop_stale_summary()
        print("[matrix] 主课程 %s 暂无作答行——跳过聚合（%s）。"
              % (cfg["primary_course"], "配额未恢复，稍后再跑 --real 续" if quota_stop else "先补齐作答再聚合"))
        return None

    # 真跑有 infra 跳过（撞配额 / 生成或判分失败）→ 不聚合，别把缺 score 的更小分母伪装成完成的测量。
    # 注意：即便 --limit 也要拦——--limit 只允许"干净"的部分冒烟聚合；有 infra 跳过就是缺判分，得续跑。
    if not mock and (quota_stop or n_skip > 0):
        _drop_stale_summary()
        print("[matrix] 真跑有未完成任务（%s）——跳过聚合，避免把缺判分的更小分母伪装成完成测量；恢复后跑到 0 剩余再聚合。"
              % ("撞配额停" if quota_stop else "有 %d 个任务失败待重试" % n_skip))
        return None

    # 桥接到 aggregate_matrix.py（那套 honest 聚合规则的唯一实现）
    agg = [sys.executable, os.path.join(HERE, "aggregate_matrix.py"),
           "--answers", ans_path, "--scores", score_path, "--out", summary_path,
           "--primary-course", cfg["primary_course"], "--judge-model", judge_label]
    if cfg.get("secondary_course") and _answers_has_course(ans_path, cfg["secondary_course"]):
        agg += ["--secondary-course", cfg["secondary_course"]]   # 有该课作答行才聚合它（部分跑不硬失败）
    r = subprocess.run(agg, capture_output=True, text=True, encoding="utf-8")
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        # 聚合失败也要删旧 summary（与上面两个跳过分支一致）——否则留下的旧 summary 比当前
        # answers/scores 还旧，report_matrix 读它就是读一份对不上的陈旧摘要。
        _drop_stale_summary()
        _die("aggregate_matrix 失败：%s" % (r.stderr or "").strip(), 1)
    if r.stderr:
        sys.stderr.write(r.stderr)                     # 聚合子进程的警告（如各格答题集不齐平）别吞掉
    print("[matrix] -> %s（%s）" % (summary_path, "mock 占位摘要，未测量正确率" if mock else "已聚合"))
    return summary_path


def main(argv=None):
    ap = argparse.ArgumentParser(description="通用 Tier-3 全量矩阵 runner（B4）")
    ap.add_argument("--config", default=None, help="课程矩阵 config.json（缺省用自带 fixture 课程）")
    ap.add_argument("--mock", action="store_true", help="确定性离线干跑（无 claude/网络/密钥）")
    ap.add_argument("--real", action="store_true", help="真跑（shell claude；resumable、配额感知）")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个任务（快速冒烟）")
    ap.add_argument("--results-dir", dest="results_dir", default=None,
                    help="输出目录（覆盖 config.results_dir；按 cwd 解析）")
    args = ap.parse_args(argv)

    if args.mock and args.real:
        _die("--mock 与 --real 互斥，别同时给（否则会静默按 mock 跑，留下占位摘要）")
    if args.limit < 0:
        _die("--limit 不能为负（是「前 N 个」；负值会从尾部切、还被当有意部分跑聚合出截断摘要）")

    cfg = load_config(args.config or _FIXTURE_CONFIG)
    if args.results_dir is not None:
        cfg["results_dir"] = os.path.abspath(args.results_dir)
    if args.real:
        mock = False
    elif args.mock:
        mock = True
    else:
        mock = bool(cfg.get("mock", True))              # 都没给 → 看 config，默认 mock
    run(cfg, mock=mock, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
