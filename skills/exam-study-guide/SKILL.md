---
name: exam-study-guide
description: 将已经讲完但尚未完成阶段门禁的一个章节整理成强类型教材清单，并在视觉模式下编译为公式可读、图片可见、知识点与全部对应例题逐项精讲的自包含 HTML/PDF。结构化工作区准备阶段完成证据、用户说 Markdown 公式仍是 raw LaTeX、图片缺失、要含课件/作业/Quiz/模拟考试题及答案的零基础讲义，或要求打印版时使用。
license: MIT
---

# Exam Study Guide

## Purpose

After teaching the current chapter, build its validated typed Study Guide manifest; in visual mode, compile that manifest into a readable, self-contained HTML Study Guide and printable PDF before phase completion. A Study Guide is a teaching artifact, not a dump of the wiki and bank: it groups knowledge points with every mapped lecture, homework, Quiz, mock-exam, past-exam, or textbook example and explains each one through formula selection, variable mapping, substitution, solution, self-check, and source trace. Keep Markdown/JSON as auditable sources and never overwrite them with a derived artifact.

## Activation

Use this module after the exam workspace/current chapter are confirmed and its substantive teaching is persisted, but before `complete-phase` in a structured workspace. Restore the current phase and effective `artifact_mode` from `study_state.json` before selecting `<N>`. `chat` still builds and imports the mandatory typed `profile=full` manifest, then stops without HTML/PDF; a recognized standing `visual` preference continues through rendering, receipt binding, and all-page QA. A direct one-shot handout request follows its explicit output scope without rewriting the stored preference. Never inspect or infer the student's subscription. Preserve the parent exam-coach language and provenance contracts in all chat summaries.

## Inputs

- Exactly one current-chapter `references/wiki/chNN*.md` file, used as source evidence rather than pasted wholesale.
- Optional `study_state.json`; its canonical language-neutral `language` code (`zh` / `en` / `bilingual`; legacy/display aliases `中文` / `English` / `双语` migrate on read) controls all agent-generated headings, notices, explanations, labels, and summaries. Missing state follows the session default (English unless the student opened in Chinese); the script's Chinese empty-value fallback exists only for legacy workspaces and is not a new-session language decision.
- The current-chapter slice of `references/teaching_examples.json`, every current-chapter entry in `references/quiz_bank.json`, and every typed current-chapter question unit, de-duplicated by item ID. A legacy `gradable=false` record remains a teaching example in the guide but is never served or graded as a quiz.
- A substantive `notebook/chNN.md` plus the validated typed teaching manifest `notebook/chNN.guide.json`.
- Workspace-local images under `references/assets/` referenced by the typed manifest.
- For ingestion-v2 workspaces, current validated `.ingest/canonical_groups.jsonl` and `.ingest/source_conflicts.jsonl` facts. These are revision-bound derived facts, not replacements for source occurrences or item/unit IDs. Do not preload unrelated chapters or hand-fold near matches.
- For ingestion-v2, `.ingest/claim_records.jsonl` and the matching `.ingest/claim_verification_receipts/chNN.json` are mandatory typed-guide inputs. The validator recomputes them against the current manifest and live source/content/group/conflict facts. The receipt's `fact_snapshot_sha256` also binds current build/parser/page-quality/review facts into its ID, so a parser identity revision requires re-verification even when content units are unchanged. Legacy/v1 workspaces stay on their compatibility path and must not be described as having this v2 evidence. The receipt scope is `location_only`, never semantic proof.

Use only `$...$` and `$$...$$` as formula delimiters in source Markdown. Forms such as `(A\cup B)`, `[P=\frac{...}]`, `\(...\)`, and `\[...\]` are not valid framework input. Confirm and migrate the source explicitly; never guess-rewrite a formula.

## Workflow

