# 防幻觉 Benchmark 框架

给 `universal-exam-cram-coach` 这个 skill 做**真实数据背书**：用实测对比"装了 skill / 没装 skill"
两组在"基于你自己课件的有据问答"上的幻觉率、忠实度、弃答率等，替代 README 里"90% / 100%"那种宣传数字。

> 现状：**框架脚手架已就绪并可跑**（`--mock` 模式端到端验证过）。还差**真实数据**——
> 你需要按下面把课件/作业、金标题集准备好，再用 Claude Code 真跑。

---

## 设计（为什么这么测）

- **配对实验**：同一道金标题，分别过两臂——
  - **baseline**：普通 `claude -p`，只给原始材料，要求"材料没有就说不知道"。
  - **skill**：在 `skill_workspace/` 里跑 `claude -p`，skill 已激活、`references/wiki/` + `quiz_bank.json`
    文件锁定知识库已建好（即 skill"读取而非现场推导"的防幻觉机制）。
- **驱动方式**：直接调 **Claude Code 无头模式 `claude -p`**，用你登录的**订阅**身份，**不需要 API key**。
  （注意：**不要用 `--bare`**——它反而需要 API key，且会跳过 skill / CLAUDE.md 加载。）Codex 买了之后把
  生成器换成 `codex exec` 即可做跨 CLI 对比，其余不用动。

### 测什么指标（每项都对标了权威基准，见 `docs/related_benchmarks.md`）

| 指标 | 含义 | 对标基准 |
| :-- | :-- | :-- |
| 忠实度 faithfulness ↑ | 答案里"能被材料支持的原子论断"占比 | RAGAS faithfulness / FACTS Grounding |
| 幻觉率 hallucination ↓ | 含 ≥1 条无依据/与材料矛盾论断的题占比 | Vectara HHEM / HalluLens（intrinsic） |
| 计算题准确率 ↑ | 数值题确定性判分（不经 LLM） | —（脚本精确判） |
| 越界弃答率 ↑ | 材料没有的题，是否正确地弃答而非编造 | RGB negative-rejection / SimpleQA 弃答 |
| 正确率 correctness ↑ | 对金标的整体正确率 | TRUE（NLI 一致性） |

---

## 一步步怎么做

**前置**：Python 3.8+（标准库即可，无需 pip）、Claude Code 已登录（终端敲 `claude` 能用）。

0. **先空跑验证流程**（不花额度）：
   ```
   cd benchmark
   python run_benchmark.py --mock --items items/items.example.jsonl
   ```
   用浏览器打开 `results/report.html` 看看产物长什么样（**中英双语、带图表和指标出处引用**；数字是占位的，仅验证管线）。

1. **放材料**：把课件/作业丢进 `materials/<课程>/`，并生成 `materials/_combined.txt`（基线臂用）。见 `materials/README.md`。

2. **建 skill 知识库**：在 `skill_workspace/` 里用 skill 把材料切成 `references/wiki/` + `references/quiz_bank.json`。见 `skill_workspace/README.md`。

3. **编金标题集**：把 `items/items.example.jsonl` 复制为 `items/items.jsonl`，依据你的材料改写，**包含若干越界探针和计算题**。见 `items/README.md`。

4. **配置**：复制 `config.example.json` 为 `config.json`，按需改 `generator_model` / `judge_repeats` 等。

5. **真跑**：
   ```
   python run_benchmark.py --config config.json
   ```
   产出 `results/report.html`（**给用户看的中英双语可视化报告**：图表 + 每个指标超链接到对应权威基准 + 末尾 References）、`results/report.md`（数据版）、`results/raw.jsonl`（逐题原始答案+评分）。

6. **裁判可信度校准（出报告前必做）**：人工标注 30~50 题子集放进 `calibration/`，用 `stats.cohen_kappa`
   算"人工 vs LLM 裁判"的一致性。**kappa ≥ 0.6 左右才信任裁判的数字**；否则先改进裁判/题目再说。

7. **读报告、写结论**：按下面的诚实规则下结论。

---

## 诚实规则（这就是报告可信度的来源，也是给实习的加分项）

- **配对统计**：幻觉率用 **McNemar**（配对二分），差值给 **bootstrap 95% CI**（见 `stats.py`）。
- **显著性只在**：CI 下界 > 0 **且** McNemar p < 0.05 时才声称"显著"（`stats.significant`）。
- **样本小就别硬说**：n 小时按描述性 + 置信区间呈现，并明确说明统计功效有限——这恰恰是学术上站得住的写法。
- **裁判偏置**：裁判 ≠ 生成器。条件允许就用**不同家族**模型当裁判（如以后用 Codex/GPT）；只有 Claude 时，
  对裁判**隐藏**答案来自哪一臂、随机化顺序、用**逐条 span 锚定**的判定（已在 `judge.py` 里这么做），并记录裁判多次复评的自一致性。

---

## 文件结构

```
benchmark/
  run_benchmark.py     # 主运行器：两臂跑 claude -p（或 --mock）→ 判分 → 统计 → 报告
  judge.py             # 判分：数值题确定性 + 事实/定义题 claim 级忠实度（LLM 裁判）
  stats.py             # McNemar + 配对 bootstrap CI + Cohen's kappa（纯标准库）
  config.example.json  # 配置模板（复制为 config.json）
  items/               # 金标题集（items.example.jsonl + 编写规范）
  materials/           # 你的原始课件/作业（+ _combined.txt）
  skill_workspace/     # skill 臂的运行目录（references/wiki + quiz_bank.json）
  calibration/         # 人工标注子集（裁判校准用）
  results/             # 运行产物（raw.jsonl + report.md）
  tests/               # 脚本自测（python -m unittest discover -s tests）
  docs/related_benchmarks.md  # 权威幻觉基准综述（报告的 related work 草稿）
```

## 路线（后续）
- **v2 portfolio 升级**：买了 API key 后可移植到 **Inspect AI**（UK AISI，Task/Solver/Scorer + 模型判分），
  作品更"硬"；或用 **promptfoo** 的 `exec:` provider 直接包 `claude -p`（业界标准，自带 HTML 报告）。
- **平台化**：当项目接入 LlamaIndex（课件入库/RAG）+ OpenAI Agents SDK（编排）后，本 benchmark 的"被测系统"
  边界可直接换成平台版，金标集和统计完全复用——它就是平台的**忠实度回归门禁**。
