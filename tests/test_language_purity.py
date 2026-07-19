# -*- coding: utf-8 -*-
"""C2 — bidirectional language-purity lint (Phase-6 doctrine). Stdlib only.

Lands as tests/test_language_purity.py.

Doctrine (Phase 6 — user override of the old anchor-invariance design):
  * Student-visible prose is strictly SINGLE-LANGUAGE per mode:
      - 中文 mode  → zero English prose,
      - English mode → zero CJK prose,
      - 双语 mode  → zh block first + a `> EN:` mirror line per block (the mirror
        line is the sanctioned English zone inside zh-canonical documents).
  * The trilingual first-ask language line 「语言 / Language：中文 / English / 双语」
    is the ONLY allowed mixed point — in zh output it travels inside 「…」; on en
    surfaces 「…」 is NOT exempt: quote the zh option values in code spans instead.
  * Persisted files / script output stay Chinese-canonical (machine vocabulary);
    English mode PARAPHRASES them in English, it never mutates them on disk.
  * Always exempt in every mode: inline code spans, filenames, commands,
    JSON keys/values, CLI flags, emoji (language-neutral symbols).
  * benchmark detectors are OUT of scope here — they keep testing zh-mode
    transcripts only; nothing in this module touches benchmark/.

Four enforcement blocks:
  T1  en-surface purity  — locales/en/SKILL.md + prompts/web_prompt.en.md (plus
      the per-skill en packs, see EN_SURFACE_FILES) carry ZERO CJK
      outside (a) inline code spans and (b) fenced-code-block lines that invoke
      official scripts. A fenced ```markdown block in web_prompt.en.md is PROSE:
      only its script command lines and inline code spans are exempt. NO token
      whitelist of any kind.
  T2  zh-output purity   — prescribed-output literals in registered zh files /
      '## Student-facing Output' sections contain no English prose. Parameterized
      by ZH_OUTPUT_PURITY_TARGETS (starts EMPTY — see the constant); machinery is
      self-tested on synthetic documents so the block is not vacuous meanwhile.
  T3  en canonical vocabulary pins — the EN vocabulary from C2_design_notes.md is
      pinned (table-driven) into docs/language-policy.md.
  T4  reverse lock — is_pure_en() rejects ANY CJK in a synthetic English-mode
      transcript; is_pure_zh_output() rejects English prose (e.g. the retired
      'question-side asset' label) in a synthetic zh transcript.

Expected state on the day this module is authored (2026-07-05):
  * T1 is RED until C2b rewrites both .en entry files (today they carry ~50 CJK
    prose lines — see C0 audit §1). EN_SURFACE_FILES is a module constant so the
    landing PR can stage it if the module must merge green ahead of C2b.
  * T2 SKIPS (roster empty by design); its machinery self-tests are green.
  * T3 is RED until C2a's docs/language-policy.md vocabulary section lands —
    this module and that edit belong to the SAME C2a PR, so at landing it is green.
  * T4 and all machinery self-tests are green from day one.

--------------------------------------------------------------------------------
EXISTING-TEST SURGERY LIST — what C2b/C2c must edit or retire
(source: C2_design_notes.md 已知测试手术清单; verified against tests/ on disk):

  1. tests/test_en_mode_shapes.py         — RETIRE / replace the whole group.
     The "feed the zh detector an en transcript" tests contradict Phase-6 (en
     output must contain NO zh anchors). Replacement = EN-vocabulary shape tests
     + the reverse lock, both provided here (T3 + T4). EN_SURFACE_TOKENS dies.
  2. tests/test_language_policy.py        — rewrite class A8cEnEntrypoints: its
     "zh anchors present in .en files" pins INVERT into zero-CJK purity pins
     (now T1 here) + EN-vocab presence pins. CANONICAL_FILES: the two .en files
     pin the three EN label sentences instead of the zh ones (zh files keep the
     zh three). NO_OLD_ENTRYPOINTS stays unchanged.
  3. tests/test_visual_asset_contract.py  — the bilingual 「题面图 / question-side
     asset」 pin splits BY LANGUAGE: zh files pin 题面图, en files pin
     Question-side asset; RUNTIME_CONTRACT_FILES gets language grouping.
  4. tests/test_control_plane_language.py — ALLOWED_TOKENS big slim-down: every
     token that existed only to escape gloss shapes (token+gloss) inside en/
     bilingual surfaces becomes dead once C2b lands; test_allowed_tokens_all_alive
     will name the corpses. Add SFO en-block purity hookup where relevant.
  5. tests/test_source_taxonomy.py + tests/test_study_state.py — their
     ENTRY_POINTS pins expect zh 临时覆盖 / 范围偏好 / study_state wording inside
     the .en files; after C2b the .en files pin the EN equivalents instead
     ("Temporarily overriding" / "scope preference").
  6. tests/test_localization_boundary.py (+ docs/localization.md) —
     REQUIRED_LABELS entries get language-attribution comments (which surface
     owns which language) — no behavioral change.
  7b. tests/test_language_policy.py class A8bLanguageDispatch — pins the ABOLISHED
     token+gloss literals ("① 题面图 (Question figure)" in exam-tutor SFO,
     "已记录到错题本 (recorded to the mistake archive)" in exam-quiz SFO,
     "LANGUAGE-INVARIANT" in exam-cram): C2c must rewrite these pins in the same
     commit that cleans each SFO — otherwise T2 registration and these pins are
     mutually unsatisfiable.
  7c. AGENTS.md language bullet ("…stay verbatim with a trailing gloss") + root
     SKILL.md dispatch line — C2b rewrites both to the single-language doctrine.
  7d. README.md 「锚点与防编题规则同款」 wording — C2b sync.
  7. tests/test_behavior_smoke.py — scenario mocks only: update en/双语 sample
     transcripts (language_persist_ok expected_lang=English path, urgent-opening
     mock wording). The detectors themselves DO NOT change (zh-only, benchmark
     doctrine).
--------------------------------------------------------------------------------
"""
import glob
import os
import re
import sys
import unittest

