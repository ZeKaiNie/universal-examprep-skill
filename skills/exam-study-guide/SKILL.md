---
name: exam-study-guide
description: 将已经讲完但尚未完成阶段门禁的一个章节整理成强类型教材清单，并在视觉模式下编译为公式可读、图片可见、知识点与全部对应例题逐项精讲的自包含 HTML/PDF。结构化工作区准备阶段完成证据、用户说 Markdown 公式仍是 raw LaTeX、图片缺失、要含课件/作业/Quiz/模拟考试题及答案的零基础讲义，或要求打印版时使用。
license: MIT
---

# Exam Study Guide

## Purpose

After teaching the current chapter, build its validated typed Study Guide manifest; in visual mode, compile that manifest into a readable, self-contained HTML Study Guide and printable PDF before phase completion. A Study Guide is a teaching artifact, not a dump of the wiki and bank: it groups knowledge points with every mapped lecture, homework, Quiz, mock-exam, past-exam, or textbook example and explains each one through formula selection, variable mapping, substitution, solution, a beginner-first explanation of why the answer follows, and source trace. Keep Markdown/JSON as auditable sources and never overwrite them with a derived artifact.

## Activation

Require explicit `study_state.json.processing_mode=full`. Lightweight mode never
invokes this module, even for a one-shot handout request; explain that Study Guide
generation requires switching to full processing and reconfirming the start gate.

Use this module after the exam workspace/current chapter are confirmed and its substantive teaching is persisted, but before `complete-phase` in a structured workspace. Restore the current phase and effective `artifact_mode` from `study_state.json` before selecting `<N>`. `chat` still builds and imports the mandatory typed `profile=full` manifest, then stops without HTML/PDF; a recognized standing `visual` preference continues through rendering, receipt binding, and all-page QA. A direct one-shot handout request follows its explicit output scope without rewriting the stored preference. Never inspect or infer the student's subscription. Preserve the parent exam-coach language and provenance contracts in all chat summaries.

## Inputs

- Exactly one current-chapter `references/wiki/chNN*.md` file, used as source evidence rather than pasted wholesale.
- Optional `study_state.json`; its canonical language-neutral `language` code (`zh` / `en` / `bilingual`; legacy/display aliases `中文` / `English` / `双语` migrate on read) controls all agent-generated headings, notices, explanations, labels, and summaries. Missing state follows the session default (English unless the student opened in Chinese); the script's Chinese empty-value fallback exists only for legacy workspaces and is not a new-session language decision.
- The current-chapter slice of `references/teaching_examples.json`, every current-chapter entry in `references/quiz_bank.json`, and every typed current-chapter question unit, de-duplicated by item ID. A legacy `gradable=false` record remains a teaching example in the guide but is never served or graded as a quiz.
- A substantive `notebook/chNN.md` plus the validated typed teaching manifest `notebook/chNN.guide.json`.
- Workspace-local images under `references/assets/` referenced by the typed manifest.
- For ingestion-v2 workspaces, current validated `.ingest/canonical_groups.jsonl` and `.ingest/source_conflicts.jsonl` facts. These are revision-bound derived facts, not replacements for source occurrences or item/unit IDs. Do not preload unrelated chapters or hand-fold near matches.
- For ingestion-v2, `.ingest/claim_records.jsonl` and the matching `.ingest/claim_verification_receipts/chNN.json` are mandatory typed-guide inputs. The validator recomputes them against the current manifest and live source/content/group/conflict facts. The receipt's `fact_snapshot_sha256` also binds current build/parser/page-quality/review facts into its ID, so a parser identity revision requires re-verification even when content units are unchanged. Legacy/v1 compatibility is read-only for an existing canonical manifest and must not be described as having this v2 evidence. The receipt scope is `location_only`, never semantic proof.

Use only `$...$` and `$$...$$` as formula delimiters in source Markdown. Forms such as `(A\cup B)`, `[P=\frac{...}]`, `\(...\)`, and `\[...\]` are not valid framework input. Confirm and migrate the source explicitly; never guess-rewrite a formula.

## Workflow

