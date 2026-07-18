# -*- coding: utf-8 -*-
"""scripts/retrieve.py: BM25 index build/search, zh bigram tokenization, terms.json
cross-lingual expansion, abstain gate, and the old-workspace no-index degradation contract.
Stdlib only; synthetic corpus fixtures (no real course data)."""
import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
sys.path.insert(0, SCRIPTS)
import retrieve  # noqa: E402

PY = sys.executable


def run_cli(*args):
    return subprocess.run([PY, os.path.join(SCRIPTS, "retrieve.py")] + list(args),
                          capture_output=True, text=True, encoding="utf-8")


def make_ws(chunks, terms=None, write_index=True):
    ws = tempfile.mkdtemp(prefix="rtv_")
    for c in chunks:
        path = os.path.join(ws, c["file"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(c["text"])
    os.makedirs(os.path.join(ws, "references"), exist_ok=True)
    terms_path = os.path.join(ws, "references", "terms.json")
    if terms is not None:
        with open(terms_path, "w", encoding="utf-8") as f:
            json.dump(terms, f, ensure_ascii=False)
    if write_index:
        integrity = {"wiki": []}
        for relative in sorted({chunk["file"] for chunk in chunks}):
            with open(os.path.join(ws, relative), "rb") as stream:
                integrity["wiki"].append({
                    "file": relative,
                    "sha256": hashlib.sha256(stream.read()).hexdigest(),
                })
        if terms is not None:
            with open(terms_path, "rb") as stream:
                integrity["terms"] = {
                    "file": "references/terms.json",
                    "sha256": hashlib.sha256(stream.read()).hexdigest(),
                }
        idx = retrieve.build_index(chunks, integrity=integrity)
        with open(os.path.join(ws, "references", "retrieval_index.json"), "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
    return ws


CORPUS = [
    {"id": "ch01/s01", "file": "references/wiki/ch01/s01_intro.md", "chapter": "1",
     "title": "Word-RAM model",
     "text": "The model of computation in this class is called the Word-RAM. "
             "Memory is an array of w-bit words; operations cost constant time."},
    {"id": "ch02/s01", "file": "references/wiki/ch02/s01_sort.md", "chapter": "2",
     "title": "Sorting lower bounds",
     "text": "Comparison sorting requires Omega(n log n) comparisons in the worst case. "
             "Merge sort achieves this bound and is stable."},
    {"id": "ch03/s01", "file": "references/wiki/ch03/s01_bystander.md", "chapter": "3",
     "title": "Bystander effect",
     "text": "The bystander effect: the presence of others reduces helping. "
             "Darley and Latane ran the classic smoke-filled room experiment."},
]


class Tokenize(unittest.TestCase):
    def test_ascii_words_lowercased(self):
        self.assertEqual(retrieve.tokenize("Word-RAM Model 2024"), ["word-ram", "model", "2024"])

    def test_cjk_becomes_bigrams(self):
        self.assertEqual(retrieve.tokenize("旁观者效应"), ["旁观", "观者", "者效", "效应"])

    def test_single_cjk_char_is_unigram(self):
        self.assertEqual(retrieve.tokenize("树"), ["树"])

    def test_mixed_language_query(self):
        toks = retrieve.tokenize("什么是Word-RAM模型")
        self.assertIn("word-ram", toks)
        self.assertIn("什么", toks)
        self.assertIn("模型", toks)


class IndexBuild(unittest.TestCase):
    def test_index_shape_and_postings(self):
        idx = retrieve.build_index(CORPUS)
        self.assertEqual(idx["version"], retrieve.INDEX_VERSION)
        self.assertEqual(idx["n_docs"], 3)
        self.assertEqual(len(idx["docs"]), 3)
        self.assertIn("word-ram", idx["vocab"])       # posting exists for the distinctive term
        self.assertGreater(idx["avgdl"], 0)

    def test_missing_field_fails_loud(self):
        with self.assertRaises(SystemExit):
            retrieve.build_index([{"id": "x", "file": ""}])


class Search(unittest.TestCase):
    def test_relevant_chunk_ranks_first(self):
        ws = make_ws(CORPUS)
        try:
            hits, _ = retrieve.search(ws, retrieve.load_index(ws), "word-ram model of computation")
            self.assertTrue(hits)
            self.assertEqual(hits[0]["id"], "ch01/s01")
            self.assertGreater(hits[0]["score"], 0)
            self.assertIn("Word-RAM", hits[0]["text"])   # snippet comes from the chunk file
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_cross_lingual_terms_expansion(self):
        # zh query hits the EN bystander chunk only via terms.json
        terms = {"旁观者效应": ["bystander effect"]}
        ws = make_ws(CORPUS, terms=terms)
        try:
            hits, _ = retrieve.search(ws, retrieve.load_index(ws), "旁观者效应 是什么")
            self.assertTrue(hits, "terms.json 扩展后 zh 查询应命中 en 材料")
            self.assertEqual(hits[0]["id"], "ch03/s01")
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_zero_hit_returns_empty(self):
        ws = make_ws(CORPUS)
        try:
            hits, _ = retrieve.search(ws, retrieve.load_index(ws), "quantum chromodynamics")
            self.assertEqual(hits, [])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_min_score_gates_low_hits(self):
        ws = make_ws(CORPUS)
        try:
            idx = retrieve.load_index(ws)
            hits, _ = retrieve.search(ws, idx, "word-ram", min_score=10 ** 6)
            self.assertEqual(hits, [], "高于任何真实分值的门限应清空命中（弃答）")
        finally:
            shutil.rmtree(ws, ignore_errors=True)


class SnippetFromOwnChunk(unittest.TestCase):
    """Codex 评审回归：docs 存每块自己的 text——命中 ch05#s02 时摘要必须来自该块本身，
    不能重开整章文件、在【第一个】查询词出现处开窗而把 s01 的内容顶到 s02 的名下。"""

    CH1 = ("FIRSTMARK introduction paragraph. The widget concept appears once here, "
           "purely in passing, with no depth at all.")
    CH2 = ("SECONDMARK splay tree section. The widget rotates; widget amortized analysis; "
           "widget access theorem details live here.")

    def _ws_shared_file(self, strip_text=False):
        # 两个 chunk 共享同一个章文件（ingest 的真实形态）；索引文本与盘上文件都可控
        ws = tempfile.mkdtemp(prefix="rtv2_")
        self.addCleanup(shutil.rmtree, ws, ignore_errors=True)
        wiki = os.path.join(ws, "references", "wiki")
        os.makedirs(wiki)
        with open(os.path.join(wiki, "ch05.md"), "w", encoding="utf-8") as f:
            f.write(self.CH1 + "\n\n" + self.CH2)
        chunks = [
            {"id": "ch05#s01", "file": "references/wiki/ch05.md", "chapter": "5",
             "title": "intro", "text": self.CH1},
            {"id": "ch05#s02", "file": "references/wiki/ch05.md", "chapter": "5",
             "title": "splay", "text": self.CH2},
        ]
        relative = "references/wiki/ch05.md"
        with open(os.path.join(ws, relative), "rb") as stream:
            integrity = {"wiki": [{
                "file": relative,
                "sha256": hashlib.sha256(stream.read()).hexdigest(),
            }]}
        idx = retrieve.build_index(chunks, integrity=integrity)
        if strip_text:
            for d in idx["docs"]:
                d.pop("text", None)                    # 旧版索引形态（升级前建出的工作区）
        with open(os.path.join(ws, "references", "retrieval_index.json"), "w",
                  encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False)
        return ws

    def test_snippet_comes_from_the_hit_chunk(self):
        ws = self._ws_shared_file()
        hits, _ = retrieve.search(ws, retrieve.load_index(ws), "widget splay")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "ch05#s02")   # splay + 3×widget 应压过 s01
        # 「widget」在整章文件里首现于 s01——按文件开窗会显示 FIRSTMARK；按块开窗显示 SECONDMARK
        self.assertIn("SECONDMARK", hits[0]["text"], hits[0]["text"])
        self.assertNotIn("FIRSTMARK", hits[0]["text"], hits[0]["text"])

    def test_index_docs_store_chunk_text(self):
        ws = self._ws_shared_file()
        idx = retrieve.load_index(ws)
        self.assertEqual([d["text"] for d in idx["docs"]], [self.CH1, self.CH2])

    def test_old_index_without_text_falls_back_to_file_scan(self):
        ws = self._ws_shared_file(strip_text=True)
        hits, _ = retrieve.search(ws, retrieve.load_index(ws), "widget splay")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["id"], "ch05#s02")
        self.assertTrue(hits[0]["text"], "旧索引降级路径仍须给出非空摘要（读章文件开窗）")
        self.assertIn("MARK", hits[0]["text"])        # 摘要仍来自真实章文件内容


class CliContract(unittest.TestCase):
    def test_hits_exit_0_and_json_shape(self):
        ws = make_ws(CORPUS)
        try:
            r = run_cli("--workspace", ws, "--query", "merge sort lower bound", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            payload = json.loads(r.stdout)
            self.assertFalse(payload["abstain"])
            self.assertEqual(payload["hits"][0]["id"], "ch02/s01")
            for k in ("id", "file", "score", "text"):
                self.assertIn(k, payload["hits"][0])   # public citation-shaped hit contract
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_abstain_exit_4(self):
        ws = make_ws(CORPUS)
        try:
            r = run_cli("--workspace", ws, "--query", "totally unrelated nonsense zzz", "--json")
            self.assertEqual(r.returncode, 4, "零命中必须走弃答退出码")
            self.assertTrue(json.loads(r.stdout)["abstain"])
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_no_index_degrades_exit_3(self):
        ws = make_ws(CORPUS, write_index=False)
        try:
            r = run_cli("--workspace", ws, "--query", "anything")
            self.assertEqual(r.returncode, 3, "无索引 = 老工作区，须走降级码而非报错")
            self.assertIn("no_index", r.stderr)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_wrong_index_version_fails_loud(self):
        ws = make_ws(CORPUS)
        try:
            p = os.path.join(ws, "references", "retrieval_index.json")
            with open(p, "r", encoding="utf-8") as f:
                idx = json.load(f)
            idx["version"] = 999
            with open(p, "w", encoding="utf-8") as f:
                json.dump(idx, f)
            r = run_cli("--workspace", ws, "--query", "word-ram")
            self.assertEqual(r.returncode, 2)
            self.assertIn("version", r.stderr)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_wiki_hash_drift_fails_closed(self):
        ws = make_ws(CORPUS)
        try:
            relative = CORPUS[0]["file"]
            path = os.path.join(ws, *relative.split("/"))
            integrity = []
            for indexed_file in sorted({chunk["file"] for chunk in CORPUS}):
                indexed_path = os.path.join(ws, *indexed_file.split("/"))
                with open(indexed_path, "rb") as stream:
                    digest = hashlib.sha256(stream.read()).hexdigest()
                integrity.append({"file": indexed_file, "sha256": digest})
            index = retrieve.build_index(
                CORPUS,
                integrity={"wiki": integrity},
            )
            with open(os.path.join(ws, "references", "retrieval_index.json"),
                      "w", encoding="utf-8") as stream:
                json.dump(index, stream)
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("\nchanged")
            result = run_cli("--workspace", ws, "--query", "word-ram")
            self.assertEqual(2, result.returncode)
            self.assertIn("stale_index", result.stderr)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_present_teaching_layer_requires_exact_integrity_binding(self):
        ws = make_ws(CORPUS)
        try:
            teaching_relative = "references/teaching_examples.json"
            teaching_path = os.path.join(ws, *teaching_relative.split("/"))
            with open(teaching_path, "w", encoding="utf-8") as stream:
                json.dump([], stream)

            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)

            index_path = os.path.join(ws, "references", "retrieval_index.json")
            with open(index_path, "r", encoding="utf-8") as stream:
                index = json.load(stream)
            with open(teaching_path, "rb") as stream:
                teaching_sha = hashlib.sha256(stream.read()).hexdigest()
            index["integrity"]["teaching_examples"] = {
                "file": teaching_relative, "sha256": teaching_sha,
            }
            with open(index_path, "w", encoding="utf-8") as stream:
                json.dump(index, stream)
            self.assertIsNotNone(retrieve.load_index(ws))

            # Mutating only the teaching layer must stale the otherwise intact
            # wiki/quiz/content index.
            with open(teaching_path, "w", encoding="utf-8") as stream:
                json.dump([{"id": "new-example"}], stream)
            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_present_terms_require_exact_integrity_binding(self):
        ws = make_ws(CORPUS)
        try:
            terms_relative = "references/terms.json"
            terms_path = os.path.join(ws, *terms_relative.split("/"))
            with open(terms_path, "w", encoding="utf-8") as stream:
                json.dump({"旁观者效应": ["bystander effect"]}, stream,
                          ensure_ascii=False)

            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)

            index_path = os.path.join(ws, "references", "retrieval_index.json")
            with open(index_path, "r", encoding="utf-8") as stream:
                index = json.load(stream)
            with open(terms_path, "rb") as stream:
                terms_sha = hashlib.sha256(stream.read()).hexdigest()
            index["integrity"]["terms"] = {
                "file": terms_relative, "sha256": terms_sha,
            }
            with open(index_path, "w", encoding="utf-8") as stream:
                json.dump(index, stream)
            loaded = retrieve.load_index(ws)
            self.assertIsNotNone(loaded)

            with open(terms_path, "w", encoding="utf-8") as stream:
                json.dump({"旁观者效应": ["changed"]}, stream,
                          ensure_ascii=False)
            # The glossary can change after the index has been loaded.  Search
            # must verify and parse one stable byte snapshot against the hash
            # carried by that already-loaded index, rather than reopening an
            # unbound live glossary.
            with self.assertRaises(SystemExit):
                retrieve.search(ws, loaded, "旁观者效应")
            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_bound_malformed_terms_fail_closed(self):
        ws = make_ws(CORPUS, terms={"bad": ["duplicate", "duplicate"]})
        try:
            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_teaching_integrity_key_cannot_bind_an_arbitrary_file(self):
        ws = make_ws(CORPUS)
        try:
            teaching_path = os.path.join(ws, "references", "teaching_examples.json")
            with open(teaching_path, "w", encoding="utf-8") as stream:
                json.dump([], stream)
            arbitrary = CORPUS[0]["file"]
            arbitrary_path = os.path.join(ws, *arbitrary.split("/"))
            with open(arbitrary_path, "rb") as stream:
                digest = hashlib.sha256(stream.read()).hexdigest()
            index_path = os.path.join(ws, "references", "retrieval_index.json")
            with open(index_path, "r", encoding="utf-8") as stream:
                index = json.load(stream)
            index["integrity"]["teaching_examples"] = {
                "file": arbitrary, "sha256": digest,
            }
            with open(index_path, "w", encoding="utf-8") as stream:
                json.dump(index, stream)
            with self.assertRaises(SystemExit):
                retrieve.load_index(ws)
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_bad_usage(self):
        ws = make_ws(CORPUS)
        try:
            self.assertEqual(run_cli("--workspace", ws, "--query", "x", "-k", "0").returncode, 2)
            self.assertEqual(run_cli("--workspace", ws, "--query", "x",
                                     "--min-score", "-1").returncode, 2)
        finally:
            shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
