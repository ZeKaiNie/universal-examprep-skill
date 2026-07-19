# Optional external OpenAI API fallback

This adapter is a separately billed, opt-in fallback for per-item Study Guide
explanations in a confirmed full ingestion-v2 workspace. It is **not** the default
isolated route. When the current host can launch a fresh child Agent with only the exact
item packet and restricted tools, use that native child instead: it needs no additional
API key and stays within the host account's own quota and privacy boundary.

Never select this adapter merely because a key exists or because the host/model is from
OpenAI. Use it only when the user explicitly asks to send the selected items to the
OpenAI API after the native route is unavailable or deliberately declined. It remains
unavailable in lightweight mode and is never enabled from a model name, subscription,
`processing_mode`, or `artifact_mode`.

## Native route before external fallback

The native child-agent route must create one fresh child per item and pass only:

- the fixed beginner-first instruction and target language;
- the exact original question and official answer, when one exists; and
- target-scoped question/answer assets.

It must not pass the main-chat history, other questions, course wiki, notebook, or
unrelated assets, and must deny filesystem, network, and other tools. If the host cannot
truthfully enforce both the context and tool boundary, use ordinary authoring. Do not
automatically fall through to this external adapter. A native host receipt and an API
adapter receipt are declarations of the applied controls, not cryptographic sandbox
attestations.

## External API capability and consent gate

The external fallback retains two distinct consent stages. Before changing the mode or
authoring an API plan, the Agent must establish a **no-upload planning opt-in**:

1. The host can make direct HTTPS calls to the OpenAI API and can keep a secret out
   of the course workspace, receipts, logs, and Git.
2. The selected model accepts image inputs and Structured Outputs.
3. The user has been told that ChatGPT/Codex subscriptions and OpenAI API billing are
   separate, which Provider would receive the data, and the current service
   retention/privacy boundary.
4. The user has explicitly allowed local mode/packet/annotation/request/plan writes,
   with no course upload during that planning stage.

Before `run`, the second consent must bind the generated plan: disclose its exact
isolated item/image upload scope, call count, and byte inventory; separately consult
current official pricing and give a bounded cost estimate with assumptions; then obtain
explicit consent for that exact `plan_id`. The plan cannot know exact final input tokens,
output tokens, or dollars.

An API key, planning opt-in, native-isolation preference, or previous visual-mode choice
is not final upload consent.
If planning consent is missing, keep `answer_explanation_mode=ordinary`; if only final
consent is missing, the prepared isolated plan stays dormant and no Provider call occurs.
The ordinary Guide still requires a detailed beginner-first explanation for every item.

## Commands

`probe` performs no network request and never reveals the key:

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json probe
```

This is only a local configuration/key preflight. `ok=true` does not verify endpoint
reachability, model access, quota, billing, or live rate limits.

After the first no-upload planning opt-in, explicitly enable the extension **before**
preparing authoring facts. Mode is bound into the packet/template/annotations, so an
ordinary-mode packet becomes stale after this state change. Prepare again; copy only
the generated wrapper's `annotations` object into the canonical annotations path, fill
every empty value and sentinel there, and do not proceed until it validates. The
isolated template intentionally omits `answer_explanation`. Then prepare the request set:

```bash
python scripts/update_progress.py --workspace <ws> set --answer-explanation-mode isolated
python scripts/study_guide_author.py --workspace <ws> prepare --chapter <N> --json
# Copy only the template wrapper's `annotations` object to
# notebook/chNN.authoring-annotations.json and fill that target completely.
python scripts/study_guide_explain.py --workspace <ws> prepare --chapter <N> --json
```

Generate the exact disclosure plan. `--limit` may be used for a separately consented
trial; omitting it selects all pending items. Inspect `selected_upload_scope`: it lists
each item/request, instruction/model-input/output-schema hash and byte count, plus every
attachment's side/path/ID/hash/bytes. Use the reported `inspect_request_command` for each
request when the user needs to inspect the actual question/answer JSON rather than only
its bounded metadata:

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json plan \
  --chapter <N> --model <vision+structured-output-model> --detail high
```

The Agent must use current official pricing outside this command to estimate cost and
state the estimate's assumptions and uncertainty. Do not describe `plan` as calculating
an exact price.

Only after the user accepts that plan may the host run it. The count acknowledgement
must equal `selected_call_count`, and the plan acknowledgement must equal `plan_id`.
That ID binds the exact pending requests/items, attachment paths and hashes, model,
image detail, output cap, timeout, zero-retry policy, key source, and a one-way binding
of the exact effective credential. The binding identifier never reveals or stores the
key, but changing the key/project credential after planning forces a new disclosure.
A stale or same-count-but-different selection therefore fails before upload:

