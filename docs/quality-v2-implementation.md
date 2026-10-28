# Quality V2 implementation ledger

Baseline: f5249637cff9312f0669d46a20ed126f9a1c8863. Branch: codex/cnkg-quality-v2.

The accepted scope is the three implementation batches in audit-2026-10-26-v2.md. Independent teacher review and real-student effect validation remain deferred by the user. Completion is reported by capability and evidence, not by an unqualified course-wide percentage.

## Batch 1 — correct feedback and one data contract

Commit: `f5b052e`.

Completed: exact/approximate/structured answer contracts, CLI option IDs, mathematical semantics, explicit Schema validation, trustworthy source observations and corroboration, duplicate-file cleanup and current documentation. Author verification now also uses bounded exact rational arithmetic rather than the former global float tolerance.

Already completed: 15 retired copies were verified byte-identical to their archives and removed. data/migrations/quality-v2-cleanup.json records every path, archive, byte count and SHA-256. All 23 active volume sources and the archives remain.

Gate: the audit's wrong-answer and legal-variant cases, CLI path, Schema incompatibilities and false corroboration must be fixed and exercised before the batch is committed.

Acceptance: all 11 release gates passed on 2026-10-26. All 2,257 author-verifiable numeric questions pass the exact/bounded evaluator; all 3,568 reference answers pass the unified student grader. Additional adversarial tests cover undefined powers, concealed unexpanded products, noninteger factors and computed-exponent resource limits. New numeric attempt records use exact rational strings and retain the original input. The three formerly truncated fraction answers were corrected in the active authoring sources and rebuilt.

Reproduce: `python -m pip install -r requirements-dev.txt`, then `python tools/release_check.py`. The release report and each gate's full log are in `build/`.

## Batch 2 — one coherent learning chain

Commit: `1bb923c`. The following counts describe this batch's commit; the final V2 counts appear under Batch 3.

Completed: stable skill IDs, textbook placement mappings, unified task/answer contracts, task-family exposure, saved self-checks and project work, next-step/review planning, and safe continuation of sessions under immutable content versions. The first coherent chain is fractions → ratio/proportion → linear functions, with explicit prerequisite links.

The registry contains 1,058 skills (20 observable, 84 composite, 954 legacy composite), 4,439 tasks and 505 materials. The three extended units contain 60 tasks and 20 new observable objectives, supported by six new mathematical diagrams. Counts describe registered resources; they do not imply that every legacy criterion is atomic or independently validated. All 477 quarantined bank records remain excluded from practice and diagnosis.

Learner drafts and revisions are stored transactionally with conflict detection and idempotent retries. Self-assessment remains learner-reported evidence. Material and reference exposure are recorded, and repeated task families do not become independent evidence. New checks with an exhausted pool offer a working practice route. Stored session content and SVGs preserve the original task version across content updates.

Acceptance: all 13 release gates passed. The full core run contained 543 tests (527 passed; 16 optional external-data tests skipped). A subsequent 25-test planner/HTTP run verified the final recommendation grouping. All six frozen browser journeys passed: self-check autosave, rubric revision, the complete 20-task linear-function unit, project history/export, stage-aware mobile layouts, and exhausted-pool recovery. The immediate retest correctly records that the delay window was not met. These are synthetic functional checks, not real-student outcome evidence.

Evidence: `build/quality-v2/batch2/` preserves the gate report/logs; `build/quality-v2/batch2-browser/` preserves the six browser results and screenshots. Legacy IDs and learning records remain readable.

## Batch 3 — course depth and maintainable delivery

Implemented: the remaining seven existing units were deepened and a low-primary ten-frame/place-value unit was added. All 11 units now contain 177 tasks (64 practice, 56 transfer, 57 retest), 44 worked examples and 89 objectives (59 observable and 30 retained composite). There are 79 process/rubric tasks; the other 98 tasks use explicit automatically checkable contracts. The two content batches add 27 mathematical SVGs, giving 38 in the project. Eighteen teaching cards were revised, including four actual statistics diagrams.

The final registry contains 1,097 skills (59 observable, 84 composite and 954 legacy composite), 4,501 tasks and 506 materials. All existing 4,439 effective answer contracts remain unchanged through the author/binding ownership migration. Stable IDs and legacy objective relationships remain readable. These totals do not imply 1,097 atomic or independently validated skills.

Twelve card references to retired memory questions were repaired individually: five received genuinely aligned active replacements, seven now point learners to the existing target-specific self-check. Twelve additional low-primary bank questions were confirmed to test the wrong target and retired. Original prompts, answers and IDs remain in `data/question_sources/retired-alignment.json`; retirement reasons, content hashes and policy/metadata changes are recorded in `data/question_retirements.json`. The full bank archive remains 3,568 records; the default active bank contains 3,079, of which 1,967 meet the current diagnostic policy. All 489 retired records are outside the default Graph/SQLite question table and available through explicit archival indexes.

The 704 application questions now have a declarative JSON source. The 23 volumes, teaching cards, learning units and projects each have one current editable source. Old application modules are read-only compatibility views; 30 historical writer CLIs stop before modifying content. The first batch's 15 duplicate-file removals remain in effect; no history or learning records were deleted. `data/authoring_migration.json` records the content-preserving source migration separately from the twelve explicit admission-policy changes.

`tools/build_project.py` is the single source-to-content build entry. Final delivery uses a raw-byte SHA-256 manifest, a clean Git commit, complete core/browser reports bound to that commit and manifest, and individually hashed logs/screenshots. CI runs the same release command. A source ZIP is independently inspectable without `.git`; packaging itself still requires a real clean repository. The bundle contains only the current branch reference, and the evidence package excludes private databases and stale/unbound output.

Targeted content validation passed for all 11 units and the 18 revised cards, including all 98 automatic task reference answers, mathematical counterexamples and all active card practice references. Delivery-risk tests cover byte fidelity, missing/new files, source-archive portability, stale reports, evidence tampering and excluded historical paths. A real restore check also exposed that the initial worktree was shallow: its bundle had valid pack bytes but lacked a boundary commit's parent. The public origin history was fetched without changing the working tree, and packaging now rejects shallow inputs and requires an actual clone, matching HEAD and full object check before publishing. It also checks the exact output file set after cleaning its own temporary lock.

Final publication requires all 14 core gates plus the browser gate (six journeys) on the same clean commit. The authoritative final result and exact commit are generated after commit in `build/regression_report.json`, `build/browser-check/report.json` and `delivery.json`; no pre-commit result is relabelled as clean-commit evidence.

## Reproduce and interpret the final delivery

See [release-workflow.md](release-workflow.md). In a prepared environment, build with `python tools/build_project.py --write`, commit the complete source and manifests, run `python tools/release_check.py --with-browser`, then `python tools/package_release.py`.

The browser suite exercises real local UI controls with synthetic answers and isolated databases. It includes the new primary entry lesson and 390px layouts. It does not establish teacher approval, real student learning effects, complete accessibility conformance, exhaustive curriculum micro-skill coverage or empirical item difficulty. Those boundaries remain explicit; the two user-deferred review/effect activities do not block this implementation.
