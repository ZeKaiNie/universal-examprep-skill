# calibration/ —— 裁判可信度校准

LLM 裁判可能不准，所以**在相信任何裁判数字之前**，先让人工（你）标一小批，量一下"人工 vs 裁判"的一致性。
一致性够高，报告里的忠实度/幻觉率才站得住。

## 步骤

1. 真跑一次后，从 `results/raw.jsonl` 里**抽 30~50 条答案**。
2. 你（人工）逐条判一个二分标签，比如 `hallucinated`：1=含无依据/编造内容，0=没有。
3. 存成 `calibration/human_labels.csv`，至少两列：

   ```csv
   id,arm,human_hallucinated
   q1,baseline,0
   q1,skill,0
   q2,baseline,1
   ...
   ```

4. 算 Cohen's kappa（标准库即可）：

   ```python
   import csv, json, sys
   sys.path.insert(0, "..")        # 引入 benchmark/stats.py
   import stats

   human = {}
   with open("human_labels.csv", encoding="utf-8") as f:
       for r in csv.DictReader(f):
           human[(r["id"], r["arm"])] = int(r["human_hallucinated"])

   judge = {}
   with open("../results/raw.jsonl", encoding="utf-8") as f:
       for line in f:
           r = json.loads(line)
           judge[(r["id"], "baseline")] = r["baseline_score"]["hallucinated"]
           judge[(r["id"], "skill")]    = r["skill_score"]["hallucinated"]

   keys = [k for k in human if k in judge]
   h = [human[k] for k in keys]
   j = [judge[k] for k in keys]
   print("n =", len(keys), " Cohen's kappa =", round(stats.cohen_kappa(h, j), 3))
   ```

5. **判定**：kappa ≥ ~0.6 视为可接受（裁判与人工大体一致），再去信报告里的裁判类指标；
   偏低就改进裁判提示/题目，或换不同家族模型当裁判（如以后用 Codex/GPT）。
   标签分布很偏（绝大多数"未幻觉"）时，kappa 会偏低，建议同时看 Gwet's AC2（更稳）。
