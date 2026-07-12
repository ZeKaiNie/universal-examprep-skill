# -*- coding: utf-8 -*-
"""PR T2 — Tier 2 behavioral smoke (deterministic, stdlib-only, no network/LLM/API key).

These tests exercise the behavior_smoke harness + detectors against the self-authored mini-course
fixture and mock outputs. They prove the DEFAULT path is CI-safe; the real-LLM smoke is opt-in only.
"""
import io
import os
import sys
import json
import re
import contextlib
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BSDIR = os.path.join(ROOT, "benchmark", "behavior_smoke")
if BSDIR not in sys.path:
    sys.path.insert(0, BSDIR)
import run_behavior_smoke as H  # noqa: E402

SIX_TYPES = {"choice", "subjective", "diagram", "fill_blank", "true_false", "code"}


def _bs(rel):
    return os.path.join(BSDIR, rel)


def _read(rel):
    with open(_bs(rel), encoding="utf-8") as f:
        return f.read()


def _silent(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(*a, **k)


class BehaviorSmokeTest(unittest.TestCase):
    # 0 — Codex 评审回归：live 沙箱契约面必须带 locales/（v4-P2 全量入口语言包），
    # 且不再引用已退役的 SKILL.en.md（复制循环会静默跳过不存在的路径 = 沙箱悄悄缺契约）
    def test_skill_contract_paths_ship_locales_not_retired_files(self):
        self.assertIn("locales", H._SKILL_CONTRACT_PATHS)
        self.assertNotIn("SKILL.en.md", H._SKILL_CONTRACT_PATHS)
        missing = [rel for rel in H._SKILL_CONTRACT_PATHS
                   if not os.path.exists(os.path.join(ROOT, rel))]
        self.assertEqual(missing, [],
                         f"_SKILL_CONTRACT_PATHS 引用了仓库里不存在的路径（复制时会被静默跳过）: {missing}")

    # 1
    def test_fixture_passes_validate_workspace(self):
        ok, errors, warnings, _ = H.validate_fixture_workspace(H.FIXTURE)
        self.assertTrue(ok, f"mini-course fixture 未通过校验: {[e['msg'] for e in errors]}")
        # the documented fixture must be 0-error AND 0-warning (a warning = a lost recommended field)
        self.assertEqual(warnings, [], f"fixture 不应有告警（会削弱 6 题型 smoke）: {[w['msg'] for w in warnings]}")

    # 2
    def test_fixture_quiz_bank_covers_all_six_types(self):
        bank = json.loads(_read("fixtures/mini_course/references/quiz_bank.json"))
        types = {q["type"] for q in bank}
        self.assertEqual(types, SIX_TYPES, f"题库未覆盖全部 6 种题型，实际: {sorted(types)}")

    # 3
    def test_scenario_spec_valid_and_references_exist(self):
        spec = H.load_scenarios()
        self.assertIn("scenarios", spec)
        self.assertTrue(os.path.isdir(_bs(spec["fixture"])), "scenarios.json 的 fixture 路径不存在")
        file_keys = (
            "mock_output", "mock_negative", "mock_negative_leak", "mock_negative_unlabeled",
            "mock_negative_prose", "mock_negative_after_prompt", "mock_negative_unsafe_path",
            "mock_negative_question_label_late", "mock_negative_missing_asset", "mock_negative_answer_text",
            "mock_negative_path", "progress_after", "transcript",
            "mock_liberal", "mock_ai_answer", "mock_negative_skip_ask", "mock_negative_formula_first",
            "mock_negative_no_source", "mock_negative_unlabeled_source", "mock_negative_missing_warn",
            "mock_negative_warn_title", "mock_negative_unsolicited_closers", "mock_optin_closers",
            "mock_negative_legacy", "mock_test", "mock_negative_recall_only",
        )
        for sc in spec["scenarios"]:
            for k in file_keys:
                if k in sc:
                    self.assertTrue(os.path.isfile(_bs(sc[k])), f"{sc['name']}.{k} 指向不存在的文件: {sc[k]}")
            if "fallback_workspace" in sc:
                self.assertTrue(os.path.isdir(_bs(sc["fallback_workspace"])), f"{sc['name']}.fallback_workspace 不存在")

    def test_b1_every_scenario_documented(self):
        # B1 覆盖矩阵收尾守卫——防止新增/改名场景后文档静默漂移、matrix 与实现脱节。
        # 守卫**从 scenarios.json 派生**（遍历 names），而非硬编码子集：新增一个场景却漏更 README
        # 或 coverage-matrix，下面任一断言都会红。
        names = [sc["name"] for sc in H.load_scenarios()["scenarios"]]
        readme = _read("README.md")
        matrix = open(os.path.join(ROOT, "benchmark", "docs", "coverage-matrix.md"), encoding="utf-8").read()
        for n in names:
            # README 是逐场景注册表（每个 scenario 一行）；coverage-matrix 也逐个按名登记（能力行的
            # mock 格里点名场景，或读者说明里点名 best-effort 场景）——两处都必须出现该场景名。
            self.assertIn("`%s`" % n, readme, "behavior_smoke/README.md 未登记场景 %s（新增场景须同步文档）" % n)
            self.assertIn("`%s`" % n, matrix, "coverage-matrix.md 未登记场景 %s（新增场景须同步 matrix）" % n)
        # matrix 点名的 Tier 4 长会话场景在 drift/ 下（非 behavior smoke），也必须真实存在且被点名
        drift_named = "mode_urgent_no_questions"
        self.assertTrue(
            os.path.isfile(os.path.join(ROOT, "benchmark", "drift", "scenarios", drift_named + ".json")),
            "coverage-matrix 点名的 Tier4 drift 场景 %s 不存在" % drift_named)
        self.assertIn("`%s`" % drift_named, matrix)

    # 4
    def test_quiz_output_only_uses_bank_ids(self):
        bank_ids = H.load_quiz_bank_ids(H.FIXTURE)
        self.assertTrue(H.assert_quiz_ids_in_bank(_read("mock/sample_outputs/quiz_output_good.txt"), bank_ids))

    # 5
    def test_detector_fails_on_invented_id(self):
        bank_ids = H.load_quiz_bank_ids(H.FIXTURE)
        self.assertFalse(
            H.assert_quiz_ids_in_bank(_read("mock/sample_outputs/quiz_output_invented.txt"), bank_ids),
            "探测器未能识别题库中不存在的 AI 即兴题号",
        )
        # an UNTAGGED invented question among tagged bank items must ALSO fail (no false confidence)
        self.assertFalse(
            H.assert_quiz_ids_in_bank("1. [#mc_q1] 合法题\n2. 这是没标号的 AI 编造题", bank_ids),
            "未标号的编造题应被判不合格（不能只看已标号的题）",
        )
        # a good output where EVERY numbered item is bank-tagged still passes
        self.assertTrue(H.assert_quiz_ids_in_bank("1. [#mc_q1] a\n2. [#mc_q2] b", bank_ids))
        # an invented tag on a NON-numbered (bullet) line must ALSO fail — scan all tags, any format
        self.assertFalse(H.assert_quiz_ids_in_bank("1. [#mc_q1] 合法\n- [#mc_q99] 项目符号编造", bank_ids),
                         "非编号行（项目符号）上的编造题号也应被抓")
        # an UNTAGGED bullet QUESTION (ends with ？) must fail...
        self.assertFalse(H.assert_quiz_ids_in_bank("1. [#mc_q1] 合法\n- 红黑树怎么删除？", bank_ids),
                         "未标号的项目符号问题（以？结尾）也应被抓")
        # ...but an instruction bullet (no ？) and option bullets (A./B.) must NOT be flagged as questions
        self.assertTrue(H.assert_quiz_ids_in_bank("1. [#mc_q1] 栈的顺序？\n- 请直接回复答案", bank_ids),
                        "非问题的指令项目符号不应被误判为未标号问题")
        self.assertTrue(H.assert_quiz_ids_in_bank("1. [#mc_q1] 栈的顺序？\n- A. LIFO\n- B. FIFO", bank_ids),
                        "选项行(A./B.)不应被误判为未标号问题")
        # a "Q." prefixed untagged question must be caught (not mis-classified as an option by A–Z)
        self.assertFalse(H.assert_quiz_ids_in_bank("1. [#mc_q1] 栈的顺序？\nQ. 红黑树怎么删除？", bank_ids),
                         "『Q.』开头的未标号问题不应被当成选项而漏检")

    def test_quiz_detector_content_and_chapter_scope(self):
        qmap = H.load_quiz_bank_map(H.FIXTURE)
        ch1 = {i: v["question"] for i, v in qmap.items() if str(v["chapter"]) == "1"}
        # a valid tag slapped on INVENTED content must fail the content check
        self.assertFalse(H.assert_quiz_ids_in_bank("1. [#mc_q1] 请证明红黑树删除算法的复杂度", ch1),
                         "把合法题号贴到编造题面上应被内容校验抓住")
        # a chapter-2 id used in a chapter-1 quiz must fail the scope check
        self.assertFalse(H.assert_quiz_ids_in_bank("1. [#mc_q4] 二叉树最多多少节点？", ch1),
                         "第1章测验里抽到第2章题号应被章节范围抓住")
        # the matching bank content within scope passes
        self.assertTrue(H.assert_quiz_ids_in_bank("1. [#mc_q1] " + qmap["mc_q1"]["question"], ch1))
        # a TAGGED BULLET with invented content must be content-checked too (not skipped)
        self.assertFalse(H.assert_quiz_ids_in_bank("- [#mc_q1] 请证明红黑树删除算法", ch1),
                         "项目符号格式的『合法题号 + 编造题面』也应被内容校验抓住")
        # tag on its OWN line + invented content on the next line must fail (no vacuous empty-text match)
        self.assertFalse(H.assert_quiz_ids_in_bank("[#mc_q1]\n请证明红黑树删除算法的复杂度。", ch1),
                         "题号单独一行、下一行是编造题面，也应被内容校验抓住")
        # SWAPPING tag↔content across items must fail (mc_q1's tag on mc_q2's text and vice versa)
        swapped = ("1. [#mc_q1] " + qmap["mc_q2"]["question"] + "\n"
                   "2. [#mc_q2] " + qmap["mc_q1"]["question"])
        self.assertFalse(H.assert_quiz_ids_in_bank(swapped, ch1),
                         "题号与题面错配（每题题面对应别的题号）应被分段内容校验抓住")
        # bank prefix + appended invented tail (END differs) must fail the both-ends content check
        self.assertFalse(H.assert_quiz_ids_in_bank("[#mc_q1] 栈（stack）的存取顺序是请证明红黑树删除算法的复杂度", ch1),
                         "题面前缀对、结尾被编造替换，应被首尾内容校验抓住")
        # a prefix-collision different question (shares prefix, different end) must fail
        self.assertFalse(H.assert_quiz_ids_in_bank("[#mc_q1] 栈（stack）的时间复杂度是多少？", ch1),
                         "共享前缀但结尾不同的题应被首尾内容校验抓住")
        # a MIDDLE paraphrase (both ends intact, drops 「（queue）」) still passes
        self.assertTrue(H.assert_quiz_ids_in_bank("1. [#mc_q2] 用一句话说明队列与栈在存取顺序上的区别。", ch1),
                        "中段改写（首尾仍在）应通过")

    def test_quiz_size_requirement(self):
        spec = H.load_scenarios()
        quiz = next(s for s in spec["scenarios"] if s["name"] == "quiz_bank_only")
        self.assertGreaterEqual(quiz.get("min_questions", 1), 3, "quiz_bank_only 场景应要求 ≥3 题")
        good = _read("mock/sample_outputs/quiz_output_good.txt")
        self.assertGreaterEqual(len(set(H.extract_question_ids(good))), quiz["min_questions"],
                                "good mock 应满足请求的题量")
        self.assertLess(len(set(H.extract_question_ids("1. [#mc_q1] 栈的顺序？"))), quiz["min_questions"],
                        "只出 1 题不应满足请求的 3 题")

    # 6
    def test_provenance_detector_recognizes_all_canonical_labels(self):
        text = _read("mock/sample_outputs/provenance_answer.txt")
        self.assertTrue(H.has_canonical_provenance_labels(text))
        # must require ALL three: dropping any one canonical label makes it fail
        for lbl in H.CANON_LABELS:
            self.assertFalse(H.has_canonical_provenance_labels(text.replace(lbl, "")),
                             f"缺少标注「{lbl}」时仍判通过，说明未检查全部 canonical 标注")
        # a mere LEGEND listing the labels (no labelled answer content) must NOT pass
        legend = "可用标签：🟢 来自资料 / 🟡 AI补充，可能与你老师讲的不完全一致 / ⚠️ AI生成答案，非老师/教材提供\n答案是栈。"
        self.assertFalse(H.has_canonical_provenance_labels(legend),
                         "只罗列标签图例、答案却不带标注，不应判通过")
        # labels used AFTER content (skill style: 结论……（🟢 来自资料）) must pass
        suffix = ("栈是 LIFO（🟢 来自资料）。红黑树较复杂（🟡 AI补充，可能与你老师讲的不完全一致）。"
                  "以下为伪代码（⚠️ AI生成答案，非老师/教材提供）。")
        self.assertTrue(H.has_canonical_provenance_labels(suffix), "标签放在内容之后（括注）也应判通过")
        # a MULTI-LINE legend (labels each on their own line, answer unlabelled) must also fail
        ml_legend = ("标签说明：\n🟢 来自资料\n🟡 AI补充，可能与你老师讲的不完全一致\n"
                     "⚠️ AI生成答案，非老师/教材提供\n答案：栈是 LIFO。")
        self.assertFalse(H.has_canonical_provenance_labels(ml_legend),
                         "多行图例（标签各自成行、答案不带标注）也不应判通过")
        # a multi-line legend where each label ends with a colon but content is on the NEXT line must fail
        ml_colon = ("🟢 来自资料：\n🟡 AI补充，可能与你老师讲的不完全一致：\n"
                    "⚠️ AI生成答案，非老师/教材提供：\n答案：栈是 LIFO。")
        self.assertFalse(H.has_canonical_provenance_labels(ml_colon),
                         "标签后只有冒号、内容却在下一行（图例式）也不应判通过")
        # consecutive PARENTHESIZED labels (a legend), answer below unlabelled, must also fail
        paren = ("标签说明（🟢 来自资料）（🟡 AI补充，可能与你老师讲的不完全一致）"
                 "（⚠️ AI生成答案，非老师/教材提供）\n答案：栈是 LIFO。")
        self.assertFalse(H.has_canonical_provenance_labels(paren),
                         "连续括注标签图例（答案不带标注）也不应判通过")

    # 7
    def test_zero_basic_detector_recognizes_sections(self):
        self.assertTrue(H.has_zero_basic_sections(_read("mock/sample_outputs/zero_basic_explain.txt")))
        self.assertFalse(H.has_zero_basic_sections("## 考点拆解\n只有一个小节"), "缺少其余小节时不应判通过")
        # a one-line checklist that merely NAMES the sections (no real headings) must not pass
        self.assertFalse(H.has_zero_basic_sections("请包含：考点拆解、标准答题步骤、易错点、3分钟速记"),
                         "仅罗列小节名（无实际小节标题）不应判通过")
        # ordered-list headings (1. 考点拆解 / 2. 标准答题步骤 …) are valid section headings
        ordered = "1. 考点拆解\n讲解\n2. 标准答题步骤\n步骤\n3. 易错点\n注意\n4. 3分钟速记\n口诀"
        self.assertTrue(H.has_zero_basic_sections(ordered), "有序列表小节标题(1. 考点拆解)也应判通过")
        # the skill's documented bracket block format 【考点拆解】 must be accepted as headings
        bracket = "【考点拆解】讲解\n【标准答题步骤】步骤\n【易错点】注意\n【3分钟速记】口诀"
        self.assertTrue(H.has_zero_basic_sections(bracket), "【…】文档块格式的小节标题也应判通过")
        # but 【…】 names crammed inline into one checklist line (not line-start headings) must NOT pass
        self.assertFalse(H.has_zero_basic_sections("请包含：【考点拆解】【标准答题步骤】【易错点】【3分钟速记】"),
                         "把【…】块名塞进一句话清单（非行首标题）不应判通过")
        # four headings with NO body text under any of them must not pass
        self.assertFalse(H.has_zero_basic_sections("## 考点拆解\n## 标准答题步骤\n## 易错点\n## 3分钟速记"),
                         "只有空小节标题、无正文不应判通过")
        # A5 起：七步模板输出（② 这题在问什么 / ⑤ 逐步演算）也满足零基础结构要求；
        # 易错点/3分钟速记 是可选收尾块，缺席不影响判定
        seven = _read("mock/sample_outputs/teaching_template_good.txt")
        self.assertTrue(H.has_zero_basic_sections(seven), "A5 七步模板输出应满足零基础精讲的结构要求")
        two_core = "## 考点拆解\n讲解\n## 标准答题步骤\n步骤"
        self.assertTrue(H.has_zero_basic_sections(two_core), "只有两大核心小节（无收尾块）也应判通过")

    def test_visual_first_asset_detector(self):
        self.assertTrue(H.visual_first_asset_display_ok(_read("mock/sample_outputs/visual_first_good.txt")))
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_answer_side_first.txt")),
            "答案侧 asset 抢在题面图前面时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_answer_before_prompt.txt")),
            "题面图后、题目/作答前泄露答案侧 asset 时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_unlabeled_solution_before_prompt.txt")),
            "题目前出现未标注的答案侧 Markdown 图片时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![题面图 / question-side asset](references/assets/venn_prompt.png)\n"
            "![worked solution](references/assets/venn_solution.png)\n\n题目：看图作答"),
            "题面图后、题目前出现未标注答案图时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_prose_before_image.txt")),
            "题面图前已有答案/讲解正文时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_answer_after_prompt.txt")),
            "题目行后泄露答案侧 asset 时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_question_label_late_asset.txt")),
            "问题行后才出现第二张题面图时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_answer_text_before_prompt.txt")),
            "题面图后、题目前泄露答案正文时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![题面图 / question-side asset](references/assets/venn_prompt.svg)\n\n"
            "题目：看图作答\n![题面图 / question-side asset](references/assets/late_prompt.svg)"),
            "题目行后才出现第二张题面图时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_unsafe_url.txt")),
            "URL 图片不能满足本地题库 asset 展示契约")
        self.assertFalse(H.visual_first_asset_display_ok(
            _read("mock/sample_outputs/visual_first_missing_asset.txt")),
            "缺失的本地 asset 文件不能满足题面图展示契约")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![题面图 / question-side asset](../outside.png)\n题目：看图作答"),
            "路径穿越不能满足本地题库 asset 展示契约")
        self.assertFalse(H.visual_first_asset_display_ok(_read("mock/sample_outputs/visual_first_path_only.txt")),
                         "只打印路径、没有 Markdown 图片渲染时应不合格")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![题面图 / question-side asset](%s)\n题目：看图作答" % ("/" + "D:/bad/path.png")),
            "slash-prefixed Windows drive-letter Markdown path must be rejected")
        for bad_drive_path in ("/C:/bad/path.png", "/d:/bad/path.png", "\\E:\\bad\\path.png"):
            self.assertFalse(H.visual_first_asset_display_ok(
                "![题面图 / question-side asset](references/assets/venn_prompt.svg)\n"
                "题目：看图作答\n%s" % bad_drive_path),
                "all slash-prefixed Windows drive-letter paths must be rejected")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![答案图 / answer-side asset; 题面图 / question-side asset](references/assets/venn_prompt.svg)\n"
            "题目：看图作答"),
            "mixed answer-side and question-side image labels must be rejected before the prompt")
        with mock.patch.object(H.os, "access", return_value=False):
            self.assertFalse(H.visual_first_asset_display_ok(
                _read("mock/sample_outputs/visual_first_good.txt")),
                "existing but unreadable fixture assets must fail closed")
        self.assertFalse(H.visual_first_asset_display_ok(
            "![题面图 / question-side asset](references/assets/venn_prompt.svg)"),
            "showing a visual asset without asking/teaching/hinting/solving must not pass")

    def test_visual_first_detector_matches_fixture_assets_and_review_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets_dir = os.path.join(tmp, "references", "assets")
            os.makedirs(assets_dir)
            for name in ("prompt_a.svg", "prompt_b.svg", "solution.svg"):
                with open(os.path.join(assets_dir, name), "w", encoding="utf-8") as f:
                    f.write("<svg xmlns=\"http://www.w3.org/2000/svg\"></svg>\n")
            bank = [{
                "id": "vis_multi",
                "requires_assets": True,
                "assets": [
                    {"role": "figure", "path": "references/assets/prompt_a.svg"},
                    {"role": "table", "path": "references/assets/prompt_b.svg"},
                    {"role": "worked_solution", "path": "references/assets/solution.svg"},
                ],
            }]
            with open(os.path.join(tmp, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
                json.dump(bank, f)

            prompt_assets = (
                "![题面图 / question-side asset](references/assets/prompt_a.svg)\n"
                "![题面图 / question-side asset](references/assets/prompt_b.svg)\n"
            )
            question = "\n题目 [#vis_multi]：看图作答"
            self.assertTrue(H.visual_first_asset_display_ok(prompt_assets + question, fixture_path=tmp))
            self.assertFalse(H.visual_first_asset_display_ok(
                "![题面图 / question-side asset](references/assets/prompt_a.svg)\n" + question,
                fixture_path=tmp),
                "all fixture question-side assets must be displayed before the prompt")
            self.assertFalse(H.visual_first_asset_display_ok(
                prompt_assets + "\n题目 [#vis_multi]：看图作答\n"
                "![答案图 / answer-side asset: worked solution](references/assets/solution.svg)",
                fixture_path=tmp),
                "answer-side assets must not appear immediately after the question prompt")
            self.assertTrue(H.visual_first_asset_display_ok(
                prompt_assets + "\n题目 [#vis_multi]：看图作答\n\n解析：如下。\n"
                "![答案图 / answer-side asset: worked solution](references/assets/solution.svg)",
                fixture_path=tmp),
                "answer-side assets are allowed during solution/review after the prompt")

    def test_visual_first_good_sample_matches_fixture_item(self):
        sample = _read("mock/sample_outputs/visual_first_good.txt")
        item_id = re.search(r"\[#([^\]]+)\]", sample).group(1)
        image_paths = re.findall(r"!\[[^\]]*题面图 / question-side asset[^\]]*\]\(([^)]+)\)", sample)
        self.assertTrue(image_paths)

        bank = json.loads(_read("fixtures/mini_course/references/quiz_bank.json"))
        item = next((q for q in bank if q["id"] == item_id), None)
        self.assertIsNotNone(item, "visual-first good sample must use a real fixture bank id")
        self.assertTrue(item.get("requires_assets") or item.get("maybe_requires_assets"))
        question_asset_paths = {
            a["path"] for a in item.get("assets", [])
            if a.get("role") in {"question_context", "figure", "diagram", "table"}
        }
        self.assertTrue(set(image_paths).issubset(question_asset_paths))
        for path in image_paths:
            self.assertTrue(os.path.isfile(_bs(os.path.join("fixtures/mini_course", path))))

    # 8
    def test_hint_skip_detector_recognizes_recovery_offer(self):
        self.assertTrue(H.has_hint_skip_offer(_read("mock/sample_outputs/hint_skip_offer.txt")))
        self.assertFalse(H.has_hint_skip_offer("继续加油，你能答对的。"), "无逃生通道时不应判通过")
        # an output that explicitly DENIES the escape hatch must not pass on keyword presence alone
        self.assertFalse(H.has_hint_skip_offer("没有提示，不能跳过，也不会归档到错题本"),
                         "明确否定『提示/跳过/归档』的文案应判不合格")
        # negation with intervening words must also be caught
        self.assertFalse(H.has_hint_skip_offer("可以提示、可以跳过，但不会把它归档到错题本"),
                         "中间夹词的否定（『不会把它归档』）也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以给提示，也可以跳过，但不会写入错题本"),
                         "『不会写入错题本』的归档否定也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以提示、跳过，但不会把这道题自动记录进错题档案"),
                         "夹词较长的归档否定（…记录进错题档案）也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以给提示，也可以跳过；错题本暂不记录此题。"),
                         "名词后否定（错题本暂不记录）也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以提示、可以跳过，但不归档到错题本"),
                         "裸『不归档』否定也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以给你一个提示，但不可以跳过；错题本会保留当前进度。"),
                         "『不可以跳过』否定也应判不合格")
        self.assertFalse(H.has_hint_skip_offer("可以提示、可以跳过，但不把这道题自动记录进错题档案"),
                         "『不把…记录进错题档案』否定也应判不合格")

    # 9
    def test_mistake_archive_detector(self):
        self.assertTrue(H.progress_has_mistake_archive(_read("mock/sample_outputs/progress_after_mistake.md")))
        # base fixture progress has an empty 错题档案 -> no archived row
        self.assertFalse(H.progress_has_mistake_archive(_read("fixtures/mini_course/study_progress.md")))
        # accept BOTH the standard template header (错题档案) and legacy mini wording (错题本)
        std = "## ❌ 错题档案记录\n| ID | 章节 | 状态 |\n| --- | --- | --- |\n| mc_q1 | 1 | 已归档 |"
        legacy = "## 错题本\n| 题号 | 状态 |\n| --- | --- |\n| mc_q2 | 已归档 |"
        self.assertTrue(H.progress_has_mistake_archive(std), "应识别标准模板表头『错题档案记录』")
        self.assertTrue(H.progress_has_mistake_archive(legacy), "应兼容旧表头『错题本』")
        # an empty-state placeholder rendered AS a table row must NOT count as an archived mistake
        empty = "## ❌ 错题档案记录\n| 错题ID | 章节 | 状态 |\n| --- | --- | --- |\n| 暂无错题 | - | - |"
        self.assertFalse(H.progress_has_mistake_archive(empty), "空状态占位行不应被当成已归档错题")
        # scenario-specific: the archived row must mention the SIMULATED wrong item, not just any row
        m = _read("mock/sample_outputs/progress_after_mistake.md")
        self.assertTrue(H.progress_has_mistake_archive(m, expect="mc_q2"))
        self.assertFalse(H.progress_has_mistake_archive(m, expect="mc_q1"),
                         "归档了错误的题（非本场景模拟的 mc_q2）不应判通过")
        # exact ID match: a row about mc_q20 must NOT satisfy expect=mc_q2 (prefix collision)
        m20 = "## ❌ 错题档案记录\n| 错题ID | 章节 | 状态 |\n| --- | --- | --- |\n| mc_q20 | 2 | 已归档 |"
        self.assertFalse(H.progress_has_mistake_archive(m20, expect="mc_q2"),
                         "mc_q20 的行不应满足 expect=mc_q2（前缀相同不算命中）")
        # a row marked 未归档 (not actually archived) must NOT count even if the ID matches
        notarch = "## ❌ 错题档案记录\n| 错题ID | 章节 | 状态 |\n| --- | --- | --- |\n| mc_q2 | 1 | 未归档 |"
        self.assertFalse(H.progress_has_mistake_archive(notarch, expect="mc_q2"),
                         "状态为『未归档』的行不应算作已归档错题")

    # 10
    def test_confusion_tracker_detector(self):
        self.assertTrue(H.progress_has_confusion_row(_read("mock/sample_outputs/progress_after_confusion.md")))
        self.assertFalse(H.progress_has_confusion_row(_read("fixtures/mini_course/study_progress.md")))

    # 11
    def test_checkpoint_recovery_reads_current_phase(self):
        phase = H.progress_current_phase(_read("fixtures/mini_course/study_progress.md"))
        self.assertEqual(phase, 2, "断点恢复探测器未能从进度读到当前阶段 2")
        # completed phases listed BEFORE the current marker must not be misread as the current phase
        reordered = "## 当前复习断点\n- 已完成：阶段 1\n- 当前进行阶段：阶段 2"
        self.assertEqual(H.progress_current_phase(reordered), 2,
                         "已完成阶段排在当前标记之前时，仍应读出当前阶段 2（而非 1）")

    # 11b — resume must point at the current phase, not restart at phase 1 (direct +/- coverage)
    def test_checkpoint_resume_refers_to_current_phase(self):
        self.assertTrue(H.resume_refers_to_phase(_read("mock/sample_outputs/resume_message.txt"), 2))
        # mentions 阶段 2 but STILL restarts at 阶段 1 → must be rejected (the exact gap Codex flagged)
        self.assertFalse(H.resume_refers_to_phase("当前在阶段 2，但先从阶段 1 重新开始", 2),
                         "虽提到阶段 2 但仍从阶段 1 重启，应判不合格")
        self.assertFalse(H.resume_refers_to_phase("从头开始复习，先看阶段 2 的目录", 2),
                         "『从头开始』的续跑文案应判不合格")
        self.assertFalse(H.resume_refers_to_phase("当前在阶段 2，但先从阶段1开始", 2),
                         "紧凑写法『从阶段1开始』（无空格）也应判重启不合格")
        # spacing / word-order variants of the CURRENT phase still count as a correct resume
        self.assertTrue(H.resume_refers_to_phase("当前在阶段2：二叉树，我们继续", 2),
                        "紧凑『阶段2』（无空格）应判为指向当前阶段")
        self.assertTrue(H.resume_refers_to_phase("从第2阶段接着复习", 2),
                        "『第2阶段』写法应判为指向当前阶段")
        # negating the current phase must be rejected even though 阶段2 is mentioned
        self.assertFalse(H.resume_refers_to_phase("你现在不是阶段2，而是阶段1。", 2),
                         "否定当前阶段（『不是阶段2』）应判不合格")
        self.assertFalse(H.resume_refers_to_phase("你现在不是第2阶段，而是第1阶段。", 2),
                         "否定『第2阶段』形式也应判不合格")
        self.assertFalse(H.resume_refers_to_phase("当前在阶段2，但先从第1阶段开始", 2),
                         "『从第1阶段开始』重启也应判不合格")
        self.assertFalse(H.resume_refers_to_phase("当前在阶段 3，但先从阶段 2 开始", 3),
                         "从非当前阶段（阶段2，当前是3）重启也应判不合格")
        self.assertFalse(H.resume_refers_to_phase("当前在阶段3，但先从第2阶段开始", 3),
                         "『从第2阶段开始』（第N语序、非当前阶段）重启也应判不合格")

    # 12
    def test_no_python_fallback_workspace_is_complete(self):
        # the mini-course is HAND-AUTHORED (not produced by ingest.py) — i.e. exactly the shape the
        # agent writes by hand when Python is unavailable; it must validate as a complete workspace.
        ok = H.validate_fixture_workspace(H.FIXTURE)[0]
        self.assertTrue(ok, "无 Python 手写产出的工作区未能校验为完整工作区")

    # 13
    def test_teaching_template_detector(self):
        good = _read("mock/sample_outputs/teaching_template_good.txt")
        self.assertTrue(H.teaching_template_ok(good), "七步齐全且按序的好例应通过")
        self.assertTrue(H.teaching_template_ok(_read("mock/sample_outputs/teaching_template_liberal_good.txt")),
                        "文科变体（材料关键句/核心概念/逐点展开论证）应通过")
        self.assertTrue(H.teaching_template_ok(_read("mock/sample_outputs/teaching_template_ai_answer_good.txt")),
                        "⑤ 标题带 ⚠️ 的 AI 答案好例应通过")
        self.assertFalse(H.teaching_template_ok(_read("mock/sample_outputs/teaching_template_skip_ask.txt")),
                         "跳过「② 这题在问什么」直接贴公式必须被抓")
        self.assertFalse(H.teaching_template_ok(_read("mock/sample_outputs/teaching_template_formula_first.txt")),
                         "七步齐全但 ④ 出现在 ② 之前（公式先行）必须被抓")
        # 步骤名只被清单式提及（不在行首做标题）不算
        checklist = ("请按 ① 题面图、② 这题在问什么、③ 图里要读的量、④ 核心公式、"
                     "⑤ 逐步演算、⑥ 答案自检、⑦ 知识点溯源 的顺序输出。")
        self.assertFalse(H.teaching_template_ok(checklist), "行内清单提及七步不算真的走了模板")
        # 只有标题没有正文不算
        empty = ("① 题面图：\n② 这题在问什么：\n③ 图里要读的量：\n④ 核心公式：\n"
                 "⑤ 逐步演算：\n⑥ 答案自检：\n⑦ 知识点溯源：\n")
        self.assertFalse(H.teaching_template_ok(empty), "空标题必须被抓")
        # ⑦ 溯源必须真的落到章节或 wiki 路径，不许空口宣称
        no_cite = good.replace(
            "第 2 章《线性表》 · references/wiki/ch02_linear_list.md · "
            "原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)",
            "见课件。")
        self.assertNotEqual(no_cite, good)
        self.assertFalse(H.teaching_template_ok(no_cite), "⑦ 无章节/wiki 引用必须被抓")
        # 裸「第 N 章」不算溯源——必须有 wiki 路径（Codex R1-F3）
        bare_ch = good.replace(
            "references/wiki/ch02_linear_list.md · 原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)",
            "见第 2 章课件")
        self.assertNotEqual(bare_ch, good)
        self.assertFalse(H.teaching_template_ok(bare_ch), "⑦ 只写章节号、无 wiki 路径必须被抓")
        # 原文页不明时如实写「来源页未知」仍通过（诚实弃答不惩罚）
        honest = good.replace("原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)", "原文来源页未知")
        self.assertNotEqual(honest, good)
        self.assertTrue(H.teaching_template_ok(honest), "wiki 路径 + 如实来源页未知 应通过")
        # ⑤ 正文里的圆圈子步骤不是新节，不得把正文切空（Codex R1-F4）
        substeps = good.replace(
            "⑤ 逐步演算：\n1. 顺序表：一次乘加直接算出地址，1 步到位。\n2. 链表：i=5 时要做 5 次 next 跳转。\n3. 结论：顺序表随机访问 O(1)，链表 O(n)，顺序表快。",
            "⑤ 逐步演算：\n① 先算顺序表：一次乘加直接算出地址。\n② 再算链表：i=5 时要做 5 次 next 跳转，顺序表快。")
        self.assertNotEqual(substeps, good)
        self.assertTrue(H.teaching_template_ok(substeps), "⑤ 正文用 ①② 圆圈子步骤的合规输出不应被误杀")
        # 子步骤文字恰好以块名开头（① 核心公式代入…）仍不算新节——0 容差边界（Codex R1-F4 残留）
        name_prefixed = good.replace(
            "⑤ 逐步演算：\n1. 顺序表：一次乘加直接算出地址，1 步到位。\n2. 链表：i=5 时要做 5 次 next 跳转。\n3. 结论：顺序表随机访问 O(1)，链表 O(n)，顺序表快。",
            "⑤ 逐步演算：\n① 核心公式代入得地址：一次乘加到位。\n② 链表要跳 5 次，顺序表快。")
        self.assertNotEqual(name_prefixed, good)
        self.assertTrue(H.teaching_template_ok(name_prefixed),
                        "子步骤以「核心公式」等块名开头但非分隔符结尾，不应被当成新节切空 ⑤")
        # ⑦ 的 wiki/链接要求只看 ⑦ 自己的正文，不能靠相邻来源块行满足（Codex R1-F3 残留）
        wiki_in_source = good.replace(
            "第 2 章《线性表》 · references/wiki/ch02_linear_list.md · "
            "原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)",
            "见第 2 章课件（原文页未详）").replace(
            "题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料",
            "题目来源：references/wiki/ch02_linear_list.md｜答案来源：hw02_sol.pdf｜🟢 来自资料")
        self.assertNotEqual(wiki_in_source, good)
        self.assertFalse(H.teaching_template_ok(wiki_in_source),
                         "⑦ 自身无 wiki、仅来源块行有 wiki 路径必须被抓")
        # ⑦ 有 wiki 但只写裸页码，链接只在 opt-in 收尾块里——也必须被抓（Codex R1-F3 残留）
        link_in_closer = good.replace(
            "第 2 章《线性表》 · references/wiki/ch02_linear_list.md · "
            "原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)",
            "第 2 章 · references/wiki/ch02_linear_list.md · 原文 lecture03.pdf 第 12 页"
        ) + "\n易错点：\n参考 [这里](../x.pdf#page=1)。\n"
        self.assertNotEqual(link_in_closer, good)
        self.assertFalse(H.teaching_template_ok(link_in_closer),
                         "⑦ 无链接、链接只在收尾块里必须被抓")

    def test_teaching_template_marker_binding_and_segmentation(self):
        # Codex R2：七步绑定期望圆圈序号 + 逐题校验 + 编号骨架不算有正文
        good = _read("mock/sample_outputs/teaching_template_good.txt")
        # HKR：七步全用 ① 编号（或错乱编号）必须被抓
        allone = good
        for mk in ("②", "③", "④", "⑤", "⑥", "⑦"):
            allone = allone.replace(mk + " ", "① ")
        self.assertNotEqual(allone, good)
        self.assertFalse(H.teaching_template_ok(allone), "七步全用 ① 编号（misnumber）必须被抓")
        # HKH：④ 标题被删、只在 ③ 正文留一条以块名开头的子步骤「① 核心公式代入…」不算 ④
        drop4 = good.replace(
            "④ 核心公式：\n顺序表定位：地址 = 基地址 + i × 元素大小 → O(1)；链表定位：从头走 i 步 → O(i)。\n",
            "").replace(
            "③ 图里要读的量：\n表长 n、要访问的下标 i；链表图里数一数从头结点走到第 i 个结点要跳几次。",
            "③ 图里要读的量：\n表长 n。\n① 核心公式代入得地址。")
        self.assertNotEqual(drop4, good)
        self.assertFalse(H.teaching_template_ok(drop4), "④ 缺标题、子步骤以块名开头不得冒充 ④")
        # HKJ：纯编号标题骨架（步下无正文）必须被抓
        skeleton = ("[#x]\n1. 题面图\n2. 这题在问什么\n3. 图里要读的量\n4. 核心公式\n5. 逐步演算\n"
                    "6. 答案自检\n7. 知识点溯源 references/wiki/ch01.md [p](../a.pdf#page=1)\n"
                    "题目来源：a.pdf｜答案来源：b.pdf｜🟢 来自资料\n")
        self.assertFalse(H.teaching_template_ok(skeleton), "纯编号标题骨架（无正文）必须被抓")
        # HKO：两题响应里第二题省略 ②/④/⑦ 必须被抓；两题都齐全才通过
        q2_bad = good + ("\n\n【第二题】[#mc_q2] 另一题\n① 题面图：\n本题无图。\n③ 图里要读的量：\nx。\n"
                         "⑤ 逐步演算：\n算。\n⑥ 答案自检：\n对。\n"
                         "题目来源：h.pdf｜答案来源：s.pdf｜🟢 来自资料\n")
        self.assertFalse(H.teaching_template_ok(q2_bad), "多题时后续题缺步必须被抓（不能靠首题满足全局）")
        q2_ok = good + ("\n\n【第二题】[#mc_q2] 另一题\n① 题面图：\n本题无图。\n② 这题在问什么：\n问啥。\n"
                        "③ 图里要读的量：\nx。\n④ 核心公式：\nf。\n⑤ 逐步演算：\n算。\n⑥ 答案自检：\n对。\n"
                        "⑦ 知识点溯源：\nreferences/wiki/ch03.md 原文 [p](../c.pdf#page=2)\n"
                        "题目来源：h.pdf｜答案来源：s.pdf｜🟢 来自资料\n")
        self.assertTrue(H.teaching_template_ok(q2_ok), "两题都各自齐全应通过")
        # 逐题来源块：第二题缺来源块必须被抓
        q2_no_src = good + ("\n\n【第二题】[#mc_q2] 另一题\n① 题面图：\n本题无图。\n② 这题在问什么：\n问啥。\n"
                            "③ 图里要读的量：\nx。\n④ 核心公式：\nf。\n⑤ 逐步演算：\n算。\n⑥ 答案自检：\n对。\n"
                            "⑦ 知识点溯源：\nreferences/wiki/ch03.md 原文 [p](../c.pdf#page=2)\n")
        self.assertFalse(H.question_source_block_ok(q2_no_src), "多题时后续题缺来源块必须被抓")

    def test_question_source_block_detector(self):
        good = _read("mock/sample_outputs/teaching_template_good.txt")
        self.assertTrue(H.question_source_block_ok(good))
        ai = _read("mock/sample_outputs/teaching_template_ai_answer_good.txt")
        self.assertTrue(H.question_source_block_ok(ai, ai_answer=True),
                        "⚠️ 同时在来源行与 ⑤ 标题的 AI 答案好例应通过")
        self.assertFalse(H.question_source_block_ok(_read("mock/sample_outputs/teaching_template_no_source.txt")),
                         "整块来源块缺失必须被抓")
        self.assertFalse(
            H.question_source_block_ok(_read("mock/sample_outputs/teaching_template_unlabeled_source.txt")),
            "来源行末尾没有 canonical 标签必须被抓")
        self.assertFalse(
            H.question_source_block_ok(_read("mock/sample_outputs/teaching_template_missing_warn.txt"),
                                       ai_answer=True),
            "AI 答案但来源行无 ⚠️ 必须被抓")
        self.assertFalse(
            H.question_source_block_ok(_read("mock/sample_outputs/teaching_template_warn_title.txt"),
                                       ai_answer=True),
            "AI 答案来源行有 ⚠️ 但答案块标题没带 ⚠️ 必须被抓")
        # Codex R2-HKM：答案块标题只有 ⚠️ 图标、没有完整「AI生成答案，非老师/教材提供」文本必须被抓
        icon_only = ai.replace("⑤ 逐步演算（⚠️ AI生成答案，非老师/教材提供）：", "⑤ 逐步演算（⚠️）：")
        self.assertNotEqual(icon_only, ai)
        self.assertFalse(H.question_source_block_ok(icon_only, ai_answer=True),
                         "答案块标题只有 ⚠️ 图标、无完整警告文本必须被抓")
        # ASCII 竖线分隔也接受
        self.assertTrue(H.question_source_block_ok(
            "题目来源：a.pdf 第 1 页（homework）| 答案来源：a_sol.pdf 第 1 页｜🟢 来自资料"))
        # 题目来源/答案来源拆在两行不算一个来源块
        self.assertFalse(H.question_source_block_ok(
            "题目来源：a.pdf 第 1 页\n答案来源：a_sol.pdf 第 1 页｜🟢 来自资料"))
        # 非 AI 答案时 🟡 标签合法
        self.assertTrue(H.question_source_block_ok(
            "题目来源：lec1.pdf 第 2 页（lecture）｜答案来源：老师课堂口述，AI 整理｜🟡 AI补充，可能与你老师讲的不完全一致"))
        # 尾标签必须逐字 canonical——行内前段塞图标、末段贴错标签骗不过（Codex R1-F1）
        forged = ("题目来源：⚠️ hw04.pdf 第 1 页（homework）｜答案来源：AI 推导（无教材答案）｜🟢 来自资料")
        self.assertFalse(H.question_source_block_ok(forged, ai_answer=True),
                         "AI 答案被尾标签标成 🟢 来自资料、⚠️ 只在行首伪装，必须被抓")
        self.assertFalse(H.question_source_block_ok("题目来源：a.pdf｜答案来源：b.pdf｜🟢 资料"),
                         "非 canonical 文本的尾标签（🟢 资料）必须被抓")
        # ⚠ 不带变体选择符 (FE0F) 的 canonical 尾标签也接受（归一化比对；非 AI 场景只查行级）
        self.assertTrue(H.question_source_block_ok(
            "题目来源：hw04.pdf 第 1 页（homework）｜答案来源：AI 推导（无教材答案）｜⚠ AI生成答案，非老师/教材提供"))
        # canonical 标签后带一个括号补充（源出处细节）合法（Codex R1-F1）
        self.assertTrue(H.question_source_block_ok(
            "题目来源：a.pdf｜答案来源：b.pdf｜🟢 来自资料（讲义 ch2 第 3 页）"),
            "canonical 标签后跟括号出处补充应通过")
        # 但 canonical 标签后接任意自由文本尾巴（非括注）必须被抓——防「贴对标签再瞎编」
        self.assertFalse(H.question_source_block_ok(
            "题目来源：a.pdf｜答案来源：b.pdf｜🟢 来自资料 但这句其实我瞎编的"),
            "canonical 标签后接自由文本尾巴必须被抓")

    def test_no_unsolicited_closing_blocks_detector(self):
        # 好例默认不带收尾块
        self.assertTrue(H.no_unsolicited_closing_blocks(
            _read("mock/sample_outputs/teaching_template_good.txt")))
        self.assertTrue(H.no_unsolicited_closing_blocks(
            _read("mock/sample_outputs/teaching_template_liberal_good.txt")))
        self.assertTrue(H.no_unsolicited_closing_blocks(
            _read("mock/sample_outputs/teaching_template_ai_answer_good.txt")))
        # 未经要求擅自附加收尾块必须被抓
        self.assertFalse(H.no_unsolicited_closing_blocks(
            _read("mock/sample_outputs/teaching_template_unsolicited_closers.txt")),
            "学生没要求却输出 易错点/3分钟速记/现在轮到你 必须被抓")
        # 探测器本身不看上下文——opt-in mock 也含收尾块标题，一样返回 False；
        # 「学生要求了则允许」的豁免在场景 dispatch 层（不跑本检查），不是探测器放水
        self.assertFalse(H.no_unsolicited_closing_blocks(
            _read("mock/sample_outputs/teaching_template_optin_closers.txt")))
        # opt-in mock 的七步与来源块仍然合格（dispatch 层实际断言的内容）
        optin = _read("mock/sample_outputs/teaching_template_optin_closers.txt")
        self.assertTrue(H.teaching_template_ok(optin))
        self.assertTrue(H.question_source_block_ok(optin))
        # 行内提及「易错点」不算标题、不误伤
        self.assertTrue(H.no_unsolicited_closing_blocks("② 这题在问什么：\n考你能不能避开常见易错点。"))
        # Codex R3-QR_5：带方括号的 markdown 收尾块标题也要抓（## 【易错点】 / **【3分钟速记】**）
        self.assertFalse(H.no_unsolicited_closing_blocks("正文\n## 【易错点】\n注意 LIFO。"),
                         "## 【易错点】 形态的收尾块必须被抓")
        self.assertFalse(H.no_unsolicited_closing_blocks("正文\n**【3分钟速记】**\n口诀。"),
                         "**【3分钟速记】** 形态的收尾块必须被抓")
        self.assertFalse(H.no_unsolicited_closing_blocks("正文\n## 现在轮到你\n试试看。"))

    def test_teaching_template_r3_rigor(self):
        # Codex R3：逐题按 ① 题面图 切段 + 诚实来源未知 + 来源块紧跟 ⑦ + 零基础走 A5
        good = _read("mock/sample_outputs/teaching_template_good.txt")
        # QR_v：未标号的第二题（有自己的 ① 但缺 ②/④）必须被抓
        q2_untagged = good + ("\n\n另一道题：\n① 题面图：\n本题无图。\n③ 图里要读的量：\nx。\n"
                              "⑤ 逐步演算：\n算。\n⑥ 答案自检：\n对。\n⑦ 知识点溯源：\n"
                              "references/wiki/ch03.md [p](../c.pdf#page=2)\n"
                              "题目来源：h.pdf｜答案来源：s.pdf｜🟢 来自资料\n")
        self.assertFalse(H.teaching_template_ok(q2_untagged), "未标号的缺步第二题必须被抓（不能只按 [#id] 切）")
        # QR_v：带标签的第二题没有自己的 ① 块（标签数 > ① 块数）必须被抓
        q2_tag_no_block = good + ("\n\n【第二题】[#mc_q2] 另一题\n随便写点没有七步。\n"
                                  "题目来源：h.pdf｜答案来源：s.pdf｜🟢 来自资料\n")
        self.assertFalse(H.teaching_template_ok(q2_tag_no_block), "带标签却无 ① 整块的题必须被抓")
        # QR_0：来源确实不明时如实写「来源未知」（⑦ 无 wiki 路径）也算合规——不惩罚诚实
        honest = good.replace(
            "第 2 章《线性表》 · references/wiki/ch02_linear_list.md · "
            "原文 [lecture03.pdf 第 12 页](../lecture03.pdf#page=12)",
            "这题的原始出处在我手上的资料里找不到，来源未知。")
        self.assertNotEqual(honest, good)
        self.assertTrue(H.teaching_template_ok(honest), "如实「来源未知」（无 wiki）应通过，不惩罚诚实弃答")
        # QR_2：opt-in 收尾块夹在 ⑦ 与来源块之间（顺序错）必须被抓
        closer_before_src = good.replace(
            "题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料",
            "易错点：\n别记反。\n\n题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料")
        self.assertNotEqual(closer_before_src, good)
        self.assertFalse(H.teaching_template_ok(closer_before_src), "收尾块夹在 ⑦ 与来源块之间必须被抓")
        # QR_8：零基础旧两段式（考点拆解 + 标准答题步骤、无 ①-⑦）必须被 A5 七步判不合格
        legacy = _read("mock/sample_outputs/zero_basic_legacy_only.txt")
        self.assertFalse(H.teaching_template_ok(legacy), "零基础只给旧两段式、无 ①-⑦ 必须被抓")
        # 零基础好例（七步 + 来源块）三项全过
        zb = _read("mock/sample_outputs/zero_basic_explain.txt")
        self.assertTrue(H.teaching_template_ok(zb) and H.question_source_block_ok(zb)
                        and H.has_zero_basic_sections(zb), "零基础七步好例应三项全过")

    def test_a6_time_budget_no_questions_detector(self):
        # ≤1天档：好例纯讲解无学生问句；反例向学生抛澄清/偏好问句必须被抓
        self.assertTrue(H.urgent_no_student_questions_ok(_read("mock/sample_outputs/time_budget_1day_good.txt")),
                        "≤1天纯讲解好例不应有学生问句")
        self.assertFalse(H.urgent_no_student_questions_ok(_read("mock/sample_outputs/time_budget_1day_bad.txt")),
                         "≤1天向学生提问必须被抓")
        # 讲解里的自答式反问（不含学生澄清线索）不算学生问句、不误伤
        self.assertFalse(H.asks_student_question("为什么顺序表随机访问更快？因为地址可直接算出。"))
        self.assertTrue(H.asks_student_question("你想先从哪一章开始？"))
        self.assertTrue(H.asks_student_question("要不要我先讲栈？"))
        # 陈述句里出现「你」但不是问句（不以 ？结尾）不算
        self.assertFalse(H.asks_student_question("接下来我给你讲栈的三个操作。"))
        # 自答式反问前缀 / 紧接自答 不算（False Positive 防护）
        self.assertFalse(H.asks_student_question("你可能会问：这道题为什么选 B？因为它满足性质。"),
                         "「你可能会问…？」自问自答不算")
        self.assertFalse(H.asks_student_question("您也许好奇：栈和队列有何区别？其实差在存取顺序。"))
        self.assertFalse(H.asks_student_question("栈是后进先出，对吧？其实就是这样。"), "反问后紧接自答不算")
        # Codex R2-IAO：≤1天 里任何面向用户的非反问问句都算（不靠白名单 cue）——收尾问句 + 通用问句
        for q in ("还有问题吗？", "接下来怎么安排？", "我先讲第1章，可以吗？", "我们开始吧，好吗？",
                  "有没有什么问题？", "Any questions?"):
            self.assertTrue(H.asks_student_question(q), "≤1天 通用面向用户问句必须被抓：%s" % q)
        # 问号非行尾 / 跨软换行 / 英文问句 都能识别（False Negative 防护）
        self.assertTrue(H.asks_student_question("你想先复习哪一章？ 告诉我。"), "问号后有尾巴也要识别")
        self.assertTrue(H.asks_student_question("请问你复习到第几章了？请回复。"))
        self.assertTrue(H.asks_student_question("你打算从哪\n章开始？"), "跨软换行的问句要识别")
        self.assertTrue(H.asks_student_question("Which chapter do you want to start with?"))
        self.assertTrue(H.asks_student_question("Do you remember big-O notation?"))
        # 选择疑问 / 「需不需要我先…吗」/「Should I…」也要抓（Codex R1-XX）
        self.assertTrue(H.asks_student_question("先讲栈还是队列？"))
        self.assertTrue(H.asks_student_question("需要先讲栈吗？"))
        self.assertTrue(H.asks_student_question("Should I start with stacks?"))
        self.assertTrue(H.asks_student_question("用不用我先过一遍公式？"))
        self.assertTrue(H.asks_student_question("先复习哪个？栈还是队列？"))
        # 陈述句（无 ？）不误伤
        self.assertFalse(H.asks_student_question("接下来我先讲栈，再讲队列。"))

    def test_a6_knowledge_window_recheck_detector(self):
        # 窗口外知识点：好例回问/实测；反例默认还会直接用必须被抓
        self.assertTrue(H.window_out_rechecked(_read("mock/sample_outputs/window_recheck_good.txt")),
                        "窗口外知识点做了回问/实测的好例应通过")
        self.assertFalse(H.window_out_rechecked(_read("mock/sample_outputs/window_recheck_bad.txt")),
                         "窗口外却默认还会、直接用必须被抓")
        # 否定式提及复核线索（不出题实测 / 无需回问还记得吗）不算真的复核
        self.assertFalse(H.window_out_rechecked("递归在窗口外了，我就不出题实测了，直接用。"))
        self.assertFalse(H.window_out_rechecked("递归窗口外，无需回问你还记得吗，直接用。"))
        # 反事实（本来该先确认却没做）不算复核
        self.assertFalse(H.window_out_rechecked("窗口外的这块，本来该先确认的，但我就不这么干了，直接用。"))
        # Codex R3-YIp：否定式安全声明「不会默认你会」不该压掉真正的复核（False Negative 防护）
        self.assertTrue(H.window_out_rechecked("递归在窗口外了，不会默认你会，先确认你还记得递归出口吗？"),
                        "「不会默认你会」是否定式安全声明，不该误伤真复核")
        # Codex R3-YIs：否定式发问/实测（不问/不实测）算拒绝复核（False Positive 防护）
        self.assertFalse(H.window_out_rechecked("递归在窗口外了，我不问你还记得吗，直接往下用。"),
                         "「我不问你还记得吗」是跳过复核")
        self.assertFalse(H.window_out_rechecked("递归窗口外了，我不实测你了，直接讲。", require_test=True))
        # 描述性「不熟/没怎么练」在别的分句里，不该压掉真正的复核（False Negative 防护）
        self.assertTrue(H.window_out_rechecked("窗口外知识点：树的遍历。这块你可能不熟，先确认你还记得前序遍历吗？"))
        self.assertTrue(H.window_out_rechecked("递归在窗口外了，你之前没怎么练这块，来一道题看看还会不会。"),
                        "「会不会」里的「不会」不是拒绝复核")
        # 没有窗口外语境时，即使有「还记得」也不算本场景（返回 False）
        self.assertFalse(H.window_out_rechecked("先确认你还记得递归吗？"))
        self.assertTrue(H.window_out_rechecked("递归在窗口外了，先确认你还记得递归出口吗？"))
        # Codex R1-Xb：光说「先确认一下」却不真的发问不算复核；末尾「我就当你会了」默认收口也不算
        self.assertFalse(H.window_out_rechecked("递归在窗口外了，先确认一下。这里我就当你会了。"),
                         "只说先确认、不发问、末尾默认还会必须被抓")
        self.assertFalse(H.window_out_rechecked("递归窗口外，你还记得吗？算了我就当你会了，直接用。"),
                         "问了又末尾默认收口，仍不算真复核")
        # 真发问（还记得…吗）或真出题（来一道题实测）才算
        self.assertTrue(H.window_out_rechecked("窗口外了，来一道题实测一下你还会不会。"))
        # Codex R2-IAZ：>7天 档（require_test）必须出题实测——只口头问「还记得吗」不算
        recall = "递归在窗口外了，先确认你还记得递归出口吗？"
        test_out = "递归在窗口外了，来一道递归难题实测一下。"
        self.assertTrue(H.window_out_rechecked(recall, require_test=False), "3-7天档口头回问算复核")
        self.assertFalse(H.window_out_rechecked(recall, require_test=True), ">7天只口头回问、不出题必须被抓")
        self.assertTrue(H.window_out_rechecked(test_out, require_test=True), ">7天出题实测算复核")
        # >7天 mock 对照
        self.assertTrue(H.window_out_rechecked(_read("mock/sample_outputs/window_recheck_test_good.txt"),
                                               require_test=True))
        self.assertFalse(H.window_out_rechecked(_read("mock/sample_outputs/window_recheck_recall_only_bad.txt"),
                                                require_test=True), ">7天只口头回问的坏例必须被抓")

    def test_notebook_persist_receipt_detector(self):
        # v4 §2.4 红线：教学回合必须「先落盘、再摘要」——回执 = add-entry 命令 + notebook/chNN.md# 链接
        good = _read("mock/sample_outputs/notebook_persist_good.txt")
        self.assertTrue(H.notebook_persist_receipt_ok(good), "落盘命令 + 学生可见锚点回执的好例应通过")
        self.assertFalse(H.notebook_persist_receipt_ok(_read("mock/sample_outputs/notebook_persist_chat_only.txt")),
                         "全程只在聊天里讲、零落盘回执必须被抓（v4 §2.4 红线）")
        # 只贴回执链接、没有 add-entry 命令证据 → 没真落盘，不算
        self.assertFalse(H.notebook_persist_receipt_ok(
            "讲解……\n完整解答：notebook/ch02.md#q13 ｜ 目录：notebook/index.md"),
            "只有链接、无 add-entry 命令证据不应判通过")
        # 只有命令、学生可见回复里没有 notebook/chNN.md# 锚点链接 → 学生找不到入口，不算
        self.assertFalse(H.notebook_persist_receipt_ok(
            "`python scripts/notebook.py --workspace . add-entry --chapter 2 --type walkthrough --id q13`\n讲完了。"),
            "只有命令、无学生可见锚点链接不应判通过")
        # 章号不一致：命令写 --chapter 3、回执却指向没写过的 ch02 → 假回执，必须被抓
        self.assertFalse(H.notebook_persist_receipt_ok(
            "`python scripts/notebook.py --workspace . add-entry --chapter 3 --type walkthrough --id q13`\n"
            "完整解答：notebook/ch02.md#q13 ｜ 目录：notebook/index.md"),
            "命令章号与回执链接章号不一致必须被抓")
        # zh canonical 回执 + code-span 命令两种证据形态都认；--chapter 2 → ch02 零填充一致
        self.assertTrue(H.notebook_persist_receipt_ok(
            "`python scripts/notebook.py --workspace . add-entry --chapter 2 --type feedback --id mc_q2 --mistake`\n"
            "完整解答：notebook/ch02.md#mc_q2 ｜ 目录：notebook/index.md"),
            "feedback + --mistake 形态的合规回执也应判通过")
        # 命令行提到 notebook.py 但不是 add-entry（如 rebuild/list）不算落盘证据
        self.assertFalse(H.notebook_persist_receipt_ok(
            "`python scripts/notebook.py --workspace . rebuild`\n完整解答：notebook/ch02.md#q13"),
            "rebuild/list 等非 add-entry 命令不算落盘证据")

    def test_workspace_confirm_detector(self):
        # v4 §2.5 红线：建区必确认——先问落点、学生肯定答复之后才允许创建调用
        good = _read("mock/sample_outputs/workspace_confirm_good.txt")
        self.assertTrue(H.workspace_target_confirmed_ok(good), "先问路径→学生确认→再建区的好例应通过")
        self.assertFalse(H.workspace_target_confirmed_ok(_read("mock/sample_outputs/workspace_confirm_silent.txt")),
                         "开场直接 ingest.py --output-dir 静默建区必须被抓（v4 §2.5 红线）")
        # 问了但不等学生答复、同一回合直接创建 → 不算确认
        self.assertFalse(H.workspace_target_confirmed_ok(
            "学生：帮我建复习库。\n辅导：工作区建在 D:\\Study\\ds 可以吗？我先建上了：\n"
            "`python scripts/ingest.py --input x --output-dir D:\\Study\\ds`"),
            "问了不等答复直接建，不算确认")
        # 学生明确拒绝/要求换位置后仍按原路径创建 → 不算确认
        self.assertFalse(H.workspace_target_confirmed_ok(
            "学生：帮我建复习库。\n辅导：工作区建在 D:\\Study\\ds 可以吗？\n学生：不要，换个位置。\n"
            "辅导：`python scripts/ingest.py --input x --output-dir D:\\Study\\ds`"),
            "学生拒绝后仍建区，不算确认")
        # 先建后问（事后追认）→ 不算：确认必须发生在创建之前
        self.assertFalse(H.workspace_target_confirmed_ok(
            "学生：帮我建复习库。\n辅导：`python scripts/ingest.py --input x --output-dir D:\\Study\\ds`\n"
            "已建好。这个工作区位置可以吗？\n学生：可以。"),
            "先建后追认不算确认（问句必须先于创建调用）")
        # 没有任何创建调用 → 本场景断言「确认过的创建」，空转 transcript 不得混绿
        self.assertFalse(H.workspace_target_confirmed_ok("学生：帮我建复习库。\n辅导：工作区想建在哪个目录？"),
                         "只问不建的空转 transcript 不应判通过（防 vacuous pass）")
        # workspace-register 也是创建事件——未确认就 register 同样是静默建区
        self.assertFalse(H.workspace_target_confirmed_ok(
            "学生：帮我建复习库。\n辅导：`python scripts/update_progress.py --workspace D:\\S "
            "workspace-register --course 数据结构`"),
            "未确认就 workspace-register 也必须被抓")
        # 学生先拒绝、辅导改址再问、学生同意 → 合规（拒绝不永久封死）
        self.assertTrue(H.workspace_target_confirmed_ok(
            "学生：帮我建复习库。\n辅导：工作区建在 D:\\Study\\ds 可以吗？\n学生：不要，换个位置。\n"
            "辅导：那换成 E:\\Study\\ds 这个位置，可以吗？\n学生：可以。\n"
            "辅导：`python scripts/ingest.py --input x --output-dir E:\\Study\\ds`"),
            "拒绝→改址→再次确认→建区 的流程应判通过")

    def test_v4_redline_scenarios_wired_into_registry(self):
        # 两个 v4 红线场景须以确定性断言（非 best_effort）注册进 scenarios.json，且 --mock 判定通过
        spec = H.load_scenarios()
        by_name = {s["name"]: s for s in spec["scenarios"]}
        for nm in ("notebook_persist_ok", "workspace_confirm_ok"):
            self.assertIn(nm, by_name, "scenarios.json 应注册 v4 红线场景 %s" % nm)
            sc = by_name[nm]
            self.assertFalse(sc.get("best_effort"), "%s 是确定性断言场景，不应标 best_effort" % nm)
            ok, detail = H.check_scenario_mock(nm, sc, H.FIXTURE)
            self.assertTrue(ok, "%s 场景在 --mock 下应通过（好例过、反例被抓）：%s" % (nm, detail))

    def test_run_mock_exits_zero(self):
        self.assertEqual(_silent(H.main, ["--mock"]), 0)

    # 14
    def test_check_fixture_exits_zero(self):
        self.assertEqual(_silent(H.main, ["--check-fixture"]), 0)

    # 15
    def test_llm_is_refused_without_env_optin(self):
        # opt-in gate: with NEITHER the env flag NOR --agent-cmd, --llm must refuse (return 2) and never
        # invoke `claude`. The real wiring (driving an agent + applying the same detectors) is now
        # exercised deterministically against a stub agent in tests/test_behavior_smoke_live.py.
        saved = os.environ.pop("RUN_SKILL_BEHAVIOR_LLM", None)
        try:
            self.assertEqual(_silent(H.main, ["--llm"]), 2,
                             "未设置 env 且未给 --agent-cmd 时 --llm 应被拒绝（返回 2）")
        finally:
            if saved is not None:
                os.environ["RUN_SKILL_BEHAVIOR_LLM"] = saved

    # 16
    def test_no_api_keys_required_or_read(self):
        src = _read("run_behavior_smoke.py")
        for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "API_KEY"):
            self.assertNotIn(key, src, f"harness 不应引用 API key: {key}")
        # with every *_API_KEY removed from the env, the default path still works
        saved = {k: os.environ.pop(k) for k in list(os.environ) if k.endswith("API_KEY")}
        try:
            self.assertEqual(_silent(H.main, ["--mock"]), 0)
            self.assertEqual(_silent(H.main, ["--check-fixture"]), 0)
        finally:
            os.environ.update(saved)

    # 17
    def test_no_network_or_paid_benchmark_by_default(self):
        src = _read("run_behavior_smoke.py")
        for net in ("requests", "urllib", "http.client", "socket."):
            self.assertNotIn(net, src, f"默认路径不应引入网络库: {net}")
        # FUNCTIONAL + transitive guard: break subprocess AND sockets/urlopen, then prove the default
        # paths (which transitively import scripts/validate_workspace.py) still pass without any of them.
        import subprocess
        import socket
        import urllib.request
        def _boom(msg):
            def f(*a, **k):
                raise AssertionError(msg)
            return f
        saved = (subprocess.run, socket.socket, urllib.request.urlopen)
        subprocess.run = _boom("默认路径不应调用 subprocess（无 claude -p / 付费真跑）")
        socket.socket = _boom("默认路径不应建立 socket（无网络）")
        urllib.request.urlopen = _boom("默认路径不应发起 HTTP 请求（无网络）")
        try:
            self.assertEqual(_silent(H.main, ["--mock"]), 0)
            self.assertEqual(_silent(H.main, ["--check-fixture"]), 0)
        finally:
            subprocess.run, socket.socket, urllib.request.urlopen = saved


if __name__ == "__main__":
    unittest.main()
