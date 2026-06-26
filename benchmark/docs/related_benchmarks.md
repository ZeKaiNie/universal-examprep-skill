# 权威幻觉 / 事实性基准综述（报告 Related-Work 草稿）

> 工作草稿，供 benchmark 报告引用。已逐项核对一手来源（arXiv / ACL·NeurIPS·AAAI / 官方 repo·leaderboard）。
> 排行榜实时数字会变（如 Vectara 头部模型 ~2–5%），引用时按"快照"对待；方法/数据描述较稳定。

**定位**：本 skill 的核心主张是**文件锁定、有据作答 + 越界弃答**。因此重点对标两类基准——
**有据性/忠实度（grounding/faithfulness）** 与 **弃答/校准（abstention/calibration）**；
纯闭卷事实性（TruthfulQA、FActScore 等）只作背景对照。

---

## 有据性 / 忠实度（最贴合）

- **FACTS Grounding（Google DeepMind, 2025）** — 最接近我们设定的公开基准：仅依据给定长文档作答，按"先合格性、再有据性"两阶段自动判分，并用多裁判（Gemini/GPT-4o/Claude）聚合降偏。1,719 例（860 公开/859 私有）。https://arxiv.org/abs/2501.03200 · https://www.kaggle.com/benchmarks/google/facts-grounding
- **Vectara HHEM / Hughes 幻觉评测榜** — "只依据原文做摘要"的事实一致性，幻觉率 = 100 − 一致性%。提供**开源分类器 HHEM-2.1-Open**，可直接拿来给我们的输出打分。https://github.com/vectara/hallucination-leaderboard
- **RAGAS faithfulness（EACL 2024 demo）** — 忠实度 =（答案中被上下文支持的论断数 ÷ 总论断数），无需参考答案，LLM 抽取+核验。基本就是我们核心主张的"操作化定义"。https://arxiv.org/abs/2309.15217
- **TRUE（NAACL 2022）** — 事实一致性**度量**的元基准（11 数据集），example-level 评测，结论：NLI / QA 类度量最强。用来论证我们为何选 NLI/蕴含来判"答案是否忠于材料"。https://arxiv.org/abs/2204.04991

## 弃答 / 校准

- **RGB（AAAI 2024）** — RAG 四能力：噪声鲁棒、**负向拒答(negative rejection)**、信息整合、反事实鲁棒。负向拒答 = 我们的"材料没有就说不知道"。中英双语。https://arxiv.org/abs/2309.01431
- **SimpleQA（OpenAI, 2024）** — 短答事实性 + **弃答/校准**：每答判 正确/错误/**未尝试**，并测置信度校准。借用其弃答/校准协议（非题目内容）。https://arxiv.org/abs/2411.04368
- **SelfCheckGPT（EMNLP 2023）** — 零资源、黑盒：多次采样的不一致性作为幻觉信号；可作为"何时该弃答"的部署级信号（不需 logprobs）。https://arxiv.org/abs/2303.08896

## 框架 / 方法（提供术语与方法论）

- **HalluLens（Meta, ACL 2025）** — 统一基准 + 清晰**分类法：intrinsic（不忠于输入上下文）vs extrinsic（不符世界知识）**；动态生成测试集以防数据污染。我们的目标可精确表述为"消除 intrinsic 幻觉、宁可弃答也不犯 extrinsic"。https://arxiv.org/abs/2504.17550
- **FActScore（EMNLP 2023）** — 长文本事实精度：拆**原子事实**逐条核对支持率。基准本身是闭卷，但**原子事实拆解法**可直接用于我们的有据判分。https://arxiv.org/abs/2305.14251
- **FELM（NeurIPS 2023）** — 评测"事实性**评判器**"本身的基准；若我们自建 LLM 裁判，它是"裁判有多可靠"的参考。https://arxiv.org/abs/2310.00741

## 闭卷事实性（背景对照，简述即可）

- **TruthfulQA（ACL 2022）** — 抗"模仿性谬误"（常见误解），817 题。最常被引的"真实性"基准，用于对照"闭卷真实性 vs 我们的有据忠实"。https://arxiv.org/abs/2109.07958
- **HaluEval / HaluEval 2.0（EMNLP 2023）** — 大规模幻觉识别基准；其知识对话/摘要分支属有据类，可引。https://arxiv.org/abs/2305.11747
- **FreshQA（Findings of ACL 2024）** — 时效性 + **错误前提(false-premise)**；时效性与我们无关，但 false-premise 弃答角度可窄引。https://arxiv.org/abs/2310.03214

---

## 建议引用（排序）

**Tier 1（核心，各对应我们主张的一块）**
1. **FACTS Grounding** — 文档内有据作答（最近、权威、公开集可复用）。
2. **Vectara HHEM** — 只据原文 + 现成可跑的幻觉率分类器。
3. **RAGAS faithfulness** — claim 级忠实度的操作化定义，可直接套用。
4. **RGB** — 弃答/负向拒答（事实性基准覆盖不到的一块）。

**Tier 2（方法/框架）**
5. **HalluLens** — intrinsic/extrinsic 分类法 + 2025 现状。
6. **TRUE** — 论证选用 NLI/蕴含度量。
7.（可选）**SimpleQA** — 弃答/校准协议。

**简述带过**：TruthfulQA、FActScore（兼提其原子事实法）、HaluEval、SelfCheckGPT、FELM、FreshQA。