1. Restore `study_state.json`, resolve output intent, and run `validate_workspace.py <ws> --json`. Read the explicit `.ingest/build_manifest.json.pipeline_version`; never infer or delete it. Only `ingestion-v2` follows the author/compiler/claim path in steps 2-6. An explicit `ingestion-v1` workspace may only read its existing canonical manifest through the legacy compatibility path below; it cannot import, relocalize, or render a new Study Guide and must never claim the v2 claim/receipt gate. A failed v2 command does not authorize downgrading to v1. `chat` stops after the canonical `profile=full` manifest import; `visual` continues through rendering and all-page QA. Persist a standing choice only through `update_progress.py set --artifact-mode chat|visual`. Separately resolve `answer_explanation_mode=ordinary|isolated`. Missing, legacy, or invalid state has stored-schema fallback `ordinary`, which still requires a detailed beginner-first explanation for every item without an isolation claim. Before authoring a full-v2 Guide, perform a native-child capability handshake: require one fresh independent child context per item plus enforceable restriction of that child's task input and tools to the exact request. When verified, default to `isolated` unless the user opted out, persist it, and disclose once that it consumes extra host quota/time; no separate API key or external-upload consent is needed. Any missing, inherited, or unverified boundary keeps `ordinary` and must be named. A separately billed external Provider is available only when the user explicitly requests it; before persisting that fallback, retain the two-stage no-upload exact plan and exact-plan pricing/privacy/upload consent. Never infer either capability or upload permission from a model family, subscription, API key, `full`, or `visual`.
2. For ingestion-v2, prepare the revision-bound current-chapter packet and annotation template; do not hand-copy source facts or reverse-engineer the compiler source:

   ```text
   python scripts/study_guide_author.py --workspace <ws> prepare --chapter <N> --json
   ```

   This fixed command atomically writes `notebook/chNN.authoring-packet.json` and the deliberately incomplete `notebook/chNN.authoring-annotations.template.json`; its JSON result reports both paths plus the template hash. Exit `10`/`status=blocked` forbids authoring until every reported review, conflict, source, asset, and denominator blocker is resolved. The packet binds source/fact/asset revisions and contains the exact semantic units, formulas, items, prompt/answer assets, and source locations the agent may use.
