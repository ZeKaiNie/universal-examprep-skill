#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Print a Markdown table from the newest eval/results/*.json per backend+model."""
import glob
import json
import os
import sys

from agent_smoke import score  # noqa: E402  (same folder)

HERE = os.path.dirname(os.path.abspath(__file__))

for _s in ("stdout",):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main():
    latest = {}
    for f in sorted(glob.glob(os.path.join(HERE, "results", "*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        latest[(d["backend"], d["model"])] = d
    rows = ["| 模型 | 跑过的命令 | 编造命令 | setup→next→quiz→check | 引用出处 | 🟢/🟡/⚠️ | 嵌入图片 | 打开图片文件 | 中文回复 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for (backend, model), d in sorted(latest.items()):
        s = score(d["turns"])
        loop = "/".join("✅" if s[k] else "❌" for k in ("ran_setup", "ran_next", "ran_quiz", "ran_check"))
        rows.append("| %s `%s` | %s | %s | %s | %d | %d/%d/%d | %d | %d | %s |" % (
            backend, model, ", ".join(dict.fromkeys(s["commands_run"])) or "(none)",
            ", ".join(s["unknown_commands"]) or "无", loop, s["citations"],
            s["labels"]["green"], s["labels"]["yellow"], s["labels"]["warn"],
            s["images_embedded_in_reply"], s["image_files_opened_by_tools"], "是" if s["reply_is_chinese"] else "否"))
    print("\n".join(rows))


if __name__ == "__main__":
    main()
