# -*- coding: utf-8 -*-
"""B5 数值抽取加固：judge.check_numeric / _extract_final_number 的边角覆盖。

旧实现（-?\\d+(?:\\.\\d+)?）会把 "1,000,000" 抓成最后一段 "000"、把 "1e6" 抓成 "6"、把 "10^6" 抓成 "6"，
数值题被静默判错。这里锁住修好后的行为。"""
import os
import sys
import unittest

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BENCH)
import judge as J  # noqa: E402


class ExtractFinalNumber(unittest.TestCase):
    def test_comma_grouped(self):
        self.assertEqual(J._extract_final_number("答案是 1,000,000 次操作"), 1000000.0)
        self.assertEqual(J._extract_final_number("12,345.67"), 12345.67)

    def test_scientific(self):
        self.assertEqual(J._extract_final_number("约 1e6"), 1000000.0)
        self.assertAlmostEqual(J._extract_final_number("1.5e-3 秒"), 0.0015)
        self.assertEqual(J._extract_final_number("结果 2E3"), 2000.0)

    def test_caret_power(self):
        self.assertEqual(J._extract_final_number("大约 10^6 次"), 1000000.0)
        self.assertEqual(J._extract_final_number("2 ^ 10"), 1024.0)

    def test_plain_decimal_negative(self):
        self.assertEqual(J._extract_final_number("3.14159"), 3.14159)
        self.assertEqual(J._extract_final_number("温度 -5 度"), -5.0)

    def test_takes_last_number(self):
        self.assertEqual(J._extract_final_number("第 2020 年，最终答案是 42"), 42.0)

    def test_percent_and_units(self):
        self.assertEqual(J._extract_final_number("准确率 50%"), 50.0)
        self.assertEqual(J._extract_final_number("$8 KB"), 8.0)

    def test_none_when_no_number(self):
        self.assertIsNone(J._extract_final_number("这里没有数字"))
        self.assertIsNone(J._extract_final_number(""))
        self.assertIsNone(J._extract_final_number(None))

    def test_huge_scientific_rejected_as_nonfinite(self):
        # 1e400 → inf → 拒绝（否则 abs(inf-inf)=nan 把精确匹配判错）
        self.assertIsNone(J._extract_final_number("1e400"))
        self.assertEqual(J.check_numeric("1e400", "1e400", 0), (False, None))

    def test_ambiguous_comma_rejected_not_fragment(self):
        # 欧式小数/乱逗号 → None（不再落片段 "3,14"→14 / "1,00"→0 造成静默误判）
        for s in ("3,14", "1,00", "12,3", "1,2,3"):
            self.assertIsNone(J._extract_final_number(s), s)
        # 关键：不再假阳（旧实现 "3,14" vs gold 14 会判对）
        self.assertEqual(J.check_numeric("答案约为 3,14", "14", 0), (False, None))
        self.assertEqual(J.check_numeric("元素有 1,2,3,4", "4", 0), (False, None))

    def test_symbolic_power_not_grabbed(self):
        # O(n^2) 的指数 2 不当答案；真数值在同句时取真数值
        self.assertEqual(J._extract_final_number("1,000,000 (即 O(n^2))"), 1000000.0)
        self.assertIsNone(J._extract_final_number("复杂度是 O(n^2)"))
        self.assertTrue(J.check_numeric("答案 1,000,000（即 O(n^2)）", "1000000", 0)[0])

    def test_comma_grouped_caret_base(self):
        self.assertEqual(J._extract_final_number("1,000^2"), 1000000.0)

    def test_ambiguous_final_token_no_fallback(self):
        # 末位 token 是歧义逗号 → None，绝不回退到前面的 42
        self.assertIsNone(J._extract_final_number("题号 42，答案是 3,14"))
        self.assertEqual(J.check_numeric("题号 42，答案是 3,14", "42", 0), (False, None))

    def test_comma_caret_base_rejected(self):
        # 1,00^2 的底数歧义 → 整个乘方作废（不算成 10000）
        self.assertIsNone(J._extract_final_number("1,00^2"))
        self.assertEqual(J.check_numeric("答案 1,00^2", "10000", 0), (False, None))

    def test_bad_final_power_no_fallback(self):
        # 末位是坏乘方 → None，不回退到前面的 42（单遍有序扫，末位无效即 None）
        self.assertIsNone(J._extract_final_number("题号 42，答案是 1,00^2"))
        self.assertEqual(J.check_numeric("题号 42，答案是 1,00^2", "42", 0), (False, None))

    def test_scientific_base_power(self):
        # 科学计数底数的乘方 1e6^2 = (1e6)^2 = 1e12（不再被拆成 1e36.0 落到 0）
        self.assertEqual(J._extract_final_number("1e6^2"), 1e12)

    def test_european_decimal_rejected(self):
        # 欧式千分位+小数 1.234,56（=1234.56）不是合法美式 → 歧义拒，绝不落尾段 "56"
        self.assertIsNone(J._extract_final_number("答案是 1.234,56"))
        self.assertEqual(J.check_numeric("答案是 1.234,56", "56", 0), (False, None))
        self.assertIsNone(J._extract_final_number("1.234.567,89"))

    def test_us_decimal_with_thousands_kept(self):
        # 合法美式千分位带小数 1,000.50 仍正常解析（改正则后的回归护栏）
        self.assertEqual(J._extract_final_number("总计 1,000.50 元"), 1000.50)
        self.assertEqual(J._extract_final_number("12,345.67"), 12345.67)
        self.assertTrue(J.check_numeric("是 1,000.50", "1000.5", 0)[0])

    def test_trailing_punctuation_not_eaten(self):
        # 尾随逗号/句点是标点不是小数分隔 → 42 照常，不被吞成歧义
        self.assertEqual(J._extract_final_number("答案是 42, 完毕"), 42.0)
        self.assertEqual(J._extract_final_number("答案是 42。"), 42.0)

    def test_leading_dot_decimal(self):
        # 无前导零小数 .05 / .32（APA/统计惯例，PSYC 110 常见）：整数部分省略也要抓成 0.05 而非落尾段 5
        self.assertAlmostEqual(J._extract_final_number("p = .05"), 0.05)
        self.assertAlmostEqual(J._extract_final_number("r = .32"), 0.32)
        self.assertAlmostEqual(J._extract_final_number("答案是 .5"), 0.5)
        self.assertAlmostEqual(J._extract_final_number("温差 -.25 度"), -0.25)
        # 关键：正确答案不再被 100x 错判
        self.assertTrue(J.check_numeric("相关系数 r = .32", "0.32", 0.001)[0])
        self.assertTrue(J.check_numeric("p = .05", "0.05", 0.001)[0])

    def test_numeric_base_symbolic_exp_skipped(self):
        # 2^n（数值底数、符号指数=复杂度记号）不是数值答案：真答案在前时取真答案，仅 2^n 时 None
        self.assertEqual(J._extract_final_number("1,000,000 (即 O(2^n))"), 1000000.0)
        self.assertIsNone(J._extract_final_number("复杂度是 O(2^n)"))
        self.assertIsNone(J._extract_final_number("指数级 2 ^ n"))
        self.assertTrue(J.check_numeric("答案 1,000,000（即 O(2^n)）", "1000000", 0)[0])


