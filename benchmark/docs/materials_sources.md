# 测试数据来源 / Materials Sources

本 benchmark **不使用任何私有/学校课件**，统一采用**名校公开课**资料（3 门理工 + 3 门文科，覆盖算法、数学、物理、哲学、心理、历史）。
资料仅**下载到本地用于测试**，**不二次分发、不提交到本仓库**（见 `benchmark/.gitignore`），并在此与报告中**注明出处 + 超链接**。

| # | 课程 | 机构 | 学科 | 资料/金标来源 | 链接 |
| :- | :-- | :-- | :-- | :-- | :-- |
| 1 | 6.006 Introduction to Algorithms (Spring 2020) | MIT OpenCourseWare | 算法 Algorithms | 讲义 + 习题集/考题**官方解答**作金标 | https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/ |
| 2 | 18.06 / 18.06SC Linear Algebra (G. Strang) | MIT OpenCourseWare | 线性代数 Linear Algebra | 讲义 + 考题**官方解答** | https://ocw.mit.edu/courses/18-06sc-linear-algebra-fall-2011/ |
| 3 | 8.01SC Classical Mechanics (Fall 2016) | MIT OpenCourseWare | 物理·力学 Physics | 讲义 + 习题集（含解答） | https://ocw.mit.edu/courses/8-01sc-classical-mechanics-fall-2016/ |
| 4 | PHIL 176 Death (S. Kagan) | Open Yale Courses | 哲学 Philosophy | 讲义转录中的事实作金标（标注 supporting span） | https://oyc.yale.edu/death/phil-176 |
| 5 | PSYC 110 Introduction to Psychology (P. Bloom) | Open Yale Courses | 心理学 Psychology | 讲义转录中的事实作金标 | https://oyc.yale.edu/introduction-psychology/psyc-110 |
| 6 | HIST 116 The American Revolution (J. Freeman) | Open Yale Courses | 历史 History | 讲义转录中的事实作金标 | https://oyc.yale.edu/history/hist-116 |

## 金标（标准答案）怎么定，保证公正

- **理工三门**：题目与标准答案直接取自 MIT OCW 的**官方习题/考题解答**——权威、非我们自编。计算题走程序确定性判分。
- **文科三门**：从讲义转录里挑事实/定义类问题，标准答案就是转录中**明确陈述的那句话**（在金标里记下 `supporting_span` 原文出处），不是我们凭空写的。
- **盲测**：负责"答题"的生成器（各 Claude 模型）只看到题目与课程材料，**看不到标准答案**；答完后才用官方解答/转录原文判分——测的是真实水平，不是背答案。
- **越界探针**：每门课加几条"材料里压根没讲"的问题，看模型是否老实弃答（这最能体现 skill 的防幻觉）。

## 版权 / 使用许可

- **MIT OpenCourseWare**：CC BY-NC-SA（署名—非商业—相同方式共享）。
- **Open Yale Courses**：CC BY-NC-SA 3.0。
- 本项目为**非商业研究/评测**用途，遵循署名要求（本文件即署名），且**不在仓库内重新分发**原始材料。