# Windows consoles may default to a non-UTF-8 code page; messages below carry CJK.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def _find_root():
    """Repo root. Landed location is tests/test_language_purity.py → parent of tests/.
    EXAMPREP_ROOT overrides for standalone runs from outside the repo (scratchpad)."""
    cand = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.exists(os.path.join(cand, "locales", "en", "SKILL.md")):
        return cand
    env = os.environ.get("EXAMPREP_ROOT")
    if env and os.path.exists(os.path.join(env, "locales", "en", "SKILL.md")):
        return env
    return cand


ROOT = _find_root()


def read_rel(relpath):
    with open(os.path.join(ROOT, *relpath.split("/")), encoding="utf-8") as f:
        return f.read()


# =============================================================================
# Character classes
# =============================================================================
# The en-side CJK detector. Derived from tests/test_control_plane_language.py
# CJK_RE (U+3400-4DBF Ext-A, U+4E00-9FFF Unified, U+3000-303F punct, selected
# fullwidth marks, ①-⑳, 【】) and DELIBERATELY extended:
#   + U+3000-303F  full block (、。「」『』【】《》〜 + ideographic space — the ask)
#   + U+FF00-FFEF  full block (！（），：；？｜ fullwidth latin, halfwidth kana — the ask)
#   + U+2E80-2EFF  CJK radicals supplement
#   + U+3040-30FF  hiragana + katakana (zero-CJK means zero-CJK, not zero-Han)
#   + U+3100-312F  bopomofo
#   + U+31C0-31EF  CJK strokes
#   + U+F900-FAFF  CJK compatibility ideographs
#   + U+FE10-FE1F  vertical presentation forms
#   + U+FE30-FE4F  CJK compatibility forms
#   + U+1100-11FF / U+3130-318F / U+AC00-D7FF / U+A960-A97F Hangul（零 CJK ≠ 零 Han——R1）
#   + U+3190-319F Kanbun, U+3200-33FF enclosed CJK, U+2F00-2FDF Kangxi radicals,
#     U+FE20-FE2F combining half marks, U+FE50-FE6F small form variants
#   + U+20000-3FFFF supplementary Han (Ext-B..I + compat supplement, all of Plane 2-3)
# and DELIBERATELY narrowed — these stay ALLOWED because they are language-neutral:
#   - U+2460-24FF enclosed alphanumerics: the ①-⑦ seven-step ordinals survive in
#     EN output ("① Question figure"), so unlike the control-plane lint we do NOT
#     ban circled digits;
#   - U+FE00-FE0F variation selectors: U+FE0F is the emoji presentation selector
#     inside ⚠️ — banning it would ban the amber label's emoji;
#   - emoji blocks themselves (⚠ U+26A0, ✅❌, 🟢🟡 U+1F7E1/2, 📊⏱️ …) are outside
#     every range above by construction.
EN_CJK_RE = re.compile(
    u"["
    u"⺀-⻿"
    u"　-〿"
    u"぀-ヿ"
    u"㄀-ㄯ"
    u"㇀-㇯"
    u"㐀-䶿"
    u"一-鿿"
    u"豈-﫿"
    u"︐-︟"
    u"︰-﹏"
    u"＀-￯"
    u"ᄀ-ᇿ"
    u"㄰-㆏"
    u"㆐-㆟"
    u"ㆠ-ㆿ"
    u"㈀-㋿"
    u"㌀-㏿"
    u"가-힯"
    u"ힰ-퟿"
    u"ꥠ-꥿"
    u"︠-︯"
    u"﹐-﹫"
    u"⼀-⿟"
    u"\U00020000-\U0003ffff"
    u"]")

CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
FENCE_RE = re.compile(r"^\s*```")
_FENCE_LINE_RE = re.compile(r"^\s*(`{3,})")   # D4：记录围栏长度，同长或更长才收栏

# Fenced-code-block COMMAND lines stay exempt in both directions (machine surface,
# e.g. `python … set --language 双语` in an en file). Only true command lines qualify
# (python-invocations); a fenced PROSE line that merely MENTIONS a script filename is
# NOT exempt (D2) — on the zh side script names are token-exempted by ZH_EXEMPT_RE's
# filename pattern while the surrounding prose is still scanned.
SCRIPT_CMD_RE = re.compile(r"^\s*(?:\$ ?)?python(?:3)?\b")


# =============================================================================
# T1 machinery — en-surface purity (NO whitelist)
# =============================================================================
# v4-P2 layout: the en full-entry pack lives at locales/en/SKILL.md (SKILL.en.md
# retired) and the per-skill en packs live under locales/en/skills/. All of them
# are strict zero-CJK-outside-code-spans surfaces EXCEPT confusion-tracker.md,
# whose fenced example deliberately shows the PERSISTED zh-canonical progress
# table (machine vocabulary that never drifts with the reply language) — that
# file plus locales/en/templates/*.md (which keep the 《科目名称》 machine anchor)
# are covered by tests/test_language_packs.py with the persisted-content
# structural strips instead of joining this strict roster.
EN_SURFACE_FILES = tuple(
    ["locales/en/SKILL.md", "prompts/web_prompt.en.md"]
    + sorted(
        os.path.relpath(p, ROOT).replace("\\", "/")
        for p in glob.glob(os.path.join(ROOT, "locales", "en", "skills", "*.md"))
        if os.path.basename(p) != "confusion-tracker.md"
    )
)