class HardeningRound2(unittest.TestCase):
    """对抗审计批次：连字符≠负号、空格千分位、分数、引用语境、小数指数、对冲弃答。"""

    def test_hyphen_is_not_minus(self):
        # 3-5 / 60-70 的 - 是连字符：第二个数不再变负
        self.assertEqual(J._extract_final_number("the answer is 3-5"), 5.0)
        self.assertEqual(J._extract_final_number("roughly 60-70 percent"), 70.0)
        self.assertEqual(J._extract_final_number("call 555-1234"), 1234.0)
        # 关键：'range 8-42' 不再撞对 gold=-42（旧行为 -42.0 假阳）
        self.assertEqual(J.check_numeric("range 8-42", "-42", 0.5), (False, 42.0))

    def test_iso_date_not_negative(self):
        # 已知局限：日期不是标量，末段取 1.0（但绝不再是 -1.0 去撞负数 gold）
        self.assertEqual(J._extract_final_number("2020-01-01"), 1.0)
        self.assertFalse(J.check_numeric("2020-01-01", "-1", 0)[0])

    def test_minus_after_cjk_still_sign(self):
        self.assertEqual(J._extract_final_number("答案是-5"), -5.0)
        self.assertEqual(J._extract_final_number("温度 -5 度"), -5.0)

    def test_space_grouped_thousands(self):
        # ISO 31-0 空格千分位（含 NBSP）：整段一个数，不再取末组
        self.assertEqual(J._extract_final_number("1 000 000"), 1000000.0)
        self.assertEqual(J._extract_final_number("1 234"), 1234.0)
        self.assertEqual(J._extract_final_number("1 000"), 1000.0)   # NBSP 分组
        self.assertEqual(J._extract_final_number("1 234.56"), 1234.56)
        # 关键：'1 000 survivors' 不再撞对 gold=0
        self.assertFalse(J.check_numeric("there were 1 000 survivors", "0", 0.5)[0])
        # 空格组 + 逗号小数（ISO 欧式 1 234,56）：空格分组已定界 → 逗号无歧义是小数点
        self.assertEqual(J._extract_final_number("1 234,56"), 1234.56)
        self.assertEqual(J._extract_final_number("总共 1 000,5 元"), 1000.5)
        # 左右边界：词粘连不并组、前缀不吞并
        self.assertEqual(J._extract_final_number("Q1 100 points"), 100.0)
        self.assertEqual(J._extract_final_number("January 1 2020"), 2020.0)

    def test_decimal_exponent(self):
        self.assertAlmostEqual(J._extract_final_number("2^2.5"), 2 ** 2.5)
        self.assertIsNone(J._extract_final_number("n^2.5"))   # 符号乘方连小数指数一起跳过

    def test_fractions(self):
        self.assertEqual(J._extract_final_number("答案是 3/4"), 0.75)
        self.assertEqual(J._extract_final_number("1/2"), 0.5)
        self.assertIsNone(J._extract_final_number("5/0"))     # 除零 → 作废不回退
        self.assertTrue(J.check_numeric("probability = 3/4", "0.75", 0.01)[0])
        # 关键：'the ratio 3/4' 不再撞对 gold=4（旧行为取分母 4.0 假阳）
        self.assertEqual(J.check_numeric("the ratio 3/4", "4", None), (False, 0.75))

    def test_citation_numbers_skipped(self):
        # 「见第 7 章 / per chapter 7 / p. 12」是引用不是答案
        self.assertEqual(J._extract_final_number("The answer is 42, per chapter 7"), 42.0)
        self.assertTrue(J.check_numeric("The answer is 42, per chapter 7", "42", 0)[0])
        self.assertFalse(J.check_numeric("The answer is 42, per chapter 7", "7", 0)[0])
        for s in ("see chapter 7", "见第 7 章", "第3讲", "see page 12", "p. 45"):
            self.assertIsNone(J._extract_final_number(s), s)
        # 数量（无「第」前缀）不是引用，照常当数
        self.assertEqual(J._extract_final_number("这本书共 7 章"), 7.0)
        # p = .05 不受 'p' 引用关键词影响（中间有 =）
        self.assertAlmostEqual(J._extract_final_number("p = .05"), 0.05)

    def test_hedged_numeric_commit_not_abstain(self):
        # 报了具体数字还挂「not sure」→ 不算弃答，瞎编照记 hallucinated
        item = {"id": "q", "question": "?", "gold_answer": "4",
                "answer_type": "numeric", "answerable": True, "tolerance": None}
        v = J.judge_answer(item, "The answer is 999, though I am not sure why", None)
        self.assertFalse(v["abstained"])
        self.assertEqual(v["hallucinated"], 1)
        # 真弃答（没报数）不受影响
        v2 = J.judge_answer(item, "材料中未涵盖，无法确定", None)
        self.assertTrue(v2["abstained"])
        self.assertEqual(v2["hallucinated"], 0)


