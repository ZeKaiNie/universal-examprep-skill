---
name: exam-ingest
description: >
  从学生上传的课件/大纲/老师勾的重点/真题，一键初始化备考工作区——拼出 ingest.py 所需的
  raw_input.json，运行脚本切出分章节 LLM Wiki、标准题库与进度文件；无 Python 时无感降级为手动写盘。
  当工作区尚未建立、或用户刚提供资料需要冷启动时使用。
license: MIT
---

# exam-ingest — 工作区初始化

把零散的备考材料转成 `exam-cram` 依赖的固定工作区结构。**只负责建库，不负责教学/判分。**

## Activation
- 工作区缺失（无 `references/wiki/`、`references/quiz_bank.json` 或 `study_progress.md`）。
- 用户刚上传课件/大纲/重点/真题，或明确要求「初始化 / 建库 / 开始备考」。

## Inputs
- 学生上传的资料：文本、教材页图片、老师勾画重点、历年真题、课堂录音转写等。
- 目标工作区目录（默认当前工作区根）。

## Workflow
1. **解析**：读取资料，抽取知识点、核心公式、高频题型、名词解释；按章节/阶段分组。
2. **后台拼 JSON**：自动构建符合 `scripts/ingest.py` 的 `raw_input.json`（含 `course_name`、`phases[]`、`quiz_bank[]`），写入临时目录。**绝不要求用户手写或修改该 JSON。**
   - 每道题**必须带 `chapter`（或 `phase`）**，否则章节复习会抽不到它；并带 `source`：`teacher`（来自老师/真题）或 `ai_generated`（AI 补的）。
   - 题型用 6 类之一：`choice / subjective / diagram / fill_blank / true_false / code`。
3. **执行导入**：运行**技能包根目录**的 `scripts/ingest.py`：`python <技能包根>/scripts/ingest.py --input <临时json>`（整包安装到 Claude Code 时 `${CLAUDE_SKILL_DIR}` 指向包根，用 `python "${CLAUDE_SKILL_DIR}/scripts/ingest.py" --input <…>`）。
4. **无 Python 降级**：脚本失败（如 `python is not recognized`）时立即无感切换——用写盘工具按 `templates/` 手动建 `references/wiki/chN_*.md`、`references/quiz_bank.json`、`study_plan.md`、`study_progress.md`。
5. **来源标注**：wiki 段落区分 🟢来自资料 / 🟡AI 补充；老师没给答案而 AI 代答的题，答案标 ⚠️ AI 生成。

## Output format
- 标准工作区：`references/wiki/`、`references/quiz_bank.json`、`study_plan.md`、`study_progress.md`。
- 一句话回执：建了几章、几道题、各题型/来源占比；然后交回 `exam-cram` 进入第二步授课。

## Boundaries
- `scripts/ingest.py` 与 `templates/` 在**技能包根目录**、不在 `skills/exam-ingest/` 内。若把本子技能**单独**安装（`CLAUDE_SKILL_DIR` 仅指向 `skills/exam-ingest/`），脚本/模板将不可用——请安装**整个技能包**（含根 `scripts/`、`templates/`），或直接走第 4 步无 Python 降级手动建库。
- 不修改 `scripts/ingest.py` 的逻辑，只调用它。
- 文件名仅 `references/wiki/` 下安全名（脚本会拒绝 `../`/绝对路径/重复名）。
- 不臆造老师没提供的「标准答案」而不加 ⚠️ 标注；资料不足就如实说明缺口。
- 不覆盖已有 `study_progress.md`（脚本默认不清；`--force` 会先备份）。
