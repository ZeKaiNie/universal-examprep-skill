# -*- coding: utf-8 -*-
"""三臂×三模型 防幻觉报告渲染器（6.006）。读取 results/matrix/summary.json → 中英双语 report.html。
纯标准库；复用 report.py 的 MATERIALS / REFERENCES。面向普通用户：通俗 + 图表 + 引用 + 诚实 caveats。"""
import os, sys, json, html
BENCH = os.path.dirname(os.path.abspath(__file__))   # portable: this file lives in benchmark/
sys.path.insert(0, BENCH)
import report as R
for s in ("stdout", "stderr"):
    try: getattr(sys, s).reconfigure(encoding="utf-8")
    except Exception: pass

OUT = os.path.join(BENCH, "results", "matrix")
ARMS = [("closedbook", "闭卷（无材料）", "Closed-book (no materials)", "#d93025"),
        ("material", "给全材料（dump）", "Full materials dumped", "#f9ab00"),
        ("skill", "skill（惰性检索）", "Skill (lazy retrieval)", "#1a7f64")]
MODELS = [("opus", "Opus 4.8"), ("sonnet", "Sonnet 4.6"), ("haiku", "Haiku 4.5")]

def pct(x):
    return "—" if x is None else f"{round(x*100)}%"

# ---------- SVG ----------
def svg_grouped(matrix, metric, en, w=680, h=320):
    """按模型分组、每组三臂的柱状图。metric: 'correct' 或 'abstention_oos'。"""
    pl, pb, pt, pr = 48, 56, 30, 14
    gw = (w - pl - pr) / len(MODELS)
    bw = gw / (len(ARMS) + 1)
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img">']
    # y 轴网格 0-100%
    for g in range(0, 101, 25):
        y = pt + (h - pt - pb) * (1 - g / 100)
        s.append(f'<line x1="{pl}" y1="{y:.0f}" x2="{w-pr}" y2="{y:.0f}" stroke="#eee"/>')
        s.append(f'<text x="{pl-6}" y="{y+4:.0f}" font-size="11" fill="#888" text-anchor="end">{g}%</text>')
    for mi, (mk, mlabel) in enumerate(MODELS):
        gx = pl + mi * gw
        for ai, (ak, zh, en_l, color) in enumerate(ARMS):
            cell = matrix.get(f"{mk}|{ak}")
            v = (cell or {}).get(metric)
            x = gx + (ai + 0.5) * bw + bw * 0.3
            if v is None:
                continue
            bh = (h - pt - pb) * v
            y = h - pb - bh
            s.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bw*0.8:.0f}" height="{bh:.0f}" fill="{color}" rx="2"/>')
            s.append(f'<text x="{x+bw*0.4:.0f}" y="{y-4:.0f}" font-size="10" fill="#444" text-anchor="middle">{round(v*100)}</text>')
        s.append(f'<text x="{gx+gw/2:.0f}" y="{h-pb+18:.0f}" font-size="12" fill="#333" text-anchor="middle">{html.escape(mlabel)}</text>')
    # 图例
    lx = pl
    for ak, zh, en_l, color in ARMS:
        s.append(f'<rect x="{lx}" y="{h-18}" width="11" height="11" fill="{color}"/>')
        lab = en_l if en else zh
        s.append(f'<text x="{lx+15}" y="{h-8}" font-size="11" fill="#444">{html.escape(lab)}</text>')
        lx += (len(en_l if en else zh)) * (6.4 if en else 11) + 36
    s.append('</svg>')
    return "\n".join(s)