class HardeningRound3(unittest.TestCase):
    """第二轮对抗验证批次：complex 乘方、引用区间/引用分数、逗号后负号、分数带乘方、顺带计数弃答豁免。"""

    def test_complex_pow_no_crash(self):
        # (-2)^0.5 是复数——不崩、拒 None（isfinite(complex) 会 TypeError）；
        # 无括号的 -2^0.5 = -(2^0.5)（一元负号后结合），是合法实数
        self.assertIsNone(J._extract_final_number("(-2)^0.5"))
        self.assertAlmostEqual(J._extract_final_number("-2^0.5"), -(2 ** 0.5))
        self.assertEqual(J.check_numeric("答案是 (-4)^0.5", "2", 0), (False, None))

    def test_citation_range_skipped(self):
        # page 12-13 / 第7-8章 的区间后半不再漏出来当答案
        self.assertEqual(J._extract_final_number("answer is 42, see page 12-13"), 42.0)
        self.assertEqual(J._extract_final_number("answer is 42 (pp. 12-13)"), 42.0)
        self.assertEqual(J._extract_final_number("答案是42，见第7-8章"), 42.0)

    def test_citation_fraction_skipped(self):
        # page 3/4 / slide 3/20 是引用不是分数答案
        self.assertEqual(J._extract_final_number("The answer is 42 (page 3/4)"), 42.0)
        self.assertIsNone(J._extract_final_number("see slide 3/20 for the answer"))

    def test_p_value_without_equals(self):
        # p .32 是 p 值不是页码（p/pp 引用**必须带点**：p. 45 才是页码）
        self.assertAlmostEqual(J._extract_final_number("significant, p .32"), 0.32)
        self.assertIsNone(J._extract_final_number("p. 45"))

    def test_sign_after_comma_kept(self):
        # (3,-2) 坐标 / roots 5,-3：逗号后的 - 仍是负号（连字符规则只挡字母数字后的 -）
        self.assertEqual(J._extract_final_number("坐标是 (3,-2)"), -2.0)
        self.assertEqual(J._extract_final_number("the roots are 5,-3"), -3.0)
        self.assertTrue(J.check_numeric("point (2,-7)", "-7", 0)[0])
        self.assertFalse(J.check_numeric("the roots are 5,-3", "3", 0)[0])   # 不再丢负号撞对 gold=3

    def test_fraction_with_power(self):
        # 1/2^10 = 1/(2^10)（^ 先于 / 结合）；2^3/4 = (2^3)/4——不再把裸指数/分母当答案
        self.assertAlmostEqual(J._extract_final_number("概率是 1/2^10"), 1.0 / 1024)
        self.assertEqual(J._extract_final_number("2^3/4"), 2.0)
        self.assertFalse(J.check_numeric("概率是 1/2^10", "10", 0)[0])

    def test_incidental_count_keeps_abstain(self):
        # 弃答里顺带提到材料规模（20 讲 / 1960 年代 / 20 lectures）→ 弃答豁免仍成立
        item = {"id": "q", "question": "?", "gold_answer": "42",
                "answer_type": "numeric", "answerable": True, "tolerance": None}
        for ans in ("材料中未涵盖该数值——我查了全部 20 讲都没提。",
                    "无法确定，材料只讨论了 1960 年代的研究",
                    "not covered in the material; I checked all 20 lectures"):
            v = J.judge_answer(item, ans, None)
            self.assertTrue(v["abstained"], ans)
            self.assertEqual(v["hallucinated"], 0, ans)
        # 真对冲（报了裸数字）仍翻转
        v = J.judge_answer(item, "The answer is 999, though I am not sure why", None)
        self.assertFalse(v["abstained"])
        self.assertEqual(v["hallucinated"], 1)