def en_purity_offenses(text):
    """[(lineno, offending_chars, snippet)] — CJK found in en-surface text.

    Exemptions (structural only, no token whitelist):
      * inline code spans `…` — stripped everywhere (also inside fenced blocks:
        the web prompt's fenced ```markdown block renders as markdown once pasted
        into the web AI, so its inline spans are real code spans);
      * fenced-block lines matching SCRIPT_CMD_RE (script invocations);
      * fence marker lines themselves (``` / ```markdown — never carry prose).
    Everything else — INCLUDING every other line inside a fenced ```markdown
    block, which is PROSE by doctrine — is scanned.
    """
    out = []
    fence_len = 0
    for n, line in enumerate(text.splitlines(), 1):
        f = _FENCE_LINE_RE.match(line)
        if f:
            ticks = len(f.group(1))
            if fence_len == 0:
                fence_len = ticks           # 开栏（info string 不算 prose）
                continue
            if ticks >= fence_len:
                fence_len = 0               # 收栏（CommonMark：同长或更长才关）
                continue
            # 更短的内层围栏行 = 外层块的内容行，落到下方照常扫描
        if fence_len and SCRIPT_CMD_RE.search(line):
            continue
        visible = CODE_SPAN_RE.sub("", line)
        hits = EN_CJK_RE.findall(visible)
        if hits:
            out.append((n, "".join(hits)[:16], visible.strip()[:80]))
    return out


def is_pure_en(output_text):
    """True iff an English-mode student-visible transcript carries zero CJK
    outside inline code spans. (T4 reverse lock helper — also the primitive
    tests/test_en_mode_shapes.py's replacement builds on.)"""
    return not en_purity_offenses(output_text)


# =============================================================================
# T2 machinery — zh-output purity
# =============================================================================
# ------------------------------------------------------------------ ROSTER --
# v4-P2 layout: the zh full-entry pack (the former root SKILL.md body) lives at
# locales/zh/SKILL.md; the former '## Student-facing Output' sections of the
# sub-skills live as zh packs under locales/zh/skills/ (no exam-audit — it has
# no student-facing surface by design). Packs that keep the
# '## Student-facing Output' heading register as "sfo" (the section body is the
# student copy; the preamble note above it is agent-facing meta); the two packs
# whose whole body is the student copy (exam-cheatsheet / confusion-tracker,
# no SFO heading) register as "file".
# ("file" = whole file minus YAML frontmatter; "sfo" = the file's
#  '## Student-facing Output' section only.)
ZH_OUTPUT_PURITY_TARGETS = [
    ("locales/zh/SKILL.md", "file"),
    ("prompts/web_prompt.md", "file"),
    ("locales/zh/skills/exam-cram.md", "sfo"),
    ("locales/zh/skills/exam-help.md", "sfo"),
    ("locales/zh/skills/exam-ingest.md", "sfo"),
    ("locales/zh/skills/exam-quiz.md", "sfo"),
    ("locales/zh/skills/exam-review.md", "sfo"),
    ("locales/zh/skills/exam-tutor.md", "sfo"),
    ("locales/zh/skills/exam-cheatsheet.md", "file"),
    ("locales/zh/skills/confusion-tracker.md", "file"),
]

# English-prose detector: any run of >=2 latin letters left after stripping.
EN_WORD_RE = re.compile(r"[A-Za-z]{2,}")

# The 双语 mirror line — the one sanctioned English zone inside zh documents.
EN_MIRROR_RE = re.compile(r"^\s*(?:>\s*)+EN[:：]")

# 「…」 handling (R1)：引号**不再**整段豁免——否则「Hint」「language / English」这类引号英文
# 会逃逸。唯一豁免 = 三语语言选择行字面（政策定义的唯一混语点）；其余引号内容照常扫描
#（纯 zh 引号自然通过；引号里的文件名/AI 等由 ZH_EXEMPT_RE 按 token 豁免）。
LANGUAGE_CHOOSER = u"「语言 / Language：中文 / English / 双语」"
CORNER_QUOTE_RE = re.compile(u"「[^」]*」")

# [text](target) — drop the target, keep the student-visible text.
LINK_TARGET_RE = re.compile(r"\]\([^)\s]*\)")


def _latin_token(tok):
    # CJK chars count as \w in unicode re, so \b fails at a latin↔CJK seam
    # ("AI补充" has no \b after AI). Latin-only lookarounds are the correct fence.
    return r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(tok)


# Tight exemption set (idea from scratchpad c0_audit.py EXEMPT, deliberately
# tightened). Machine vocabulary is expected to live in code spans — this regex
# only covers what LEGITIMATELY appears bare in prescribed zh output:
_EXEMPT_PATTERNS = [
    # URLs
    r"https?://[^\s)>]+",
    # filenames incl. page anchors: hw02.pdf, ch1_xxx.md, lecture03.pdf#page=12
    r"[\w.\\/-]+\.(?:py|md|json|jsonl|pdf|png|jpe?g|gif|svg|txt|csv|html?|ya?ml)(?:#page=\d+)?(?![A-Za-z])",
    # repo-relative directory paths
    r"(?<![A-Za-z])(?:references|scripts|templates|docs|skills|prompts)[/\\][\w.\\/-]*",
    # CLI flags + env placeholders
    r"--[\w=-]+",
    r"\$\{?[A-Za-z_]+\}?",
    # source_type enum values, ONLY in their source-block slot （<source_type>）
    # — machine vocab persisted in quiz_bank.json; bare occurrences stay flagged
    u"(?<=页)（(?:homework|exam|lecture|textbook|notes|slides)）",   # D3：仅来源块「第N页（…）」槽位
    # single-token technical notation (policy: O(n)/DNA/pH「按符号对待，不算 prose」).
    # CURATED allowlist only — NOT a blanket all-caps rule (that would exempt English prose
    # like "NEVER ASK QUESTIONS"). Any acronym not listed here goes in a code span in zh output.
    _latin_token("DNA"), _latin_token("RNA"), _latin_token("mRNA"), _latin_token("tRNA"),
    _latin_token("ATP"), _latin_token("ADP"), _latin_token("NADH"), _latin_token("PCR"),
    _latin_token("GDP"), _latin_token("GNP"), _latin_token("CPI"), _latin_token("IS"), _latin_token("LM"),
    _latin_token("CPU"), _latin_token("GPU"), _latin_token("RAM"), _latin_token("ROM"),
    _latin_token("API"), _latin_token("URL"), _latin_token("SQL"), _latin_token("XML"),
    _latin_token("CSS"), _latin_token("UML"), _latin_token("FSM"), _latin_token("BFS"),
    _latin_token("DFS"), _latin_token("LIFO"), _latin_token("FIFO"), _latin_token("NP"),
    _latin_token("pH"), _latin_token("pKa"), _latin_token("Hz"), _latin_token("Pa"), _latin_token("Kb"),
    # format / notation names (language-neutral tech nouns)
    _latin_token("JSON"), _latin_token("Markdown"), _latin_token("Mermaid"),
    _latin_token("ASCII"), _latin_token("YAML"), _latin_token("UTF-8"),
    _latin_token("PDF"), _latin_token("PNG"), _latin_token("HTML"),
    _latin_token("Python"), _latin_token("Wiki"), _latin_token("LLM"),
    # canonical-label letters & schema-ish initialisms that sit inside zh tokens
    _latin_token("AI"),   # 🟡 AI补充… / ⚠️ AI生成答案… are canonical zh vocabulary
    _latin_token("ID"),   # 题目 ID（quiz_bank 条目号）
    # host / product names
    _latin_token("Claude Code"), _latin_token("Claude"), _latin_token("ChatGPT"),
    _latin_token("DeepSeek"), _latin_token("Cursor"), _latin_token("Windsurf"),
    _latin_token("Codex"), _latin_token("Antigravity"),
]
# NOT exempt on purpose (each is a C2 cleanup target): Hint, Cheat Sheet, gloss
# parentheticals like (recorded to the mistake archive), decorative EN titles,
# bare schema fields outside code spans, bare 'English' (belongs in `English`),
# 'question-side asset' / 'answer-side asset' (zh label is 题面图 / 答案图).
ZH_EXEMPT_RE = re.compile("|".join("(?:%s)" % p for p in _EXEMPT_PATTERNS))


