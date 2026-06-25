# 🎓 通用期末考试 1天极速备考智能教练 (Universal Exam Cram Coach Agent Skill)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Agent: Antigravity](https://img.shields.io/badge/Agent-Antigravity-orange.svg)](#)
[![Capability: Multi-Subject](https://img.shields.io/badge/Capability-Multi--Subject-brightgreen.svg)](#)

这是一个**全科通用**的期末考试极速备考 AI 智能体技能（Agent Skill）。

只要将本技能导入支持的智能体（如 VS Code 智能体插件、Cursor 或网页版 GPTs/Gemini），并提供你想要复习的科目资料（无论是高等数学、大学物理，还是历史、政治、解剖学或程序设计），智能体就会化身你的**私人专属备考教练**，带你在 1 天内突击通关。

---

## 🌟 技能特色功能

1. **自动逆向规划**：自动解析用户上传的复习大纲或重点题目，生成 `study_plan.md`（备考时间表）和 `study_progress.md`（实时掌握记录进度表）。
2. **启发式生活隐喻**：用最接地气的生活常识解释枯燥的概念。*（例如：用“媒婆相亲”解释化学催化剂，用“快递箱”解释计算机寄存器）*。
3. **强制关卡测试**：每个复习阶段结束后，智能体会自动进行出题测验，通关后方能进入下一阶段，绝不让模糊的概念蒙混过关。
4. **个性化易错查杀**：复习尾声自动提取错题记录，进行终极扫雷自测，并生成该科目的**“考前极简速记小抄”（Cheat Sheet）**。

---

## 📂 技能包目录结构

```text
universal-examprep-skill/
  ├── SKILL.md            # 技能定义核心文件（含全科通用辅导系统提示词及物理防幻觉协议）
  ├── README.md           # 本使用说明
  └── templates/          # 极速备考防幻觉记忆模板
        ├── study_plan_template.md        # 6阶段备考突击表模板
        ├── study_progress_template.md    # 知识点打卡自测追踪表模板
        ├── exam_questions_template.md    # 唯一重点题库锁定模板
        └── reference_answers_template.md # 考前标准答案锁定模板
```

---

## 🛠️ 如何导入并使用本技能

### 方式 1：导入本地 AI 编辑器/插件（如 VS Code、Cursor 等）
1. 下载或克隆本技能文件夹 `universal-examprep-skill` 到你的电脑。
2. 将其放入你的智能体自定义技能目录下（例如工作区根目录下的 `.agents/skills/` 文件夹中）。
3. 开启新对话，发送：“*启动备考教练，我的科目是【科目名称】，这是我的大纲文件...*”，即可开启学习。

### 方式 2：作为 Custom Instructions 导入网页端 AI（如 ChatGPT, Claude, Gemini Advanced）
1. 打开 `SKILL.md`，复制其全部内容，粘贴到 AI 的**系统指令（System Instructions / Custom Instructions）**中。
2. 把 `templates/` 下的模板文件和你的教材/大纲一起作为附件上传。
3. 对 AI 说：“*你好教练，这是我的复习大纲，请开始规划我的 1 天速通课程。*”

---

## 📝 开源协议
本项目基于 **MIT License** 协议开源。欢迎大家提交 Pull Request 贡献更多科目的复习模板或辅助脚本！

**祝所有临考冲刺的学生考神附体，高分通关！🎓🔥**