1. Persist every substantive walkthrough first with `scripts/notebook.py add-entry`. Do not invoke this compiler for an empty notebook or before the chapter has actually been taught.
2. Resolve output intent. Every structured workspace continues through typed-manifest validation/import. `chat` stops only after step 5 and produces no automatic HTML/PDF; `visual` continues through the printable path and all-page QA. A one-shot request follows exactly the requested HTML/PDF scope. Persist a standing choice only through `update_progress.py set --artifact-mode chat|visual`.
3. Before drafting, require current workspace validation. In ingestion-v2, canonical groups may suppress repeated display/retrieval copies only while every original occurrence and location-derived ID remains traceable. Near matches are not canonical merely because their text is similar, source priority never silently selects a winner, and any unresolved source conflict blocks guide assertions/rendering/completion until evidence-backed review resolves it. Then build a draft for `notebook/chNN.guide.json` from only the current chapter. It MUST use the schema enforced by `scripts/study_guide_content.py`:
   - every knowledge point has localized title/explanation, source references, formulas, variable meanings, applicability, exact mapped example IDs, and exact `source_unit_ids`; knowledge-point coverage plus reasoned `semantic_exclusions` must partition every current-chapter material/AI-recovered semantic unit, and formula units cannot be excluded;
   - every walkthrough has one canonical `source_type`, explicit `answer_provenance=material|ai_supplemented|ai_generated`, a visible prompt or prompt image, language-aware translation, what is asked, knowns/unknowns, formula uses, variable mapping, substitution, step-by-step solution, answer, self-check, source trace, and a `notebook_anchor` that already exists in the chapter notebook. Question units require `metadata.source_language=zh|en`; formula/symbol-only `zxx` units never support zh/en Guide prose, prompts, or material answers. Material prompt, answer, and formula claims are matched against normalized exact unit payloads, not merely a shared filename/location or fuzzy keywords. A `material` answer needs a same-language answer/solution source ref (`source_file` + locations or `source_unit_id`). A source-ref `quote_span` alone is only authored supporting metadata and never satisfies the v2 exact-location gate;
   - in ingestion-v2, attach each strict `claim_id` to the exact source ref whose `source_unit_id` equals the claim's unit and whose guide role is compatible with the claim role. Require one direct material claim for each rendered knowledge-point explanation language backed by a textual unit in that same source language (`concept` / `concept_evidence`), every formula `latex` (`formula` / `formula_evidence`), printed `prompt_text` unless a `full_prompt` image replaces it (`question` / `question_evidence`), and every answer language whose provenance is `material` (`answer|solution` / `answer_evidence`). AI translations, pedagogical explanations, `ai_supplemented`, and `ai_generated` answers do not acquire material claims merely to fill the gate;
   - `profile=full` covers the exact de-duplicated union of current-chapter teaching-example IDs, all current-chapter bank IDs (including teaching-only `gradable=false` records), and typed question-unit external IDs, and has no omissions; this proves that explicit denominator, not semantic recall of every source claim. `profile=abridged` may be used only when the student requested a shortened artifact and every omitted ID has a reasoned ledger entry; it never satisfies structured phase completion. A `≤1天` time budget never silently overrides an explicit request for all examples.
