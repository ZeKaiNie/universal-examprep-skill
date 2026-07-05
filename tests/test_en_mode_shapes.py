# -*- coding: utf-8 -*-
"""A8b T5 — English/双语 输出形态必须被**未修改的** behavior_smoke 探测器接受。

锚点不变性原则（docs/language-policy.md）：canonical 中文字面在任何语言模式下逐字节原样输出，
英文 gloss 只在 token 之后/下一行。本测试用规范形态合成 en/双语 transcript 片段，直接喂给
run_behavior_smoke 的探测器——若某正则不容忍 gloss，修**模板形态**，绝不改探测器。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "benchmark", "behavior_smoke"))
import run_behavior_smoke as B  # noqa: E402

# 规范 en 形态（与 skills/exam-tutor/SKILL.md 的 English rendering 块同款）
EN_SEVEN_STEP = """题目 [#q1] What is a linked list?

① 题面图 (Question figure):
本题无图，直接看题干条件。 (No figure — read the given conditions.)

② 这题在问什么 (What is being asked): the definition and memory layout of a linked list.

③ 图里要读的量 (What to read off the figure): n/a — read the two given conditions from the prompt.

④ 核心公式 (Core formula): node = (value, next-pointer); access cost O(n).

⑤ 逐步演算 (Step-by-step work): start from the head pointer, follow next three times, stop at NULL.

⑥ 答案自检 (Answer self-check): three nodes traversed, matches the given length.

⑦ 知识点溯源 (Source trace): 第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lec03.pdf 第 12 页](../lec03.pdf#page=12)

题目来源：lec03.pdf 第 12 页（lecture）｜答案来源：老师·教材提供｜🟢 来自资料
> EN: Question from lec03.pdf p.12 (lecture) | answer from the teacher/textbook | 🟢 from the materials
"""

# 双语组合规则真样例：逐块 zh 单元在前、`> EN:` 镜像随后；锚点只出现一次
BI_SEVEN_STEP = """题目 [#q1] 什么是链表？

① 题面图：
本题无图，直接看题干条件。
> EN: No figure — read the given conditions.

② 这题在问什么：链表的定义与内存布局。
> EN: The definition and memory layout of a linked list.

③ 图里要读的量：无图——从题干读两个给定条件。
> EN: n/a — read the two given conditions from the prompt.

④ 核心公式：节点 = (值, next 指针)；访问代价 O(n)。
> EN: node = (value, next pointer); access cost O(n).

⑤ 逐步演算：从头指针出发，沿 next 走三步，遇 NULL 停。
> EN: Start from the head pointer, follow next three times, stop at NULL.

⑥ 答案自检：遍历三个节点，与给定长度一致。
> EN: Three nodes traversed, matches the given length.

⑦ 知识点溯源：第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lec03.pdf 第 12 页](../lec03.pdf#page=12)
> EN: Chapter 2 Linear Lists · wiki ch02 · original page 12 of lec03.pdf

题目来源：lec03.pdf 第 12 页（lecture）｜答案来源：老师·教材提供｜🟢 来自资料
> EN: Question from lec03.pdf p.12 (lecture) | answer from the teacher/textbook | 🟢 from the materials
"""

EN_OVERRIDE = ("⚠️ 临时覆盖你的 homework-only 范围偏好 (temporarily overriding your homework-only "
               "scope preference): this round draws from lecture items instead.\n\n题目 [#mc_q1] Which…?")

# 标注形态不变式：标签必须**标注内容**（label：内容 或 内容（label…））；gloss 跟在完整 token 之后
EN_LABELS = ("🟢 来自资料：the stored answer says X (from the materials).\n"
             "Background note Y（🟡 AI补充，可能与你老师讲的不完全一致 — may differ from your teacher）.\n"
             "参考答案（⚠️ AI生成答案，非老师/教材提供 / AI-generated, not from teacher or textbook）：Z.")


class EnModeShapes(unittest.TestCase):
    def test_seven_step_detector_accepts_glossed_headings(self):
        self.assertTrue(B.teaching_template_ok(EN_SEVEN_STEP),
                        "en 形态（token+gloss）必须被未修改的七步探测器接受")

    def test_scope_override_detector_accepts_gloss(self):
        self.assertTrue(B.scope_override_declared(EN_OVERRIDE))

    def test_canonical_labels_survive_gloss(self):
        self.assertTrue(B.has_canonical_provenance_labels(EN_LABELS))

    def test_bilingual_composition_keeps_anchors(self):
        # 双语组合规则的**真**样例：逐块 zh 单元在前 + `> EN:` 镜像随后；锚点只出现一次
        bi = BI_SEVEN_STEP
        self.assertTrue(B.teaching_template_ok(bi))
        # 组合形态自检：每个编号块后都跟了 EN 镜像行（防"纯英文也算双语"）
        self.assertGreaterEqual(bi.count("> EN:"), 7)
        for tok in ("① 题面图", "② 这题在问什么", "⑦ 知识点溯源"):
            self.assertEqual(bi.count(tok), 1, tok)      # 锚点只出现一次（不双份模板）

    def test_pure_translation_would_fail(self):
        # 反向锁：把锚点译掉（无中文块标）必须被探测器拒绝——证明锚点不变性不是摆设
        translated = EN_SEVEN_STEP.replace("① 题面图", "① Question figure").replace(
            "② 这题在问什么", "② What is being asked")
        self.assertFalse(B.teaching_template_ok(translated))


if __name__ == "__main__":
    unittest.main()
