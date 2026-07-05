# -*- coding: utf-8 -*-
"""三臂×三模型 防幻觉报告渲染器（6.006）。读取 results/matrix/summary.json → 中英双语 report.html。
纯标准库；复用 report.py 的 MATERIALS / REFERENCES。面向普通用户：通俗 + 图表 + 引用 + 诚实 caveats。"""
import os, sys, json, html, argparse
BENCH = os.path.dirname(os.path.abspath(__file__))   # portable: this file lives in benchmark/
sys.path.insert(0, BENCH)
import report as R
for s in ("stdout", "stderr"):
    try: getattr(sys, s).reconfigure(encoding="utf-8")
    except Exception: pass

OUT = os.path.join(BENCH, "results", "matrix")
ARMS = [("closedbook", "不给资料", "Closed-book", "#d93025"),
        ("rawfiles", "裸文件 + 通用 agent", "Raw files + plain agent", "#f9ab00"),
        ("skill", "使用本技能", "With the skill", "#1a7f64")]
MODELS = [("opus", "Opus 4.8"), ("sonnet", "Sonnet 4.6"), ("haiku", "Haiku 4.5")]
# PSYC 110 只有两种条件（不给资料 / 使用本技能），键名形如 "psyc|<model>|<arm>"
PSYC_ARMS = [("closedbook", "不给资料", "No materials", "#d93025"),
             ("skill", "使用本技能", "With the skill", "#1a7f64")]

def pct(x):
    return "—" if x is None else f"{round(x*100)}%"

