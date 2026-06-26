# skill_workspace/ —— skill 臂的运行目录

这是**装了 skill 的那一组**（treatment）跑 `claude -p` 时的工作目录。它要处于
"skill 已激活 + 文件锁定知识库已建好"的状态，这样测的才是 skill 真正的防幻觉机制。

## 准备步骤（每门课跑测试前做一次）

1. **让 skill 在这个目录可用**：把技能放到本目录的 `.claude/skills/universal-exam-cram-coach/`
   （项目级），或确认它已装在 `~/.claude/skills/`（个人级，全局可用）。

2. **用 skill 把 `../materials/` 的资料建成知识库**：在本目录里用 Claude Code 跑 skill 的初始化
   （即 skill 第一步：解析大纲 → `ingest.py` 切片），产出：
   ```
   skill_workspace/
     references/
       wiki/ch1_*.md ...      # 分章知识切片
       quiz_bank.json          # 固定题库（答案在此被"锁定"）
     study_plan.md
     study_progress.md
   ```

3. **确认 `references/wiki/` 和 `references/quiz_bank.json` 已存在** —— skill 臂回答时就靠读它们
   （"读取而非现场重新推导"），这正是要被检验的那个机制。

> 注意：建知识库用的是 `../materials/` 里的原始资料，**不要**用 `items/items.jsonl` 的金标答案来建——
> 否则就成了"拿答案考自己"，测不出真实防幻觉效果。金标集要独立于 skill 的题库另外编写。