3. Read the generated template instead of guessing the annotation schema. Copy only its `annotations` object to `notebook/chNN.authoring-annotations.json`, then replace every empty value and `__...__` sentinel. The template already contains the exact ID and field shape for every formula group and walkthrough item, plus an explicit knowledge-point schema placeholder and the full inventories to partition. The wrapper says `template_status=incomplete` and `valid_annotations=false`; the template itself and its untouched inner object are intentionally invalid and cannot satisfy persistence, compilation, or completion. Author only explanations in the target file, bound to the packet's `packet_sha256`. Do not change packet IDs, source text, exact LaTeX, assets, locations, or source roles. Use the canonical `zh|en|bilingual` language shape and explicit provenance for every authored field. A translation must be `ai_translation` and remain visibly AI-labelled; unsupported reasoning uses visibly labelled `ai_supplement`, never fake material provenance. In `ordinary`, the template also requires each item's detailed zero-prerequisite `answer_explanation` and exact per-language `ai_supplement` provenance; it must explain symbols, formula/rule choice, substitutions or reasoning, every subpart, and final meaning without merely repeating the answer. In `isolated`, do not author that field in annotations; step 4 supplies it through the receipt-bound extension. Each item must map to at least one knowledge point. A knowledge point may have `example_ids=[]` when the materials truly provide no matching item; the compiler emits the active-language “materials do not provide a corresponding example” notice. Knowledge points still exactly partition semantic units/formula groups, and all items remain globally covered. If a crop-receipt-only upgrade changes the packet after a large canonical annotations file was already authored, run `study_guide_author.py rebase-annotations --chapter <N>` instead of hand-editing or regenerating it. That command may change only `packet_sha256`, add a missing mode binding on the compatible path, and remove paired legacy `self_check` fields; it atomically publishes only after the entire current annotation validator passes against the new packet. Any item/formula/knowledge-point drift is refused.
4. Only when `answer_explanation_mode=isolated`, generate exactly one isolated answer explanation per item before persistence. In `ordinary`, skip this entire step: `study_guide_explain.py status` reports `disabled/not_applicable`, and every mutating explainer command must fail. The protocol script never calls a provider itself: it emits one hash-bound request at a time. The preferred host-native route must create a fresh independent, tool-disabled child context containing only that request's fixed instruction, exact question, exact answer when present, target language, and listed item-scoped assets; it uses the current host allowance and no separate API key. Do not batch multiple items into one context, add the parent conversation, course history/wiki/retrieval, expose a whole page containing unrelated questions or answers, or let the child browse the workspace/network. If the host cannot enforce these boundaries, use `ordinary`. A bundled external-Provider adapter is only an explicit-user-request fallback: it may prepare a non-uploading exact plan after the first consent, and its `run` requires the second exact-plan consent. Adapter completion finalizes only the isolated explanation receipt, not notebooks, compiled Guide, claims, import, rendering, QA, or phase completion.

   ```text
   python scripts/study_guide_explain.py --workspace <ws> prepare --chapter <N> --json
   python scripts/study_guide_explain.py --workspace <ws> status --chapter <N> --json
   python scripts/study_guide_explain.py --workspace <ws> show --chapter <N> --request-id <request_id> --json
   # Call one fresh/stateless tool-disabled model with exactly that request. The model result contains only answer_explanation plus non-rendered coverage.
   python scripts/study_guide_explain.py --workspace <ws> make-host-receipt --chapter <N> --request-id <request_id> --invocation-id <unique_id> --isolation-mode <fresh_context|stateless_api> --provider <provider> --model <model> --json
   python scripts/study_guide_explain.py --workspace <ws> import-result --chapter <N> --request-id <request_id> --input <one-model-result.json> --host-receipt <one-host-receipt.json> --json
   python scripts/study_guide_explain.py --workspace <ws> finalize --chapter <N> --json
   ```

   Repeat `show` → one native-child or explicitly consented external invocation → `make-host-receipt` → `import-result` for every pending item. The untrusted model result contains only `answer_explanation` plus the required non-rendered `coverage` object; the separate host receipt records the exact request/instruction/model-input/attachment hashes plus provider/model/invocation/isolation/tool declaration. It is a host declaration, not a sandbox or model-supplied attestation. Every invocation ID must be unique; changed packet, annotations, language, source revision, asset, crop, prompt, or response invalidates the receipt. A page-shaped image is allowed only as a revision-bound target-scoped crop: `target_item_only`, or prompt-only `target_with_required_context` with exact sorted `required_context_ids`; every answer image remains target-only. Preserve the compact semantic schema/context/isolation controls into the model attachment binding. Ordinary item-specific diagrams may remain as their native asset. The fixed prompt requires a detailed zero-prerequisite explanation, all symbols and substitutions/reasoning, every subpart, honest ambiguity handling, and no answer-self-check panel. `coverage` must use the exact target-language keys, enumerate addressed parts and at least two reasoning steps, and attest formula/rule plus final-meaning coverage; it is hash-bound through the response ledger and final receipt but never copied into the typed Guide or rendered. When upgrading an existing response ledger, schema-1 events remain immutable historical chain entries; they cannot satisfy any schema-2 request, which still requires a fresh response with `coverage`.

   For `answer_origin=inline_material`, authoring must close the answer to its explicit `inline_material_source_unit_id`: one same-source-revision/page native material text unit with identical text/title and an explicit `zh|en` source language. Missing, ambiguous, `zxx`, or mismatched evidence blocks authoring. The prompt must use its current item-scoped semantic-v2 crop; a `full_prompt` crop suppresses duplicate printed prompt text. In a monolingual isolated request, when the exact material answer already equals `ANSWER.text`, `material_evidence.text_ref` points to that field instead of copying the full material passage a second time. Bilingual or genuinely distinct translation/teaching-copy evidence keeps a separate packet-bound material payload. Its model-transport copy may remove only leading/trailing whitespace such as a parser's page-final newline; internal source text is not rewritten, and the unchanged author packet/source revision remains hash-bound.