def svg_convergence(conv, en, w=560, h=300):
    pts = [conv.get(f"conv_r{i}") for i in (1, 2, 3)]
    xs_lab = ["7", "14", "20"]
    pl, pb, pt, pr = 50, 50, 24, 18
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img">']
    for g in range(0, 101, 25):
        y = pt + (h - pt - pb) * (1 - g / 100)
        s.append(f'<line x1="{pl}" y1="{y:.0f}" x2="{w-pr}" y2="{y:.0f}" stroke="#eee"/>')
        s.append(f'<text x="{pl-6}" y="{y+4:.0f}" font-size="11" fill="#888" text-anchor="end">{g}%</text>')
    n = 3
    xcoord = lambda i: pl + (w - pl - pr) * (i / (n - 1))
    ycoord = lambda v: pt + (h - pt - pb) * (1 - v)
    coords = []
    for i, c in enumerate(pts):
        if c and c.get("correct") is not None:
            coords.append((xcoord(i), ycoord(c["correct"]), c["correct"]))
    if len(coords) >= 2:
        s.append('<polyline fill="none" stroke="#1a7f64" stroke-width="2.5" points="'
                 + " ".join(f'{x:.0f},{y:.0f}' for x, y, _ in coords) + '"/>')
    for i, c in enumerate(pts):
        if c and c.get("correct") is not None:
            x, y = xcoord(i), ycoord(c["correct"])
            s.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="4" fill="#1a7f64"/>')
            s.append(f'<text x="{x:.0f}" y="{y-9:.0f}" font-size="11" fill="#1a7f64" text-anchor="middle">{round(c["correct"]*100)}%</text>')
        lab = (f"{xs_lab[i]} ch" if en else f"{xs_lab[i]} 章")
        s.append(f'<text x="{xcoord(i):.0f}" y="{h-pb+18:.0f}" font-size="12" fill="#333" text-anchor="middle">{lab}</text>')
    s.append('</svg>')
    return "\n".join(s)

