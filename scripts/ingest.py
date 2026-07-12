#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""One-shot parse and generate the cram LLM wiki directory structure and progress files。

依赖：Python 3.7+ 标准库，无需 pip 安装。
设计原则：发现问题就大声报错并停下，绝不静默产出残缺文件。
"""

import os
import re
import sys
import json
import shutil
import argparse
from datetime import datetime

# 在 Windows 默认 GBK 控制台上避免中文状态输出变成乱码
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass  # 老版本解释器或非常规环境则保持默认

import i18n

SUBJECT_TOKEN = "《科目名称》"               # 模板中待替换的科目占位符
PHASE_TABLE_MARKER = "<!-- PHASE_TABLE -->"        # study_plan 模板里表格插入点
PHASE_CHECKLIST_MARKER = "<!-- PHASE_CHECKLIST -->"  # study_progress 模板里打卡列表插入点
LANGUAGE_MARKER = "<!-- LANGUAGE -->"              # 显式 --lang 时替换为语言代号，否则整行移除
SAFE_FILENAME = re.compile(r"^[\w.\-]+\.md$")      # 仅允许不含路径的 *.md 文件名
VALID_QUIZ_TYPES = {"choice", "subjective", "diagram", "fill_blank", "true_false", "code"}


def is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def get_template_path(template_name, lang="zh"):
    # 脚本位于 <package>/scripts/，模板位于 <package>/locales/<lang>/templates/（v4 P2 语言包分离）。
    # 请求语言的模板缺失时回落 zh 包（历史 canonical，覆盖最全），两者都缺才返回 None。
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for lg in dict.fromkeys((lang, "zh")):
        template_path = os.path.join(script_dir, "..", "locales", lg, "templates", template_name)
        if os.path.exists(template_path):
            return template_path
    return None


def fail(messages):
    """打印所有问题并以非零状态退出，避免静默生成残缺的复习环境。"""
    print("\n[-] 初始化已中止：输入数据存在以下问题，请修正后重试：")
    for m in messages:
        print(f"    • {m}")
    sys.exit(1)


def validate(data):
    """校验输入 JSON。返回 (course_name, phases, quiz_bank, missing_answer_ids)。

    结构性问题（缺字段、类型错、文件名不安全、缺答案的选择题选项等）会直接中止；
    主观/计算题缺少标准答案只作为警告列出，交由学生决定是否让 AI 补全。
    """
    if not isinstance(data, dict):
        fail(["顶层 JSON 必须是对象，应包含 course_name / phases / quiz_bank。"])

    errors = []

    course_name = data.get("course_name")
    if not isinstance(course_name, str) or not course_name.strip():
        errors.append("缺少有效的 course_name（科目名称）。")
        course_name = "未命名科目"

    phases = data.get("phases")
    if not isinstance(phases, list) or len(phases) == 0:
        errors.append("phases 必须是非空数组（至少包含一个复习阶段）。")
        phases = []

    seen_files = {}
    for i, p in enumerate(phases):
        idx = i + 1
        if not isinstance(p, dict):
            errors.append(f"第 {idx} 个阶段不是对象。")
            continue
        for key in ("phase_num", "phase_name", "wiki_filename", "wiki_content"):
            val = p.get(key)
            if val is None or (isinstance(val, str) and not val.strip()):
                errors.append(f"第 {idx} 个阶段缺少字段「{key}」。")
        # phase_num 在校验期就规范化成正整数（手写/LLM 生成的 JSON 常给 "1" 字符串）——
        # 否则要到写盘中途的 chunk id 格式化才 TypeError，留下残缺工作区（Codex r2）。
        pn = p.get("phase_num")
        if pn is not None:
            if isinstance(pn, bool) or not isinstance(pn, (int, str)):
                errors.append(f"第 {idx} 个阶段的 phase_num 必须是正整数（当前 {pn!r}）。")
            elif isinstance(pn, str):
                if pn.strip().isdigit() and int(pn.strip()) >= 1:
                    p["phase_num"] = int(pn.strip())
                else:
                    errors.append(f"第 {idx} 个阶段的 phase_num 必须是正整数（当前 {pn!r}）。")
            elif pn < 1:
                errors.append(f"第 {idx} 个阶段的 phase_num 必须 ≥1（当前 {pn}）。")
        # 文件名安全 + 去重（防止 ../ 越界写盘或互相覆盖）
        fn = p.get("wiki_filename")
        if isinstance(fn, str) and fn.strip():
            stripped = fn.strip()
            base = os.path.basename(stripped)
            if base != stripped or not SAFE_FILENAME.match(base):
                errors.append(
                    f"第 {idx} 个阶段的 wiki_filename「{fn}」不合法："
                    "只能是不含路径分隔符、不含 .. 的 *.md 文件名。"
                )
            elif base in seen_files:
                errors.append(
                    f"wiki_filename「{base}」在第 {seen_files[base]} 和第 {idx} 个阶段重复，会互相覆盖。"
                )
            else:
                seen_files[base] = idx

    quiz_bank = data.get("quiz_bank", [])
    if not isinstance(quiz_bank, list):
        errors.append("quiz_bank 必须是数组。")
        quiz_bank = []

    missing_answer_ids = []
    for i, q in enumerate(quiz_bank):
        raw_id = q.get("id") if isinstance(q, dict) else None
        tag = str(raw_id) if not is_blank(raw_id) else f"#{i + 1}"
        if not isinstance(q, dict):
            errors.append(f"题目 {tag} 不是对象。")
            continue
        qtype = q.get("type")
        if qtype not in VALID_QUIZ_TYPES:
            errors.append(f"题目 {tag} 的 type 必须是 {'/'.join(sorted(VALID_QUIZ_TYPES))} 之一（当前为 {qtype!r}）。")
        if not q.get("question"):
            errors.append(f"题目 {tag} 缺少题干 question。")
        if qtype == "choice" and not q.get("options"):
            errors.append(f"选择题 {tag} 缺少 options 选项。")
        if is_blank(q.get("answer")):
            missing_answer_ids.append(tag)

    if errors:
        fail(errors)

    return course_name, phases, quiz_bank, missing_answer_ids


def build_phase_table(phases, lang="zh"):
    # 插入行必须跟模板同语言（单语言纯净）：en 模板里混入 阶段/未开始 会产出混语工作区；
    # 读侧（update_progress._plan_phases / validate_workspace._plan_phase_nums /
    # build_knowledge_index._PHASE_RE）本就同时认 「阶段N」 与 「Phase N」。
    if lang == "en":
        lines = [
            "| Phase | Core task | Linked wiki chapter file | Status |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for p in phases:
            fn = os.path.basename(p["wiki_filename"].strip())
            lines.append(f"| **Phase {p['phase_num']}** | {p['phase_name']} | `references/wiki/{fn}` | Not started |")
        lines.append("| **Mock test** | Final mixed self-test | `references/quiz_bank.json` | Not started |")
        lines.append("| **Pitfall sweep** | Mistake-archive revisit & cheat sheet | `study_progress.md` mistake archive | Not started |")
        return "\n".join(lines)
    lines = [
        "| 阶段 | 核心任务 | 关联 Wiki 章节文件 | 状态 |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for p in phases:
        fn = os.path.basename(p["wiki_filename"].strip())
        lines.append(f"| **阶段 {p['phase_num']}** | {p['phase_name']} | `references/wiki/{fn}` | 未开始 |")
    lines.append("| **模拟测试** | 综合真题自测 | `references/quiz_bank.json` | 未开始 |")
    lines.append("| **易错扫雷** | 错题本重温与考前小抄 | `study_progress.md` 错题本 | 未开始 |")
    return "\n".join(lines)


def build_phase_checklist(phases, lang="zh"):
    if lang == "en":
        lines = []
        for p in phases:
            fn = os.path.basename(p["wiki_filename"].strip())
            lines.append(f"- [ ] **Phase {p['phase_num']}**: {p['phase_name']} (see `references/wiki/{fn}`)")
        lines.append("- [ ] **Mock test**: Final mixed self-test (see `references/quiz_bank.json`)")
        lines.append("- [ ] **Pitfall sweep**: mistake self-test")
        return "\n".join(lines)
    lines = []
    for p in phases:
        fn = os.path.basename(p["wiki_filename"].strip())
        lines.append(f"- [ ] **阶段 {p['phase_num']}**：{p['phase_name']} (关联 `references/wiki/{fn}`)")
    lines.append("- [ ] **模拟测试**：综合真题自测 (关联 `references/quiz_bank.json`)")
    lines.append("- [ ] **易错扫雷**：错题自测")
    return "\n".join(lines)


def render_template(template_name, replacements, markers, lang="zh"):
    """读取模板并按固定锚点 / 占位符渲染。

    用不显眼的注释锚点（如 <!-- PHASE_TABLE -->）替代以往“按 emoji 标题切割”的脆弱做法：
    可见标题被改动也不影响渲染。若锚点缺失或重复则报错，绝不静默输出错误内容。
    """
    path = get_template_path(template_name, lang)
    if not path:
        fail([f"未找到模板 {template_name}，无法生成。"])
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for marker in markers:
        count = content.count(marker)
        if count != 1:
            fail([f"模板 {template_name} 中锚点 {marker} 应恰好出现 1 次（实际 {count} 次），模板可能被改动。"])
    for token, value in replacements.items():
        content = content.replace(token, value)
    return content


def main():
    parser = argparse.ArgumentParser(description="One-shot parse and generate the cram LLM wiki directory structure and progress files")
    parser.add_argument("--input", "-i", type=str, default="raw_input.json", help="input structured-outline JSON path")
    parser.add_argument("--output-dir", "-o", type=str, default=".", help="target workspace path (default: current directory)")
    parser.add_argument("--force", action="store_true", help="allow overwriting an existing study_progress.md (auto-backup first)")
    parser.add_argument("--lang", type=str, default=None,
                        help="language pack for generated plan/progress files: zh or en "
                             "(aliases accepted via i18n.canon_language; default zh; "
                             "missing en template files fall back to the zh pack). When given "
                             "EXPLICITLY it is also seeded into the progress file so "
                             "update_progress init migrates it into study_state.language")
    args = parser.parse_args()

    lang_explicit = args.lang is not None
    lang, lang_warn = i18n.canon_language(args.lang or "zh")
    if lang not in ("zh", "en"):
        fail([lang_warn or f"--lang 仅支持 zh / en（当前为 {args.lang!r}）。"])

    if not os.path.exists(args.input):
        print(f"[-] 错误: 输入文件 '{args.input}' 不存在。")
        print("请提供正确的 JSON 数据文件。格式示例:")
        print(json.dumps({
            "course_name": "科目名称",
            "phases": [
                {
                    "phase_num": 1,
                    "phase_name": "基础概念篇",
                    "wiki_filename": "ch1_concepts.md",
                    "wiki_content": "# 阶段一：基础概念篇\n\n内容..."
                }
            ],
            "quiz_bank": []
        }, indent=2, ensure_ascii=False))
        sys.exit(1)

    print(f"[+] 正在读取输入数据: {args.input} ...")
    with open(args.input, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except Exception as e:
            fail([f"JSON 解析失败：{e}"])

    course_name, phases, quiz_bank, missing_answer_ids = validate(data)

    # ── 后处理：补全 id + 规范化 true_false 答案 ──────────────────
    TRUE_FALSE_NORMALIZE = {
        "正确": True, "对": True, "是": True, "真": True,
        "true": True, "yes": True, "√": True,
        "错误": False, "错": False, "否": False, "假": False,
        "false": False, "no": False, "×": False,
    }
    # 收集已有 id，避免补全时撞号
    existing_ids = {q["id"] for q in quiz_bank if not is_blank(q.get("id"))}
    next_id = 1
    for q in quiz_bank:
        # 补全 id（validate 不强制 id，但出口文件需要）
        if is_blank(q.get("id")):
            while f"q{next_id}" in existing_ids:
                next_id += 1
            new_id = f"q{next_id}"
            q["id"] = new_id
            existing_ids.add(new_id)
        # 规范化 true_false 答案
        if q.get("type") == "true_false" and isinstance(q.get("answer"), str):
            normalized = TRUE_FALSE_NORMALIZE.get(q["answer"].strip().lower(), q["answer"])
            q["answer"] = normalized
    # ────────────────────────────────────────────────────────────────
    # 补号后重算缺答案清单——validate 阶段无 id 的题记的是「#序号」占位，
    # 持久化报告必须指向题库里真实存在的 id，后续会话的 AI 才能定位接手
    missing_answer_ids = [q["id"] for q in quiz_bank if is_blank(q.get("answer"))]

    print(f"[+] 识别到科目: {course_name}")
    print(f"[+] 阶段数量: {len(phases)} 个")
    print(f"[+] 题目数量: {len(quiz_bank)} 道")
    if missing_answer_ids:
        print("\n[!] 注意：以下题目缺少标准答案（answer 为空）：")
        print("    " + ", ".join(missing_answer_ids))
        print("    这些题在测验时没有可对照的标准答案；请先补全，或在对话中让 AI 为它们生成答案后再录入。")
        print("    ⚠️ 若由 AI 代为生成答案，必须向学生明确标注「⚠️ AI生成答案，非老师/教材提供」，")
        print("       严禁把 AI 生成的答案伪装成老师的标准答案（详见 SKILL.md 知识来源透明化协议）。")

    output_dir = os.path.abspath(args.output_dir)
    wiki_dir = os.path.join(output_dir, "references", "wiki")
    os.makedirs(wiki_dir, exist_ok=True)
    real_wiki_dir = os.path.realpath(wiki_dir)
    print(f"[+] 创建 Wiki 目录: {wiki_dir}")

    # 1. 写入各阶段 Wiki 文件（文件名已在 validate 中校验，这里再做一次包含性断言）
    for p in phases:
        filename = os.path.basename(p["wiki_filename"].strip())
        wiki_file_path = os.path.join(wiki_dir, filename)
        if os.path.commonpath([os.path.realpath(wiki_file_path), real_wiki_dir]) != real_wiki_dir:
            fail([f"文件名「{filename}」试图写出 wiki 目录之外，已拒绝。"])
        with open(wiki_file_path, "w", encoding="utf-8") as wf:
            wf.write(p["wiki_content"])
        print(f"[+] 已写入 Wiki 文件: references/wiki/{filename}")

    # 1b. v4-P3：小节级切块（仅索引粒度——章文件仍逐字写盘，现有契约零破坏）→ BM25 检索索引。
    #     检索时 retrieve.py 返回 文件+标题+词窗摘要，弃答门限先于任何生成（spike 契约）。
    import hashlib
    import chunk as _chunk
    import retrieve as _retrieve
    all_chunks, wiki_meta = [], {}
    for p in phases:
        filename = os.path.basename(p["wiki_filename"].strip())
        _, chs = _chunk.chunk_text(p["wiki_content"])
        ch_id = "ch%02d" % p["phase_num"]
        for k, c in enumerate(chs, 1):
            all_chunks.append({"id": "%s#s%02d" % (ch_id, k),
                               "file": "references/wiki/" + filename,
                               "chapter": str(p["phase_num"]),
                               "title": c["title"], "text": c["text"]})
        wiki_meta[filename] = {
            "chapter": p["phase_num"], "n_chunks": len(chs),
            "sha256": hashlib.sha256(p["wiki_content"].encode("utf-8")).hexdigest()}
    if all_chunks:
        index = _retrieve.build_index(all_chunks)
        with open(os.path.join(output_dir, "references", "retrieval_index.json"),
                  "w", encoding="utf-8") as xf:
            json.dump(index, xf, ensure_ascii=False)
        print(f"[+] 已建检索索引: references/retrieval_index.json（{len(all_chunks)} 块 / {len(phases)} 章）")
    with open(os.path.join(output_dir, "references", "wiki_meta.json"), "w", encoding="utf-8") as mf:
        json.dump(wiki_meta, mf, ensure_ascii=False, indent=2)
    # 术语对照（跨语言检索桥）：raw_input.json 可带顶层 "terms"（AI 建库时产出、可人工校对）
    terms = data.get("terms")
    if isinstance(terms, dict) and terms:
        with open(os.path.join(output_dir, "references", "terms.json"), "w", encoding="utf-8") as tf:
            json.dump(terms, tf, ensure_ascii=False, indent=2)
        print(f"[+] 已写入术语对照: references/terms.json（{len(terms)} 组）")

    # 2. 写入题库 JSON
    quiz_file_path = os.path.join(output_dir, "references", "quiz_bank.json")
    with open(quiz_file_path, "w", encoding="utf-8") as qf:
        json.dump(quiz_bank, qf, indent=2, ensure_ascii=False)
    print("[+] 已写入题库文件: references/quiz_bank.json")

    # 导入报告持久化——缺答案清单只留在控制台会随会话丢失，后续会话的 AI 无从接手
    ingest_report = {
        "course_name": course_name, "phases": len(phases), "quiz_bank": len(quiz_bank),
        "missing_answer_ids": missing_answer_ids,
        "note": "missing_answer_ids 的题没有标准答案：测验前需补全，或由 AI 生成并向学生明确标注"
                "「⚠️ AI生成答案，非老师/教材提供」。",
    }
    with open(os.path.join(output_dir, "ingest_report.json"), "w", encoding="utf-8") as rf:
        json.dump(ingest_report, rf, ensure_ascii=False, indent=2)
    print("[+] 已写入导入报告: ingest_report.json")

    # 3. 生成 study_plan.md（可重复生成，无用户状态）
    plan_content = render_template(
        "study_plan_template.md",
        {SUBJECT_TOKEN: f"《{course_name}》", PHASE_TABLE_MARKER: build_phase_table(phases, lang)},
        markers=[PHASE_TABLE_MARKER],
        lang=lang,
    )
    plan_out_path = os.path.join(output_dir, "study_plan.md")
    with open(plan_out_path, "w", encoding="utf-8") as pf:
        pf.write(plan_content)
    print("[+] 已生成: study_plan.md")

    # 4. 生成 study_progress.md（含断点与错题本，是用户状态，默认不覆盖）
    progress_out_path = os.path.join(output_dir, "study_progress.md")
    if os.path.exists(progress_out_path) and not args.force:
        print(f"[!] 已存在 {progress_out_path}，为保护你的复习进度与错题本，未覆盖它。")
        print("    如确实要重新生成，请加 --force（会先自动备份旧文件）。")
    else:
        if os.path.exists(progress_out_path):  # --force：先备份再覆盖
            backup = f"{progress_out_path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.copy2(progress_out_path, backup)
            print(f"[+] 已备份旧进度文件: {os.path.basename(backup)}")
        # 断点种子行同语言：en 进度文件写 「Phase 1: …」（读侧 current phase 解析两种都认）
        first_phase = (f"Phase 1: {phases[0]['phase_name']}" if lang == "en"
                       else f"阶段 1：{phases[0]['phase_name']}")
        prog_content = render_template(
            "study_progress_template.md",
            {
                SUBJECT_TOKEN: f"《{course_name}》",
                "{CURRENT_PHASE}": first_phase,
                PHASE_CHECKLIST_MARKER: build_phase_checklist(phases, lang),
            },
            markers=[PHASE_CHECKLIST_MARKER],
            lang=lang,
        )
        # 语言持久化闭环（Codex r5）：显式 --lang 时把语言代号种进进度文件，update_progress init
        # 迁移即得 study_state.language——否则 en 工作区 init 后 language=null，工具全回落 zh。
        # 未显式给 --lang 则整行删除（不预占语言，留给合并首问决定——缺省英文政策不被预置 zh 顶掉）。
        if lang_explicit:
            prog_content = prog_content.replace(LANGUAGE_MARKER, lang)
        else:
            prog_content = "\n".join(l for l in prog_content.splitlines()
                                     if LANGUAGE_MARKER not in l) + "\n"
        with open(progress_out_path, "w", encoding="utf-8") as prf:
            prf.write(prog_content)
        print("[+] 已生成: study_progress.md")

    print(f"\n[+] 恭喜! 《{course_name}》的 LLM Wiki 备考环境初始化成功！")
    print("你可以直接开始复习了。")


if __name__ == "__main__":
    main()