5. Persist all validated walkthroughs, then compile the typed full manifest. In `ordinary`, the detailed explanations come from validated annotations and an isolated receipt/contract is forbidden. In `isolated`, persistence and compilation additionally require the finalized canonical explanation receipt from step 4:

   ```text
   python scripts/study_guide_author.py --workspace <ws> persist-notebooks --chapter <N> --json
   python scripts/study_guide_author.py --workspace <ws> compile --chapter <N> --json
   ```

   These commands use only the fixed packet, annotations, bindings, claim draft/proposals, and—only for `isolated`—canonical answer-explanation request/ledger/receipt files. Notebook publication is one rollback-protected batch. Packet, annotations, bindings, manifest, renderer and QA receipts bind the exact selected mode; switching modes makes the unfinished chain stale. The compiler rechecks all bound facts/assets and either the ordinary authored explanations or the complete isolated per-item receipt, applies the `full_prompt` image rule, excludes every `student_attempt`, keeps target-only answer crops after the solution, places the detailed explanation after that answer/asset, omits the deprecated self-check panel, localizes all human headings/labels/AI markers, and renders source anchors honestly as PDF page, PPTX slide, XLSX worksheet, or DOCX logical segment; only PDF links receive `#page=`.
6. Import compiler claims, attach their canonical IDs, and sign the exact attached manifest. The normal proposal route is `create`: it compiles the ergonomic proposals and atomically imports/merges the resulting strict ClaimRecords. Use `import` instead only when a complete reviewed ClaimRecord JSONL already exists; never run both routes for one update.

   ```text
   python scripts/verify_claims.py create --workspace <ws> --input-proposals notebook/chNN.claim-proposals.json --json
   # Complete-sidecar alternative only:
   python scripts/verify_claims.py import --workspace <ws> --input-claims <complete-claims.jsonl> --json
   python scripts/study_guide_author.py --workspace <ws> attach-claims --chapter <N> --json
   python scripts/verify_claims.py verify --workspace <ws> --manifest notebook/chNN.guide.claims.json --chapter <N> --json
   ```

   `attach-claims` writes only `notebook/chNN.guide.claims.json` and fails on missing, ambiguous, stale, wrong-unit, or wrong-role claims. The receipt is `location_only`: it proves exact authored-field text membership and source unit/location/revision binding, not entailment or correctness. Finish every intended global claim-sidecar mutation before signing all chapter receipts that must remain current.
7. Validate and atomically import that exact attached manifest:

   ```text
   python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --input <ws>/notebook/chNN.guide.claims.json --json
   python scripts/study_guide_content.py --workspace <ws> import --chapter <N> --input <ws>/notebook/chNN.guide.claims.json --json
   ```

   Import publishes canonical `notebook/chNN.guide.json` plus its bounded notebook block and invalidates stale derived artifacts. `profile=full` must cover the exact de-duplicated current-chapter union of teaching examples, all bank items (including teaching-only `gradable=false`), and typed question-unit external IDs; a `≤1天` budget never shrinks that denominator. In `chat`, return to `exam-tutor` after this import and do not render. After a language change, rerun authoring from target-language annotations; `ordinary` rewrites and revalidates those explanations, while `isolated` also reruns the complete per-item request/receipt chain. Ingestion-v2 `relocalize` fails early because explanations, crops, notebook blocks, claims, modes, and receipts are language-bound. Do not relabel or reuse a stale-language manifest.

   Only when Python truly cannot start may an ingestion-v2 host use the older hand-written complete-draft fallback. Label it unverified, preserve all provenance/source limitations in chat, and never claim structured phase completion, claim verification, HTML/PDF readiness, or successful local persistence from that fallback. A failed command, invalid annotation, or missing dependency is not “no Python.”

### Historical mode-less ingestion-v2 read-only seam

An already-existing canonical protocol-v2 `notebook/chNN.guide.json` that lacks `answer_explanation_mode` but has a complete, currently verifiable isolated contract may be inspected only with `study_guide_content.py --workspace <ws> validate --chapter <N> --json`, omitting `--input`. This narrow seam cannot import, render, run QA, satisfy completion, or accept another input; library validators and every new publication require an explicit canonical mode. Any revision requires rebuilding the full authoring chain under `ordinary` or `isolated`.

### Legacy ingestion-v1 read-only compatibility

An explicit `pipeline_version=ingestion-v1` workspace may inspect only an already existing canonical `notebook/chNN.guide.json`:

```text
python scripts/study_guide_content.py --workspace <ws> validate --chapter <N> --json
```

The machine report identifies `ingestion_pipeline_version=ingestion-v1`, `legacy_compatibility=read_only`, and `claim_verification.status=not_applicable` with `required=false`. Do not pass another `--input`, import, relocalize, or render this manifest into a new visual Guide. Existing historical JSON/HTML/PDF files remain readable as historical artifacts, but they satisfy no new completion or QA claim. To revise content, language, crops, explanations, HTML, or PDF, migrate/re-ingest the workspace as ingestion-v2 and run the complete authoring chain. If a workspace says v2, missing claims or a failed author command is a blocker rather than permission to use this branch.
8. For `visual`, read [`docs/pdf-capability-adapters.md`](../../docs/pdf-capability-adapters.md), probe [`docs/pdf-capability-adapters.json`](../../docs/pdf-capability-adapters.json), and select exactly one backend:
   - `native`: an already installed host PDF capability can print/convert the exact validated `study_guide/chNN.html` to `study_guide/chNN.pdf` and can render the result for QA;
   - `browser`: use the repository fallback with a detected local Edge/Chrome;
   - `html`: HTML-only request, so no PDF backend is required.
9. Run the content/backend-aware preflight after the typed manifest exists but **before** invoking the renderer:

   ```text
   python scripts/check_deps.py --workspace <ws> --chapter <N> --artifact-mode visual --pdf-backend <native|browser|html>
   ```

   `chapter_math_status=needs_recovery` is a content blocker, not “no math.” Formula conversion becomes required when typed formulas/substitutions exist. Edge/Chrome is required only for the browser route. Explain only the exact missing dependency and obtain consent before installation.
10. Render the selected chapter. The default artifact type is the real typed Study Guide; backend/profile are explicit assertions:

   ```text
   python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile <full|abridged> --pdf-backend <html|browser|native>
   ```

11. For the browser PDF route, create the PDF only after HTML validation:

   ```text
   python scripts/study_guide_render.py --workspace <ws> --chapter <N> --profile <full|abridged> --pdf-backend browser --pdf
   ```

   For `native`, the first render leaves a deliberately non-deliverable `awaiting_native_pdf` receipt. Before conversion, the host adapter must record that receipt's exact `html_sha256` and `conversion_start_gate_sha256`, its declared registry `adapter_id` and exact loaded version, and a UTC start timestamp. It must consume those exact HTML bytes and write only the canonical `study_guide/chNN.pdf`; after it records the UTC completion timestamp, atomically bind the result:

   ```text
   python scripts/study_guide_render.py --workspace <ws> --chapter <N> --pdf-backend native --bind-native --native-pdf-path <ws>/study_guide/chNN.pdf --native-adapter-id <declared-id> --native-adapter-version <exact-version> --conversion-input-html-sha256 <receipt-html-sha256> --conversion-start-gate-sha256 <receipt-gate-sha256> --conversion-started-at <UTC-Z> --conversion-completed-at <UTC-Z> --json
   ```

   The binding command invokes no adapter, network, installer, or renderer. It revalidates the current typed manifest, HTML, full-processing/runtime gate, allow-listed adapter identity, canonical PDF path/signature/hash, and timestamps under the workspace publication lock, then atomically changes the receipt to `qa_pending`. Any mismatch leaves the old receipt unbound, so merely dropping a PDF beside the HTML never makes it acceptable. `--pdf` is browser-only. The adapter/version fields are host declarations bound into the conversion hash, not an attestation that the host process was sandboxed. If the host cannot report the exact loaded adapter version, native binding is unavailable; explicitly fall back to `browser` or HTML rather than guessing `latest`.