# ---------- HTML ----------
def block(lang, S):
    en = lang == "en"
    tr = lambda zh, eng: eng if en else zh
    mx = S["matrix"]; conv = S.get("convergence") or {}
    o = [f'<div id="{lang}">']
    o.append(f'<h1>{tr("装了这个备考 skill，AI 真的更少「胡编」吗？——MIT 6.006 实测",
                         "Does the exam-prep skill cut hallucination? A MIT 6.006 benchmark")}</h1>')
    # 方法
    ni = S["n_items"]
    zh_m = f"测法：同一套金标题（{ni} 题，全部锚定 MIT 6.006 官方讲义/习题），让 3 个模型在 3 种条件下作答——"
    en_m = f"Method: one gold set ({ni} items, all grounded in official MIT 6.006 lecture notes / problem sets) answered by 3 models under 3 conditions —"
    o.append(f'<p class="muted">{tr(zh_m, en_m)}</p>')
    o.append("<ul>")
    o.append(f'<li><b>{tr("闭卷","Closed-book")}</b>：{tr("不给任何材料，只靠模型自己的知识。","no materials, parametric knowledge only.")}</li>')
    o.append(f'<li><b>{tr("给全材料","Full-materials")}</b>：{tr("把整门课全文塞进提示里（dump）。","the entire course text dumped into the prompt.")}</li>')
    o.append(f'<li><b>{tr("skill","Skill")}</b>：{tr("只把课件建成知识库，模型按需检索（skill 的防幻觉机制）。","course built into a wiki the model retrieves from on demand (the skill\'s regime).")}</li>')
    o.append("</ul>")
    # 头条
    sk = mx.get("haiku|skill"); cb = mx.get("haiku|closedbook"); md = mx.get("haiku|material")
    if sk and cb:
        cbc, mdc, skc = pct(cb.get("correct")), pct((md or {}).get("correct")), pct(sk.get("correct"))
        ska, cba = pct(sk.get("abstention_oos")), pct(cb.get("abstention_oos"))
        zh_h = (f"在最弱的 Haiku 上，闭卷正确率 {cbc} → 给全材料 {mdc} → skill 惰性检索 {skc}；"
                f"越界题上 skill 的弃答率 {ska}（闭卷只有 {cba}，即闭卷在不会的题上更爱硬编）。")
        en_h = (f"On the weakest model (Haiku): closed-book correctness {cbc} → full-materials {mdc} → "
                f"skill retrieval {skc}; on out-of-scope probes the skill abstains {ska} vs closed-book {cba} "
                f"(closed-book fabricates more when it doesn't know).")
        o.append('<div class="card">')
        o.append(f'<b>{tr("一句话结论","Bottom line")}</b>：{tr(zh_h, en_h)}')
        o.append('</div>')
    # 主表
    o.append(f'<h2>{tr("📊 正确率：3 模型 × 3 条件","📊 Correctness: 3 models × 3 conditions")}</h2>')
    o.append(svg_grouped(mx, "correct", en))
    o.append(f'<table><tr><th class=l>{tr("模型","Model")}</th>'
             + "".join(f'<th>{tr(z,e)}</th>' for _, z, e, _ in ARMS) + "</tr>")
    for mk, ml in MODELS:
        cells = "".join(f'<td>{pct((mx.get(f"{mk}|{ak}") or {}).get("correct"))}</td>' for ak, *_ in ARMS)
        o.append(f'<tr><td class=l>{ml}</td>{cells}</tr>')
    o.append("</table>")
    # 幻觉率 + 越界弃答
    o.append(f'<h2>{tr("🧪 幻觉率 & 越界弃答","🧪 Hallucination & out-of-scope abstention")}</h2>')
    o.append(f'<p class="muted">{tr("幻觉率＝答案里出现材料未支持/相矛盾论断的比例（越低越好，按整篇讲义为依据判，会惩罚“正确但材料没写”的展开）；越界弃答率＝材料没覆盖的探针题上老实说“未涵盖”的比例（越高越好）。","Hallucination = share of answers with claims not supported by (or contradicting) the source (lower is better; judged against the full lecture, so it penalizes correct-but-unsourced elaboration). OOS abstention = share of not-covered probes where the model honestly says “not covered” (higher is better).")}</p>')
    o.append(f'<table><tr><th class=l>{tr("模型 / 指标","Model / metric")}</th>'
             + "".join(f'<th>{tr(z,e)}</th>' for _, z, e, _ in ARMS) + "</tr>")
    for mk, ml in MODELS:
        h_cells = "".join(f'<td>{pct((mx.get(f"{mk}|{ak}") or {}).get("hallucination"))}</td>' for ak, *_ in ARMS)
        o.append(f'<tr><td class=l>{ml} · {tr("幻觉","halluc.")}</td>{h_cells}</tr>')
    for mk, ml in MODELS:
        a_cells = "".join(f'<td>{pct((mx.get(f"{mk}|{ak}") or {}).get("abstention_oos"))}</td>' for ak, *_ in ARMS)
        o.append(f'<tr><td class=l>{ml} · {tr("越界弃答","OOS abstain")}</td>{a_cells}</tr>')
    o.append("</table>")
    # 收敛
    if conv:
        o.append(f'<h2>{tr("📈 迭代逼近：知识库越全，越答得对","📈 Convergence: more wiki coverage → more correct")}</h2>')
        o.append(f'<p class="muted">{tr("skill 臂分 3 轮，知识库从 7 章逐步补到 14、20 章（讲义题+探针，Haiku）。","Skill arm over 3 rounds as the wiki grows 7 → 14 → 20 chapters (lecture items + probes, Haiku).")}</p>')
        o.append(svg_convergence(conv, en))
    # caveats
    o.append(f'<h2>{tr("⚠️ 诚实声明 Caveats","⚠️ Caveats")}</h2><ul>')
    cav = [
        (f"题量 n={S['n_items']}（{tr('每条都跨 3 臂同题对比，最公平','same items across all arms')}）。",
         f"n={S['n_items']} items, each answered under all conditions."),
        (f"裁判＝{S.get('judge_model','sonnet')}（judge_repeats={S.get('judge_repeats',1)}），数值题为确定性判分。裁判与被测同属一个模型家族，仅做了跨模型而非人工 kappa 校准——属已知局限。",
         f"Judge = {S.get('judge_model','sonnet')} (repeats={S.get('judge_repeats',1)}); numeric items scored deterministically. Judge and tested models share one family; only cross-model (not human-kappa) calibration was done — a known limitation."),
        ("幻觉/忠实度以整篇讲义为依据，会把“正确但讲义没写”的展开也算作不忠实——对 grounding 基准是合理口径，但解读时需知晓。",
         "Faithfulness is judged against the full lecture, so correct-but-unsourced elaboration counts as unfaithful — a reasonable grounding criterion, but worth knowing."),
        ("本报告只针对该 skill 本身、与任何未来平台无关。数据与脚本可复现。",
         "This report concerns the skill itself only, independent of any future platform. Data and code are reproducible."),
    ]
    for zh, eng in cav:
        o.append(f"<li>{tr(zh, eng)}</li>")
    o.append("</ul>")
    if S.get("total_cost_usd"):
        o.append(f'<p class="muted">{tr("本次实测真实推理花费","Measured inference cost")}: ${S["total_cost_usd"]:.2f}</p>')
    # Materials + References（复用 report.py）
    o.append(f'<h2>{tr("📂 数据来源 Materials","📂 Materials")}</h2><ul>')
    for course, inst, zh_s, en_s, url in R.MATERIALS:
        if "6.006" not in course:  # 本报告只用了 6.006
            continue
        subj = en_s if en else zh_s
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(course)}</a> — <span class="muted">{html.escape(inst)} · {html.escape(subj)} · {tr("讲义 L1–L20 + 习题 PS0–8（含官方解答）","lectures L1–L20 + problem sets PS0–8 with official solutions")}</span></li>')
    o.append("</ul>")
    o.append(f'<h2>{tr("📚 参考基准 References","📚 References")}</h2><ol class="refs">')
    for title, url, zh, eng in R.REFERENCES:
        note = eng if en else zh
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(title)}</a> — <span class="muted">{html.escape(note)}</span></li>')
    o.append("</ol></div>")
    return "\n".join(o)

