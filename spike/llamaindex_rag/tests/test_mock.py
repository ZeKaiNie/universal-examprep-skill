# -*- coding: utf-8 -*-
"""stdlib-only regression for the RAG spike's mock path + the lazy-import seam.

Runs on a CLEAN checkout with ZERO heavy deps installed:
    cd spike/llamaindex_rag && python -m unittest discover -s tests
It deliberately lives OUTSIDE the repo-root/benchmark test suites (never wired into CI).
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import contract          # noqa: E402
import backend as B      # noqa: E402
import rag               # noqa: E402

# 若 mock 路径 import 了这些，就是隔离被破坏（--mock 必须纯 stdlib）。
HEAVY = ("llama_index", "llama_index.core", "openai", "numpy", "faiss",
         "chromadb", "torch", "sentence_transformers")


class MockPipeline(unittest.TestCase):
    def setUp(self):
        self.out = tempfile.mkdtemp(prefix="ragspike_")
        self.addCleanup(shutil.rmtree, self.out, True)

    def _run(self, *extra):
        return rag.main(["--mock", "--results-dir", self.out, *extra])

    def _raw(self):
        return contract.load_jsonl(os.path.join(self.out, "raw.jsonl"))

    def test_mock_runs_end_to_end(self):
        self.assertEqual(self._run(), 0)
        raw = self._raw()
        self.assertEqual(len(raw), 6)
        for r in raw:
            self.assertEqual(set(r.keys()), {"id", "question", "rag"})   # judge-compatible 形态

    def test_mock_imports_only_stdlib(self):
        before = set(sys.modules)
        self._run()
        for h in HEAVY:
            self.assertNotIn(h, sys.modules, "mock 路径不应 import 重依赖：%s" % h)
        # 确认这一跑确实没新引入重依赖
        self.assertFalse(set(HEAVY) & (set(sys.modules) - before))

    def test_probe_abstains_exactly(self):
        self._run()
        raw = {r["id"]: r for r in self._raw()}
        self.assertEqual(raw["mini_probe_offscope"]["rag"], contract.ABSTAIN)
        self.assertTrue(contract.looks_abstained(raw["mini_probe_offscope"]["rag"]))
        for k, v in raw.items():
            if k != "mini_probe_offscope":
                self.assertFalse(contract.looks_abstained(v["rag"]), "可答项不应弃答：%s" % k)

    def _read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_deterministic_byte_identical(self):
        self._run()
        a = self._read(os.path.join(self.out, "raw.jsonl"))
        out2 = tempfile.mkdtemp(prefix="ragspike2_")
        self.addCleanup(shutil.rmtree, out2, True)
        rag.main(["--mock", "--results-dir", out2])
        b = self._read(os.path.join(out2, "raw.jsonl"))
        self.assertEqual(a, b)

    def test_summary_marks_placeholder(self):
        self._run()
        s = json.loads(self._read(os.path.join(self.out, "summary.json")))
        self.assertTrue(s["mock"])
        self.assertEqual(s["abstained"], 1)             # 只有越界探针弃答
        self.assertEqual(s["answered"], 5)
        self.assertIn("placeholder", s["note"])

    def test_limit(self):
        self._run("--limit", "2")
        self.assertEqual(len(self._raw()), 2)

    def test_self_test_exit_zero(self):
        self.assertEqual(rag.main(["--self-test"]), 0)

    def test_llamaindex_backend_top_is_stdlib_and_factory_no_guard(self):
        before = set(sys.modules)
        import llamaindex_backend            # noqa: F401  模块顶必须可安全 import
        self.assertFalse(set(HEAVY) & (set(sys.modules) - before),
                         "llamaindex_backend 模块顶不得 import 重依赖")
        cfg = dict(contract.DEFAULT_CFG)
        cfg["backend"] = "mock"
        self.assertEqual(type(B.make_backend(cfg)).__name__, "MockBackend")

    def test_real_without_key_fails_loud(self):
        # --real 无密钥应明确报错（不静默、不联网）
        with self.assertRaises(SystemExit):
            rag.main(["--real", "--results-dir", self.out])

    def test_looks_abstained_mirrors_judge_markers(self):
        # 与 benchmark/judge.py 的 ABSTAIN_MARKERS 逐字一致：含英文标记、大小写不敏感
        for s in ("材料中未涵盖", "无法确定", "未提及", "没有提到", "不确定",
                  "Cannot determine from the provided material", "not covered",
                  "I don't know the answer", "I'm not sure", "It is not in the material"):
            self.assertTrue(contract.looks_abstained(s), "应判为弃答：%r" % s)
        for s in ("The answer is 42", "marlin.wal", "8 kilobytes", "", "covered in lecture 3"):
            self.assertFalse(contract.looks_abstained(s), "不应判为弃答：%r" % s)

    def test_default_min_score_is_025(self):
        # 弃答门缺省 min_score = 0.25（与 DEFAULT_CFG/config/README 一致；0.15 分支已死）
        self.assertEqual(contract.DEFAULT_CFG["min_score"], 0.25)

        class _Stub(B.Backend):
            def __init__(self, config, score):
                super().__init__(config)
                self._score = score

            def index(self, materials):
                pass

            def retrieve(self, question):
                return [B.Chunk("x", self._score)]

            def generate(self, question, chunks):
                return "GEN"

        self.assertEqual(_Stub({}, 0.20).answer_for_item({"question": "q"}, ""), B.ABSTAIN)  # <0.25 → 弃答
        self.assertEqual(_Stub({}, 0.30).answer_for_item({"question": "q"}, ""), "GEN")       # ≥0.25 → 生成

    def test_mock_flag_overrides_config_backend(self):
        # config backend:llamaindex + --mock → 仍走 mock（flag 盖过 config backend）
        cfgp = os.path.join(self.out, "cfg.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"backend": "llamaindex", "mock": False, "results_dir": self.out}, f)
        self.assertEqual(rag.main(["--mock", "--config", cfgp, "--results-dir", self.out]), 0)
        s = json.loads(self._read(os.path.join(self.out, "summary.json")))
        self.assertTrue(s["mock"])
        self.assertEqual(s["backend"], "mock")

    def test_real_flag_overrides_config_mock_backend(self):
        # config backend:mock + --real → 走真跑（backend 被 flag 清掉），无 key → fail-loud
        cfgp = os.path.join(self.out, "cfg.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"backend": "mock", "mock": True}, f)
        with self.assertRaises(SystemExit):
            rag.main(["--real", "--config", cfgp, "--results-dir", self.out])

    def test_explicit_backend_flag_beats_mode_flag(self):
        # 显式 --backend mock 优先级最高，即便同时 --real；且 summary 从实际后端推导，不被 --real 错标
        cfgp = os.path.join(self.out, "cfg.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"results_dir": self.out}, f)
        self.assertEqual(rag.main(["--real", "--backend", "mock", "--config", cfgp,
                                   "--results-dir", self.out]), 0)
        s = json.loads(self._read(os.path.join(self.out, "summary.json")))
        self.assertEqual(s["backend"], "mock")
        self.assertTrue(s["mock"])          # 跑的是 mock → summary 必须标 mock:true（不因 --real 错标 real）

    def test_resolve_backend_name_single_source(self):
        # summary/密钥校验的事实源：resolve_backend_name 与 make_backend 实际选择一致
        self.assertEqual(B.resolve_backend_name({"mock": True}), "mock")
        self.assertEqual(B.resolve_backend_name({"mock": False}), "llamaindex")
        self.assertEqual(B.resolve_backend_name({"backend": "mock", "mock": False}), "mock")
        self.assertEqual(B.resolve_backend_name({"backend": "real"}), "llamaindex")
        self.assertEqual(B.resolve_backend_name({}), "mock")           # 默认 mock

    def test_config_relative_paths_anchored_to_config_dir(self):
        # config 里的相对路径按 config 文件所在目录解析（不是 cwd）——隔离不被破坏
        d = tempfile.mkdtemp(prefix="ragcfg_")
        self.addCleanup(shutil.rmtree, d, True)
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"results_dir": "results", "items_path": "fixtures/x.jsonl"}, f)
        args = rag.build_argparser().parse_args(["--config", cfgp])
        cfg = contract.load_config(args)
        self.assertEqual(os.path.normpath(cfg["results_dir"]), os.path.normpath(os.path.join(d, "results")))
        self.assertEqual(os.path.normpath(cfg["items_path"]),
                         os.path.normpath(os.path.join(d, "fixtures", "x.jsonl")))
        self.assertTrue(os.path.isabs(cfg["results_dir"]) and os.path.isabs(cfg["items_path"]))

    def test_cli_results_dir_not_anchored_to_config(self):
        # CLI --results-dir 按 cwd（标准），不被 config 目录锚定
        d = tempfile.mkdtemp(prefix="ragcfg2_")
        self.addCleanup(shutil.rmtree, d, True)
        cfgp = os.path.join(d, "config.json")
        with open(cfgp, "w", encoding="utf-8") as f:
            json.dump({"results_dir": "results"}, f)
        args = rag.build_argparser().parse_args(["--config", cfgp, "--results-dir", self.out])
        cfg = contract.load_config(args)
        self.assertEqual(cfg["results_dir"], self.out)      # CLI 值原样，不锚定到 config 目录


if __name__ == "__main__":
    unittest.main()
