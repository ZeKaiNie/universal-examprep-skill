#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""迭代逼近（convergence）可视化：多轮反馈后各指标向「上限」收敛的折线图 + 轮次表。

协议（"自我检查 + 逐轮逼近"）：
  第 r 轮：skill 答题 → 判分 → 自检反馈（哪些题失败；失败题的支撑材料是否在 skill 的
  references/wiki 里——不在 = "漏了资料"）→ skill 补齐 KB / 修正题库 → 第 r+1 轮 再答。
  直到主指标连续两轮 Δ < EPS（收敛）或触顶。这样既能让 skill **自查漏了什么资料**，
  又能用一张图说清"加了反馈后指标怎么一路逼近上限"。

本模块负责可视化（折线图 + 表）。单独演示（mock 多轮数据）：
    python rounds.py        # 生成 results/convergence.html
"""

import os
import sys
import html
import report as R

for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8")
    except Exception:
        pass

# (聚合键, 中文名, 英文名, 颜色)；都为「越高越好」
PALETTE = [
    ("faithfulness", "忠实度", "Faithfulness", "#1a7f64"),
    ("correctness", "正确率", "Correctness", "#1a73e8"),
    ("abstention", "弃答率", "Abstention", "#8e44ad"),
    ("non_halluc", "无幻觉率", "Non-hallucination", "#e67e22"),
]
EPS = 0.02  # 收敛阈值：主指标(忠实度)连续两轮 Δ < EPS 即视为收敛


def _series(rounds):
    s = {k: [] for k, _, _, _ in PALETTE}
    for r in rounds:
        s["faithfulness"].append(r["faithfulness"])
        s["correctness"].append(r["correctness"])
        s["abstention"].append(r["abstention"])
        s["non_halluc"].append(1 - r["hallucination"])
    return s


def _converged_round(vals):
    """返回首次收敛的轮次(1-based)，否则 None。需要 >=3 轮、最近两个 Δ 都 < EPS。"""
    for i in range(2, len(vals)):
        if abs(vals[i] - vals[i - 1]) < EPS and abs(vals[i - 1] - vals[i - 2]) < EPS:
            return i + 1
    return None


def _svg_convergence(title, round_labels, series, ceiling_label, w=720, h=430):
    pl, pr, pt, pb = 54, 138, 56, 50
    pw, ph = w - pl - pr, h - pt - pb
    n = len(round_labels)

    def X(i):
        return pl + (pw * i / (n - 1) if n > 1 else pw / 2)

    def Y(v):
        return pt + ph * (1 - v)

    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">']
    s.append(f'<text x="{w/2}" y="28" text-anchor="middle" font-size="16" '
             f'font-weight="700" fill="{R.C_INK}">{html.escape(title)}</text>')
    for frac in (0.0, 0.5, 1.0):
        y = Y(frac)
        s.append(f'<line x1="{pl}" y1="{y:.1f}" x2="{pl+pw}" y2="{y:.1f}" stroke="{R.C_GRID}"/>')
        s.append(f'<text x="{pl-8}" y="{y+4:.1f}" text-anchor="end" font-size="11" '
                 f'fill="{R.C_MUTE}">{int(frac*100)}%</text>')
    # 上限线（100% 处虚线 + 标注）
    s.append(f'<line x1="{pl}" y1="{Y(1.0):.1f}" x2="{pl+pw}" y2="{Y(1.0):.1f}" '
             f'stroke="{R.C_MUTE}" stroke-dasharray="5 4"/>')
    s.append(f'<text x="{pl+pw}" y="{Y(1.0)-6:.1f}" text-anchor="end" font-size="11" '
             f'fill="{R.C_MUTE}">{html.escape(ceiling_label)}</text>')
    for i, lab in enumerate(round_labels):
        s.append(f'<text x="{X(i):.1f}" y="{pt+ph+20:.1f}" text-anchor="middle" '
                 f'font-size="11.5" fill="{R.C_INK}">{html.escape(lab)}</text>')
    for label, vals, color in series:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.5"/>')
        for i, v in enumerate(vals):
            s.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3.5" fill="{color}"/>')
        ly = Y(vals[-1])
        s.append(f'<text x="{pl+pw+8:.1f}" y="{ly+4:.1f}" font-size="11.5" '
                 f'font-weight="600" fill="{color}">{html.escape(label)} {vals[-1]*100:.0f}%</text>')
    s.append('</svg>')
    return "\n".join(s)


def _table(round_labels, rounds, series, conv, en):
    def tr(z, e):
        return e if en else z
    th = [tr("轮次", "Round")] + [(p[2] if en else p[1]) for p in PALETTE] + \
         [tr("缺失资料", "Missing"), tr("状态", "Status")]
    o = ["<table><tr>" + "".join(f"<th>{h}</th>" for h in th) + "</tr>"]
    keys = [p[0] for p in PALETTE]
    for i, r in enumerate(rounds):
        cells = [round_labels[i]]
        for k in keys:
            v = series[k][i]
            if i == 0:
                cells.append(f"{v*100:.0f}%")
            else:
                d = (v - series[k][i - 1]) * 100
                cells.append(f"{v*100:.0f}% <span class='muted'>({d:+.0f})</span>")
        cells.append(str(r.get("missing", "—")))
        if i == 0:
            status = tr("起点", "start")
        elif conv and (i + 1) >= conv:
            status = tr("✓ 已收敛", "✓ converged")
        else:
            status = tr("↑ 改进中", "↑ improving")
        cells.append(f"<b>{status}</b>")
        o.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    o.append("</table>")
    return "".join(o)


def _block(lang, rounds, round_labels_map, series, conv, charts, mock):
    en = lang == "en"

    def tr(z, e):
        return e if en else z
    o = [f'<div id="{lang}" class="langblock"{" hidden" if en else ""}>']
    o.append(f"<h1>{tr('逐轮反馈，看 skill 怎样一步步逼近上限', 'Round-by-round feedback: how the skill climbs toward its ceiling')}</h1>")
    if mock:
        o.append('<div class="card warn">' + tr(
            "⚠️ 这是 <b>MOCK 演示数据</b>，用来展示「迭代逼近」图表长什么样，不是真实结果。",
            "⚠️ <b>MOCK demo data</b> — illustrates the convergence view; not real results.") + "</div>")
    last = rounds[-1]
    o.append('<div class="card">📌 ' + tr(
        f"读法：每一轮做「答题 → 看哪里错了 / 漏了哪段资料 → 补上 → 再答」，各指标就往上爬一截；"
        f"爬到几乎不动（收敛）就说明 skill 能补的都补齐了、逼近了这套题的上限。"
        f"本例第 {conv or '—'} 轮收敛，最终忠实度 {last['faithfulness']*100:.0f}%、"
        f"无幻觉率 {(1-last['hallucination'])*100:.0f}%、漏的资料从 {rounds[0].get('missing','?')} 条降到 {last.get('missing','?')} 条。",
        f"How to read: each round runs ‘answer → see what was wrong / which material was missing → fill it in → answer again’, "
        f"so every metric climbs a bit; once it barely moves (converges), the skill has filled what it can and approached this set's ceiling. "
        f"Here it converges at round {conv or '—'}: final faithfulness {last['faithfulness']*100:.0f}%, "
        f"non-hallucination {(1-last['hallucination'])*100:.0f}%, missing materials down from {rounds[0].get('missing','?')} to {last.get('missing','?')}.") + "</div>")
    o.append(charts[lang])
    o.append(f"<h2>{tr('逐轮明细', 'Per-round detail')}</h2>")
    o.append(_table(round_labels_map[lang], rounds, series, conv, en))
    o.append(f"<p class='muted'>{tr('括号内为相对上一轮的变化（百分点）。收敛判据：忠实度连续两轮变化 < 2 个百分点。', 'Parentheses show change vs. the previous round (pts). Convergence: faithfulness changes < 2 pts for two consecutive rounds.')}</p>")
    o.append("</div>")
    return "".join(o)


def render_convergence(rounds, out_dir, mock=False):
    os.makedirs(out_dir, exist_ok=True)
    series = _series(rounds)
    conv = _converged_round(series["faithfulness"])
    round_labels_map = {
        "zh": [f"第{r['round']}轮" for r in rounds],
        "en": [f"R{r['round']}" for r in rounds],
    }
    charts = {}
    for lang, idx in (("zh", 1), ("en", 2)):
        srs = [(p[idx], series[p[0]], p[3]) for p in PALETTE]
        title = ("各指标随反馈轮次逼近上限" if lang == "zh"
                 else "Metrics converging toward the ceiling across feedback rounds")
        ceil = "上限 100%" if lang == "zh" else "ceiling 100%"
        charts[lang] = _svg_convergence(title, round_labels_map[lang], srs, ceil)
        with open(os.path.join(out_dir, f"convergence_{lang}.svg"), "w", encoding="utf-8") as f:
            f.write(charts[lang])

    css = ("body{max-width:860px;margin:0 auto;padding:22px 18px;color:#202124;"
           "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;line-height:1.6}"
           "h1{font-size:24px}h2{font-size:18px;margin-top:28px;border-bottom:2px solid #e8eaed;padding-bottom:6px}"
           ".card{background:#f1f8f5;border:1px solid #cfe8de;border-radius:12px;padding:14px 18px;margin:16px 0}"
           ".warn{background:#fff4e5;border-color:#ffd699}"
           "table{border-collapse:collapse;width:100%;margin:12px 0;font-size:14px}"
           "th,td{border:1px solid #e8eaed;padding:7px 9px;text-align:center}th{background:#f8f9fa}"
           ".muted{color:#5f6368;font-size:12px}svg{width:100%;height:auto;margin:6px 0}"
           ".langbar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #e8eaed;margin-bottom:8px}"
           ".langbar button{font:inherit;cursor:pointer;border:1px solid #cfe8de;background:#f1f8f5;border-radius:20px;padding:4px 14px;margin-right:6px}"
           ".langbar button.on{background:#1a7f64;color:#fff;border-color:#1a7f64}")
    js = ("function setLang(l){for(const x of ['zh','en']){document.getElementById(x).hidden=(x!==l);"
          "document.getElementById('btn-'+x).className=(x===l?'on':'')}}"
          "window.addEventListener('DOMContentLoaded',function(){setLang('zh')});")
    page = ["<!doctype html><html><head><meta charset=utf-8>",
            "<meta name=viewport content='width=device-width,initial-scale=1'>",
            "<title>迭代逼近 / Convergence</title>", f"<style>{css}</style></head><body>",
            "<div class='langbar'><button id='btn-zh' class='on' onclick=\"setLang('zh')\">中文</button>"
            "<button id='btn-en' onclick=\"setLang('en')\">English</button></div>",
            _block("zh", rounds, round_labels_map, series, conv, charts, mock),
            _block("en", rounds, round_labels_map, series, conv, charts, mock),
            f"<script>{js}</script></body></html>"]
    with open(os.path.join(out_dir, "convergence.html"), "w", encoding="utf-8") as f:
        f.write("\n".join(page))


if __name__ == "__main__":
    demo = [
        {"round": 1, "faithfulness": .60, "correctness": .56, "abstention": .48, "hallucination": .36, "missing": 8},
        {"round": 2, "faithfulness": .80, "correctness": .78, "abstention": .78, "hallucination": .15, "missing": 4},
        {"round": 3, "faithfulness": .92, "correctness": .89, "abstention": .93, "hallucination": .06, "missing": 2},
        {"round": 4, "faithfulness": .935, "correctness": .92, "abstention": .97, "hallucination": .04, "missing": 1},
        {"round": 5, "faithfulness": .945, "correctness": .93, "abstention": 1.0, "hallucination": .03, "missing": 0},
    ]
    render_convergence(demo, "results", mock=True)
    print("生成: results/convergence.html （浏览器打开看「迭代逼近」图 + 轮次表）")
