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
import math
import argparse
import tempfile
from datetime import datetime
from pathlib import Path

# 在 Windows 默认 GBK 控制台上避免中文状态输出变成乱码
for _stream in ("stdout", "stderr"):
    try:
        getattr(sys, _stream).reconfigure(encoding="utf-8")
    except Exception:
        pass  # 老版本解释器或非常规环境则保持默认

import i18n
try:
    from asset_policy import (
        STUDENT_ATTEMPT,
        audit_asset_policy,
        has_tainted_official_asset,
        student_attempt_tainted_keys,
    )
except ImportError:  # imported as scripts.ingest in unit tests
    from scripts.asset_policy import (
        STUDENT_ATTEMPT,
        audit_asset_policy,
        has_tainted_official_asset,
        student_attempt_tainted_keys,
    )
try:
    import strict_json
except ImportError:  # imported as scripts.ingest in unit tests
    from scripts import strict_json

try:
    from ingestion.pipeline import (
        _compile_review_outputs_unlocked,
        _compile_structured_visuals as _compile_structured_visuals_core,
        _persist_payload_unlocked,
        _phase_inventory,
        _strict_payload,
        authorize_material_build_generation,
        finalize_material_build_generation,
        refresh_build_manifest,
        verify_material_build_receipt,
    )
    from ingestion.identifiers import (
        UnsafePathError,
        is_link_or_reparse,
        safe_workspace_entry,
    )
    from ingestion.storage import (
        ConflictError,
        IngestionStore,
        read_json,
        stable_read_bytes,
        workspace_publication_lock,
    )
except ImportError:  # imported as scripts.ingest in unit tests
    from scripts.ingestion.pipeline import (
        _compile_review_outputs_unlocked,
        _compile_structured_visuals as _compile_structured_visuals_core,
        _persist_payload_unlocked,
        _phase_inventory,
        _strict_payload,
        authorize_material_build_generation,
        finalize_material_build_generation,
        refresh_build_manifest,
        verify_material_build_receipt,
    )
    from scripts.ingestion.identifiers import (
        UnsafePathError,
        is_link_or_reparse,
        safe_workspace_entry,
    )
    from scripts.ingestion.storage import (
        ConflictError,
        IngestionStore,
        read_json,
        stable_read_bytes,
        workspace_publication_lock,
    )

SUBJECT_TOKEN = "《科目名称》"               # 模板中待替换的科目占位符
PHASE_TABLE_MARKER = "<!-- PHASE_TABLE -->"        # study_plan 模板里表格插入点
PHASE_CHECKLIST_MARKER = "<!-- PHASE_CHECKLIST -->"  # study_progress 模板里打卡列表插入点
LANGUAGE_MARKER = "<!-- LANGUAGE -->"              # 显式 --lang 时替换为语言代号，否则整行移除
SAFE_FILENAME = re.compile(r"^[\w.\-]+\.md$")      # 仅允许不含路径的 *.md 文件名
SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]+$")
CHAPTER_ID_RE = re.compile(r"^(?:ch(?:apter)?[_-]?)?0*([1-9]\d*)$", re.I)
WIKI_CHAPTER_RE = re.compile(r"^ch0*([1-9]\d*)(?:[^0-9].*)?\.md$", re.I)
VALID_QUIZ_TYPES = {"choice", "subjective", "diagram", "fill_blank", "true_false", "code"}
VALID_TEACHING_ROLES = {"paired_problem", "worked_example"}