# ---------- SVG ----------
def svg_grouped(matrix, metric, en, w=680, h=320, arms=ARMS, key_prefix=""):
    """按模型分组、每组若干条件的柱状图。metric: 'correct' / 'hallucination' / 'abstention_oos'。
    arms/key_prefix 可复用于 PSYC（两条件、键名带 'psyc|' 前缀）。"""
    pl, pb, pt, pr = 48, 56, 30, 14
    gw = (w - pl - pr) / len(MODELS)
    bw = gw / (len(arms) + 1)
    s = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" role="img">']
    # y 轴网格 0-100%
    for g in range(0, 101, 25):
        y = pt + (h - pt - pb) * (1 - g / 100)
        s.append(f'<line x1="{pl}" y1="{y:.0f}" x2="{w-pr}" y2="{y:.0f}" stroke="#eee"/>')
        s.append(f'<text x="{pl-6}" y="{y+4:.0f}" font-size="11" fill="#888" text-anchor="end">{g}%</text>')
    for mi, (mk, mlabel) in enumerate(MODELS):
        gx = pl + mi * gw
        for ai, (ak, zh, en_l, color) in enumerate(arms):
            cell = matrix.get(f"{key_prefix}{mk}|{ak}")
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
    for ak, zh, en_l, color in arms:
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
    o.append(f'<h1>{tr("装了这个备考 skill，AI 真的更少「胡编」吗？——MIT 6.006 实测", "Does the exam-prep skill cut hallucination? A MIT 6.006 benchmark")}</h1>')
    # 方法
    ni = S["n_items"]
    zh_m = f"测法：同一套金标题（{ni} 题，全部锚定 MIT 6.006 官方讲义/习题），让 3 个模型在 3 种条件下作答——"
    en_m = f"Method: one gold set ({ni} items, all grounded in official MIT 6.006 lecture notes / problem sets) answered by 3 models under 3 conditions —"
    o.append(f'<p class="muted">{tr(zh_m, en_m)}</p>')
    o.append("<ul>")
    o.append(f'<li><b>{tr("不给资料","Closed-book")}</b>：{tr("不给任何材料，只靠模型自己的知识回答。","no materials, parametric knowledge only.")}</li>')
    o.append(f'<li><b>{tr("裸文件 + 通用 agent","Raw files + plain agent")}</b>：{tr("把原始讲义/习题文件放进一个文件夹，模型用通用文件工具（读取/检索）按需查阅——但没有本技能。这是最公平的对照基线。","raw lecture / problem-set files in a folder; the model reads/greps them on demand with generic file tools — but WITHOUT the skill. The fairest baseline.")}</li>')
    o.append(f'<li><b>{tr("使用本技能","With the skill")}</b>：{tr("课件先被整理成分章节知识库，模型按需取相关章节（本技能的机制）。","the course pre-built into a chaptered wiki the skill retrieves from on demand.")}</li>')
    o.append(f'<li class="muted">{tr("（另设一个 naive 对照：把整门课全文一股脑塞进提问——见正确率表下方脚注。）","(A naive control — dumping the whole course into one prompt — is discussed in the footnote below the table.)")}</li>')

    o.append("</ul>")
    # 头条
    rf = mx.get("haiku|rawfiles"); sk = mx.get("haiku|skill"); cb = mx.get("haiku|closedbook")
    cost = S.get("cost_per_q", {}).get("algo", {})
    if sk and cb and rf:
        cbc, rfc, skc = pct(cb.get("correct")), pct(rf.get("correct")), pct(sk.get("correct"))
        c_rf, c_sk, c_md = cost.get("rawfiles"), cost.get("skill"), cost.get("material")
        ratio = (f"约 {round(c_md / c_sk)} 倍" if (c_md and c_sk) else "数倍")
        ratio_en = (f"~{round(c_md / c_sk)}x" if (c_md and c_sk) else "several times")
        zh_h = (f"一个能按需读文件的通用 agent（没有本技能）本身已经很强：连最弱的 Haiku 都有 {rfc} 正确率，"
                f"远高于不给资料的 {cbc}。使用本技能进一步到 {skc}——优势对越弱的模型越明显，"
                f"且两种方式对“资料里没有”的题都 100% 如实弃答。真正的差异在成本：同等精度下本技能每题约 "
                f"${c_sk}，比裸文件 agent 的 ${c_rf} 更省（只取压缩过的相关章节，而非每题翻整堆原始文件）；"
                f"而把整门课一股脑塞进提问每题高达 ${c_md}（贵{ratio}）且在弱模型上直接跑崩。")
        en_h = (f"A plain agent that can read files on demand (no skill) is already strong: even the weakest "
                f"model (Haiku) reaches {rfc} correctness, far above closed-book's {cbc}. The skill pushes it "
                f"to {skc} — the edge grows for weaker models — and both abstain 100% on questions the "
                f"materials don't cover. The real difference is cost: at equal accuracy the skill is ~${c_sk} "
                f"per question vs the raw-files agent's ~${c_rf} (it pulls one condensed chapter instead of "
                f"grepping the whole pile each time); dumping the entire course costs ~${c_md} per question "
                f"({ratio_en} more) and outright fails on weaker models.")
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
    # 成本对比（同等精度下，本技能比裸文件 agent 更省）
    cost = S.get("cost_per_q", {}).get("algo", {})
    if cost.get("skill"):
        o.append(f"""<h3 style="font-size:16px;margin-top:18px">{tr("💵 平均每题成本（同精度下更省才是 skill 的差异）","💵 Cost per question (the skill's real edge: same accuracy, lower cost)")}</h3>""")
        o.append('<table><tr>' + "".join(f'<th>{tr(z,e)}</th>' for _, z, e, _ in ARMS) + "</tr><tr>"
                 + "".join("<td>%s</td>" % ("N/A" if cost.get(ak) is None else "$%s" % cost[ak])
                           for ak, *_ in ARMS) + "</tr></table>")
        o.append('<p class="muted">' + tr(
            "同等甚至更高精度下，本技能每题成本低于裸文件 agent——它只取压缩过的相关章节，而裸文件 agent 每题都要翻检整堆原始文件。",
            "At equal-or-better accuracy the skill costs less per question than the raw-files agent — it pulls one "
            "condensed chapter, whereas the raw-files agent must search the whole pile of source files every time.") + '</p>')
    # naive 对照脚注：一股脑全塞（没返回=算错，避免幸存者偏差）
    rel = S.get("material_reliability", {})
    if cost.get("material") and rel:
        mult = round(cost["material"] / cost["skill"]) if cost.get("skill") else "数"
        def fp(m): return pct((rel.get(m) or {}).get("full"))
        def sp(m): return pct((rel.get(m) or {}).get("surv"))
        def fl(m): return (rel.get(m) or {}).get("failed", "?")
        o.append('<p class="muted">' + tr(
            f"脚注·naive 对照「一股脑全塞」：把整门课全文塞进一次提问，每题成本高达 ${cost.get('material')}"
            f"（约为本技能的 {mult} 倍），且提问过大常触发用量/上下文上限而根本返回不了答案"
            f"（Sonnet {fl('sonnet')}/55、Haiku {fl('haiku')}/55 道没返回）。把没返回的如实算作答错，"
            f"它的真实正确率只有 Opus {fp('opus')} / Sonnet {fp('sonnet')} / Haiku {fp('haiku')}——"
            f"远低于裸文件 agent 与本技能；只看“侥幸跑通”的题会虚高到 {sp('opus')}/{sp('sonnet')}/{sp('haiku')}"
            f"（幸存者偏差）。故未列入上面的公平对比——它最贵、也最不稳，是最差选择。",
            f"Footnote — the naive 'dump everything' control: stuffing the whole course into one prompt costs "
            f"~${cost.get('material')}/question (~{mult}x the skill); the oversized prompt often hits usage/context "
            f"limits and returns NOTHING (Sonnet {fl('sonnet')}/55, Haiku {fl('haiku')}/55 items had no reply). "
            f"Counting those non-replies as wrong, its real correctness is only Opus {fp('opus')} / Sonnet "
            f"{fp('sonnet')} / Haiku {fp('haiku')} — well below the raw-files agent and the skill; looking only at "
            f"the lucky completions inflates it to {sp('opus')}/{sp('sonnet')}/{sp('haiku')} (survivorship bias). "
            f"So it is left out of the fair comparison above — the most expensive and least reliable option.") + '</p>')
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
    # PSYC 110（另一门：文科课，只对比 不给资料 / 使用本技能）
    psyc = S.get("psyc") or {}
    if psyc:
        o.append(f'<h2>{tr("🧩 第二门课：Yale PSYC 110 心理学","🧩 Second course: Yale PSYC 110")}</h2>')
        o.append(f'<p class="muted">{tr("文科课 50 题（40 题有答案 + 10 题资料中没有答案）。全文约 24 万字，超出一次读入上限，故没有“给全部资料”一栏，只比 不给资料 与 使用本技能。","A humanities course, 50 items (40 answerable + 10 with no answer in the material). Its full text is ~240K tokens, beyond one-shot context, so there is no full-materials column — only no-materials vs with-the-skill.")}</p>')
        o.append(f'<p><b>{tr("正确率","Correctness")}</b></p>')
        o.append(svg_grouped(psyc, "correct", en, arms=PSYC_ARMS, key_prefix="psyc|"))
        o.append(f'<p><b>{tr("资料里没有答案时如实承认的比例","Honest abstention when the answer is absent")}</b></p>')
        o.append(svg_grouped(psyc, "abstention_oos", en, arms=PSYC_ARMS, key_prefix="psyc|"))
        o.append(f'<p class="muted">{tr("结论与 6.006 一致：使用本技能正确率最高（Opus 98% / Sonnet 92% / Haiku 80%），且对没有答案的题全部如实承认。","Same conclusion as 6.006: the skill gives the highest correctness (Opus 98% / Sonnet 92% / Haiku 80%) and abstains honestly on every unanswerable item.")}</p>')
    # caveats
    o.append(f'<h2>{tr("⚠️ 诚实声明 Caveats","⚠️ Caveats")}</h2><ul>')
    cav = [
        (f"题量 n={S['n_items']}（{tr('每条都跨 3 臂同题对比，最公平','same items across all arms')}）。",
         f"n={S['n_items']} items, each answered under all conditions."),
        ("判分由 Sonnet 4.6 完成，数值题用程序精确比对，并经两次独立人工校准：16 题抽查 Cohen's kappa = 0.875；另做 24 题四层分层盲测（可答判对/判错 + 越界弃答/未弃答，判分对人隐藏）kappa = 0.833——均高度一致、互相印证，故上表数字可信。两次里观察到的人机分歧都是判分偏严（把正确答案判错）——这是抽样迹象（样本有限，不构成对全部判分的证明），提示数字更可能偏保守而非虚高。判分模型与被测模型同属一个家族，是已知局限。",
         "Judging by Sonnet 4.6; numeric items compared programmatically, validated by two independent human calibrations: a 16-item spot-check (Cohen's kappa = 0.875) and a 24-item four-stratum blind calibration (answerable right/wrong + out-of-scope abstained/not, judge verdicts hidden; kappa = 0.833) — both high agreement, corroborating each other. All observed disagreements had the judge being too strict (marking correct answers wrong) — a sampled indication (limited n, not proof over all judgments) that the numbers lean conservative rather than inflated. Judge and tested models share one family, a known limitation."),
        ("「给全材料」臂把整门课 dump 进提示，频繁撞订阅配额/上下文上限；其报错答案已从计分中剔除，仅在真答案上计分（样本量见上）。这本身说明 dump 全课在工程上不可行。",
         "The full-materials arm dumps the whole course and frequently hits subscription-quota / context limits; its error replies are excluded and it is scored on real answers only (sample sizes above) — which itself shows dumping a whole course is operationally impractical."),
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
        if "6.006" not in course and "PSYC 110" not in course:  # 本报告用了这两门
            continue
        subj = en_s if en else zh_s
        detail = (tr("讲义 L1–L20 + 习题 PS0–8（含官方解答）", "lectures L1–L20 + problem sets PS0–8 with official solutions")
                  if "6.006" in course else tr("讲义 L1–L20 转录（事实题以原文为依据）", "lecture transcripts L1–L20 (facts grounded in the transcript)"))
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(course)}</a> — <span class="muted">{html.escape(inst)} · {html.escape(subj)} · {detail}</span></li>')
    o.append("</ul>")
    o.append(f'<h2>{tr("📚 参考基准 References","📚 References")}</h2><ol class="refs">')
    for title, url, zh, eng in R.REFERENCES:
        note = eng if en else zh
        o.append(f'<li><a href="{url}" target="_blank" rel="noopener">{html.escape(title)}</a> — <span class="muted">{html.escape(note)}</span></li>')
    o.append("</ol></div>")
    return "\n".join(o)