def zh_output_offenses(text, first_line=1, allow_mirror=True):
    """[(lineno, [words], snippet)] — English prose found in prescribed zh output.

    Per-line pipeline: fence tracking (marker lines skipped; fenced script
    command lines skipped; other fenced lines are prescribed output → scanned)
    → skip `> EN:` mirror lines → strip 「…」 → strip inline code spans → strip
    markdown link targets → strip ZH_EXEMPT_RE → flag [A-Za-z]{2,} runs.
    """
    out = []
    fence_len = 0
    for n, line in enumerate(text.splitlines(), first_line):
        f = _FENCE_LINE_RE.match(line)
        if f:
            ticks = len(f.group(1))
            if fence_len == 0:
                fence_len = ticks
                continue
            if ticks >= fence_len:
                fence_len = 0
                continue
            # 内层短围栏 = 内容行，继续扫描
        if fence_len and SCRIPT_CMD_RE.search(line):
            continue
        m = EN_MIRROR_RE.match(line)
        if m:
            if not allow_mirror:
                # mono 中文 模式：不该出现 `> EN:` 镜像行本身
                out.append((n, [u"<mirror-in-mono-zh>"], line.strip()[:80]))
                continue
            # 双语镜像行必须纯英文（R1：不再无条件豁免）——code span 外出现 CJK 即违规
            mirror_visible = CODE_SPAN_RE.sub("", line[m.end():])
            cjk = EN_CJK_RE.findall(mirror_visible)
            if cjk:
                out.append((n, [u"<CJK-in-EN-mirror>"] + cjk[:5], line.strip()[:80]))
            continue
        s = line.replace(LANGUAGE_CHOOSER, "")   # 唯一豁免的混语点（R1：引号不再整段豁免）
        s = CODE_SPAN_RE.sub("", s)
        s = LINK_TARGET_RE.sub("]", s)
        s = ZH_EXEMPT_RE.sub(" ", s)
        words = EN_WORD_RE.findall(s)
        if words:
            out.append((n, words[:6], line.strip()[:80]))
    return out


def is_pure_zh_output(output_text):
    """True iff a **mono `中文`-mode** student-visible transcript carries zero English prose
    (T4 reverse lock helper). A `> EN:` mirror line is ITSELF a violation here — pure 中文
    mode has no bilingual mirrors (those belong to 双语 composition, validated by T5). File
    scanning uses zh_output_offenses(allow_mirror=True) instead, which tolerates a mirror line
    but still requires it to be pure English."""
    return not zh_output_offenses(output_text, allow_mirror=False)


