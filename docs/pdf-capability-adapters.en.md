# PDF capability adapters

English · [中文](pdf-capability-adapters.md)

This repository separates “how to organize exam-preparation textbooks” from “how a particular Agent operates on PDFs.” The former is handled by this repository’s `exam-study-guide` skill and `scripts/study_guide_render.py`. The latter may reuse host-native capabilities, but it must not change this repository’s contracts for provenance labels, showing prompt images before answer images, path safety, and page-by-page visual acceptance.

Every Study Guide/PDF route on this page belongs only to explicit `processing_mode=full`. In lightweight mode, even a saved `artifact_mode=visual` preference is dormant and effective output remains `chat`; a one-shot PDF request cannot bypass the full-processing gate. Host-native visual reading of PDFs for a lightweight page batch is not “generating a Study Guide.” Its evidence must remain in `.lightweight/assets/` and follow the lightweight-session contract.

See [`pdf-capability-adapters.json`](pdf-capability-adapters.json) for the machine-readable routing table. GitHub references in that table are pinned to `review_commit`. Installation commands still obtain the current upstream version, so they may be run only with the user’s explicit consent, followed by another review of the license, the skill actually loaded, and behavioral differences.

## Routing order

1. If the host already provides a PDF skill and detection succeeds, select `native`. After the chapter preflight, use the repository renderer to produce validated, self-contained HTML, then have the native capability print/convert that HTML to the canonical PDF path. Do not install it again, and do not require Edge/Chrome.
2. If no native capability exists, use this repository’s `exam-study-guide` with the local Edge/Chrome browser-print fallback.
3. If the fallback is insufficient and the user consents, recommend only the official source for that host.
4. If dependencies, a browser, or rendering capability are still unavailable, fail explicitly and list what is missing. Never leave behind a counterfeit deliverable containing raw LaTeX.

Every route that actually runs must follow the same order: **validate/import the typed chapter teaching manifest → detect and select a backend → run dependency preflight for the current chapter and backend → generate and validate HTML → generate the PDF → `study_guide_qa.py render` → inspect every page visually → explicitly accept**. Selecting a native capability does not mean generating a file first; dependency preflight must still occur before chapter HTML rendering.

Formula rendering uses the reviewed and pinned `latex2mathml==3.60.0` (MIT, reviewed commit `de87cf0f228416e3152218c12b8bdb4ee6f4ecca`). This version supports the repository’s Python 3.7+ range. The current upstream version requires a newer Python, so an un-installable “latest version” command must not be offered to older runtimes. Wheel/sdist SHA-256 values, sources, and license information are recorded under `audited_dependencies` in the machine-readable table. Initial material preflight handles only PDF reading/rendering dependencies that can be determined from the source materials. It does not infer from a `visual` preference that the current chapter necessarily contains formulas or will necessarily use a browser. After the chapter source of truth is persisted and the backend selected, run:

```text
python scripts/check_deps.py --workspace <ws> --chapter <N> --artifact-mode visual --pdf-backend <native|browser|html>
```

Only when the current chapter actually contains canonical formulas—including typed-manifest formulas and substituted expressions—is `latex2mathml` marked as required. Unrecovered evidence such as `formula_hint`, control characters, or mojibake produces `chapter_math_status=needs_recovery` and blocks visual output; it must never be interpreted as “this chapter has no formulas.” Edge/Chrome is marked as required only when the `browser` backend is selected. If anything is missing, provide only the pinned installation recommendation and still require user consent.

`native` does not mean that placing any PDF in the directory completes the workflow. After generating HTML with `--pdf-backend native`, the chapter receipt remains `awaiting_native_pdf` and exposes the `html_sha256` and `conversion_start_gate_sha256` that this conversion must use. Before conversion, the host adapter records those two values, the machine-table `adapter_id`, the exact version actually loaded, and the UTC start time. It consumes only that exact HTML and writes the result to the canonical path `<ws>/study_guide/chNN.pdf`. After recording the UTC completion time, run:

```text
python scripts/study_guide_render.py --workspace <ws> --chapter <N> --pdf-backend native --bind-native --native-pdf-path <ws>/study_guide/chNN.pdf --native-adapter-id <declared-id> --native-adapter-version <exact-version> --conversion-input-html-sha256 <receipt-html-sha256> --conversion-start-gate-sha256 <receipt-gate-sha256> --conversion-started-at <UTC-Z> --conversion-completed-at <UTC-Z> --json
```