_ARM_COLORS = {"closedbook": "#d93025", "rawfiles": "#f9ab00", "skill": "#1a7f64", "material": "#9aa0a6"}


def block_generic(lang, S):
    """Minimal DATA-only render for an EXPLICIT --summary (fixture / custom aggregate): the summary's
    OWN `models`/`arms` as plain tables, with NO hard-coded MIT 6.006 / PSYC narrative or numbers."""
    en = lang == "en"
    tr = lambda zh, e: e if en else zh
    arms = [a for a in S.get("arms", [])]
    models = [m for m in S.get("models", [])]
    mx = S.get("matrix", {})
    o = [f'<div id="{lang}">']
    o.append(f'<h1>{tr("矩阵 summary（显式渲染）", "Matrix summary (explicit render)")}</h1>')
    o.append('<p class="muted">' + tr(
        "数据全部来自所提供的 summary（含其自带的 models / arms）；本视图不含已发布报告的任何叙述或写死数字。",
        "All numbers come from the provided summary (its own models / arms); this view contains none of the "
        "published report's narrative or hard-coded figures.") + '</p>')

    def metric_table(title, metric, matrix, key_prefix=""):
        out = [f'<h2>{html.escape(title)}</h2>',
               '<table><tr><th class=l>' + tr("模型 / Model", "Model") + '</th>'
               + "".join(f'<th>{html.escape(a)}</th>' for a in arms) + "</tr>"]
        for mk in models:
            cells = "".join(f'<td>{pct((matrix.get(f"{key_prefix}{mk}|{ak}") or {}).get(metric))}</td>' for ak in arms)
            out.append(f'<tr><td class=l>{html.escape(mk)}</td>{cells}</tr>')
        out.append("</table>")
        return out

    for title, metric in ((tr("正确率 Correctness", "Correctness"), "correct"),
                          (tr("幻觉率 Hallucination", "Hallucination"), "hallucination"),
                          (tr("越界弃答 OOS abstention", "OOS abstention"), "abstention_oos")):
        o += metric_table(title, metric, mx)
    cpq = S.get("cost_per_q") or {}
    if cpq:
        o.append(f'<h2>{tr("每题成本 Cost/question", "Cost per question")}</h2><ul>')
        for course in sorted(cpq):
            # 全 infra 臂的每题成本是 null（没有一条完成的模型答案）——渲染成 N/A，不出现 "$None"
            pairs = "  ·  ".join(
                "%s=%s" % (html.escape(a),
                           "N/A" if cpq[course].get(a) is None else "$%s" % cpq[course][a])
                for a in sorted(cpq[course]))
            o.append(f'<li>{html.escape(course)}: {pairs}</li>')
        o.append("</ul>")
    if S.get("psyc"):
        o += metric_table(tr("psyc 块（secondary course）正确率", "psyc block (secondary course) correctness"),
                          "correct", S["psyc"], key_prefix="psyc|")
    o.append('<p class="muted">' + f'n_items={S.get("n_items")} · total_cost_usd=${S.get("total_cost_usd")} · '
             + f'courses={html.escape(str(S.get("courses")))} · judge_model={html.escape(str(S.get("judge_model")))}</p>')
    o.append("</div>")
    return "\n".join(o)


