# Contributing

English · [中文](CONTRIBUTING.zh.md)

Thank you for helping make Exam Cram Coach more reliable and more useful to students. Small, focused pull requests are very welcome. Most PRs can be merged after ordinary review when they explain the user problem clearly, preserve source and learner-data safety, and include verification proportional to the change. If an idea needs adjustment, maintainers will normally help refine it rather than reject it outright.

You do not need to ask permission before opening a focused PR. For a large redesign, a short issue or draft PR first can prevent duplicated work.

## What we prioritize

We review contributions in this order:

1. **Debugging and reliability.** Reproducible bugs, data-loss or state-corruption risks, missing or mixed-up questions and answers, parser/validator contract failures, source-trace mistakes, privacy or path-safety issues, cross-platform failures, and CI regressions come first. A small regression test is especially valuable.
2. **Real teaching results and maintenance.** Legally shareable or properly anonymized course fixtures, Gold Sets, real-course acceptance findings, clearer beginner explanations, image/formula quality, localization, accessibility, performance, package-size reduction, dependency maintenance, and documentation corrections come next. Please separate observed evidence from personal impressions.
3. **New features.** Focus on one clear student problem. Keep the default path lightweight, preserve the learning state machine and provenance rules, support both language modes where the feature is student-visible, and make heavy dependencies, network calls, and uploads optional and consent-gated.
4. **Other useful improvements.** Better tests and benchmark methods, installation and agent compatibility, error messages, developer tooling, translations, examples, and small cleanup PRs are welcome too.

Priority is not a rejection rule. A well-scoped new feature can still merge while maintenance work is ongoing; urgent correctness and learner-data safety issues are simply reviewed first.

## Strong community examples

- [#13 — auto-generate question IDs and normalize true/false answers](https://github.com/ZeKaiNie/universal-examprep-skill/pull/13) by [@ky-2332](https://github.com/ky-2332) is a strong debugging example: it reproduced a real ingestion-to-validation contract failure, kept the fix focused, incorporated review feedback, and added regression coverage.
- [#1 — add concept-confusion tracking](https://github.com/ZeKaiNie/universal-examprep-skill/pull/1) by [@BIueOrange](https://github.com/BIueOrange) is a good example of a focused student-centered idea. The implementation has evolved since then, but the PR identified one concrete learning problem without bundling unrelated changes.

These examples are guides, not templates every contributor must copy.

## Before opening a PR

- Describe the learner or maintainer problem and how to reproduce it.
- Keep unrelated formatting, refactors, generated files, and feature work out of the same PR.
- Preserve existing user changes and compatibility unless the PR explicitly documents a migration.
- Never commit API keys, private course files, student work, personal data, generated workspaces, caches, or unlicensed exam material.
- For a real-material regression, prefer a redistributable fixture, a minimal synthetic reproduction, or a hash/metadata-only report. State the material's license or anonymization method.
- Keep machine tokens, schemas, IDs, source receipts, and canonical state values stable. Translate human-facing prose, not machine contracts.
- Do not silently add downloads, uploads, telemetry, remote services, or heavy local dependencies. Explain the boundary and require explicit user consent where appropriate.

## Verification

Finish the implementation first, then run the smallest relevant checks and concentrate the broader test pass once the behavior is ready. In the PR, list exactly what you ran and any platform or real-material check you could not run.

Common commands are:

```bash
python -m unittest <relevant.test.module> -v
python -m unittest discover -s tests -v
python scripts/build_dist.py
```

Not every documentation-only PR needs the full suite. Changes to ingestion, state, receipts, asset safety, localization contracts, packaging, or CI usually do. Do not weaken a failing test merely to obtain a green check; fix the underlying contract or explain why the contract itself is wrong.

## Pull-request checklist

- [ ] The PR explains the problem, scope, and user-visible result.
- [ ] The diff is focused and does not include private or generated data.
- [ ] Source provenance, learner state, image/answer separation, and privacy boundaries remain intact.
- [ ] English and Chinese reader-facing documentation are updated together when applicable.
- [ ] Relevant tests or real-material checks are listed, including known limitations.
- [ ] New dependencies or remote capabilities are optional, justified, and documented.

If you are unsure whether a PR is ready, open it as a draft and say what remains uncertain. Early, honest context makes review easier, and maintainers are happy to help turn a useful contribution into a mergeable one.