That command does not invoke the adapter, access the network, install dependencies, or render a PDF. Under the workspace publication lock, it revalidates the current typed manifest, HTML, full-processing/runtime gate, adapter allow-list, canonical PDF path/signature/hash, and time ordering, then atomically changes the receipt to `qa_pending`. Any mismatch preserves the original unbound receipt. Consequently, bypass writes, stale PDFs, and hand-edited receipts cannot enter QA. The adapter ID/version is host-declared identity included in the conversion hash; it is not proof of host sandbox behavior. If the host cannot report the exact version actually loaded, it cannot use `native` binding and must explicitly fall back to `browser` or deliver HTML only.

Regardless of route, the final PDF must be rendered page by page to PNG, the latest render inspected, and the following run only after known visual defects reach zero:

```text
python scripts/study_guide_qa.py --workspace <ws> --chapter <N> --json render
python scripts/study_guide_qa.py --workspace <ws> --chapter <N> accept --inspected-pages all --reviewer <name> --reviewer-kind agent --page-verdict 1=pass
```

For a multi-page PDF, pass `--page-verdict N=pass:<notes>` once for every page. The command above is the minimal shape for a one-page PDF.

Delivery is allowed only while the receipt hashes still match and `visual_qa.status=ready`. An external skill supplies tool capability; it does not replace acceptance.

## Agent-specific adapters

### Codex

- Prefer the `pdf` skill already visible in the runtime.
- Native binding uses the stable ID `codex.pdf` from the machine-readable table. The version must be the exact skill/plugin version actually loaded by the host, never `latest` or a guessed value.
- After successful detection, first generate `study_guide/chNN.html`, then have that native skill convert the HTML to the canonical PDF path. Preflight uses `--pdf-backend native` and must not fail early merely because no local browser is present.
- Current plugin directory: [`openai/plugins`](https://github.com/openai/plugins).
- [`openai/skills`’ historical PDF skill](https://github.com/openai/skills/tree/49f948faa9258a0c61caceaf225e179651397431/skills/.curated/pdf) is only an Apache-2.0 behavioral reference. That repository is declared deprecated and is not a new installation recommendation.
- If the native `pdf` is not visible, use this repository’s fallback directly. Do not silently install from the historical directory.

### Claude Code

- If `pdf` or `document-skills:pdf` is already visible, use it directly.
- Native binding uses the stable ID `claude_code.document-skills.pdf` from the machine-readable table. The version must be the exact plugin version actually loaded by the host.
- When installed-capability detection succeeds, preflight uses `--pdf-backend native`, and the capability consumes the already validated chapter HTML. Change to `--pdf-backend browser` only when falling back to the repository’s browser print path.
- If it is missing, the following official commands may be run after obtaining user consent:

  ```text
  /plugin marketplace add anthropics/skills
  /plugin install document-skills@anthropic-agent-skills
  ```

- The review snapshot is [`skills/pdf`](https://github.com/anthropics/skills/tree/9d2f1ae187231d8199c64b5b762e1bdf2244733d/skills/pdf) in Anthropic’s official repository. That document skill is proprietary/source-available, and its license prohibits copying, derivation, and redistribution. This repository therefore links to it only; it neither vendors nor “adapts it for this scenario.”
- There is currently a [public upstream issue](https://github.com/anthropics/skills/issues/1087) about a plugin potentially loading skills outside its declared scope. Inspect the actual skill list after installation. This repository never runs that installation automatically.

### Generic Agent Skills host

- This repository’s `skills/exam-study-guide/SKILL.md` follows the [Agent Skills specification](https://agentskills.io/specification) and is loaded by the host as an ordinary project skill.
- If the host does not discover skills automatically, `scripts/study_guide_render.py` may also be run directly. The repository fallback is `--pdf-backend browser`, so only this route requires Edge/Chrome. Handle missing dependencies through the fail-loud preflight/script output.
- Cursor, Windsurf, or Agents that support only project rules use this fallback route and continue to read the core contract in `AGENTS.md`.

## Selection and upgrade rules

- “Popular” may be used only to discover candidates; it is not an adoption criterion. A candidate must simultaneously have an official source, match the task, use an acceptable license, support pinning a reviewed version, and provide a fallback.
- Every change to `review_commit` requires rereading the skill, license, and installation manifest, followed by the synthetic-textbook regression.
- Never turn an external GitHub URL into a runtime auto-download hook. Exam preparation must not become uncontrolled because of network availability, upstream drift, or supply-chain changes.
