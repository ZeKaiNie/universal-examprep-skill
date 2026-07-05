#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""B5 判分校准（通用）：对 run_matrix 的**任意课程**输出做「人工 vs 裁判」Cohen's kappa 校准。

取代 calibrate.py 硬编码的 algo/psyc——直接读 B4 `run_matrix` 的 results_dir（answers.jsonl + scores.jsonl）
配 config 的金标，任意课程可用。流程：
  1) sample —— 抽**分层**样本（一半裁判判对、一半判错，避免 kappa 退化），写出**隐藏裁判判定**的待填表；
              你只看 question + gold + reference_span 判 model_answer 对不对（越界题以「是否老实弃答」为准），
              在 human_correct 列填 1/0。
  2) kappa  —— 填完后算 Cohen's kappa(human, judge) + 原始一致率 + 列出人机分歧（裁判最可能错的地方）。

    python calibrate_matrix.py sample --results-dir <dir> --config <config.json> --n 30 [--seed 7]
    python calibrate_matrix.py kappa  --results-dir <dir>

诚实：kappa < ~0.6 时别信任裁判数字（先改裁判/题目）。**跨家族裁判**：裁判与生成器同模型家族（都 Claude）
有自我偏好嫌疑——sample 会警告，建议换个不同家族的裁判重判再校准。纯 stdlib、零依赖。
"""
import argparse
import csv
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_matrix as RM   # noqa: E402  复用 load_config/load_items
import stats as S         # noqa: E402  cohen_kappa

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

# 待填表**不含 model/arm**——标注者看到答案来自 skill/closedbook/某模型会带偏判断（隐藏在 key 里，留作后续按臂分析）
_FIELDS = ["ref_id", "course", "answerable", "question", "gold_answer",
           "reference_span", "model_answer", "human_correct"]


def _die(msg, code=2):
    sys.stderr.write("calibrate_matrix: " + msg + "\n")
    raise SystemExit(code)


def _flat(s):
    return " ".join(str(s or "").split())


def _csv_safe(s):
    """Excel/Sheets 公式注入防护：单元格以 = + - @ 或制表/回车开头时加 ' 前缀（Excel 视为文本标记、
    不显示）。模型答案是**不可信文本**——'=HYPERLINK(...)' 直接进表会在标注者打开时被当公式执行。"""
    s = str(s)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def _model_family(model):
    m = (model or "").lower()
    if any(t in m for t in ("opus", "sonnet", "haiku", "claude")):
        return "claude"
    if "gemini" in m:
        return "gemini"
    if any(t in m for t in ("gpt", "o1", "o3", "openai")):
        return "openai"
    if "deepseek" in m:
        return "deepseek"
    if "mock" in m:
        return "mock"
    return m or "unknown"


def _load_jsonl(path):
    # 坏行**不静默丢**：中断写/手改导致的半行会缩小样本、把失败/难例排除在外，让 kappa 虚高 → 直接报错行号。
    out = []
    if os.path.isfile(path):
        # utf-8-sig：编辑器（记事本/Excel）重存 .jsonl 会加 BOM——用 sig 读会剥掉，别把合法首行误判成坏行 die。
        with open(path, encoding="utf-8-sig") as f:
            for ln, line in enumerate(f, 1):
                s = line.strip()
                if not s:
                    continue
                try:
                    out.append(json.loads(s))
                except ValueError as e:
                    _die("坏 JSONL 行（第 %d 行，%s）：%s\n  %s" % (ln, os.path.basename(path), e, s[:120]))
    return out


def build_pool(results_dir, cfg):
    """把 answers.jsonl + scores.jsonl + config 金标 join 成校准池（只留有裁判判定的项）。"""
    ans_rows = _load_jsonl(os.path.join(results_dir, "answers.jsonl"))
    score_rows = _load_jsonl(os.path.join(results_dir, "scores.jsonl"))
    if not ans_rows or not score_rows:
        _die("results_dir 里没有 answers.jsonl / scores.jsonl（先跑 run_matrix 生成）：%s" % results_dir)
    # 金标：course → {id: item}
    gold = {}
    for c in cfg["courses"]:
        gold[c["name"]] = {str(it["id"]): it for it in RM.load_items(c)}

    def key(r):
        return (r.get("course"), r.get("model"), r.get("arm"), str(r.get("item_id")))
    # 重复行（同 (course,model,arm,item) 多条）说明文件被拼接/损坏——保哪条都可能让人标注的答案
    # 与裁判实际判的答案**不是同一条**，直接拒绝，别猜。
    answers = {}
    for r in ans_rows:
        k = key(r)
        if k in answers:
            _die("answers.jsonl 有重复行 (course=%s, model=%s, arm=%s, item=%s)——文件疑被拼接/损坏，"
                 "人工核对去重后再校准" % k)
        answers[k] = r.get("answer", "")
    seen_scores = set()
    pool = []
    for sc in score_rows:
        if sc.get("judge_error"):                      # 判分失败：没有有效裁判判定，不进校准池
            continue
        k = key(sc)
        if k in seen_scores:
            _die("scores.jsonl 有重复行 (course=%s, model=%s, arm=%s, item=%s)——同一答案存在多条裁判判定，"
                 "无法确定人工该对哪条校准；人工核对去重后再来" % k)
        seen_scores.add(k)
        item = gold.get(sc.get("course"), {}).get(str(sc.get("item_id")))
        # 判定行配不上答案/金标 = 账本失同步或 config 配错——静默丢会缩小样本、把 kappa 偏向剩下的行。
        # 与重复行/坏行同一条 fail-loud 政策：报出 key 和出路，不猜。
        if k not in answers:
            _die("scores.jsonl 有判定但 answers.jsonl 里没有对应答案 (course=%s, model=%s, arm=%s, item=%s)"
                 "——answers/scores 失同步（截断/拼接？），修复账本后再校准" % k)
        if item is None:
            _die("scores.jsonl 的判定行在 config 金标里找不到题 (course=%s, item=%s)——config 与该 results_dir"
                 " 不配套（题集变了/指到错的 items 文件），请用产出该目录的同一 config"
                 % (sc.get("course"), sc.get("item_id")))
        pool.append({
            "course": sc.get("course"), "model": sc.get("model"), "arm": sc.get("arm"),
            "id": str(sc.get("item_id")),
            "answerable": bool(item.get("answerable", True)),
            "answer_type": item.get("answer_type", "factual"),
            "scored_by": sc.get("scored_by"),
            "question": item.get("question", ""), "gold_answer": item.get("gold_answer", ""),
            "reference_span": item.get("supporting_span", ""), "answer": answers[k],
            "judge_correct": 1 if sc.get("correct") else 0,
        })
    return pool


def _is_deterministic(p):
    """确定性判分且**无需人工校准**的项——数值题（check_numeric）与词法快路（scored_by=lexical）
    天然一致会灌高 kappa，抽样排除。**越界探针（answerable=false）保留在样本里**：它们的判定来自
    弃答检测器（looks_abstained 关键词启发式，照样会错），待填表已标注「越界题以是否老实弃答为准」，
    人工 vs 弃答检测器的一致性正是要校准的东西之一。"""
    return p.get("answerable") and (p.get("answer_type") == "numeric"
                                    or p.get("scored_by") == "lexical")


def _sheet_paths(out_dir):
    return (os.path.join(out_dir, "calibration_sheet.csv"),
            os.path.join(out_dir, ".calibration_key.jsonl"))


def _assert_config_matches(results_dir, cfg):
    """校验给的 config 与产出该 results_dir 的一致（run_matrix 写的 .run_meta 指纹）——否则会把旧答案/判定
    按 (course,id) 配到**新题面/金标**上（id 没变、内容变了），静默出错。
    · meta 缺失（手拼目录）→ 响亮警告但放行（指纹无从核对，责任交还操作者）；
    · meta 存在但坏/缺指纹 → 直接拒——这曾是 run_matrix 目录，读不出指纹时**假定不匹配**比静默放行安全。"""
    meta_path = os.path.join(results_dir, ".run_meta.json")
    if not os.path.isfile(meta_path):
        sys.stderr.write("calibrate_matrix: ⚠️ 该 results_dir 没有 .run_meta.json——无法核对 config 指纹。"
                         "请自行确保这就是产出这些 answers/scores 的**同一 config**（金标配错会把标注带偏）。\n")
        return
    try:
        with open(meta_path, encoding="utf-8") as f:
            prev_fp = json.load(f).get("fingerprint")
    except (ValueError, OSError):
        _die(".run_meta.json 损坏（读不出/非法 JSON）——无法核对 config 指纹，不能默认放行；"
             "人工确认 config 无误后可删掉该文件重试（等价于放弃指纹校验，会有警告）")
    if not prev_fp:
        _die(".run_meta.json 缺 fingerprint 字段——无法核对 config 指纹；"
             "人工确认 config 无误后可删掉该文件重试（等价于放弃指纹校验，会有警告）")
    if prev_fp != RM._config_fingerprint(cfg):
        _die("给的 config 与产出该 results_dir 的 run_matrix 记录不一致（config/题集/材料变了）——"
             "校准会把旧答案/判定配到新题面/金标上；请用**产出该 results_dir 的同一 config**")


def cmd_sample(args):
    cfg = RM.load_config(args.config)
    _assert_config_matches(args.results_dir, cfg)
    pool = build_pool(args.results_dir, cfg)
    if not pool:
        _die("没有可抽样的条目（answers/scores 与 config 金标对不上，或题集为空）")

    # 只校准 LLM 裁判真正判的项——排除确定性判分（numeric/lexical 快路），它们天然一致会灌高 kappa。
    judged = [p for p in pool if not _is_deterministic(p)]
    det_n = len(pool) - len(judged)
    if judged:
        pool = judged
        if det_n:
            print("[i] 已排除 %d 条确定性判分（numeric/词法快路）——它们不测 LLM 裁判、会灌水 kappa。" % det_n)
    else:
        print("[i] 池里全是确定性判分（numeric/词法快路），无 LLM 裁判判定可校准——照抽以验流程，但 kappa 对裁判无意义。")

    # 四层分层：可答判对 / 可答判错 / 越界弃答 / 越界未弃答（可答:越界 ≈ 2:1，判对:判错各半）。
    # 只按判对/判错两层抽会让越界探针的占比随池子波动——弃答检测器也是校准对象，得保证两个方向都进样。
    # 某层供给不足就先层内配平、再全池补满（补满会打破配比，末尾如实报出实际构成）。
    rng = random.Random(args.seed)
    strata = {(True, 1): [], (True, 0): [], (False, 1): [], (False, 0): []}
    for p in pool:
        strata[(bool(p["answerable"]), p["judge_correct"])].append(p)
    for v in strata.values():
        rng.shuffle(v)
    third = args.n // 3
    targets = {(True, 1): third, (True, 0): third,
               (False, 1): (args.n - 2 * third) // 2,
               (False, 0): args.n - 2 * third - (args.n - 2 * third) // 2}
    pick = []
    for k, want in targets.items():
        pick += strata[k][:want]
        strata[k] = strata[k][want:]
    if len(pick) < args.n:                              # 某层不够 → 用其余层补满（先随机混合）
        extra = [p for v in strata.values() for p in v]
        rng.shuffle(extra)
        pick += extra[:args.n - len(pick)]
    rng.shuffle(pick)

    out_dir = args.out_dir or os.path.join(args.results_dir, "calibration")
    os.makedirs(out_dir, exist_ok=True)
    sheet, keyp = _sheet_paths(out_dir)
    with open(sheet, "w", encoding="utf-8-sig", newline="") as f, open(keyp, "w", encoding="utf-8") as kf:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for i, p in enumerate(pick, 1):
            ref = "cal_%03d" % i
            note = "" if p["answerable"] else "  ←【越界题：材料无答案，正确=老实弃答】"
            w.writerow({"ref_id": ref, "course": _csv_safe(_flat(p["course"])),
                        "answerable": int(p["answerable"]),
                        "question": _csv_safe(_flat(p["question"]) + note),
                        "gold_answer": _csv_safe(_flat(p["gold_answer"])),
                        "reference_span": _csv_safe(_flat(p["reference_span"])),
                        "model_answer": _csv_safe(_flat(p["answer"])), "human_correct": ""})
            # model/arm 藏进 key（不进待填表，避免带偏标注），留作后续按臂/模型分析
            kf.write(json.dumps({"ref_id": ref, "judge_correct": p["judge_correct"],
                                 "model": p["model"], "arm": p["arm"]}, ensure_ascii=False) + "\n")

    n_pos = sum(1 for p in pick if p["judge_correct"] == 1)   # 实际抽到的判对/判错数（一层空时补满会打破配比）
    comp = {"可答判对": 0, "可答判错": 0, "越界弃答": 0, "越界未弃答": 0}
    for p in pick:
        if p["answerable"]:
            comp["可答判对" if p["judge_correct"] else "可答判错"] += 1
        else:
            comp["越界弃答" if p["judge_correct"] else "越界未弃答"] += 1
    print("[+] 抽样 %d 条（裁判判对 %d / 判错 %d；构成 %s），已写待填表：\n    %s"
          % (len(pick), n_pos, len(pick) - n_pos,
             " ".join("%s=%d" % kv for kv in comp.items()), sheet))
    if n_pos == len(pick) or n_pos == 0:
        print("    注：这批裁判判定全同（分层不成）——真校准需 answers/scores 里判对判错都有（真跑数据）。")
    tail = "" if not args.out_dir else " --out-dir %s" % args.out_dir   # 自定义 out-dir 也带进续跑命令
    print("    用 Excel/编辑器打开，给 human_correct 列填 1（对/可接受）或 0（错）；填完跑："
          "python calibrate_matrix.py kappa --results-dir %s%s" % (args.results_dir, tail))
    _warn_self_preference(args.results_dir, pool, cfg)
    return 0


def _warn_self_preference(results_dir, pool, cfg=None):
    """裁判与生成器同家族 → 自我偏好嫌疑。裁判模型优先从 summary.json 读；summary 缺（infra 跳过的真跑会
    删掉过期 summary）时按**该目录实际跑的模式**（.run_meta 的 mode——config 写着 mock:true 也可能被
    --real 覆盖跑）推断：mock→mock，real→judge_model 或 run_matrix 默认 haiku。"""
    judge_model = None
    sp = os.path.join(results_dir, "summary.json")
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                judge_model = json.load(f).get("judge_model")
        except ValueError:
            pass
    if not judge_model and cfg:
        mode = None
        try:
            with open(os.path.join(results_dir, ".run_meta.json"), encoding="utf-8") as f:
                mode = json.load(f).get("mode")
        except (OSError, ValueError):
            pass                                        # 无/坏 meta → 退回 config 推断
        if mode not in ("mock", "real"):
            mode = "mock" if cfg.get("mock") else "real"
        judge_model = "mock" if mode == "mock" else (cfg.get("judge_model") or "haiku")
    if not judge_model:
        return
    jf = _model_family(judge_model)
    gen_families = {_model_family(p["model"]) for p in pool}
    if jf in gen_families and jf not in ("mock", "unknown"):
        print("    ⚠️ 跨家族提醒：裁判(%s，家族=%s) 与生成器家族 %s 重叠——有自我偏好嫌疑；"
              "建议用不同家族的裁判重判后再校准，或在报告里注明。"
              % (judge_model, jf, "/".join(sorted(gen_families))))


def _parse_label(hv):
    """人工标注单元格 → 1/0；容忍 Excel 数字化（'1.0'/'０'），其余（yes/2/x）返回 None 由上层 fail-loud。"""
    try:
        v = float(hv)
    except (TypeError, ValueError):
        return None
    return int(v) if v in (0.0, 1.0) else None


def cmd_kappa(args):
    out_dir = args.out_dir or os.path.join(args.results_dir, "calibration")
    sheet, keyp = _sheet_paths(out_dir)
    if not (os.path.isfile(sheet) and os.path.isfile(keyp)):
        _die("找不到 calibration_sheet.csv / .calibration_key.jsonl（先跑 sample）：%s" % out_dir)
    key = {d["ref_id"]: d["judge_correct"] for d in _load_jsonl(keyp)}
    with open(sheet, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    human, judge, disagree, blank, unmatched, invalid = [], [], [], 0, 0, []
    for r in rows:
        hv = (r.get("human_correct") or "").strip()
        if hv == "":
            blank += 1                                 # 真空单元格 = 还没填，可跳
            continue
        h = _parse_label(hv)
        if h is None:
            invalid.append((r.get("ref_id", "?"), hv))  # 填了但不是 0/1（yes/2/Excel 改写）——不能当空格静默丢
            continue
        ref = r["ref_id"]
        if ref not in key:
            unmatched += 1                             # 填了但 ref_id 对不上 key（表被改/串了）——别静默丢
            continue
        j = int(key[ref])
        human.append(h); judge.append(j)
        if h != j:
            disagree.append((ref, j, h, (r.get("question", "") or "")[:70]))
    if invalid:
        _die("有 %d 行 human_correct 不是 0/1（不能当没填静默丢，会让 kappa 虚高）：%s\n"
             "  只接受 1（对/可接受）或 0（错）；改好这些格再跑。"
             % (len(invalid), ", ".join("%s=%r" % (ref, v) for ref, v in invalid[:10])))
    n = len(human)
    if n == 0:
        _die("还没有已填的 human_correct（%d 行为空，%d 行 ref_id 对不上 key）。先在 %s 填好再跑。"
             % (blank, unmatched, sheet), 1)
    if unmatched:
        sys.stderr.write("calibrate_matrix: ⚠️ %d 行已填但 ref_id 对不上 .calibration_key.jsonl（表可能被改/换过）"
                         "——这些行未计入 kappa。\n" % unmatched)
    agree = sum(1 for h, j in zip(human, judge) if h == j) / n
    k = S.cohen_kappa(human, judge)
    degenerate = len(set(judge)) < 2 or len(set(human)) < 2   # 裁判(或你)判定全同 → kappa 退化，不是有效校准
    print("=== 人工 vs 裁判一致性（n=%d，未填 %d，未匹配 %d）===" % (n, blank, unmatched))
    print("  原始一致率 agreement = %.1f%%" % (agree * 100))
    if degenerate:
        print("  Cohen's kappa        = %.3f   ->  ⚠️ 退化：样本里裁判%s判定全同（%s），kappa 无意义——"
              "需要判对判错都有的分层样本（真跑数据）才是有效校准。"
              % (k, "" if len(set(judge)) < 2 else "或你", "非分层" if len(set(judge)) < 2 else "你全填成一类"))
    else:
        print("  Cohen's kappa        = %.3f   ->  %s"
              % (k, "可信(>=0.6)" if k >= 0.6 else "偏低，先改进裁判/题目再信任数字"))
    if disagree:
        print("\n  人机分歧 %d 条（judge=裁判判, human=你判；这些是裁判最可能错的地方）：" % len(disagree))
        for ref, j, h, q in disagree:
            print("    %s: judge=%d human=%d | %s" % (ref, j, h, q))
    else:
        print("\n  无分歧 —— 裁判与你完全一致。")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="通用判分校准（人工 vs 裁判 kappa，源自 run_matrix 输出）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sample", help="抽分层样本、生成待填校准表")
    sp.add_argument("--results-dir", required=True, help="run_matrix 的 results_dir")
    sp.add_argument("--config", required=True, help="对应的 config.json（读金标）")
    sp.add_argument("--n", type=int, default=30)
    sp.add_argument("--seed", type=int, default=7)
    sp.add_argument("--out-dir", default=None, help="待填表输出目录（默认 results_dir/calibration）")
    kp = sub.add_parser("kappa", help="读已填表算 Cohen's kappa")
    kp.add_argument("--results-dir", required=True)
    kp.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        return cmd_sample(args)
    return cmd_kappa(args)


if __name__ == "__main__":
    raise SystemExit(main())