class HardeningRound4(unittest.TestCase):
    """第 5 轮评审批次：括号负底数乘方、equation (7) 引用、带单位猜测不豁免。"""

    def test_parenthesized_negative_base_power(self):
        # (-2)^2 是标准写法：不再落到裸指数 2
        self.assertEqual(J._extract_final_number("(-2)^2"), 4.0)
        self.assertEqual(J._extract_final_number("答案是 (-3)^2"), 9.0)
        self.assertTrue(J.check_numeric("the result is (-2)^2", "4", 0)[0])
        self.assertIsNone(J._extract_final_number("(-2)^0.5"))   # 复数仍拒

    def test_plural_citation_labels(self):
        # 复数引用（pages 12-13 / slides 3/20 / chapters 5-6）同样跳过；stages/messages 不误伤
        self.assertEqual(J._extract_final_number("answer is 42, see pages 12-13"), 42.0)
        self.assertIsNone(J._extract_final_number("see slides 3/20"))
        self.assertEqual(J._extract_final_number("answer is 42 (chapters 5-6)"), 42.0)
        self.assertTrue(J.check_numeric("answer is 42, see pages 12-13", "42", 0)[0])
        self.assertEqual(J._extract_final_number("there are 3 stages 5"), 5.0)   # stages 不是引用词

    def test_parenthesized_equation_citation(self):
        # Eq. (7) / equation (7)：括号形式的公式引用也跳过
        self.assertEqual(J._extract_final_number("answer is 42 by equation (7)"), 42.0)
        self.assertEqual(J._extract_final_number("answer is 42, see Eq. (7)"), 42.0)
        self.assertIsNone(J._extract_final_number("see equation (7)"))
        self.assertTrue(J.check_numeric("answer is 42 by equation (7)", "42", 0)[0])

    def test_unit_bearing_guess_not_exempt(self):
        # 带单位的**猜测**（不确定+报数=对冲）不豁免——只有材料范围陈述（前有提示词）才豁免
        item = {"id": "q", "question": "?", "gold_answer": "42",
                "answer_type": "numeric", "answerable": True, "tolerance": None}
        for ans in ("not sure, maybe 5 years", "无法确定，可能是 1960 年"):
            v = J.judge_answer(item, ans, None)
            self.assertFalse(v["abstained"], ans)
            self.assertEqual(v["hallucinated"], 1, ans)
        # 材料范围陈述仍豁免（有 材料/全部/查了/checked 等提示词）
        for ans in ("材料中未涵盖该数值——我查了全部 20 讲都没提。",
                    "无法确定，材料只讨论了 1960 年代的研究",
                    "not covered in the material; I checked all 20 lectures"):
            v = J.judge_answer(item, ans, None)
            self.assertTrue(v["abstained"], ans)
            self.assertEqual(v["hallucinated"], 0, ans)