```bash
python scripts/host_adapters/openai_study_guide.py --workspace <ws> --json run \
  --chapter <N> --model <same-model> --detail high \
  --consent-upload --confirm-call-count <exact-count> --confirm-plan-id <exact-plan-id>
```

The command is resumable: each accepted response is imported through the canonical
append-only explanation ledger. A rerun selects only current pending requests. When
the final item is imported, the adapter finalizes the canonical isolated-explanation
receipt unless `--no-finalize` was supplied.

That success is not a completed Study Guide. Continue with the canonical workflow:

```bash
python scripts/study_guide_author.py --workspace <ws> persist-notebooks --chapter <N> --json
python scripts/study_guide_author.py --workspace <ws> compile --chapter <N> --json
# Then create/import claims, attach and verify them, and run study_guide_content.py
# validate/import exactly as specified by skills/exam-study-guide/SKILL.md.
```

Rendering, all-page visual QA, and phase completion remain separate later gates.

To return to the ordinary route, set the mode and prepare again; fill the new ordinary
template's required detailed `answer_explanation` fields instead of reusing isolated
annotations or receipts:

```bash
python scripts/update_progress.py --workspace <ws> set --answer-explanation-mode ordinary
python scripts/study_guide_author.py --workspace <ws> prepare --chapter <N> --json
```

Changing the preference does not rewrite or relabel a historical isolated Guide. A
new authoring run binds its own mode and fails closed on stale annotations, bindings,
or receipts.

## Provider request boundary

For every selected item the adapter makes at most one `POST /v1/responses` attempt containing
only:

- the fixed beginner-first instruction from the current request;
- that item's exact question/answer JSON and target languages;
- that item's revision-bound question/answer attachments, each already constrained by
  the Study Guide crop policy; and
- the request's strict JSON output schema.

The request sets `store=false`, supplies no tools, conversation identifier,
`previous_response_id`, file store, or background job. The adapter accepts exactly one
structured `output_text`, imports only `answer_explanation` plus the non-rendered
coverage object, and does not save the raw Provider response. It recomputes every live
attachment hash before upload.

Automatic HTTP retries are disabled. A timeout or disconnect can be ambiguous—the
Provider may have received the POST even though the adapter accepted no response—so the
command stops instead of risking an unapproved duplicate. Before any manual resume, run
`plan` again, disclose that uncertainty, and obtain acknowledgement of the new exact
count and plan ID. Accepted items are already absent from the new pending set.
One independent workspace/chapter run mutex is held across the command, while ordinary
state writes remain unblocked; before every POST the adapter rehydrates the mode,
current request/ledger order, attachment revisions, and credential. A second concurrent
run, revoked isolated mode, changed request, or changed credential stops before its next
upload.

These controls support a stateless host declaration; they are not a cryptographic
sandbox attestation. OpenAI may still process abuse-monitoring logs according to the
account's data controls. According to the current official data-control table,
`/v1/responses` content is not used for training by default, but abuse-monitoring
retention can be up to 30 days unless an approved retention control applies; the
default `store=true` application-state behavior is why this adapter sends
`store=false`. Always disclose the policy that applies at call time.

## Secret handling

The adapter looks first for `OPENAI_API_KEY` in the host environment and then for
`OPENAI_API_KEY` in the repository-local `.env.local`. Prefer host/operating-system
secret storage. `.env.local` is only a git-ignored plaintext fallback; Git ignore does
not encrypt it or prevent another local process/user from reading it. The adapter does
not accept a key on the command line, print it, place it in a receipt, or copy it into
the student workspace. It makes no key-creation or dependency-install request.

## Inherent limitations

- Not every GPT-based Agent or host can provision keys, make arbitrary API calls, or
  truthfully declare a fresh/stateless tool-disabled invocation.
- One selected item means at most one POST attempt in that authorized run. Rate limits,
  latency, image tokens, and output tokens therefore scale with the chapter roster;
  manual recovery after an ambiguous delivery can still create Provider-side duplicate
  processing and must be disclosed before a new plan is authorized.
- `store=false` does not by itself grant Zero Data Retention or eliminate abuse logs.
- Structured JSON guarantees shape, not pedagogical correctness. The existing source,
  crop, provenance, coverage, claim, and visual-QA gates still apply.
- A language, prompt, material revision, annotation, or attachment change invalidates
  the request/receipt and may require a paid rerun.
- Unsupported image encodings fail before upload; the adapter does not silently convert
  or download an asset.

Official references:

- [Authentication](https://developers.openai.com/api/reference/overview#authentication)
- [Images and vision](https://developers.openai.com/api/docs/guides/images-vision)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Data controls](https://developers.openai.com/api/docs/guides/your-data)
- [Rate limits](https://developers.openai.com/api/docs/guides/rate-limits)
