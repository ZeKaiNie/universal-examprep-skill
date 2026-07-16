# -*- coding: utf-8 -*-
"""B6 · deterministic ingest → validate INTEGRATION fixture (closes the audited Tier-1 gap).

The audit honestly recorded: the validator's LOGIC was unit-tested on hand-made fixtures, but nothing
ran the real `validate_workspace.py` CLI on a REAL `ingest.py` product — so a drift between what ingest
emits and what the validator demands could ship silently. This suite runs BOTH real CLIs end-to-end
(subprocess, exactly as a user would) on a self-authored mini course: happy path must exit 0, and a
tampered/corrupted product must exit 1/2. Pure stdlib; zero cost; runs in CI via root discovery."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INGEST = os.path.join(ROOT, "scripts", "ingest.py")
VALIDATE = os.path.join(ROOT, "scripts", "validate_workspace.py")
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8ffff3f0005fe02fe0def46b80000000049454e44ae426082"
)

RAW = {
    "course_name": "集成自检课",
    "phases": [
        {"phase_num": 1, "phase_name": "基础", "wiki_filename": "ch1_basics.md",
         "wiki_content": "# 第一章 基础\n栈是后进先出的结构。"},
        {"phase_num": 2, "phase_name": "进阶", "wiki_filename": "ch2_more.md",
         "wiki_content": "# 第二章 进阶\n队列是先进先出的结构。"},
    ],
    "quiz_bank": [
        {"id": "c1", "chapter": 1, "type": "choice", "question": "栈的顺序?",
         "options": ["A.FIFO", "B.LIFO", "C.随机", "D.无序"], "answer": "B",
         "explanation": "后进先出", "source": "material", "ai_generated": False},
        {"id": "s1", "chapter": 1, "type": "subjective", "question": "解释队列。",
         "answer": "先进先出", "keywords": ["先进先出"], "source": "teacher", "ai_generated": False},
        {"id": "t1", "chapter": 2, "type": "true_false", "question": "队列是 FIFO。",
         "answer": True, "source": "material", "ai_generated": False},
        {"id": "f1", "chapter": 2, "type": "fill_blank", "question": "栈顶操作是 ____。",
         "answer": "push/pop", "source": "material", "ai_generated": False},
        {"id": "v1", "chapter": 2, "type": "subjective", "question": "根据图示判断结构。",
         "answer": "见图", "source": "material", "ai_generated": False,
         "requires_assets": True, "source_file": "lectures/ch02.pdf", "source_pages": [3],
         "assets": [{"path": "references/assets/v1_p3.png", "role": "question_context",
                     "type": "page_image", "caption": "原页截图"}]},
    ],
}


def _run(script, *args):
    return subprocess.run([sys.executable, script] + list(args),
                          capture_output=True, text=True, encoding="utf-8")


def _ingest(raw, out_dir):
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    try:
        return _run(INGEST, "-i", path, "-o", out_dir)
    finally:
        os.remove(path)


class IngestThenValidateCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _build_workspace(self):
        r = _ingest(RAW, self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # ingest does NOT create assets/ — the agent writes the image file (documented contract);
        # simulate that so the visual item v1 is complete before validation
        assets = os.path.join(self.tmp, "references", "assets")
        os.makedirs(assets, exist_ok=True)
        with open(os.path.join(assets, "v1_p3.png"), "wb") as f:
            f.write(PNG)
        return self.tmp

    def test_real_ingest_product_passes_real_validator_cli(self):
        ws = self._build_workspace()
        r = _run(VALIDATE, ws)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)   # THE integration guarantee

    def test_tampered_source_enum_fails_validator(self):
        ws = self._build_workspace()
        bank_path = os.path.join(ws, "references", "quiz_bank.json")
        bank = json.load(open(bank_path, encoding="utf-8"))
        bank[0]["source"] = "invented_src"                        # illegal provenance enum
        json.dump(bank, open(bank_path, "w", encoding="utf-8"), ensure_ascii=False)
        r = _run(VALIDATE, ws)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)    # error, not a warning

    def test_missing_visual_asset_fails_closed(self):
        ws = self._build_workspace()
        os.remove(os.path.join(ws, "references", "assets", "v1_p3.png"))
        r = _run(VALIDATE, ws)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)    # requires_assets without file = error

    def test_corrupted_bank_json_exits_2(self):
        ws = self._build_workspace()
        with open(os.path.join(ws, "references", "quiz_bank.json"), "w", encoding="utf-8") as f:
            f.write("{ not json")
        r = _run(VALIDATE, ws)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)    # fatal, structured


if __name__ == "__main__":
    unittest.main(verbosity=2)