class HardeningRound5(unittest.TestCase):
    """第 6 轮评审批次：一元负号后结合、解析失败的对冲数字、判定行配不上答案 fail-loud（后者在 calibrate 测）。"""

    def test_unary_minus_binds_after_power(self):
        # 数学约定：-2^2 = -(2^2) = -4；只有 (-2)^2 才是 4
        self.assertEqual(J._extract_final_number("-2^2"), -4.0)
        self.assertEqual(J._extract_final_number("(-2)^2"), 4.0)
        self.assertFalse(J.check_numeric("-2^2", "4", 0)[0])       # 不再把 -2^2 撞对 gold=4
        self.assertTrue(J.check_numeric("-2^2", "-4", 0)[0])
        self.assertTrue(J.check_numeric("结果是 -3^2", "-9", 0)[0])

    def test_unparseable_hedged_guess_still_commit(self):
        # 「可能是 3,14」「maybe 5/0」：报了个数字样的东西（判不出值）≠ 没作答——弃答豁免不成立
        item = {"id": "q", "question": "?", "gold_answer": "42",
                "answer_type": "numeric", "answerable": True, "tolerance": None}
        for ans in ("无法确定，可能是 3,14", "not sure, maybe 5/0"):
            v = J.judge_answer(item, ans, None)
            self.assertFalse(v["abstained"], ans)
            self.assertEqual(v["hallucinated"], 1, ans)
        # 纯文字弃答（无数字）与引用型弃答（第20讲被抽取器跳过）仍豁免
        for ans in ("材料中未涵盖，无法确定", "材料只讲到第 20 讲，未提及该数值"):
            v = J.judge_answer(item, ans, None)
            self.assertTrue(v["abstained"], ans)
            self.assertEqual(v["hallucinated"], 0, ans)