def _write_standalone_svgs(S):
    """Also emit standalone chart SVGs (zh/en) so the README can embed the comparison charts."""
    m, conv = S.get("matrix", {}), S.get("convergence", {})
    for en, suf in ((False, "zh"), (True, "en")):
        for metric, name in (("correct", "correct"), ("hallucination", "hallu"),
                             ("abstention_oos", "oos")):
            open(os.path.join(OUT, f"chart_{name}_{suf}.svg"), "w", encoding="utf-8").write(
                svg_grouped(m, metric, en))
        open(os.path.join(OUT, f"chart_convergence_{suf}.svg"), "w", encoding="utf-8").write(
            svg_convergence(conv, en))


def main():
    S = json.load(open(os.path.join(OUT, "summary.json"), encoding="utf-8"))
    _write_standalone_svgs(S)
    css = ("body{max-width:860px;margin:0 auto;padding:24px 18px;color:#202124;"
           "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.65}"
           "h1{font-size:24px}h2{font-size:19px;margin-top:32px;border-bottom:2px solid #e8eaed;padding-bottom:6px}"
           ".card{background:#f1f8f5;border:1px solid #cfe8de;border-radius:12px;padding:14px 18px;margin:16px 0}"
           "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}"
           "th,td{border:1px solid #e8eaed;padding:7px 10px;text-align:center}th{background:#f8f9fa}"
           "td.l,th.l{text-align:left}.muted{color:#5f6368;font-size:13px}"
           "a{color:#1a73e8;text-decoration:none}a:hover{text-decoration:underline}svg{width:100%;height:auto;margin:6px 0}"
           ".langbar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #e8eaed;margin-bottom:8px}"
           ".langbar button{font:inherit;cursor:pointer;border:1px solid #cfe8de;background:#f1f8f5;border-radius:20px;padding:4px 14px;margin-right:6px}"
           ".langbar button.on{background:#1a7f64;color:#fff;border-color:#1a7f64}")
    js = ("function setLang(l){for(const x of ['zh','en']){document.getElementById(x).hidden=(x!==l);"
          "document.getElementById('btn-'+x).className=(x===l?'on':'');}try{localStorage.setItem('rlang',l)}catch(e){}}"
          "window.addEventListener('DOMContentLoaded',function(){var l='zh';try{l=localStorage.getItem('rlang')||'zh'}catch(e){}setLang(l)});")
    page = ["<!doctype html><html><head><meta charset=utf-8>",
            "<meta name=viewport content='width=device-width,initial-scale=1'>",
            "<title>6.006 防幻觉实测 / Hallucination Benchmark</title>",
            f"<style>{css}</style></head><body>",
            "<div class='langbar'><button id='btn-zh' class='on' onclick=\"setLang('zh')\">中文</button>"
            "<button id='btn-en' onclick=\"setLang('en')\">English</button></div>",
            block("zh", S), block("en", S), f"<script>{js}</script>", "</body></html>"]
    path = os.path.join(OUT, "report.html")
    open(path, "w", encoding="utf-8").write("\n".join(page))
    print("[+] 写出", path)

if __name__ == "__main__":
    main()