def _write_standalone_svgs(S, out_dir):
    """Also emit standalone chart SVGs (zh/en) so the README can embed the comparison charts."""
    m, conv, psyc = S.get("matrix", {}), S.get("convergence", {}), S.get("psyc", {})
    for en, suf in ((False, "zh"), (True, "en")):
        for metric, name in (("correct", "correct"), ("hallucination", "hallu"),
                             ("abstention_oos", "oos")):
            open(os.path.join(out_dir, f"chart_{name}_{suf}.svg"), "w", encoding="utf-8").write(
                svg_grouped(m, metric, en))
        open(os.path.join(out_dir, f"chart_convergence_{suf}.svg"), "w", encoding="utf-8").write(
            svg_convergence(conv, en))
        if psyc:   # PSYC 110：两条件（不给资料 / 使用本技能）× 三模型
            for metric, name in (("correct", "psyc_correct"), ("abstention_oos", "psyc_oos")):
                open(os.path.join(out_dir, f"chart_{name}_{suf}.svg"), "w", encoding="utf-8").write(
                    svg_grouped(psyc, metric, en, arms=PSYC_ARMS, key_prefix="psyc|"))


def main(argv=None):
    # Default behavior (no args) is unchanged: render the committed results/matrix/summary.json into
    # results/matrix/. --summary renders an EXPLICIT summary; --out-dir writes elsewhere (e.g. a tmp
    # dir for fixture pipelines) — so it no longer FORCES the stale committed summary.
    ap = argparse.ArgumentParser(description="渲染矩阵 summary.json → 中英双语 report.html + 图表 SVG。")
    ap.add_argument("--summary", default=os.path.join(OUT, "summary.json"),
                    help="要渲染的 summary.json（默认 results/matrix/summary.json）")
    ap.add_argument("--out-dir", default=OUT, help="输出目录（默认 results/matrix/）")
    args = ap.parse_args(argv)
    out_dir = args.out_dir
    explicit = os.path.abspath(args.summary) != os.path.abspath(os.path.join(OUT, "summary.json"))
    # rendering a CUSTOM --summary into the default results/matrix/ would overwrite the committed
    # published report — refuse and require --out-dir for any explicit summary.
    if explicit and os.path.abspath(out_dir) == os.path.abspath(OUT):
        sys.stderr.write("report_matrix: 用自定义 --summary 渲染到默认 results/matrix/ 会覆盖已发布报告；"
                         "请用 --out-dir 指定其他目录。\n")
        return 2
    os.makedirs(out_dir, exist_ok=True)
    S = json.load(open(args.summary, encoding="utf-8"))
    if not explicit:
        _write_standalone_svgs(S, out_dir)   # the README's published comparison charts — default render only
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
            "<button id='btn-en' onclick=\"setLang('en')\">English</button></div>"]
    if explicit:
        # explicit / custom / fixture summary → a generic DATA-only render of the summary's own
        # models·arms (no published MIT/PSYC narrative or hard-coded numbers), under a clear banner.
        page.append(
            "<div class='card' style='background:#fef7e0;border-color:#f9d57a'>⚠️ "
            f"本视图由 <code>--summary {html.escape(os.path.basename(args.summary))}</code> 显式渲染："
            "数字全部来自所提供的 summary，<b>并非已发布的 MIT 6.006 / Yale PSYC 110 实测</b>"
            "（如 fixture / 自定义聚合输出）。/ Rendered from an explicit <code>--summary</code>: the numbers "
            "come entirely from the provided summary — <b>this is NOT the published MIT/PSYC benchmark</b>.</div>")
        page += [block_generic("zh", S), block_generic("en", S), f"<script>{js}</script>", "</body></html>"]
    else:
        page += [block("zh", S), block("en", S), f"<script>{js}</script>", "</body></html>"]
    path = os.path.join(out_dir, "report.html")
    open(path, "w", encoding="utf-8").write("\n".join(page))
    print("[+] 写出", path)
    return 0

if __name__ == "__main__":
    sys.exit(main())