12. Render and lint every PDF page, then inspect every PNG visually:

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
- Explain the provenance legend in full exactly once near the beginning. In later teaching content use only the legend emoji at the end of the relevant paragraph/run, and collapse consecutive paragraphs with the same provenance to one terminal marker. Keep the complete provenance sidecars and receipts machine-readable.
- Never render an unrelated full question/answer page merely because it has the right page number. Every newly rendered Study Guide requires authoring protocol v2; page-shaped assets require a current schema-v2, source-revision-bound crop receipt whose full single-region or explicit deterministic-composite variant passes the shared live verifier. New receipts use semantic-review schema v2: target-only has empty contexts plus `isolation=target_item_only`, while a dependent prompt contains only the target plus exact sorted `required_context_ids` and uses the distinct `isolation=target_with_required_context`; detected IDs, crop hash, every composite region/bbox, and output pixels must close exactly. Preserve those semantic schema/context/isolation controls into the author packet and isolated-explanation input. Historical receipt schema v1 and historical semantic-review v1 (including semantic v1 inside an otherwise readable receipt-v2 record) are read-only and cannot satisfy current Study Guide authoring; layout-only crops, stale/missing review evidence, unrelated content, undeclared detected IDs, or student-attempt output evidence also block authoring. A tainted parent page is not itself rendered and may supply a verified clean prompt region; answer-side evidence remains official-only.
- Retain `source_file`, the adapter's honest location anchors (for example PDF page, PPTX slide, XLSX worksheet, or DOCX logical segment), and the canonical provenance labels from the workspace.
- Produce `study_guide/chNN.receipt.json` with manifest/HTML/PDF hashes, exact coverage of the current chapter's de-duplicated teaching-example + all-bank-item + typed-question-unit ID denominator, selected backend/converter, and QA state. This does not prove semantic recall of every source claim. Never claim completion from file existence alone.
- For ingestion-v2, retain the matching `.ingest/claim_verification_receipts/chNN.json` and the validator's `claim_verification` report. Describe them only as required material-claim coverage plus explicitly referenced authored-field membership/text identity, same-ref unit/role binding, source location/revision, and canonical strict-JSON guide/fact hash binding. They are not answer-correctness or semantic-entailment receipts and are never inferred from `quote_span` presence. Legacy/v1 output must not claim this gate.
- If a maintainer wants the older four-layer dump for diagnosis, use `--artifact-type source_packet`. It writes `chNN.source-packet.html`; it is never called a Study Guide and never satisfies artifact readiness.
- After full visual acceptance, return a 3-5 line digest plus links to the HTML and, when present, the PDF.

## Boundaries

- Do not render the entire course to bypass chapter lazy-loading.
- Do not run because a host appears to have a low/high subscription. The only standing switch is canonical `artifact_mode=chat|visual`; missing and unknown values fail safe to `chat`.
- Do not silently machine-translate source evidence. Translation fields are explicitly AI-authored/localized teaching blocks and must be labeled by placement; do not pass them off as official wording.
- Non-PNG visual readiness conditionally requires an installed local Pillow decoder for full pixel verification. If missing, block the asset, explain the dependency, obtain consent, and never install silently.
- The raw-material preflight (`check_deps.py --materials <dir> --artifact-mode visual`) cannot know the final chapter content or host PDF backend and therefore must not trigger speculative MathML/browser installation. Before visual generation, rerun it with `--workspace <ws> --chapter <N> --pdf-backend <native|browser|html>`. If that chapter contains formula content without the audited `latex2mathml==3.60.0`, the preflight/renderer prints the exact pinned command. Explain the dependency and obtain consent before installation; never install silently. Never present an older `chNN.html` as the result of a failed render.
- Reject URL, absolute, parent-traversal, missing, unreadable, or symlinked assets and paths. The sole compatibility exception is `../assets/<safe-relative-tail>` inside a selected `references/wiki/*.md`, because `build_visual_index --apply-wiki` emits that shape. Resolve it only to `<ws>/references/assets/<safe-relative-tail>`, reject every additional `..` and every symlink component, and never extend this exception to teaching examples, quiz items, or notebook content.
- A missing local browser blocks only the selected `browser` backend. It does not block a successfully probed `native` adapter. Any failed PDF route is an HTML-only degradation, not a PDF success.
- Do not auto-download an untrusted third-party skill. Use only an adapter declared by the repository capability registry and confirmed by a successful probe.
- Do not treat location-derived source/unit IDs as content hashes. Bind exact revisions with the persisted source/unit digests, and never let a canonical-group display choice erase a source occurrence or adjudicate an unresolved conflict.