def is_blank(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _file_sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_int(value):
    """Return a normalized positive int, or None (bool is never accepted as integer identity)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 1 else None
    return None


def _chapter_from_id(value):
    """Accept common raw-input chapter IDs but normalize them to a numeric chapter."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if value >= 1 else None
    if not isinstance(value, str):
        return None
    match = CHAPTER_ID_RE.fullmatch(value.strip())
    return int(match.group(1)) if match else None


def _chapter_from_wiki_filename(value):
    if not isinstance(value, str):
        return None
    match = WIKI_CHAPTER_RE.fullmatch(os.path.basename(value.strip()))
    return int(match.group(1)) if match else None


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


def _safe_output_tree(output_dir):
    """Create references/wiki without following workspace-internal directory symlinks."""
    if os.path.lexists(output_dir) and is_link_or_reparse(output_dir):
        fail([f"输出工作区是符号链接，拒绝沿链接写盘：{output_dir}"])
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as exc:
        fail([f"无法创建输出目录：{exc}"])
    if not os.path.isdir(output_dir):
        fail([f"输出路径不是目录：{output_dir}"])
    root = os.path.abspath(output_dir)
    current = root
    for relative in ("references", "references/wiki"):
        try:
            current = str(safe_workspace_entry(root, relative))
        except UnsafePathError as exc:
            fail([
                "输出目录 %s 含符号链接/junction/reparse point 或越界路径，拒绝写盘：%s"
                % (relative, exc)
            ])
        if os.path.lexists(current):
            if is_link_or_reparse(current):
                fail([f"输出目录 {os.path.relpath(current, output_dir)} 是符号链接；拒绝经链接写出工作区"])
            if not os.path.isdir(current):
                fail([f"输出路径 {os.path.relpath(current, output_dir)} 已存在但不是目录"])
        else:
            os.mkdir(current)
        try:
            safe_workspace_entry(root, relative)
        except UnsafePathError as exc:
            fail([
                "输出目录 %s 在创建期间变成符号链接/junction/reparse point，拒绝写盘：%s"
                % (relative, exc)
            ])
    return current


def _guard_write_target(path, label):
    """Reject links and special files before atomically replacing a generated artifact."""
    if os.path.lexists(path) and (is_link_or_reparse(path) or not os.path.isfile(path)):
        fail([f"{label} 目标是符号链接或特殊文件，拒绝覆盖：{path}"])


def _atomic_bytes(path, payload, label):
    """Atomically replace a file; replacing a hard link never mutates its other inode name."""
    _guard_write_target(path, label)
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".%s." % os.path.basename(path), suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        fail([f"{label} 原子写入失败：{exc}"])


def _atomic_text(path, value, label):
    _atomic_bytes(path, value.encode("utf-8"), label)


def _atomic_json(path, value, label):
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    _atomic_text(path, rendered, label)


def _merge_teaching_baseline(path, current_by_chapter):
    """Merge the current teaching IDs into an append-only independent retention baseline."""
    previous = {}
    if os.path.lexists(path):
        _guard_write_target(path, "教学例题保留基线")
        try:
            with open(path, "r", encoding="utf-8") as stream:
                payload = strict_json.load(stream)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            fail([f"教学例题保留基线无法读取，拒绝用较小快照覆盖：{exc}"])
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            fail(["references/teaching_baseline.json 结构或 schema_version 无效，拒绝缩减基线"])
        raw_map = payload.get("teaching_example_ids_by_chapter")
        raw_ids = payload.get("teaching_example_ids")
        if not isinstance(raw_map, dict) or not isinstance(raw_ids, list):
            fail(["references/teaching_baseline.json 缺少教学例题 ID 映射，拒绝缩减基线"])
        for chapter, values in raw_map.items():
            if (not isinstance(chapter, str) or not chapter.strip()
                    or not isinstance(values, list)
                    or not all(isinstance(value, str) and value.strip() for value in values)
                    or len(values) != len(set(values))):
                fail([f"teaching_baseline 的章节 {chapter!r} 含无效或重复 ID"])
            previous[chapter.strip()] = set(value.strip() for value in values)
        flattened = set().union(*previous.values()) if previous else set()
        if (not all(isinstance(value, str) and value.strip() for value in raw_ids)
                or len(raw_ids) != len(set(raw_ids))
                or flattened != set(value.strip() for value in raw_ids)):
            fail(["teaching_baseline 的逐章映射与 ID 全集不一致，拒绝缩减基线"])

    merged = {chapter: set(values) for chapter, values in previous.items()}
    for chapter, values in current_by_chapter.items():
        merged.setdefault(str(chapter), set()).update(values)
    id_to_chapter = {}
    for chapter, values in merged.items():
        for ident in values:
            other = id_to_chapter.setdefault(ident, chapter)
            if other != chapter:
                fail([f"教学例题 ID {ident} 同时属于章节 {other} 与 {chapter}，无法建立可靠基线"])
    ordered_map = {chapter: sorted(values) for chapter, values in sorted(merged.items())}
    ordered_ids = sorted(id_to_chapter)
    baseline = {
        "schema_version": 1,
        "policy": "append_only",
        "teaching_example_ids": ordered_ids,
        "teaching_example_ids_by_chapter": ordered_map,
    }
    _atomic_json(path, baseline, "教学例题保留基线")
    return baseline


def validate(data):
    """校验输入 JSON。返回 (course_name, phases, quiz_bank, teaching_examples, missing_answer_ids)。

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
    seen_phase_ids = {}
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
            normalized_phase = _positive_int(pn)
            if normalized_phase is None:
                errors.append(f"第 {idx} 个阶段的 phase_num 必须是正整数（当前 {pn!r}）。")
            else:
                p["phase_num"] = normalized_phase

        # phase_num 是复习顺序，chapter 是材料的真实章号。新输入可显式给 chapter / chapter_id /
        # phase_id；旧输入则从 chNN*.md 文件名推断真实章号，完全没有章线索时才回退 phase_num。
        raw_chapter = p.get("chapter")
        explicit_chapter = None
        if raw_chapter is not None:
            explicit_chapter = _positive_int(raw_chapter)
            if explicit_chapter is None:
                errors.append(f"第 {idx} 个阶段的 chapter 必须是正整数（当前 {raw_chapter!r}）。")

        raw_chapter_id = p.get("chapter_id")
        id_chapter = None
        if raw_chapter_id is not None:
            id_chapter = _chapter_from_id(raw_chapter_id)
            if id_chapter is None:
                errors.append(
                    f"第 {idx} 个阶段的 chapter_id 必须是 chNN/chapterNN/正整数形式"
                    f"（当前 {raw_chapter_id!r}）。"
                )

        raw_phase_id = p.get("phase_id")
        if raw_phase_id is not None:
            if isinstance(raw_phase_id, int) and not isinstance(raw_phase_id, bool) and raw_phase_id >= 1:
                raw_phase_id = "phase%02d" % raw_phase_id
            if not (isinstance(raw_phase_id, str) and raw_phase_id.strip()
                    and SAFE_ID.fullmatch(raw_phase_id.strip())):
                errors.append(
                    f"第 {idx} 个阶段的 phase_id 必须是不含路径的非空标识"
                    f"（当前 {p.get('phase_id')!r}）。"
                )
            else:
                raw_phase_id = raw_phase_id.strip()
                if raw_phase_id in seen_phase_ids:
                    errors.append(
                        f"phase_id「{raw_phase_id}」在第 {seen_phase_ids[raw_phase_id]} 和第 {idx} 个阶段重复。"
                    )
                else:
                    seen_phase_ids[raw_phase_id] = idx
                    p["phase_id"] = raw_phase_id
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

        filename_chapter = _chapter_from_wiki_filename(fn)
        chapter_hints = [value for value in (explicit_chapter, id_chapter, filename_chapter)
                         if value is not None]
        if chapter_hints and any(value != chapter_hints[0] for value in chapter_hints[1:]):
            errors.append(
                f"第 {idx} 个阶段的 chapter/chapter_id/wiki_filename 章号不一致："
                f"chapter={raw_chapter!r}, chapter_id={raw_chapter_id!r}, wiki_filename={fn!r}。"
            )
        resolved_chapter = (explicit_chapter or id_chapter or filename_chapter
                            or _positive_int(p.get("phase_num")))
        if resolved_chapter is not None:
            p["chapter"] = resolved_chapter
            p["chapter_id"] = "ch%02d" % resolved_chapter
        if "phase_id" not in p and _positive_int(p.get("phase_num")) is not None:
            p["phase_id"] = "phase%02d" % p["phase_num"]

    quiz_bank = data.get("quiz_bank", [])
    if not isinstance(quiz_bank, list):
        errors.append("quiz_bank 必须是数组。")
        quiz_bank = []

    missing_answer_ids = []
    seen_quiz_ids = {}
    for i, q in enumerate(quiz_bank):
        raw_id = q.get("id") if isinstance(q, dict) else None
        tag = str(raw_id) if not is_blank(raw_id) else f"#{i + 1}"
        if not isinstance(q, dict):
            errors.append(f"题目 {tag} 不是对象。")
            continue
        if not is_blank(raw_id):
            if (isinstance(raw_id, bool) or not isinstance(raw_id, (str, int, float))
                    or (isinstance(raw_id, float) and not math.isfinite(raw_id))):
                errors.append(f"题目 {tag} 的 id 必须是有限数字或非空字符串。")
            else:
                canonical_id = str(raw_id).strip()
                if not canonical_id or any(char in canonical_id for char in ("\x00", "\n", "\r")):
                    errors.append(f"题目 {tag} 的 id 必须是非空单行标识。")
                elif canonical_id in seen_quiz_ids:
                    errors.append(
                        f"题目 id「{canonical_id}」在第 {seen_quiz_ids[canonical_id]} "
                        f"和第 {i + 1} 道题重复。"
                    )
                else:
                    seen_quiz_ids[canonical_id] = i + 1
                    # Preserve a caller's finite numeric identifier in the
                    # public quiz bank.  Canonical string identity is used only
                    # for duplicate detection and the structured IR link.
                    if isinstance(raw_id, str):
                        q["id"] = canonical_id
        qtype = q.get("type")
        if qtype not in VALID_QUIZ_TYPES:
            errors.append(f"题目 {tag} 的 type 必须是 {'/'.join(sorted(VALID_QUIZ_TYPES))} 之一（当前为 {qtype!r}）。")
        gradable = q.get("gradable")
        if gradable is not None and not isinstance(gradable, bool):
            errors.append(f"题目 {tag} 的 gradable 必须是布尔型 true/false（当前为 {gradable!r}）。")
        is_gradable = gradable is not False
        if not q.get("question"):
            errors.append(f"题目 {tag} 缺少题干 question。")
        if is_gradable and qtype == "choice" and not q.get("options"):
            errors.append(f"选择题 {tag} 缺少 options 选项。")
        if is_gradable and is_blank(q.get("answer")):
            missing_answer_ids.append(tag)

    # Optional, backward-compatible teaching layer.  ``None`` means a legacy raw input omitted the
    # field entirely (ingest must not create/overwrite a manifest in that case); an explicit [] means
    # the producer intentionally supplied an empty teaching snapshot.
    teaching_examples = None
    if "teaching_examples" in data:
        teaching_examples = data.get("teaching_examples")
        if not isinstance(teaching_examples, list):
            errors.append("teaching_examples 必须是数组。")
            teaching_examples = []
        seen_teaching_ids = set()
        for i, ex in enumerate(teaching_examples):
            tag = f"教学例题 #{i + 1}"
            if not isinstance(ex, dict):
                errors.append(f"{tag} 不是对象。")
                continue
            ex_id = ex.get("id")
            if not isinstance(ex_id, str) or not ex_id.strip():
                errors.append(f"{tag} 缺少非空字符串 id。")
            elif ex_id in seen_teaching_ids:
                errors.append(f"重复的教学例题 id: {ex_id}")
            else:
                seen_teaching_ids.add(ex_id)
                tag = f"教学例题 {ex_id}"
            role = ex.get("teaching_role")
            if role not in VALID_TEACHING_ROLES:
                errors.append(
                    f"{tag} 的 teaching_role 必须是 "
                    f"{'/'.join(sorted(VALID_TEACHING_ROLES))} 之一（当前为 {role!r}）。"
                )
            gradable = ex.get("gradable")
            if gradable is not None and not isinstance(gradable, bool):
                errors.append(
                    f"{tag} 的 gradable 必须是布尔型 true/false（当前为 {gradable!r}）。"
                )
            if ex.get("chapter") in (None, "") and ex.get("phase") in (None, ""):
                errors.append(f"{tag} 缺少 chapter 或 phase，无法按当前章惰性列举。")
            if not isinstance(ex.get("question"), str) or not ex.get("question", "").strip():
                errors.append(f"{tag} 缺少非空教学内容 question。")
            if not isinstance(ex.get("source_file"), str) or not ex.get("source_file", "").strip():
                errors.append(f"{tag} 缺少非空字符串 source_file。")
            for field in ("source_pages", "answer_source_pages"):
                pages = ex.get(field)
                if field == "answer_source_pages" and pages is None:
                    continue
                if not (isinstance(pages, list) and pages and all(
                        isinstance(p, int) and not isinstance(p, bool) and p > 0 for p in pages)):
                    errors.append(f"{tag} 的 {field} 必须是非空正整数页码数组。")
            if ex.get("assets") is not None and not isinstance(ex.get("assets"), list):
                errors.append(f"{tag} 的 assets 必须是数组。")

    # The glossary is a live retrieval input, not decorative metadata.  Reject
    # malformed producer output instead of silently dropping part/all of query
    # expansion while still claiming a successful generation.  An explicit
    # empty object is the canonical "no glossary" snapshot.
    if "terms" in data:
        terms = data.get("terms")
        if not isinstance(terms, dict):
            errors.append("terms 必须是 {术语: [对应术语, ...]} 对象。")
        else:
            for term, equivalents in terms.items():
                if (not isinstance(term, str) or not term.strip()
                        or term != term.strip()):
                    errors.append("terms 的术语键必须是无首尾空白的非空字符串。")
                    continue
                if (not isinstance(equivalents, list) or not equivalents
                        or any(not isinstance(value, str) or not value.strip()
                               or value != value.strip()
                               for value in equivalents)
                        or len(set(equivalents)) != len(equivalents)):
                    errors.append(
                        "terms[%r] 必须是非空、无重复、无首尾空白的字符串数组。"
                        % term
                    )

    if errors:
        fail(errors)

    return course_name, phases, quiz_bank, teaching_examples, missing_answer_ids


def build_phase_table(phases, lang="zh"):
    # 插入行必须跟模板同语言（单语言纯净）：en 模板里混入 阶段/未开始 会产出混语工作区；
    # 读侧（update_progress._plan_phases / validate_workspace._plan_phase_nums）
    # 本就同时认 「阶段N」 与 「Phase N」。
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


def _argument_parser():
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
    parser.add_argument(
        "--expected-input-sha256",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def _prepare_cli_input(args):
    """Read, parse, and validate one stable input-file generation."""

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
    expected_input_sha256 = getattr(args, "expected_input_sha256", None)
    if (expected_input_sha256 is not None
            and not re.fullmatch(r"[0-9a-f]{64}", expected_input_sha256)):
        fail(["--expected-input-sha256 must be a lowercase 64-character SHA-256"])

    try:
        payload, snapshot = stable_read_bytes(args.input)
        if (expected_input_sha256 is not None
                and snapshot.get("sha256") != expected_input_sha256):
            fail([
                "input generation drifted before compilation: expected SHA-256 %s, found %s"
                % (expected_input_sha256, snapshot.get("sha256")),
            ])
        data = strict_json.loads(payload.decode("utf-8"))
    except Exception as e:
        fail([f"JSON 解析失败：{e}"])

    validated = validate(data)
    ingestion_payload = data.get("ingestion")
    if ingestion_payload is not None:
        try:
            _strict_payload(ingestion_payload)
        except Exception as exc:
            fail([f"结构化 ingestion envelope 校验/持久化失败：{exc}"])
    return data, lang_explicit, lang, validated


def _ensure_workspace_root(output_dir):
    """Create only the workspace root; validator-visible files wait for the publication lock."""

    root = os.path.abspath(output_dir)
    if os.path.lexists(root) and is_link_or_reparse(root):
        fail([f"输出工作区是符号链接，拒绝沿链接写盘：{root}"])
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        fail([f"无法创建输出目录：{exc}"])
    if not os.path.isdir(root):
        fail([f"输出路径不是目录：{root}"])
    return root


def _compile_visuals_unlocked(workspace, source_items=()):
    """Run the structured-visual core while the caller holds the ingestion lock."""

    workspace_path = Path(workspace).resolve()
    manifest = read_json(workspace_path / ".ingest" / "build_manifest.json")
    store = IngestionStore(workspace_path, source_root=manifest.get("source_root"))
    expected_units, _expected_mappings = store._expected_compiled_state()
    quiz = source_items[0] if len(source_items) > 0 else ()
    teaching = source_items[1] if len(source_items) > 1 else ()
    audit = audit_asset_policy(
        quiz_rows=quiz,
        teaching_rows=teaching,
        content_units=expected_units.values(),
    )
    problems = audit["invalid_declarations"] + audit["conflicts"]
    if problems:
        raise ValueError("asset policy failed: %s" % "; ".join(problems))
    units, _mappings = store.rebuild_compiled_from_ledger()
    return _compile_structured_visuals_core(
        workspace_path, units, _phase_inventory(workspace_path),
        tainted_keys=audit["tainted_keys"],
    )


def _before_publication_lock(_args, _output_dir):
    """No-op test seam for exercising input replacement immediately before lock acquisition."""


def _material_compiler_transaction_paths(data, phases, material_generation=None):
    """Return every mutable compiler target outside the structured fact plan."""

    paths = {
        ".ingest/material_build_pending.json",
        ".ingest/material_build_receipt.json",
        "references/quiz_bank.json",
        "references/teaching_examples.json",
        "references/teaching_baseline.json",
        "references/retrieval_index.json",
        "ingest_report.json",
        "study_plan.md",
        "study_progress.md",
    }
    for phase in phases:
        paths.add(
            "references/wiki/" + os.path.basename(
                phase["wiki_filename"].strip()
            )
        )
    # A material generation is a complete producer snapshot.  Even when its
    # raw input omits/empties `terms`, the old glossary must be transactionally
    # removed so query expansion cannot leak across generations.
    paths.add("references/terms.json")
    if isinstance(material_generation, dict):
        recovery = material_generation.get("recovery")
        if isinstance(recovery, dict) and isinstance(recovery.get("path"), str):
            paths.add(recovery["path"])
        for ancestor in material_generation.get("ancestor_recoveries") or ():
            if isinstance(ancestor, dict) and isinstance(ancestor.get("path"), str):
                paths.add(ancestor["path"])
    return sorted(paths)


def _main_unlocked(args, prepared):
    """Publish one validated input while the caller holds the required workspace locks."""

    import hashlib
    import chunk as _chunk
    import retrieve as _retrieve

    data, lang_explicit, lang, validated = prepared
    course_name, phases, quiz_bank, teaching_examples, missing_answer_ids = validated

    # ── 后处理：补全 id + 规范化 true_false 答案 ──────────────────
    TRUE_FALSE_NORMALIZE = {
        "正确": True, "对": True, "是": True, "真": True,
        "true": True, "yes": True, "√": True,
        "错误": False, "错": False, "否": False, "假": False,
        "false": False, "no": False, "×": False,
    }
    # 收集已有 id，避免补全时撞号
    existing_ids = {
        str(q["id"]).strip() for q in quiz_bank if not is_blank(q.get("id"))
    }
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
    normalized_ids = [str(q["id"]).strip() for q in quiz_bank]
    if len(set(normalized_ids)) != len(normalized_ids):
        fail(["题目 id 补全/规范化后仍有重复；拒绝写出歧义题库。"])
    # ────────────────────────────────────────────────────────────────
    # 补号后重算缺答案清单——validate 阶段无 id 的题记的是「#序号」占位，
    # 持久化报告必须指向题库里真实存在的 id，后续会话的 AI 才能定位接手
    missing_answer_ids = [
        str(q["id"]).strip() for q in quiz_bank
        if q.get("gradable") is not False and is_blank(q.get("answer"))
    ]

    # Cross-layer, pair-aware policy must pass before `_safe_output_tree`,
    # `_persist_payload_unlocked`, or any student-facing derivative is written.
    ingestion_payload = data.get("ingestion")
    try:
        # This is an in-memory producer boundary: all three source layers are
        # present before any workspace path is created or ingestion payload is
        # persisted.  The opaque capability cannot be replaced by a raw set.
        _chunk._verified_asset_policy_from_layers(
            quiz_rows=quiz_bank,
            teaching_rows=teaching_examples or (),
            content_units=(
                ingestion_payload.get("content_units", ())
                if isinstance(ingestion_payload, dict) else ()
            ),
        )
    except (TypeError, ValueError) as exc:
        fail(["asset policy failed before publication: %s" % exc])

    try:
        material_generation = authorize_material_build_generation(
            args.output_dir, data
        )
        if material_generation is None:
            verify_material_build_receipt(
                args.output_dir, raw_input=data, require_manifest_binding=False
            )
    except Exception as exc:
        fail(["material build generation authorization failed: %s" % exc])

    print(f"[+] 识别到科目: {course_name}")
    print(f"[+] 阶段数量: {len(phases)} 个")
    print(f"[+] 题目数量: {len(quiz_bank)} 道")
    if teaching_examples is not None:
        print(f"[+] 教学例题快照: {len(teaching_examples)} 条")
    if missing_answer_ids:
        print("\n[!] 注意：以下题目缺少标准答案（answer 为空）：")
        print("    " + ", ".join(missing_answer_ids))
        print("    这些题在测验时没有可对照的标准答案；请先补全，或在对话中让 AI 为它们生成答案后再录入。")
        print("    ⚠️ 若由 AI 代为生成答案，必须向学生明确标注「⚠️ AI生成答案，非老师/教材提供」，")
        print("       严禁把 AI 生成的答案伪装成老师的标准答案（详见 SKILL.md 知识来源透明化协议）。")

    output_dir = os.path.abspath(args.output_dir)
    wiki_dir = _safe_output_tree(output_dir)
    real_wiki_dir = os.path.realpath(wiki_dir)
    print(f"[+] 创建 Wiki 目录: {wiki_dir}")

    if ingestion_payload is not None:
        transaction_holder = None
        extra_transaction_paths = ()
        if material_generation is not None:
            if args.force:
                fail([
                    "--force is not supported during a pending material generation; "
                    "preserve canonical learner state and retry without --force"
                ])
            transaction_holder = getattr(
                args, "_material_transaction_holder", None
            )
            if not isinstance(transaction_holder, dict):
                fail(["material generation compiler transaction is unavailable"])
            extra_transaction_paths = _material_compiler_transaction_paths(
                data, phases, material_generation=material_generation
            )
        try:
            ingestion_build_manifest = _persist_payload_unlocked(
                output_dir,
                ingestion_payload,
                material_generation=material_generation,
                extra_transaction_paths=extra_transaction_paths,
                transaction_holder=transaction_holder,
            )
        except Exception as exc:
            fail([f"结构化 ingestion envelope 校验/持久化失败：{exc}"])
        print(
            "[+] 已写入结构化导入状态: .ingest/（%d 来源 / %d 内容单元 / %d 接管事项）"
            % (
                ingestion_build_manifest["source_count"],
                ingestion_build_manifest["unit_count"],
                ingestion_build_manifest["review_issue_count"]
                + ingestion_build_manifest["unbound_review_count"],
            )
        )

    # 1. 写入各阶段 Wiki 文件（文件名已在 validate 中校验，这里再做一次包含性断言）
    for p in phases:
        filename = os.path.basename(p["wiki_filename"].strip())
        wiki_file_path = os.path.join(wiki_dir, filename)
        # Classify a pre-existing link/special target before realpath containment.  Otherwise a
        # symlink that points outside the workspace is reported merely as a filename traversal and
        # the more important "do not follow this link" guard becomes order-dependent by platform.
        _guard_write_target(wiki_file_path, "Wiki 文件")
        if os.path.commonpath([os.path.realpath(wiki_file_path), real_wiki_dir]) != real_wiki_dir:
            fail([f"文件名「{filename}」试图写出 wiki 目录之外，已拒绝。"])
        _atomic_text(wiki_file_path, p["wiki_content"], "Wiki 文件")
        print(f"[+] 已写入 Wiki 文件: references/wiki/{filename}")

    if ingestion_payload is not None:
        try:
            visual_counts = _compile_visuals_unlocked(
                output_dir, (quiz_bank, teaching_examples or [])
            )
        except Exception as exc:
            fail([f"结构化图片编译进章节 wiki 失败：{exc}"])
        if sum(visual_counts.values()):
            print(
                "[+] 已挂载结构化资料原图: %d 张"
                % sum(visual_counts.values())
            )

    # 1b. 结构化内容单元优先切块；legacy raw input 继续使用 Markdown 小节切块。
    #     检索索引携带 wiki/source-IR 哈希，任何派生产物漂移都 fail closed。
    try:
        from ingestion.dedup import (
            CANONICAL_GROUPS_PATH as _CANONICAL_GROUPS_PATH,
            SOURCE_CONFLICTS_PATH as _SOURCE_CONFLICTS_PATH,
            load_canonical_groups as _load_canonical_groups,
        )
        from ingestion.retrieval_folding import fold_units_for_retrieval as _fold_units
    except ImportError:
        from scripts.ingestion.dedup import (
            CANONICAL_GROUPS_PATH as _CANONICAL_GROUPS_PATH,
            SOURCE_CONFLICTS_PATH as _SOURCE_CONFLICTS_PATH,
            load_canonical_groups as _load_canonical_groups,
        )
        from scripts.ingestion.retrieval_folding import fold_units_for_retrieval as _fold_units
    all_chunks = []
    wiki_by_chapter = {
        p["chapter_id"]: "references/wiki/" + os.path.basename(p["wiki_filename"].strip())
        for p in phases
    }
    structured_rows = ingestion_payload.get("content_units", []) if ingestion_payload else []
    tainted_keys = student_attempt_tainted_keys(
        structured_rows, quiz_bank, teaching_examples or []
    )
    if any(
            isinstance(item, dict)
            and has_tainted_official_asset(item, tainted_keys)
            for item in quiz_bank + (teaching_examples or [])):
        fail(["student_attempt 污染路径不能编译为题库、教学例题或检索概念"])
    if structured_rows:
        # Read the compiled store so applied review patches survive a deterministic rebuild.
        compiled_units_path = os.path.join(output_dir, ".ingest", "content_units.jsonl")
        try:
            with open(compiled_units_path, "r", encoding="utf-8") as stream:
                structured_rows = [strict_json.loads(line) for line in stream if line.strip()]
        except (OSError, ValueError) as exc:
            fail([f"结构化内容单元无法读取，拒绝构建检索索引：{exc}"])
        tainted_keys = student_attempt_tainted_keys(
            structured_rows, quiz_bank, teaching_examples or []
        )
        if any(
                isinstance(item, dict)
                and has_tainted_official_asset(item, tainted_keys)
                for item in quiz_bank + (teaching_examples or [])):
            fail(["student_attempt 污染路径不能编译为题库、教学例题或检索概念"])
        try:
            canonical_group_path = os.path.join(
                output_dir, *_CANONICAL_GROUPS_PATH.split("/")
            )
            canonical_groups = (
                _load_canonical_groups(output_dir)
                if os.path.isfile(canonical_group_path) else ()
            )
            chunk_policy = _chunk._verified_asset_policy_from_layers(
                quiz_rows=quiz_bank,
                teaching_rows=teaching_examples or [],
                content_units=structured_rows,
                canonical_groups=canonical_groups,
            )
            structured_rows = _fold_units(
                structured_rows,
                canonical_groups,
            )
        except Exception as exc:
            fail([f"canonical_group 检索折叠失败：{exc}"])
        for structured in _chunk.chunk_units(
                structured_rows, tainted_keys=chunk_policy):
            chapter_id = structured.get("chapter_id")
            if not chapter_id or chapter_id not in wiki_by_chapter:
                continue
            structured["file"] = (
                "references/quiz_bank.json"
                if structured.get("kind") == "question"
                else wiki_by_chapter[chapter_id]
            )
            structured["chapter"] = str(_chapter_from_id(chapter_id) or "")
            all_chunks.append(structured)
    else:
        for p in phases:
            filename = os.path.basename(p["wiki_filename"].strip())
            _, chapter_chunks = _chunk.chunk_text(p["wiki_content"])
            for number, current in enumerate(chapter_chunks, 1):
                all_chunks.append({
                    "id": "%s#s%02d" % (p["chapter_id"], number),
                    "file": "references/wiki/" + filename,
                    "chapter": str(p["chapter"]),
                    "chapter_id": p["chapter_id"],
                    "phase_id": p["phase_id"],
                    "title": current["title"],
                    "text": current["text"],
                })

    # Deterministic concept postings improve recall without a heavyweight vector DB.
    question_unit_ids = {}
    for unit in structured_rows:
        if (isinstance(unit, dict) and unit.get("kind") == "question"
                and unit.get("external_id")
                and unit.get("asset_role") != STUDENT_ATTEMPT
                and not has_tainted_official_asset(unit, tainted_keys)):
            question_unit_ids.setdefault(str(unit["external_id"]), []).extend(
                unit.get("retrieval_occurrence_unit_ids") or [unit.get("unit_id")]
            )

    for item in quiz_bank:
        points = item.get("knowledge_points")
        if isinstance(points, str):
            points = [points]
        if not isinstance(points, list):
            point = item.get("knowledge_point")
            points = [point] if isinstance(point, str) else []
        points = [point.strip() for point in points if isinstance(point, str) and point.strip()]
        if not points:
            continue
        chapter = _positive_int(item.get("chapter"))
        chapter_id = "ch%02d" % chapter if chapter else None
        file_name = wiki_by_chapter.get(chapter_id, "references/quiz_bank.json")
        all_chunks.append({
            "id": "concept:%s" % item["id"],
            "file": file_name,
            "chapter": str(chapter) if chapter else None,
            "chapter_id": chapter_id,
            "title": "Knowledge points",
            "text": "\n".join(points + [str(item.get("question") or "")]),
            "kind": "concept",
            "source_file": item.get("source_file"),
            "pages": item.get("source_pages") or [],
            "unit_ids": sorted(set(question_unit_ids.get(str(item.get("id")), ()))),
        })

    # Bind the exact teaching layer that will exist after this invocation.  An
    # explicit snapshot has not been published yet, so hash the byte-identical
    # `_atomic_json` representation; a legacy rerun instead binds the existing
    # preserved file.  Resolve this before publishing the index so an unsafe
    # existing target cannot leave a newly written but unusable index behind.
    teaching_path = os.path.join(output_dir, "references", "teaching_examples.json")
    teaching_integrity = None
    if os.path.lexists(teaching_path) and (
            is_link_or_reparse(teaching_path) or not os.path.isfile(teaching_path)):
        fail([f"教学例题索引目标是符号链接或特殊文件，拒绝读取/覆盖：{teaching_path}"])
    if teaching_examples is not None:
        teaching_payload = (
            json.dumps(teaching_examples, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        teaching_integrity = {
            "file": "references/teaching_examples.json",
            "sha256": hashlib.sha256(teaching_payload).hexdigest(),
        }
    elif os.path.isfile(teaching_path):
        with open(teaching_path, "rb") as stream:
            teaching_integrity = {
                "file": "references/teaching_examples.json",
                "sha256": hashlib.sha256(stream.read()).hexdigest(),
            }

    # A glossary changes query expansion even though its terms are not embedded
    # in the BM25 postings.  Bind the exact bytes that will coexist with this
    # index so a later edit cannot silently change retrieval semantics.  Legacy
    # inputs that omit ``terms`` preserve and bind an existing safe glossary;
    # material generations that omit/empty it intentionally publish no binding
    # because the same transaction removes the old file below.
    terms = data.get("terms")
    terms_path = os.path.join(output_dir, "references", "terms.json")
    if os.path.lexists(terms_path) and (
            is_link_or_reparse(terms_path) or not os.path.isfile(terms_path)):
        fail([f"术语索引目标是符号链接或特殊文件，拒绝读取/覆盖：{terms_path}"])
    terms_payload = None
    terms_integrity = None
    if isinstance(terms, dict) and terms:
        terms_payload = (
            json.dumps(terms, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        terms_integrity = {
            "file": "references/terms.json",
            "sha256": hashlib.sha256(terms_payload).hexdigest(),
        }
    elif material_generation is None and os.path.isfile(terms_path):
        with open(terms_path, "rb") as stream:
            terms_integrity = {
                "file": "references/terms.json",
                "sha256": hashlib.sha256(stream.read()).hexdigest(),
            }

    integrity = {
        "wiki": [
            {
                "file": "references/wiki/" + os.path.basename(p["wiki_filename"].strip()),
                "sha256": _file_sha256(os.path.join(
                    output_dir,
                    "references",
                    "wiki",
                    os.path.basename(p["wiki_filename"].strip()),
                )),
            }
            for p in phases
        ],
        "phases": [
            {
                "chapter": p["chapter"], "chapter_id": p["chapter_id"],
                "phase_num": p["phase_num"], "phase_id": p["phase_id"],
                "wiki_file": "references/wiki/" + os.path.basename(p["wiki_filename"].strip()),
            }
            for p in phases
        ],
        "quiz_bank": {
            "file": "references/quiz_bank.json",
            "sha256": hashlib.sha256(
                (json.dumps(quiz_bank, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            ).hexdigest(),
        },
    }
    if teaching_integrity is not None:
        integrity["teaching_examples"] = teaching_integrity
    if terms_integrity is not None:
        integrity["terms"] = terms_integrity
    for key, relative in (
        ("source_manifest", ".ingest/source_manifest.json"),
        ("content_units", ".ingest/content_units.jsonl"),
        ("canonical_groups", _CANONICAL_GROUPS_PATH),
        ("source_conflicts", _SOURCE_CONFLICTS_PATH),
    ):
        absolute = os.path.join(output_dir, relative.replace("/", os.sep))
        if os.path.isfile(absolute) and not is_link_or_reparse(absolute):
            with open(absolute, "rb") as stream:
                integrity[key] = {
                    "file": relative,
                    "sha256": hashlib.sha256(stream.read()).hexdigest(),
                }
    if all_chunks:
        index = _retrieve.build_index(all_chunks, integrity=integrity)
        _atomic_json(
            os.path.join(output_dir, "references", "retrieval_index.json"),
            index,
            "检索索引",
        )
        print(f"[+] 已建检索索引: references/retrieval_index.json（{len(all_chunks)} 块 / {len(phases)} 章）")
    # 术语对照（跨语言检索桥）：raw_input.json 可带顶层 "terms"（AI 建库时产出、可人工校对）
    if isinstance(terms, dict) and terms:
        _atomic_json(
            terms_path,
            terms,
            "术语索引",
        )
        print(f"[+] 已写入术语对照: references/terms.json（{len(terms)} 组）")
    elif material_generation is not None and os.path.lexists(terms_path):
        _guard_write_target(terms_path, "术语索引")
        if not os.path.isfile(terms_path):
            fail(["references/terms.json 不是可安全移除的常规文件"])
        os.unlink(terms_path)
        print("[+] 本代未声明术语对照；已移除上一代 references/terms.json")

    # 2. 写入题库 JSON
    quiz_file_path = os.path.join(output_dir, "references", "quiz_bank.json")
    _atomic_json(quiz_file_path, quiz_bank, "题库")
    print("[+] 已写入题库文件: references/quiz_bank.json")

    # 2b. Optional teaching-example snapshot.  Absence of the top-level raw-input field is the
    # legacy compatibility signal: do not create or overwrite a manifest that the caller did not
    # explicitly provide.  The official material builder always emits the field (including []).
    if teaching_examples is not None:
        _atomic_json(teaching_path, teaching_examples, "教学例题索引")
        print("[+] 已写入教学例题索引: references/teaching_examples.json")

    # 导入报告持久化——缺答案清单只留在控制台会随会话丢失，后续会话的 AI 无从接手
    _teaching_list = teaching_examples or []
    _teaching_manifest_preserved = False
    if (teaching_examples is None and os.path.isfile(teaching_path)
            and not is_link_or_reparse(teaching_path)):
        # Legacy rerun: preserve not only the file but also its retention baseline.  Replacing the
        # report IDs with [] would make validate_workspace unable to notice a later deletion from
        # both quiz_bank and the teaching layer.
        try:
            with open(teaching_path, "r", encoding="utf-8") as tf:
                _existing_teaching = strict_json.load(tf)
            if (isinstance(_existing_teaching, list)
                    and all(isinstance(ex, dict) and isinstance(ex.get("id"), str)
                            and ex["id"].strip() for ex in _existing_teaching)):
                _teaching_list = _existing_teaching
                _teaching_manifest_preserved = True
                print("[+] legacy raw input 未带 teaching_examples；已保留现有教学例题索引与报告基线")
            else:
                print("[!] 现有 references/teaching_examples.json 结构无效；文件未覆盖，但报告无法沿用其基线")
        except (OSError, ValueError) as e:
            print(f"[!] 现有 references/teaching_examples.json 无法读取；文件未覆盖，但报告无法沿用其基线: {e}")
    _teaching_by_chapter = {}
    _teaching_ids_by_chapter = {}
    for _ex in _teaching_list:
        _ch = _ex.get("chapter") if _ex.get("chapter") is not None else _ex.get("phase")
        _key = str(_ch)
        _teaching_by_chapter[_key] = _teaching_by_chapter.get(_key, 0) + 1
        _teaching_ids_by_chapter.setdefault(_key, []).append(_ex["id"])
    teaching_baseline_path = os.path.join(
        output_dir, "references", "teaching_baseline.json")
    teaching_baseline = _merge_teaching_baseline(
        teaching_baseline_path, _teaching_ids_by_chapter)
    ingest_report = {
        "course_name": course_name, "phases": len(phases), "quiz_bank": len(quiz_bank),
        "teaching_examples": len(_teaching_list),
        "current_teaching_example_ids": [ex["id"] for ex in _teaching_list],
        "current_teaching_example_ids_by_chapter": _teaching_ids_by_chapter,
        "teaching_baseline_manifest": "references/teaching_baseline.json",
        "teaching_example_ids": teaching_baseline["teaching_example_ids"],
        "teaching_examples_by_chapter": _teaching_by_chapter,
        # Keep the ID→chapter baseline, not only aggregate counts.  If a later AI review removes an
        # example from both the gradable bank and the teaching layer, phase completion can now identify
        # exactly which chapter lost required teaching evidence and fail closed.
        "teaching_example_ids_by_chapter": teaching_baseline[
            "teaching_example_ids_by_chapter"],
        "teaching_manifest_preserved_from_legacy_rerun": _teaching_manifest_preserved,
        "missing_answer_ids": missing_answer_ids,
        "note": "missing_answer_ids 的题没有标准答案：测验前需补全，或由 AI 生成并向学生明确标注"
                "「⚠️ AI生成答案，非老师/教材提供」。",
    }
    _atomic_json(
        os.path.join(output_dir, "ingest_report.json"),
        ingest_report,
        "导入报告",
    )
    print("[+] 已写入导入报告: ingest_report.json")

    if ingestion_payload is not None:
        derived = {
            "quiz_bank": "references/quiz_bank.json",
            "teaching_examples": "references/teaching_examples.json",
            "teaching_baseline": "references/teaching_baseline.json",
            "retrieval_index": "references/retrieval_index.json",
            "ingest_report": "ingest_report.json",
        }
        if isinstance(terms, dict) and terms:
            derived["terms"] = "references/terms.json"
        for phase in phases:
            derived["wiki:%s" % phase["chapter_id"]] = (
                "references/wiki/" + os.path.basename(phase["wiki_filename"].strip())
            )
        try:
            refresh_build_manifest(output_dir, derived)
        except Exception as exc:
            fail([f".ingest/build_manifest.json 派生产物完整性刷新失败：{exc}"])

        # Rebuilding the deterministic base files above must not erase terminal,
        # evidence-validated review work.  Recompile the append-only ledger before
        # validation so issue status and student-facing artifacts cannot diverge.
        ledger_path = os.path.join(output_dir, ".ingest", "review_patches.jsonl")
        if os.path.isfile(ledger_path) and os.path.getsize(ledger_path) > 0:
            try:
                compiled = _compile_review_outputs_unlocked(output_dir)
            except Exception as exc:
                fail([f"已应用 ingestion review patch 重新编译失败：{exc}"])
            print(
                "[+] 已重放审核补丁: wiki %d 条 / 题库 %d 项 / 检索 %d 块"
                % (
                    sum(compiled["recovered_units_by_chapter"].values()),
                    compiled["quiz_updates"],
                    compiled["retrieval_chunks"],
                )
            )

    # 3. 生成 study_plan.md（可重复生成，无用户状态）
    plan_content = render_template(
        "study_plan_template.md",
        {SUBJECT_TOKEN: f"《{course_name}》", PHASE_TABLE_MARKER: build_phase_table(phases, lang)},
        markers=[PHASE_TABLE_MARKER],
        lang=lang,
    )
    plan_out_path = os.path.join(output_dir, "study_plan.md")
    _atomic_text(plan_out_path, plan_content, "复习计划")
    print("[+] 已生成: study_plan.md")

    # 4. 生成 study_progress.md（含断点与错题本，是用户状态，默认不覆盖）
    progress_out_path = os.path.join(output_dir, "study_progress.md")
    if os.path.lexists(progress_out_path):
        _guard_write_target(progress_out_path, "复习进度")
    if os.path.isfile(progress_out_path) and not args.force:
        print(f"[!] 已存在 {progress_out_path}，为保护你的复习进度与错题本，未覆盖它。")
        print("    如确实要重新生成，请加 --force（会先自动备份旧文件）。")
    else:
        if os.path.isfile(progress_out_path):  # --force：先备份再覆盖
            backup = f"{progress_out_path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
            try:
                with open(progress_out_path, "rb") as old_progress:
                    old_progress_bytes = old_progress.read()
            except OSError as exc:
                fail([f"无法读取待备份的旧进度文件：{exc}"])
            _atomic_bytes(backup, old_progress_bytes, "复习进度备份")
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
        _atomic_text(progress_out_path, prog_content, "复习进度")
        print("[+] 已生成: study_progress.md")

    if material_generation is not None:
        try:
            finalize_material_build_generation(output_dir, material_generation)
        except Exception as exc:
            fail(["material build generation finalization failed: %s" % exc])

    return course_name


def main():
    args = _argument_parser().parse_args()
    output_dir = _ensure_workspace_root(args.output_dir)
    ingest_path = os.path.join(output_dir, ".ingest")
    ingest_preexisting = os.path.lexists(ingest_path)
    _before_publication_lock(args, output_dir)

    material_transaction_holder = {}
    args._material_transaction_holder = material_transaction_holder
    compiled_course_name = None
    try:
        with workspace_publication_lock(
                output_dir, allow_material_generation=True):
            try:
                prepared = _prepare_cli_input(args)
                data = prepared[0]
                structured = data.get("ingestion") is not None
                if ingest_preexisting and not os.path.lexists(ingest_path):
                    raise ConflictError(".ingest changed while acquiring the publication lock")
                if structured and not ingest_preexisting:
                    # A brand-new structured workspace has no ingestion lock for
                    # workspace_publication_lock to acquire.  Create and hold it under
                    # the already-held state lock before the first validator-visible write.
                    with IngestionStore(output_dir).mutation_lock():
                        compiled_course_name = _main_unlocked(args, prepared)
                else:
                    compiled_course_name = _main_unlocked(args, prepared)
                transaction = material_transaction_holder.pop("context", None)
                if transaction is not None:
                    transaction.__exit__(None, None, None)
            except BaseException:
                transaction = material_transaction_holder.pop("context", None)
                if transaction is not None:
                    transaction.__exit__(*sys.exc_info())
                raise
    except ConflictError as exc:
        fail([f"工作区发布冲突，未写入任何课程产物：{exc}"])
    print(f"\n[+] 《{compiled_course_name}》工作区工程编译完成。")
    print("[!] 编译成功不等于教学就绪；请运行 validate_workspace.py，或使用 ingest_course.py 的 readiness 结果。")


if __name__ == "__main__":
    main()