4. Apply the prompt-image rule before import. `full_prompt` means the original question is already visible in the source image, so omit duplicate OCR/original question text. Show only a translation needed by the active language: if an English prompt image is visible, `zh` and `bilingual` may show its Chinese translation but never repeat the English original; reverse this for a Chinese image. `figure_only` still requires the full original prompt text because the image is only a diagram/table. Never put answer-side images before the worked solution.
5. Bind v2 claims, validate, then import the typed content. First read `.ingest/build_manifest.json.pipeline_version`; never upgrade a legacy/v1 workspace by assertion. For ingestion-v2, author the intended claim proposals and run `create`, or import already complete strict ClaimRecords:

   ```text
   python scripts/verify_claims.py create --workspace <ws> --input-proposals <proposals.json> --json
   python scripts/verify_claims.py import --workspace <ws> --input-claims <complete-claims.jsonl> --json
   ```

   Use exactly one route for the current update. `create` computes revision/payload/span hashes and, by default, atomically merges across chapters by the complete subject key: an incoming subject replaces its prior record, other records are retained only after their live revisions revalidate. Its returned `claim_ids` are only the newly created IDs; inspect `created_claim_count`, `retained_claim_count`, and `replace_all`. `create --replace-all` deliberately discards all non-input records. `import` is always a strict full-sidecar replacement. All `create`/`import`/`verify` operations share `.ingest/mutation.lock` with Study Guide import. Repeated quote text requires an explicit Unicode code-point `start`. Insert the returned/existing `claim_id` into the exact same-unit source refs described in step 3, then save the complete draft at a safe workspace-relative staging path such as `notebook/chNN.guide.claim-draft.json`. Generate the chapter receipt from that exact draft, then validate and import the same canonical content:

   ```text
   python scripts/verify_claims.py verify --workspace <ws> --manifest notebook/chNN.guide.claim-draft.json --chapter <N> --json
   python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <ws>/notebook/chNN.guide.claim-draft.json --json
   python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <ws>/notebook/chNN.guide.claim-draft.json --json
   ```

   The typed validator reparses the facts, recomputes the location-only verification and canonical guide/source/content/group/conflict/claim/fact-snapshot hashes, checks same-ref unit/role binding and required material-assertion coverage, and rejects a stale/missing/different receipt. Any guide, claim, source, unit, group, conflict, parser receipt, page-quality inventory, or review-ledger change requires a fresh receipt; a source/unit revision change also requires rebuilt claim records. Because every receipt binds the global sidecar and canonical fact-snapshot hashes, finish all intended mutations first, then re-run `verify` for every chapter receipt that must remain current. Unreferenced sidecar claims are absent from a chapter's verified ID list but still change that bound hash. This remains exact membership/text/location verification, not a judgment that the quote semantically supports the authored assertion. A legacy/v1 guide skips this command sequence rather than fabricating a v2 receipt.

   Import atomically adds a bounded generated block to the existing notebook and publishes the JSON only after validation. Any coverage, language, formula, source-type, asset, path, control-character, claim, or source conflict blocks rendering or structured phase completion. Fix the typed draft or underlying ingestion evidence; never bypass the validator. In `chat`, return to `exam-tutor` after a successful `profile=full` import so it can call `complete-phase`; do not render HTML/PDF. In `visual`, do not call `complete-phase` yet—continue through receipt-bound rendering and all-page QA below.
   After `update_progress.py set --language ...` changes the course language, do not reuse a stale-language artifact. If all newly required localized blocks were already authored, project them without machine translation. In ingestion-v2, `--output` is mandatory and writes a validated-but-unsigned staging manifest without replacing the canonical guide or deleting current artifacts:

   ```text
   python scripts/study_guide_content.py --workspace <ws> relocalize --chapter <N> --language <zh|en|bilingual> --output notebook/chNN.<language>.draft.json --json
   python scripts/verify_claims.py verify --workspace <ws> --manifest notebook/chNN.<language>.draft.json --chapter <N> --json
   python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <ws>/notebook/chNN.<language>.draft.json --json
   ```

   Between staging and `verify`, use default-merge `create` for newly required localized ClaimRecords (or supply a deliberate complete replacement to `import`) and place their IDs on the exact refs. After the final sidecar mutation, re-sign every chapter receipt that must remain current. `import` holds the ingestion mutation lock from live verification through publication and invalidates older chapter HTML/PDF/render receipt/QA only after the signed staging manifest passes. In ingestion-v1, omit `--output`: the legacy one-command relocalize path validates, publishes, and invalidates artifacts directly. Missing locale content fails loud; author it source-consciously and repeat this flow. Dormant translations remain available for later switches, while rendering still shows only the translation absent from the visible original prompt.
6. Read [`docs/pdf-capability-adapters.md`](../../docs/pdf-capability-adapters.md), probe [`docs/pdf-capability-adapters.json`](../../docs/pdf-capability-adapters.json), and select exactly one backend:
   - `native`: an already installed host PDF capability can print/convert the exact validated `study_guide/chNN.html` to `study_guide/chNN.pdf` and can render the result for QA;
   - `browser`: use the repository fallback with a detected local Edge/Chrome;
   - `html`: HTML-only request, so no PDF backend is required.
7. Run the content/backend-aware preflight after the typed manifest exists but **before** invoking the renderer:

   ```text
   python scripts/check_deps.py --workspace <ws> --chapter <N> --artifact-mode visual --pdf-backend <native|browser|html>
   ```

   `chapter_math_status=needs_recovery` is a content blocker, not “no math.” Formula conversion becomes required when typed formulas/substitutions exist. Edge/Chrome is required only for the browser route. Explain only the exact missing dependency and obtain consent before installation.
8. Render the selected chapter. The default artifact type is the real typed Study Guide; backend/profile are explicit assertions:

   ```text
   python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile <full|abridged> --pdf-backend <html|browser|native>
   ```

9. For the browser PDF route, create the PDF only after HTML validation:

   ```text
   python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile <full|abridged> --pdf-backend browser --pdf
   ```

   For `native`, convert the exact validated HTML externally to the canonical PDF, bind its hash into the receipt, then continue with the same QA. `--pdf` is browser-only.