class HardeningRound6(unittest.TestCase):
    """第 7 轮评审批次：对冲检查覆盖**所有**数值单元（不只末位）。"""

    def test_mid_text_guess_with_trailing_count(self):
        # 中间的 999 是猜测，末位 20 lectures 是顺带计数——任一非豁免单元即算对冲
        item = {"id": "q", "question": "?", "gold_answer": "42",
                "answer_type": "numeric", "answerable": True, "tolerance": None}
        v = J.judge_answer(item, "not covered; maybe 999. I checked all 20 lectures", None)
        self.assertFalse(v["abstained"])
        self.assertEqual(v["hallucinated"], 1)
        v2 = J.judge_answer(item, "材料未涵盖——可能是 999。我查了全部 20 讲都没提。", None)
        self.assertFalse(v2["abstained"])
        self.assertEqual(v2["hallucinated"], 1)
        # 只有顺带计数（无猜测单元）仍豁免
        v3 = J.judge_answer(item, "not covered in the material; I checked all 20 lectures", None)
        self.assertTrue(v3["abstained"])
        self.assertEqual(v3["hallucinated"], 0)


class HardeningRound7(unittest.TestCase):
    """第 9 轮评审批次：系数科学计数 2×10^6、引用列表续接 pages 12, 13。（逗号列表 5,3 歧义拒——见注）"""

    def test_coefficient_scientific(self):
        # 2×10^6 / 2*10^6 / 1.5 x 10^-3 = 系数·10^指数（不再只取末位 10^…）
        self.assertEqual(J._extract_final_number("2×10^6"), 2e6)
        self.assertEqual(J._extract_final_number("2*10^6"), 2e6)
        self.assertAlmostEqual(J._extract_final_number("1.5 x 10^-3"), 0.0015)
        self.assertEqual(J._extract_final_number("约 1.2×10^3 次"), 1200.0)
        self.assertTrue(J.check_numeric("2×10^6", "2000000", 0)[0])

    def test_citation_list_continuation(self):
        # pages 12, 13 / pages 12 and 13 的后续页码同样跳过（区间已在前一轮覆盖）
        self.assertEqual(J._extract_final_number("answer is 42, see pages 12, 13"), 42.0)
        self.assertEqual(J._extract_final_number("answer is 42, see pages 12 and 13"), 42.0)
        self.assertIsNone(J._extract_final_number("see slides 3, 4, 5"))
        self.assertTrue(J.check_numeric("answer is 42, see pages 12, 13", "42", 0)[0])

    def test_ambiguous_comma_list_stays_rejected(self):
        # 设计决策（宁拒不猜，防 3,14 欧式假阳）：无空格逗号列表 5,3 / (3,4) → None，不猜末位元素。
        # 这与「越界弃答」同一诚实取向——标量数值题的金标不该是逗号元组；判不出比猜错安全。
        self.assertIsNone(J._extract_final_number("roots 5,3"))
        self.assertIsNone(J._extract_final_number("point (3,4)"))
        self.assertIsNone(J._extract_final_number("答案约为 3,14"))   # 欧式小数歧义仍拒（防假阳）


