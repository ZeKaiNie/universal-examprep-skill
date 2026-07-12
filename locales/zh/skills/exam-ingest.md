# exam-ingest — zh 学生侧文案包

> 本文件是学生可见文案的 zh 语言包；行为逻辑在 [skills/exam-ingest/SKILL.md](../../../skills/exam-ingest/SKILL.md)（控制层，单一事实源）。

## Student-facing Output
一句话回执（默认简体中文），例：
  `已初始化备考空间：3 章 wiki + 18 道题（含 2 道 ⚠️ AI生成答案，非老师/教材提供），进度已建。下一步开讲第 1 章。`
  然后交回 `exam-cram` 进入第二步授课。

依赖预检的一句话征询（材料含 PDF 且缺后端时，问一次）：

> 你的材料里有 PDF，读取它需要装一个解析库（一条命令：`pip install pymupdf`，约几秒）。现在装吗？不装的话 PDF 部分会跳过、只导入文本材料。

安装完成回执：

> 依赖已装好，开始建库——这一步只做一次，以后不会再问。