10. Render and lint every PDF page, then inspect every PNG visually:

    ```text
    python scripts/study_guide_qa.py --workspace <ws> --chapter <N> --json render
    python scripts/study_guide_qa.py --workspace <ws> --chapter <N> accept --inspected-pages all --reviewer <name> --reviewer-kind agent --page-verdict 1=pass
    ```

    Repeat `--page-verdict N=pass:<notes>` once for every rendered page; the one-page command above is only the minimal shape. Check formulas, glyphs, prompt/answer order, image clarity, clipping, tables, margins, page numbers, page breaks, orphan headings, and abnormal blank space. Any defect requires a source/renderer fix, regeneration, and a fresh inspection from page 1. `artifact_ready` remains false until the receipt has matching hashes, `visual_qa.status=ready`, every page is recorded, and unresolved defects are empty. Only after `artifact_ready=ready` return to `exam-tutor` to call `complete-phase`.

## Output Contract

- Produce `study_guide/chNN.html` as an offline document with inline CSS, native MathML, and data-URI images. It must require no network, CDN, script, or browser extension.
- Dispatch every agent-authored heading, explanation, step, answer, and receipt from canonical `zh|en|bilingual`. Bilingual content is complete blockwise zh+en—not merely bilingual UI chrome. Source quotations/images stay original-language evidence and use the translation rule above.
- Hero source inventory uses only typed walkthroughs: localize counts; mark absent `mock_exam`/`past_exam` “not provided in the current workspace/material set.” Scoped zeroes change neither coverage nor global claims.
- Place prompt-side assets first and answer-side assets later. The printable Study Guide contains no hidden `details`, answer toggle, form control, or screen-only answer.
- Retain `source_file`, the adapter's honest location anchors (for example PDF page, PPTX slide, XLSX worksheet, or DOCX logical segment), and the canonical provenance labels from the workspace.
- Produce `study_guide/chNN.receipt.json` with manifest/HTML/PDF hashes, exact coverage of the current chapter's de-duplicated teaching-example + all-bank-item + typed-question-unit ID denominator, selected backend/converter, and QA state. This does not prove semantic recall of every source claim. Never claim completion from file existence alone.
- For ingestion-v2, retain the matching `.ingest/claim_verification_receipts/chNN.json` and the validator's `claim_verification` report. Describe them only as required material-claim coverage plus explicitly referenced authored-field membership/text identity, same-ref unit/role binding, source location/revision, and canonical strict-JSON guide/fact hash binding. They are not answer-correctness or semantic-entailment receipts and are never inferred from `quote_span` presence. Legacy/v1 output must not claim this gate.
- If a maintainer wants the older four-layer dump for diagnosis, use `--artifact-type source_packet`. It writes `chNN.source-packet.html`; it is never called a Study Guide and never satisfies artifact readiness.
- After full visual acceptance, return a 3-5 line digest plus links to the HTML and, when present, the PDF.

## Boundaries

- Do not render the entire course to bypass chapter lazy-loading.
- Do not run because a host appears to have a low/high subscription. The only standing switch is canonical `artifact_mode=chat|visual`; missing and unknown values fail safe to `chat`.
- Do not silently machine-translate source evidence. Translation fields are explicitly AI-authored/localized teaching blocks and must be labeled by placement; do not pass them off as official wording.
- The raw-material preflight (`check_deps.py --materials <dir> --artifact-mode visual`) cannot know the final chapter content or host PDF backend and therefore must not trigger speculative MathML/browser installation. Before visual generation, rerun it with `--workspace <ws> --chapter <N> --pdf-backend <native|browser|html>`. If that chapter contains formula content without the audited `latex2mathml==3.60.0`, the preflight/renderer prints the exact pinned command. Explain the dependency and obtain consent before installation; never install silently. Never present an older `chNN.html` as the result of a failed render.
- Reject URL, absolute, parent-traversal, missing, unreadable, or symlinked assets and paths. The sole compatibility exception is `../assets/<safe-relative-tail>` inside a selected `references/wiki/*.md`, because `build_visual_index --apply-wiki` emits that shape. Resolve it only to `<ws>/references/assets/<safe-relative-tail>`, reject every additional `..` and every symlink component, and never extend this exception to teaching examples, quiz items, or notebook content.
- A missing local browser blocks only the selected `browser` backend. It does not block a successfully probed `native` adapter. Any failed PDF route is an HTML-only degradation, not a PDF success.
- Do not auto-download an untrusted third-party skill. Use only an adapter declared by the repository capability registry and confirmed by a successful probe.
- Do not treat location-derived source/unit IDs as content hashes. Bind exact revisions with the persisted source/unit digests, and never let a canonical-group display choice erase a source occurrence or adjudicate an unresolved conflict.
