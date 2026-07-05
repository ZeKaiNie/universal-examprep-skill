# 运行账本（B7 · run ledger）

每次**真实**运行（T5c live smoke、rejudge 导出、未来的矩阵生成/长会话/判分校准）向
`benchmark/runs/ledger.jsonl` 追加一行：run_id / kind / model / prompt_hash / workspace_hash /
transcript_path / summary_path / cost·tokens / exit_code / notes / created_at——“哪个模型、哪份
prompt、哪个工作区、花了多少、产物在哪”一处可查。

```bash
python benchmark/runs/ledger.py record --kind live_smoke --model claude-x --transcript /tmp/t.jsonl --exit-code 0
python benchmark/runs/ledger.py show --last 5
python benchmark/runs/ledger.py verify
```

诚实边界：
- `ledger.jsonl` 是**本地产物**（gitignored，可能含私有课程路径），提交的只有本模块、schema 与自撰样例；
- 哈希用于**可复现记账**（prompt/工作区是否变了），不是安全签名；cost/tokens 由调用方上报，账本不自测；
- **记账绝不影响运行**：接入方（run_live_smoke / rejudge --scores-out）把记账失败降级为提示。
