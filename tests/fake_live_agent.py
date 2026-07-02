# -*- coding: utf-8 -*-
"""Deterministic stand-in agent for T5c live-smoke tests (NO model, NO network).

Reads the full tutoring prompt as argv[1], answers the LAST student line with a canned, contract-
compliant tutor reply (bank-tagged quiz items, canonical provenance labels, checkpoint-respecting
resume language). With FAKE_DRIFT=1 it misbehaves on quiz turns (invents an off-bank question) so the
pipeline's detectors can be proven to actually gate the exit code."""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

prompt = sys.argv[1] if len(sys.argv) > 1 else ""
m = re.findall(r"学生：(.*)", prompt)
last = m[-1].strip() if m else ""
drift = os.environ.get("FAKE_DRIFT") == "1"

QUIZ = {
    "从阶段1考我": "[#stack_lifo_1] 栈遵循什么访问顺序？",
    "再来一道阶段1": "[#queue_fifo_1] 队列的出队顺序是什么？",
    "考我一道阶段2": "[#tree_height_1] 一棵只有根节点的树，高度是多少？",
}

reply = None
for key, q in QUIZ.items():
    if key in last:
        leak = os.environ.get("FAKE_LEAK") == "1"
        if leak and "阶段2" in key:                    # leak on a NON-choice item (choice options are
            reply = "[#bst_property_1] 二叉搜索树的中序遍历有什么特点？（标准答案: 从小到大 有序）"
        else:                                          # legitimately shown and exempt from the check)
            reply = "[#made_up_99] 跳表的期望复杂度是多少？" if drift else q
        break
if reply is None:
    if "回来" in last and "接着复习" in last:
        reply = "欢迎回来！我们接着阶段2继续复习二叉搜索树。"
    elif "回来" in last:
        reply = "欢迎回来！我们从阶段1继续复习栈与队列，接着上次的进度往下走。"
    elif "FIFO" in last:
        reply = "不对哦。🟢 来自资料：栈是 LIFO（后进先出），我把这题记进错题本稍后复盘。"
    elif "解释一下栈" in last:
        reply = "🟢 来自资料：栈只在同一端（栈顶）压入和弹出，最后进的元素最先出来，所以是 LIFO。"
    elif "二叉搜索树" in last:
        reply = ("🟢 来自资料：二叉搜索树中任一节点左子树的值都小于它、右子树的值都大于它，"
                 "所以中序遍历得到从小到大的有序序列。")
    elif "进入阶段2" in last:
        reply = "好，阶段1 完成，我们进入阶段2 树，继续期末复习。"
    elif "聊聊游戏" in last:
        reply = "先专注期末复习吧，冲刺阶段每一分钟都很关键；复习完这章我们就休息。"
    else:
        reply = "🟢 来自资料：我们继续按复习计划推进。"

sys.stdout.write(reply)
