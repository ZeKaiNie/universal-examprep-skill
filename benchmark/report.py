#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Report generation: scored results -> a USER-FACING, BILINGUAL web report with charts.

Audience = end users, so the tone is plain and the visuals carry the message. Every metric
is hyperlinked to the authoritative benchmark it is grounded in, and a References section
lists them — so the numbers are credible, not just cool-sounding.

Zero dependencies: charts are hand-rolled SVG (no matplotlib). The HTML page ships BOTH
Chinese and English with a one-click language toggle (the project will get an English
version, so bilingual is built in from the start). Produces in results/:
  report.html  — the polished bilingual page (inline SVG + citations + 中文/English toggle)
  report.md    — a compact markdown version
  chart_*.svg  — chart images (zh/en variants)
"""

import os
import html
import stats as S

C_BASE, C_SKILL, C_INK, C_MUTE, C_GRID = "#9aa0a6", "#1a7f64", "#202124", "#5f6368", "#e8eaed"

# 每个指标对标的权威基准： key -> [(显示名, URL), ...]
CITES = {
    "faithfulness": [("RAGAS faithfulness", "https://arxiv.org/abs/2309.15217"),
                     ("FACTS Grounding", "https://arxiv.org/abs/2501.03200")],
    "hallucination": [("Vectara HHEM", "https://github.com/vectara/hallucination-leaderboard"),
                      ("HalluLens", "https://arxiv.org/abs/2504.17550")],
    "correctness": [("TRUE (NAACL 2022)", "https://arxiv.org/abs/2204.04991")],
    "numeric": [],  # 确定性精确判分，无需 LLM 基准
    "abstention": [("RGB negative rejection", "https://arxiv.org/abs/2309.01431"),
                   ("SimpleQA abstention", "https://arxiv.org/abs/2411.04368")],
}

# 完整参考文献（Tier-1 + Tier-2），(标题, URL, 中文一句, 英文一句)
REFERENCES = [
    ("FACTS Grounding (Google DeepMind, 2025)", "https://arxiv.org/abs/2501.03200",
     "仅依据给定文档作答的有据性基准——与本测试最贴合。", "Grounding answers to a supplied document — the closest analogue to our setting."),
    ("Vectara HHEM Hallucination Leaderboard", "https://github.com/vectara/hallucination-leaderboard",
     "“只据原文”的幻觉率，附开源分类器 HHEM-2.1。", "Source-only hallucination rate, with the open HHEM-2.1 classifier."),
    ("RAGAS: Faithfulness (Es et al., 2023)", "https://arxiv.org/abs/2309.15217",
     "忠实度 = 答案被上下文支持的论断占比。", "Faithfulness = share of answer claims supported by the context."),
    ("RGB: Retrieval-Augmented Generation Benchmark (Chen et al., AAAI 2024)", "https://arxiv.org/abs/2309.01431",
     "负向拒答 / 弃答能力。", "Negative rejection / abstention under retrieval."),
    ("HalluLens (Bang et al., ACL 2025)", "https://arxiv.org/abs/2504.17550",
     "intrinsic / extrinsic 幻觉分类法。", "Intrinsic vs. extrinsic hallucination taxonomy."),
    ("TRUE: Factual Consistency Evaluation (Honovich et al., NAACL 2022)", "https://arxiv.org/abs/2204.04991",
     "忠于来源的事实一致性度量（NLI）。", "Faithfulness / NLI factual-consistency metric."),
    ("SimpleQA (Wei et al., OpenAI 2024)", "https://arxiv.org/abs/2411.04368",
     "弃答与置信度校准评测协议。", "Abstention & confidence-calibration protocol."),
]

# 测试用名校公开课资料：(课程, 机构, 中文学科, 英文学科, URL)
MATERIALS = [
    ("6.006 Introduction to Algorithms", "MIT OCW", "算法", "Algorithms", "https://ocw.mit.edu/courses/6-006-introduction-to-algorithms-spring-2020/"),
    ("18.06 Linear Algebra (G. Strang)", "MIT OCW", "线性代数", "Linear Algebra", "https://ocw.mit.edu/courses/18-06sc-linear-algebra-fall-2011/"),
    ("8.01SC Classical Mechanics", "MIT OCW", "物理·力学", "Physics", "https://ocw.mit.edu/courses/8-01sc-classical-mechanics-fall-2016/"),
    ("PHIL 176 Death (S. Kagan)", "Open Yale", "哲学", "Philosophy", "https://oyc.yale.edu/death/phil-176"),
    ("PSYC 110 Introduction to Psychology (P. Bloom)", "Open Yale", "心理学", "Psychology", "https://oyc.yale.edu/introduction-psychology/psyc-110"),
    ("HIST 116 The American Revolution (J. Freeman)", "Open Yale", "历史", "History", "https://oyc.yale.edu/history/hist-116"),
]

# metric key -> (中文名, 英文名, 中文人话, 英文人话, aggregate键)
METRICS = [
    ("faithfulness", "忠实度", "Faithfulness",
     "AI 有没有照着你的材料说话，不瞎编。越高越好。",
     "Whether the AI sticks to your own materials instead of making things up. Higher is better.", "faithfulness"),
    ("hallucination", "幻觉率", "Hallucination rate",
     "有多少题 AI 说了材料里没有 / 与材料矛盾的话。越低越好。",
     "Share of questions where the AI states things absent from or contradicting the materials. Lower is better.", "hallucination"),
    ("correctness", "正确率", "Correctness",
     "答案和标准答案对得上的比例。越高越好。",
     "Share of answers that match the gold answer. Higher is better.", "correctness"),
    ("numeric", "计算题准确率", "Numeric accuracy",
     "数值 / 计算题算对的比例（由程序精确判分，不靠 AI 评）。越高越好。",
     "Share of numeric problems solved correctly (graded deterministically, not by an LLM). Higher is better.", "numeric_accuracy"),
    ("abstention", "越界弃答率", "Abstention (out-of-scope)",
     "材料没讲的问题，AI 有没有老实说“不知道”而不是硬编。越高越好——最能体现防幻觉。",
     "On questions the materials don't cover, whether the AI honestly says ‘not covered’ instead of fabricating. Higher is better — the clearest sign of anti-hallucination.", "abstention"),
]


def aggregate(scored, arm):
    js = [r[arm] for r in scored]
    answerable = [j for j in js if j["answerable"]]
    numeric = [j for j in js if j["answer_type"] == "numeric" and j["answerable"]]
    unans = [j for j in js if not j["answerable"]]
    return {
        "n": len(js),
        "faithfulness": S.mean([j["faithfulness"] for j in answerable]) if answerable else None,
        "hallucination": S.mean([j["hallucinated"] for j in js]),
        "correctness": S.mean([1.0 if j["correct"] else 0.0 for j in answerable]) if answerable else None,
        "numeric_accuracy": S.mean([1.0 if j["correct"] else 0.0 for j in numeric]) if numeric else None,
        "abstention": S.mean([1.0 if j["abstained"] else 0.0 for j in unans]) if unans else None,
    }


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def _cite_html(key):
    items = CITES.get(key, [])
    if not items:
        return ""
    return ", ".join(f'<a href="{u}" target="_blank" rel="noopener">{html.escape(n)}↗</a>' for n, u in items)


# ---------------- SVG charts ----------------
def _svg_grouped_bar(title, cats, base_vals, skill_vals, leg_base, leg_skill, w=680, h=400):
    pl, pr, pt, pb = 56, 24, 54, 88
    pw, ph = w - pl - pr, h - pt - pb
    n = max(1, len(cats))
    gw = pw / n
    bw = min(46, gw * 0.30)
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">']
    s.append(f'<text x="{w/2}" y="28" text-anchor="middle" font-size="16" font-weight="700" fill="{C_INK}">{html.escape(title)}</text>')
    for frac in (0.0, 0.5, 1.0):
        y = pt + ph * (1 - frac)
        s.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{w-pr}" y2="{y:.1f}" stroke="{C_GRID}"/>')
        s.append(f'<text x="{pl-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="{C_MUTE}">{int(frac*100)}%</text>')
    for i, cat in enumerate(cats):
        cx = pl + gw * (i + 0.5)
        for val, color, off in ((base_vals[i], C_BASE, -0.55), (skill_vals[i], C_SKILL, 0.55)):
            if val is None:
                continue
            bh = ph * val
            x = cx + off * bw - bw / 2
            y = pt + ph - bh
            s.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{color}"/>')
            s.append(f'<text x="{x+bw/2:.1f}" y="{y-5:.1f}" text-anchor="middle" font-size="11.5" font-weight="600" fill="{color}">{_pct(val)}</text>')
        s.append(f'<text x="{cx:.1f}" y="{pt+ph+18:.1f}" text-anchor="middle" font-size="11.5" fill="{C_INK}">{html.escape(cat)}</text>')
    ly = h - 26
    s.append(f'<rect x="{pl}" y="{ly}" width="13" height="13" rx="2" fill="{C_BASE}"/>')
    s.append(f'<text x="{pl+18}" y="{ly+11}" font-size="12" fill="{C_MUTE}">{html.escape(leg_base)}</text>')
    s.append(f'<rect x="{pl+185}" y="{ly}" width="13" height="13" rx="2" fill="{C_SKILL}"/>')
    s.append(f'<text x="{pl+203}" y="{ly+11}" font-size="12" fill="{C_MUTE}">{html.escape(leg_skill)}</text>')
    s.append('</svg>')
    return "\n".join(s)


def _svg_delta(title, labels, deltas, los, his, zero_label, unit, w=680, h=300):
    pl, pr, pt, pb = 150, 96, 50, 44
    pw, ph = w - pl - pr, h - pt - pb
    lim = max(0.2, max(abs(v) for v in (los + his + deltas))) * 1.15
    def X(v):
        return pl + pw * (v + lim) / (2 * lim)
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">']
    s.append(f'<text x="{w/2}" y="26" text-anchor="middle" font-size="16" font-weight="700" fill="{C_INK}">{html.escape(title)}</text>')
    x0 = X(0)
    s.append(f'<line x1="{x0:.1f}" y1="{pt}" x2="{x0:.1f}" y2="{pt+ph}" stroke="{C_MUTE}" stroke-dasharray="4 3"/>')
    s.append(f'<text x="{x0:.1f}" y="{pt+ph+22:.1f}" text-anchor="middle" font-size="11" fill="{C_MUTE}">{html.escape(zero_label)}</text>')
    rh = ph / max(1, len(labels))
    for i, lab in enumerate(labels):
        cy = pt + rh * (i + 0.5)
        color = C_SKILL if deltas[i] > 0 else "#c0392b"
        s.append(f'<line x1="{X(los[i]):.1f}" y1="{cy:.1f}" x2="{X(his[i]):.1f}" y2="{cy:.1f}" stroke="{color}" stroke-width="3"/>')
        for v in (los[i], his[i]):
            s.append(f'<line x1="{X(v):.1f}" y1="{cy-6:.1f}" x2="{X(v):.1f}" y2="{cy+6:.1f}" stroke="{color}" stroke-width="2"/>')
        s.append(f'<circle cx="{X(deltas[i]):.1f}" cy="{cy:.1f}" r="5" fill="{color}"/>')
        s.append(f'<text x="{pl-12}" y="{cy+4:.1f}" text-anchor="end" font-size="12.5" fill="{C_INK}">{html.escape(lab)}</text>')
        s.append(f'<text x="{min(w-pr+6, X(his[i])+8):.1f}" y="{cy+4:.1f}" font-size="11.5" font-weight="600" fill="{color}">{deltas[i]*100:+.0f} {html.escape(unit)}</text>')
    s.append('</svg>')
    return "\n".join(s)


def _verdict(scored):
    hb = [r["baseline"]["hallucinated"] for r in scored]
    hs = [r["skill"]["hallucinated"] for r in scored]
    fb = [r["baseline"]["faithfulness"] for r in scored]
    fs = [r["skill"]["faithfulness"] for r in scored]
    return S.mcnemar(hb, hs), S.paired_bootstrap_ci(hb, hs), S.paired_bootstrap_ci(fb, fs)


def _cats(b, s, idx):
    """idx: 1=中文名, 2=英文名。返回 (cats, base_vals, skill_vals)。"""
    cats, bv, sv = [], [], []
    for m in METRICS:
        if m[0] in ("hallucination",):
            continue  # 幻觉率改用“无幻觉率”同向展示
        key = m[5]
        if b[key] is None or s[key] is None:
            continue
        cats.append(m[idx]); bv.append(b[key]); sv.append(s[key])
    nz = ("无幻觉率" if idx == 1 else "Non-hallucination")
    cats.append(nz); bv.append(1 - b["hallucination"]); sv.append(1 - s["hallucination"])
    return cats, bv, sv


def generate(scored, cfg, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    b, s = aggregate(scored, "baseline"), aggregate(scored, "skill")
    mc, d_h, d_f = _verdict(scored)
    sig = S.significant(mc, d_h[1], d_h[2])
    mock = cfg.get("mock")
    halluc_drop = b["hallucination"] - s["hallucination"]

    # 双语图表
    charts = {}
    for lang, idx, t1, lb, ls, t2, dl, zl, unit in (
        ("zh", 1, "装了 skill vs 没装：各项指标对比（越高越好）", "没装 skill（基线）", "装了 skill",
         "skill 带来的提升（点=实测，横线=95% 置信区间）", ["忠实度提升", "幻觉率下降"], "0（没差别）", "个百分点"),
        ("en", 2, "With vs without the skill (higher is better)", "Without skill (baseline)", "With skill",
         "Improvement from the skill (dot = measured, bar = 95% CI)", ["Faithfulness gain", "Hallucination drop"], "0 (no difference)", "pts"),
    ):
        cats, bv, sv = _cats(b, s, idx)
        c1 = _svg_grouped_bar(t1, cats, bv, sv, lb, ls)
        c2 = _svg_delta(t2, dl, [d_f[0], halluc_drop], [d_f[1], -d_h[2]], [d_f[2], -d_h[1]], zl, unit)
        charts[lang] = (c1, c2)
    for lang in ("zh", "en"):
        for nm, svg in zip(("chart_metrics", "chart_delta"), charts[lang]):
            with open(os.path.join(out_dir, f"{nm}_{lang}.svg"), "w", encoding="utf-8") as f:
                f.write(svg)

    _write_html(scored, b, s, mc, d_h, d_f, sig, mock, charts,
                os.path.join(out_dir, "report.html"))
    _write_md(scored, b, s, mc, d_h, d_f, sig, mock, os.path.join(out_dir, "report.md"))


def _block(lang, scored, b, s, mc, d_h, d_f, sig, mock, charts):
    en = lang == "en"
    idx_zh = 2 if en else 1
    c1, c2 = charts[lang]

    def tr(zh, eng):
        return eng if en else zh

    o = [f'<div id="{lang}" class="langblock"{" hidden" if en else ""}>']
    o.append(f'<h1>{tr("这个备考 skill，真的更不容易「胡编」吗？——一份实测报告", "Does this exam-prep skill really hallucinate less? A benchmark report")}</h1>')
    if mock:
        o.append('<div class="card warn">' + tr(
            "⚠️ 这是 <b>MOCK 占位运行</b>，下面的数字仅用于演示报告样式，<b>不是真实结论</b>。换上真实课件与题目重跑后才作数。",
            "⚠️ This is a <b>MOCK run</b>. The numbers below only demo the report layout and are <b>not real results</b> — rerun on real materials to get actual numbers.") + "</div>")
    # headline
    if sig:
        head = tr(f"装了 skill 后，幻觉率从 {_pct(b['hallucination'])} 降到 {_pct(s['hallucination'])}，差异在统计上站得住（不是运气）。",
                  f"With the skill, the hallucination rate dropped from {_pct(b['hallucination'])} to {_pct(s['hallucination'])}, and the difference is statistically solid (not luck).")
    else:
        head = tr(f"装了 skill 这组明显更好（幻觉率 {_pct(b['hallucination'])} → {_pct(s['hallucination'])}），但题量偏小，暂不能 100% 排除运气——属于「方向对、需更多题坐实」。",
                  f"The skill arm looks clearly better (hallucination {_pct(b['hallucination'])} → {_pct(s['hallucination'])}), but the sample is small, so luck isn't fully ruled out yet — right direction, needs more items to confirm.")
    o.append(f'<div class="card">📌 <b>{tr("一句话结论：", "Bottom line: ")}</b>{html.escape(head)}</div>')
    o.append(f'<p class="muted">{tr("怎么测的：拿同样的题，分别让「没装 skill 的普通 AI」和「装了 skill 的 AI」来答，再逐题对照你材料里的标准答案打分。同一道题两边都答，对比最公平。", "How: the same questions are answered by a plain AI (no skill) and by the AI with the skill, then graded against your gold answers. Each item goes through both arms, for a fair paired comparison.")}</p>')

    o.append(f'<h2>{tr("📊 一眼看懂：两组对比", "📊 At a glance: the two arms")}</h2>{c1}')
    o.append(f'<h2>{tr("📈 到底提升了多少？（带误差范围）", "📈 How much did it actually help? (with error bars)")}</h2>{c2}')
    o.append(f'<p class="muted">{tr("点是实测提升，横线是 95% 置信区间。横线<b>整段都在 0 右边</b>才能说「真有提升、不是运气」。题量越多，横线越短、结论越硬。", "The dot is the measured gain; the bar is the 95% CI. Only when the whole bar sits right of 0 can we claim a real gain. More items → shorter bar → firmer conclusion.")}</p>')

    o.append(f'<h2>{tr("🔤 这些指标都是啥意思 & 出处", "🔤 What each metric means & its source")}</h2><ul>')
    for m in METRICS:
        name = m[idx_zh]
        desc = m[4] if en else m[3]
        cites = _cite_html(m[0])
        basis = (f' <span class="muted">{tr("依据", "Basis")}: {cites}</span>' if cites else
                 f' <span class="muted">{tr("（程序确定性精确判分）", "(deterministic exact-match grading)")}</span>')
        o.append(f"<li><b>{html.escape(name)}</b>：{html.escape(desc)}{basis}</li>")
    o.append("</ul>")

    o.append(f'<h2>{tr("🔢 详细数字", "🔢 Detailed numbers")}</h2><table><tr>'
             f'<th class=l>{tr("指标", "Metric")}</th><th>{tr("没装 skill", "Without skill")}</th><th>{tr("装了 skill", "With skill")}</th></tr>')
    for m in METRICS:
        o.append(f'<tr><td class=l>{html.escape(m[idx_zh])}</td><td>{_pct(b[m[5]])}</td><td>{_pct(s[m[5]])}</td></tr>')
    o.append("</table>")
    o.append(f'<p class="muted">{tr("题量", "n")}={len(scored)}；McNemar p={mc["p_value"]:.3f}；'
             f'{tr("幻觉率差值 95% 区间", "hallucination-rate 95% CI")} [{d_h[1]*100:+.0f}, {d_h[2]*100:+.0f}] '
             f'{tr("个百分点。", "pts.")} {tr("差异显著。" if sig else "题量偏小、暂未达统计显著，按实测趋势看待、不夸大。", "Significant." if sig else "Sample small; not yet statistically significant — read as a trend, no over-claiming.")}</p>')

    o.append(f'<h2>{tr("🧪 逐题明细", "🧪 Per-item")}</h2><table><tr>'
             f'<th>{tr("题号","ID")}</th><th>{tr("类型","Type")}</th><th>{tr("可答","Answerable")}</th>'
             f'<th>{tr("base 幻觉","base halluc.")}</th><th>{tr("skill 幻觉","skill halluc.")}</th></tr>')
    for r in scored:
        bb, ss = r["baseline"], r["skill"]
        o.append(f'<tr><td>{html.escape(str(r["id"]))}</td><td>{bb["answer_type"]}</td>'
                 f'<td>{tr("是","Y") if bb["answerable"] else tr("否","N")}</td>'
                 f'<td>{"❌" if bb["hallucinated"] else "✅"}</td><td>{"❌" if ss["hallucinated"] else "✅"}</td></tr>')
    o.append("</table>")

    # References
    o.append(f'<h2>{tr("📂 数据来源 Materials", "📂 Materials")}</h2>')
    o.append(f'<p class="muted">{tr("测试材料全部取自名校公开课（MIT OpenCourseWare / Open Yale Courses，CC BY-NC-SA），仅本地用于评测、不二次分发：", "All test materials are public open-courseware (MIT OpenCourseWare / Open Yale Courses, CC BY-NC-SA), used locally for evaluation and not redistributed:")}</p><ul>')
    for course, inst, zh_s, en_s, url in MATERIALS:
        subj = en_s if en else zh_s
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(course)}</a> — <span class="muted">{html.escape(inst)} · {html.escape(subj)}</span></li>')
    o.append("</ul>")
    o.append(f'<h2 id="refs-{lang}">{tr("📚 参考基准 References", "📚 References")}</h2><ol class="refs">')
    for title, url, zh, eng in REFERENCES:
        note = eng if en else zh
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(title)}</a> — <span class="muted">{html.escape(note)}</span></li>')
    o.append("</ol>")
    o.append(f'<p class="muted">{tr("统计方法：配对设计 + McNemar 检验 + bootstrap 置信区间 + Cohen’s kappa 裁判校准。数据与脚本全部开源、可复现。", "Statistics: paired design + McNemar test + bootstrap CIs + Cohen’s kappa judge calibration. All data and code are open and reproducible.")}</p>')
    o.append("</div>")
    return "\n".join(o)


def _write_html(scored, b, s, mc, d_h, d_f, sig, mock, charts, path):
    css = ("body{max-width:840px;margin:0 auto;padding:24px 18px;color:#202124;"
           "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.65}"
           "h1{font-size:25px}h2{font-size:19px;margin-top:34px;border-bottom:2px solid #e8eaed;padding-bottom:6px}"
           ".card{background:#f1f8f5;border:1px solid #cfe8de;border-radius:12px;padding:14px 18px;margin:16px 0}"
           ".warn{background:#fff4e5;border-color:#ffd699}"
           "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}"
           "th,td{border:1px solid #e8eaed;padding:7px 10px;text-align:center}th{background:#f8f9fa}"
           "td.l,th.l{text-align:left}.muted{color:#5f6368;font-size:13px}"
           "a{color:#1a73e8;text-decoration:none}a:hover{text-decoration:underline}"
           "svg{width:100%;height:auto;margin:6px 0}ol.refs li{margin:5px 0}"
           ".langbar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #e8eaed;margin-bottom:8px}"
           ".langbar button{font:inherit;cursor:pointer;border:1px solid #cfe8de;background:#f1f8f5;border-radius:20px;padding:4px 14px;margin-right:6px}"
           ".langbar button.on{background:#1a7f64;color:#fff;border-color:#1a7f64}")
    js = ("function setLang(l){for(const x of ['zh','en']){"
          "document.getElementById(x).hidden=(x!==l);"
          "document.getElementById('btn-'+x).className=(x===l?'on':'');}"
          "try{localStorage.setItem('rlang',l)}catch(e){}}"
          "window.addEventListener('DOMContentLoaded',function(){"
          "var l='zh';try{l=localStorage.getItem('rlang')||'zh'}catch(e){}setLang(l)});")
    o = ["<!doctype html><html><head><meta charset=utf-8>",
         "<meta name=viewport content='width=device-width,initial-scale=1'>",
         "<title>防幻觉实测报告 / Hallucination Benchmark</title>",
         f"<style>{css}</style></head><body>",
         "<div class='langbar'><button id='btn-zh' class='on' onclick=\"setLang('zh')\">中文</button>"
         "<button id='btn-en' onclick=\"setLang('en')\">English</button></div>",
         _block("zh", scored, b, s, mc, d_h, d_f, sig, mock, charts),
         _block("en", scored, b, s, mc, d_h, d_f, sig, mock, charts),
         f"<script>{js}</script>", "</body></html>"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(o))


def _write_md(scored, b, s, mc, d_h, d_f, sig, mock, path):
    note = ("差异显著。" if sig else "题量偏小，暂未达显著，按趋势看待、不夸大。")
    L = ["# 防幻觉实测报告 / Hallucination Benchmark" + ("（MOCK 占位，非真实数据）" if mock else ""), "",
         "> 完整可视化、中英双语、带引用的版本见 `report.html`（用浏览器打开）。", "",
         "![指标对比](chart_metrics_zh.svg)", "", "![提升幅度](chart_delta_zh.svg)", "",
         "## 数字 / Numbers", "| 指标 Metric | 没装 skill | 装了 skill | 依据 Basis |", "| :-- | :--: | :--: | :-- |"]
    for m in METRICS:
        cites = ", ".join(f"[{n}]({u})" for n, u in CITES.get(m[0], [])) or "确定性判分"
        L.append(f"| {m[1]} {m[2]} | {_pct(b[m[5]])} | {_pct(s[m[5]])} | {cites} |")
    L += ["", f"n={len(scored)}；McNemar p={mc['p_value']:.3f}；幻觉率差值 95% CI [{d_h[1]*100:+.0f}, {d_h[2]*100:+.0f}] 个百分点。{note}",
          "", "## 参考基准 / References"]
    for title, url, zh, eng in REFERENCES:
        L.append(f"- [{title}]({url}) — {zh}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