class ContainsGoldHardening(unittest.TestCase):
    """词法快路加固：ASCII 词边界 + 小句级否定。False 是安全方向（落回 LLM 裁判）。"""

    def test_ascii_word_boundary(self):
        self.assertFalse(J.contains_gold("microRAM", "RAM"))
        self.assertFalse(J.contains_gold("the DRAMatic case", "RAM"))
        self.assertTrue(J.contains_gold("The answer is RAM", "RAM"))
        self.assertTrue(J.contains_gold("Word-RAM。", "Word-RAM"))
        self.assertTrue(J.contains_gold("word - ram", "Word-RAM"))   # 标点邻空格归一保留

    def test_clause_negation(self):
        self.assertFalse(J.contains_gold("Word-RAM is not the answer", "Word-RAM"))
        self.assertFalse(J.contains_gold("这个模型并不是我们讨论的那种 Word-RAM", "Word-RAM"))
        self.assertFalse(J.contains_gold("不是 Word-RAM", "Word-RAM"))
        self.assertFalse(J.contains_gold("the answer word-ram is wrong", "word-ram"))
        # 否定在**别的小句**不误伤
        self.assertTrue(J.contains_gold("没有别的名字，就叫 Word-RAM", "Word-RAM"))
        # note/know 不触发 not/no（词边界）
        self.assertTrue(J.contains_gold("note that it is Word-RAM", "Word-RAM"))
        self.assertTrue(J.contains_gold("know it is Word-RAM", "Word-RAM"))

    def test_all_occurrences_checked(self):
        # 第一处被否定、第二处干净 → True（逐处检查，不只看第一处）
        self.assertTrue(J.contains_gold(
            "Some say it is not Word-RAM, but the correct model is Word-RAM.", "Word-RAM"))

    def test_single_char_neg_only_adjacent(self):
        # 「非常明显是 X」的「非」在 6 字窗里但不贴邻 → 不误伤；贴邻的「非/未」仍拒
        self.assertTrue(J.contains_gold("非常明显是 Word-RAM", "Word-RAM"))
        self.assertFalse(J.contains_gold("非 Word-RAM", "Word-RAM"))

    def test_cjk_gold_unaffected(self):
        self.assertTrue(J.contains_gold("短时记忆又称工作记忆", "工作记忆"))


class CheckNumeric(unittest.TestCase):
    def test_comma_gold_and_answer(self):
        ok, parsed = J.check_numeric("答案 1,000,000", "1000000", 0)
        self.assertTrue(ok)
        self.assertEqual(parsed, 1000000.0)
        self.assertTrue(J.check_numeric("是 1000000", "1,000,000", 0)[0])   # gold 带逗号

    def test_scientific_answer_matches_plain_gold(self):
        self.assertTrue(J.check_numeric("约为 1e6 次", "1000000", 0)[0])
        self.assertTrue(J.check_numeric("10^6", "1000000", 0)[0])

    def test_tolerance(self):
        self.assertTrue(J.check_numeric("约 3.14159", "3.14", 0.01)[0])
        self.assertFalse(J.check_numeric("3.20", "3.14", 0.01)[0])

    def test_wrong(self):
        self.assertFalse(J.check_numeric("我认为是 6", "4", 0)[0])

    def test_no_number_or_bad_gold(self):
        self.assertEqual(J.check_numeric("没有数字", "4", 0), (False, None))
        self.assertEqual(J.check_numeric("42", "abc", 0), (False, None))   # 坏 gold 不崩

    def test_negative_tolerance_treated_absolute(self):
        # 负 tolerance 取绝对值（run_matrix 已在 load 时拦，但 judge 层也要稳）
        self.assertTrue(J.check_numeric("5", "5", -1)[0])

    def test_old_bug_regression(self):
        # 旧实现会把这些判错——现在应判对
        self.assertTrue(J.check_numeric("最终 1,000,000", "1000000", 0)[0])
        self.assertTrue(J.check_numeric("答案 1e6", "1000000", 0)[0])


if __name__ == "__main__":
    unittest.main()