def split_frontmatter(text):
    """(frontmatter, rest) — same contract as test_control_plane_language.py."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            nl = text.find("\n", end + 1)
            if nl == -1:
                return text, ""
            return text[:nl + 1], text[nl + 1:]
    return "", text


SFO_HEADING_RE = re.compile(r"(?m)^## Student-facing Output[^\n]*$")
NEXT_H2_RE = re.compile(r"(?m)^## ")


def sfo_section(text):
    """(body, first_line) of the '## Student-facing Output' section, else (None, 0).
    Fence-aware (D1): a literal '## ' line INSIDE a fenced example (e.g. confusion-tracker's
    study_progress.md table header) is content, not the next heading."""
    m = SFO_HEADING_RE.search(text)
    if not m:
        return None, 0
    start = m.end()
    first = text.count("\n", 0, start) + 1
    fence_len = 0
    pos = 0
    for ln in text[start:].splitlines(keepends=True):
        f = _FENCE_LINE_RE.match(ln)
        if f:
            ticks = len(f.group(1))
            if fence_len == 0:
                fence_len = ticks
            elif ticks >= fence_len:
                fence_len = 0
        elif fence_len == 0 and ln.startswith("## "):
            break
        pos += len(ln)
    return text[start:start + pos], first


def zh_target_offenses(text, scope):
    """Apply zh-output purity to a registered target.
    scope='file' → whole file minus YAML frontmatter (the frontmatter description
    is the trigger surface and legitimately carries English trigger keywords);
    scope='sfo'  → only the '## Student-facing Output' section (fail loud if absent).
    """
    if scope == "file":
        fm, body = split_frontmatter(text)
        return zh_output_offenses(body, first_line=fm.count("\n") + 1)
    if scope == "sfo":
        body, first = sfo_section(text)
        if body is None:
            return [(0, ["<missing '## Student-facing Output' section>"], "")]
        return zh_output_offenses(body, first_line=first)
    raise ValueError("unknown scope: %r" % scope)


# =============================================================================
# T3 — EN canonical vocabulary (from C2_design_notes.md, C2a pins it into
# docs/language-policy.md). Byte-exact: ASCII apostrophe in "What's", em dash
# U+2014 in the labels, ASCII hyphen in "3-minute".
# =============================================================================
EN_CANONICAL_VOCAB = (
    # provenance labels — three FULL sentences (label = whole sentence, never emoji alone)
    ("provenance-green", u"🟢 From your materials"),
    ("provenance-yellow", u"🟡 AI-supplemented — may differ from what your teacher taught"),
    ("provenance-amber", u"⚠️ AI-generated answer — not from your teacher or textbook"),
    # the seven-step block heads (ordinals ①-⑦ are language-neutral and stay)
    ("step-1", u"① Question figure"),
    ("step-2", u"② What's being asked"),
    ("step-3", u"③ What to read off the figure"),
    ("step-4", u"④ Core formula"),
    ("step-5", u"⑤ Step-by-step solution"),
    ("step-6", u"⑥ Why this answer works"),
    ("step-7", u"⑦ Source trace"),
    # source block
    ("source-block-q", u"Question source:"),
    ("source-block-a", u"Answer source:"),
    # closers (default-off; same trigger rule as zh)
    ("closer-pitfalls", u"Common pitfalls"),
    ("closer-mnemonic", u"3-minute mnemonic"),
    ("closer-your-turn", u"Your turn"),
    # receipts + stage anchor
    ("receipt-mistake", u"Recorded to the mistake archive"),
    ("receipt-confusion", u"Recorded to the confusion log"),
    ("stage-anchor", u"Stage N"),
    ("stage-resume", u"Resuming from Stage N"),
    ("source-unknown", u"Source unknown"),
    ("source-page-unknown", u"Source page unknown"),
    # abstention
    ("abstention", u"The materials do not contain an answer to this question."),
    # scope override
    ("scope-override", u"⚠️ Temporarily overriding your <scope> scope preference"),
    # asset labels
    ("asset-question", u"Question-side asset"),
    ("asset-answer", u"Answer-side asset"),
    # progress panel field names
    ("panel-subject", u"Subject"),
    ("panel-now", u"Current stage"),
    ("panel-progress", u"Progress"),
    ("panel-mistakes", u"Mistake log"),
)

LANGUAGE_POLICY = "docs/language-policy.md"


# =============================================================================
# Tests
# =============================================================================
class T1EnSurfacePurity(unittest.TestCase):
    """en 面零 CJK：无白名单，仅结构性豁免（行内代码 span / fenced 脚本命令行）。"""

    def test_en_entry_files_zero_cjk(self):
        bad = []
        for rel in EN_SURFACE_FILES:
            for n, chars, snippet in en_purity_offenses(read_rel(rel)):
                bad.append("%s L%d [%s] %s" % (rel, n, chars, snippet))
        self.assertFalse(
            bad,
            u"en 面残留 CJK（含全角标点/CJK 标点；仅豁免行内代码 span 与 fenced 脚本命令行，"
            u"无 token 白名单）：\n" + "\n".join(bad))

    # ---- machinery self-tests (keep green regardless of file state) ----

    def test_machinery_fenced_markdown_block_is_prose(self):
        # the web prompt's ```markdown block is PROSE: only script command lines
        # and inline code spans inside it are exempt.
        sample = "\n".join([
            u"```markdown",
            u"Reply 「提示」 to get a hint.",                            # L2 prose → CJK punct trips
            u"python scripts/update_progress.py set --language 双语",   # L3 command line → exempt
            u"Say `跳过` to skip the current item.",                    # L4 inline span → exempt
            u"```",
        ])
        self.assertEqual([o[0] for o in en_purity_offenses(sample)], [2],
                         u"fenced markdown 块应按 prose 扫描：只豁免脚本命令行与行内代码 span")

    def test_machinery_fullwidth_and_cjk_punct_trip(self):
        # the deliberate range extension: U+3000-303F + U+FF00-FFEF
        for ch in (u"，", u"（", u"）", u"【", u"】", u"「", u"」", u"、",
                   u"｜", u"：", u"《", u"》", u"　"):
            self.assertTrue(EN_CJK_RE.search(ch),
                            u"全角/CJK 标点 %r 应被 en 面判违规" % ch)

    def test_machinery_language_neutral_symbols_stay_allowed(self):
        # emoji + circled digits are language-neutral and must NOT trip
        for ch in (u"⚠", u"️", u"🟢", u"🟡", u"✅", u"❌", u"📊",
                   u"①", u"②", u"③", u"④", u"⑤", u"⑥", u"⑦", u"—", u"…"):
            self.assertFalse(EN_CJK_RE.search(ch),
                             u"语言中性符号 %r 不应被 en 面判违规" % ch)


class T2ZhOutputPurity(unittest.TestCase):
    """zh 输出字面零英文 prose：按 ZH_OUTPUT_PURITY_TARGETS 逐文件接入（C2b/C2c）。"""

    def test_registered_targets_are_pure(self):
        if not ZH_OUTPUT_PURITY_TARGETS:
            self.skipTest(u"C2a 脚手架：目标清单为空——C2b/C2c 清一个文件、登记一个文件")
        bad = []
        for rel, scope in ZH_OUTPUT_PURITY_TARGETS:
            path = os.path.join(ROOT, *rel.split("/"))
            self.assertTrue(os.path.exists(path), u"清单里登记了不存在的文件: %s" % rel)
            for n, words, snippet in zh_target_offenses(read_rel(rel), scope):
                bad.append("%s L%d %s | %s" % (rel, n, ",".join(map(str, words)), snippet))
        self.assertFalse(
            bad,
            u"zh 输出字面出现英文 prose（结构性豁免：代码 span/「…」/链接目标/`> EN:` 镜像行/"
            u"fenced 脚本命令行/紧口径 EXEMPT）：\n" + "\n".join(bad))

    # ---- machinery self-tests: the block is not vacuous while the roster is empty ----

    def test_machinery_flags_english_prose_in_zh_document(self):
        doc = "\n".join([
            u"# 通用期末考试极速备考教练指令 (Universal Exam Cram Coach - LLM Wiki Edition)",
            u"",
            u"1. **生成 Cheat Sheet**：全员通关后生成小抄。",
            u"![题面图 / question-side asset](references/assets/ch02_p12_fig.png)",
        ])
        off = zh_target_offenses(doc, "file")
        self.assertEqual([o[0] for o in off], [1, 3, 4],
                         u"装饰性英文标题 / Cheat Sheet / 双语资产标签 都应被抓到（LLM/Wiki 属豁免）: %r" % off)

    def test_machinery_accepts_clean_zh_document(self):
        doc = "\n".join([
            u"---",
            u'description: "帮助学生备考（关键词：期末/备考；exam, cram, study plan, quiz, review）"',
            u"---",
            u"# 期末极速备考",
            u"",
            u"存在 `study_state.json` 时一律经官方脚本更新进度。",
            u"题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料",
            u"详见 [文件格式](docs/file-format.md)。",
            u"在 Claude Code 中可直接运行官方脚本。",
            u"",
            u"```markdown",
            u"备考科目：《数据结构》",
            u"python scripts/update_progress.py set --language 中文",
            u"```",
        ])
        self.assertEqual(zh_target_offenses(doc, "file"), [],
                         u"frontmatter 触发词/文件名/（source_type）/链接目标/宿主名/fenced 命令行 均为豁免面")

    def test_machinery_corner_quote_no_longer_blanket_exempt(self):
        # R1：引号英文照抓；唯一豁免 = 语言选择行字面
        self.assertTrue(zh_output_offenses(u"请回复「Hint」获取提示。"))
        self.assertTrue(zh_output_offenses(u"回答「language / English」即可。"))
        self.assertEqual(zh_output_offenses(u"回答「语言 / Language：中文 / English / 双语」即可切换语言。"), [])
        self.assertEqual(zh_output_offenses(u"学生主动要求（「有什么易错点」「给我个口诀」）才输出。"), [])

    def test_machinery_mirror_lines_validated_not_skipped(self):
        # R1：镜像行含 CJK 即违规；纯英文镜像照常豁免
        self.assertTrue(zh_output_offenses(u"> EN: ① 题面图"))
        self.assertTrue(zh_output_offenses(u"> EN: Recorded to 错题本."))
        self.assertEqual(zh_output_offenses(u"> EN: Recorded to the mistake archive."), [])
        self.assertEqual(zh_output_offenses(u"> EN: Persist it with `set --language 双语`."), [])

    def test_machinery_hangul_and_extra_cjk_blocks_trip(self):
        # R1：零 CJK ≠ 零 Han——韩文/围字/康熙部首都算
        for s in (u"This line has 한국어.", u"ㄱ", u"㈜", u"㊣", u"⼀"):
            self.assertFalse(is_pure_en(s), u"%r 应被 en 面判违规" % s)

    def test_machinery_sfo_scope_extraction(self):
        doc = "\n".join([
            u"## Workflow",
            u"English control prose is fine here — out of scope for this lint.",
            u"",
            u"## Student-facing Output",
            u"已记录到错题本。",
            u"当前阶段：阶段 2 (Current stage: Stage 2)",   # ← gloss must be caught
            u"",
            u"## Boundaries",
            u"More English control prose, also out of scope.",
        ])
        off = zh_target_offenses(doc, "sfo")
        self.assertEqual([o[0] for o in off], [6],
                         u"sfo 口径应只扫 Student-facing Output 段并给出正确行号: %r" % off)
        missing = zh_target_offenses(u"## Workflow\nno sfo here\n", "sfo")
        self.assertTrue(missing and missing[0][0] == 0,
                        u"登记为 sfo 的文件缺 Student-facing Output 段时必须 fail-loud")


class T3EnCanonicalVocabulary(unittest.TestCase):
    """en canonical 词表（C2_design_notes.md）钉进 docs/language-policy.md。"""

    def test_vocab_pinned_in_language_policy(self):
        policy = read_rel(LANGUAGE_POLICY)
        missing = ["%s: %r" % (key, lit)
                   for key, lit in EN_CANONICAL_VOCAB if lit not in policy]
        self.assertFalse(
            missing,
            u"%s 缺 en canonical 词表条目（C2a 应与本测试同 PR 落地；字节精确，"
            u"ASCII 撇号/em dash U+2014/ASCII 连字符）：\n" % LANGUAGE_POLICY
            + "\n".join(missing))

    def test_vocab_is_itself_pure_en(self):
        # self-consistency: every pinned EN literal must be emittable in en mode
        bad = ["%s: %r" % (key, lit)
               for key, lit in EN_CANONICAL_VOCAB if not is_pure_en(lit)]
        self.assertFalse(bad, u"en 词表条目自身必须通过 en 纯度检查：\n" + "\n".join(bad))


class T4ReverseLock(unittest.TestCase):
    """反向锁：en 输出含任意 CJK 即败；zh 输出含英文 prose（如 question-side asset）即败。"""

    EN_GOOD = (
        u"① Question figure: rendered above.",
        u"② What's being asked: state the tested point in plain words.",
        u"🟢 From your materials — see `references/wiki/ch02_linear_list.md`.",
        u"⚠️ AI-generated answer — not from your teacher or textbook",
        u"Recorded to the mistake archive. Stage 2 stays current.",
        u"The materials do not contain an answer to this question.",
        u"Persist it with `update_progress.py set --language 双语`.",  # CJK in code span = exempt
        u"⚠️ Temporarily overriding your homework-only scope preference",
    )
    EN_BAD = (
        u"① 题面图 (Question figure): rendered above.",          # legacy token+gloss shape
        u"Question source: hw02.pdf 第 3 页",                    # Han
        u"题目来源：…｜答案来源：…｜🟢 来自资料",                    # zh source block emitted in en mode
        u"Reply 「提示」 to get a hint.",                         # CJK corner brackets outside a span
        u"Progress: [██░░░░░░] 25%（stage 2/8 cleared）",        # fullwidth parens
        u"Recorded to the mistake archive（已记录到错题本）",
    )
    ZH_GOOD = (
        u"④ 核心公式：这题依赖的公式与定理，逐符号讲清含义与单位。",
        u"题目来源：hw02.pdf 第 3 页（homework）｜答案来源：hw02_sol.pdf 第 1 页｜🟢 来自资料",
        u"⚠️ AI生成答案，非老师/教材提供",                          # canonical label ('AI' exempt)
        u"已记录到错题本。当前阶段：阶段 2。",
        u"⚠️ 临时覆盖你的 <范围> 范围偏好",
        u"进度打卡：[██░░░░░░] 25%（第 2/8 阶段已通关）",
        u"存在 `study_state.json` 时一律经 `update_progress.py` 更新进度。",
        u"回答「语言 / Language：中文 / English / 双语」即可切换语言。",  # the ONLY mixed point, inside 「…」
    )
    ZH_BAD = (
        u"这里有一个 question-side asset 需要先展示。",             # retired bilingual label
        u"![题面图 / question-side asset](references/assets/ch02_p12_fig.png)",
        u"已记录到错题本 (recorded to the mistake archive)",       # trailing gloss
        u"当前阶段：阶段 2 (Current stage: Stage 2 — Linear Lists)",
        u"### 第三步：标准真题通关测验 (Quiz-Bank Assessment)",     # decorative EN title
        u"指出逻辑漏洞并给出提示（Hint）",
        u"请回复「Hint」获取当前测试线索。",              # R1：引号英文不再豁免
        u"> EN: This should not appear in a mono 中文 transcript.",   # R3/T4：mono-zh 拒镜像
        u"> EN: 已记录到错题本 (Recorded).",              # R1：镜像行 CJK 违规
    )

    def test_is_pure_en_accepts_clean_english(self):
        for s in self.EN_GOOD:
            self.assertTrue(is_pure_en(s), u"纯英文输出被误判: %r → %r"
                            % (s, en_purity_offenses(s)))

    def test_is_pure_en_rejects_any_cjk(self):
        for s in self.EN_BAD:
            self.assertFalse(is_pure_en(s), u"en 输出含 CJK 却未被反向锁拦下: %r" % s)

    def test_is_pure_zh_output_accepts_canonical_zh(self):
        for s in self.ZH_GOOD:
            self.assertTrue(is_pure_zh_output(s), u"canonical zh 输出被误判: %r → %r"
                            % (s, zh_output_offenses(s)))

    def test_is_pure_zh_output_rejects_english_prose(self):
        for s in self.ZH_BAD:
            self.assertFalse(is_pure_zh_output(s),
                             u"zh 输出含英文 prose 却未被反向锁拦下: %r" % s)


class T5BilingualComposition(unittest.TestCase):
    """双语组合锁（承接自已退役的 tests/test_en_mode_shapes.py）：zh 行纯中文、`> EN:` 镜像行
    纯英文、锚点每侧各一次，且 zh 侧仍可被**未修改的** zh 探测器解析（判分层只测 zh 的前提）。
    旧文件的 token+gloss 组（EN_SEVEN_STEP/EN_OVERRIDE/EN_LABELS/反向翻译锁）随锚点不变性废除。"""

    BI_SEVEN_STEP = """题目 [#q1] 什么是链表？

① 题面图：
本题无图，直接看题干条件。
> EN: No figure — read the given conditions.

② 这题在问什么：链表的定义与内存布局。
> EN: The definition and memory layout of a linked list.

③ 图里要读的量：无图——从题干读两个给定条件。
> EN: n/a — read the two given conditions from the prompt.

④ 核心公式：节点 = (值, `next` 指针)；访问代价 O(n)。
> EN: node = (value, next pointer); access cost O(n).

⑤ 逐步演算：从头指针出发，沿 `next` 走三步，遇 `NULL` 停。
> EN: Start from the head pointer, follow next three times, stop at NULL.

⑥ 为什么这个答案成立：遍历三个节点，与给定长度一致，因此结论与题目条件相符。
> EN: Why this answer works: traversing three nodes matches the given length, so the conclusion fits the prompt.

⑦ 知识点溯源：第 2 章《线性表》 · references/wiki/ch02_linear_list.md · 原文 [lec03.pdf 第 12 页](../lec03.pdf#page=12)
> EN: Chapter 2 Linear Lists · wiki ch02 · original page 12 of lec03.pdf

题目来源：lec03.pdf 第 12 页（lecture）｜答案来源：老师·教材提供｜🟢 来自资料
> EN: Question source: lec03.pdf p.12 (lecture) | Answer source: the teacher/textbook | 🟢 From your materials
"""

    def test_each_side_is_pure(self):
        # zh 侧：用 allow_mirror=True 扫描（镜像行须纯英文，零 CJK-in-mirror）
        self.assertEqual(zh_output_offenses(self.BI_SEVEN_STEP, allow_mirror=True), [],
                         repr(zh_output_offenses(self.BI_SEVEN_STEP, allow_mirror=True)))
        mirrors = 0
        for ln in self.BI_SEVEN_STEP.splitlines():
            if EN_MIRROR_RE.match(ln):
                mirrors += 1
                mirror = re.sub(u"^\\s*(?:>\\s*)+EN[:：]\\s*", "", ln)
                self.assertTrue(is_pure_en(mirror), mirror)
        # 防退化：双语组合每个编号块后都要有 `> EN:` 镜像——至少 7 条（七步块）
        self.assertGreaterEqual(mirrors, 7, u"双语组合镜像数退化——每块须跟一条 `> EN:` 镜像")
        for tok in (u"① 题面图", u"② 这题在问什么", u"⑦ 知识点溯源"):
            self.assertEqual(self.BI_SEVEN_STEP.count(tok), 1, tok)   # 锚点每侧一次、不双份

    def test_zh_detector_still_parses_bilingual(self):
        sys.path.insert(0, os.path.join(ROOT, "benchmark", "behavior_smoke"))
        import run_behavior_smoke as B
        self.assertTrue(B.teaching_template_ok(self.BI_SEVEN_STEP),
                        u"双语组合的 zh 侧必须仍被未修改的七步探测器接受")
        for tok in (u"① 题面图", u"② 这题在问什么", u"⑦ 知识点溯源"):
            self.assertEqual(self.BI_SEVEN_STEP.count(tok), 1, tok)


# =============================================================================
# C3 — no internal stage codenames in runtime surfaces
# =============================================================================
# Roadmap codenames (A2/A4/A7/A8b/B4/D1/P0/T5 …) are contributor-side labels; a
# model that reads them into context can parrot 「按 A2 契约…」 to students. Runtime
# files must NOT carry them outside code spans. Exempt: docs/ (the codename↔behavior
# map lives there), CHANGELOG.md, benchmark/. Digit is REQUIRED so 「B 树」/「A.」 option
# labels do not false-positive.
_CODENAME_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])(A[1-9][a-z]?|B[1-9]x?|C[0-5][a-z]?|D[1-5]|P[0-3][A-Za-z]?|T[1-5][a-z]?)"
    r"(?![A-Za-z0-9_])")   # C 系列（C0-C5/C2c…）是阶段6自身的路标，必须钉

_RUNTIME_CODENAME_FILES = (
    ["SKILL.md", "AGENTS.md", "prompts/web_prompt.md", "prompts/web_prompt.en.md",
     "locales/zh/SKILL.md", "locales/en/SKILL.md"]
    + ["skills/%s/SKILL.md" % s for s in
       ("exam-cram", "exam-tutor", "exam-quiz", "exam-review", "exam-cheatsheet",
        "exam-ingest", "exam-audit", "exam-help", "confusion-tracker")]
    # the language packs + templates are runtime text too — codename lint covers them all
    + sorted(os.path.relpath(p, ROOT).replace("\\", "/")
             for pat in ("locales/*/skills/*.md", "locales/*/templates/*.md")
             for p in glob.glob(os.path.join(ROOT, *pat.split("/")))))


class C3NoStageCodenames(unittest.TestCase):
    """运行时面零阶段代号（docs/CHANGELOG/benchmark 豁免；代码 span 内豁免）。"""

    def test_runtime_files_have_no_stage_codenames(self):
        bad = []
        for rel in _RUNTIME_CODENAME_FILES:
            for n, line in enumerate(read_rel(rel).splitlines(), 1):
                for m in _CODENAME_RE.finditer(CODE_SPAN_RE.sub("", line)):
                    bad.append("%s L%d [%s] %s" % (rel, n, m.group(1), line.strip()[:70]))
        self.assertFalse(bad, u"运行时面残留内部阶段代号（应改行为描述名；代码 span/docs/CHANGELOG "
                              u"豁免）：\n" + "\n".join(bad))

    def test_lint_machinery_fires_and_exempts(self):
        # 机制自测：代码 span 内豁免、真代号被抓、B 树/A. 不误伤
        def hits(s):
            return [m.group(1) for m in _CODENAME_RE.finditer(CODE_SPAN_RE.sub("", s))]
        self.assertEqual(hits(u"范围过滤契约（A2）：默认混合池"), ["A2"])
        self.assertEqual(hits(u"见 C2c 语言分离、C3 去代号"), ["C2c", "C3"])   # C 系列被抓
        self.assertEqual(hits(u"P0A / P0D 阶段"), ["P0A", "P0D"])   # P 字母后缀被抓
        self.assertEqual(hits(u"存在 `A4 结构化状态` 时"), [])          # 代码 span 豁免
        self.assertEqual(hits(u"二叉树/B 树、AVL 旋转"), [])            # 无数字不误伤
        self.assertEqual(hits(u"选项 A. 与 B. 二选一"), [])            # 选项标签不误伤


class C2cEnRenderingBlocksArePureEnglish(unittest.TestCase):
    """C2c/v4-P2：旧 `### English rendering` 子块已拆出为 en 语言包 —— exam-tutor / exam-quiz
    的英文学生可见样例现在整文件住在 locales/en/skills/ 下，必须零 CJK（代码 span 除外），
    token+gloss 旧形态已废除。"""

    EN_RENDER_FILES = ("locales/en/skills/exam-tutor.md", "locales/en/skills/exam-quiz.md")

    def test_en_rendering_packs_zero_cjk(self):
        for rel in self.EN_RENDER_FILES:
            off = en_purity_offenses(read_rel(rel))
            self.assertFalse(off, u"%s 的 en 学生侧文案包残留 CJK（代码 span 外）：%r" % (rel, off[:6]))

    def test_en_rendering_packs_use_en_vocab(self):
        for rel in self.EN_RENDER_FILES:
            block = read_rel(rel)
            # 至少携带来源块行英文形与一个 EN 来源标签句（防退化回中文样例）
            self.assertIn("Question source:", block, rel)
            self.assertTrue(any(lbl in block for _, lbl in EN_CANONICAL_VOCAB
                                if lbl.startswith(u"🟢") or lbl.startswith(u"⚠️")), rel)


if __name__ == "__main__":
    unittest.main()
