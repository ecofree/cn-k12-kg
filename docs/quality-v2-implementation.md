# Quality V2 implementation ledger

Baseline: f5249637cff9312f0669d46a20ed126f9a1c8863. Branch: codex/cnkg-quality-v2.

The accepted scope is the three implementation batches in audit-2026-10-26-v2.md. Independent teacher review and real-student effect validation remain deferred by the user. Completion is reported by capability and evidence, not by an unqualified course-wide percentage.

## Batch 1 — correct feedback and one data contract

Completed: exact/approximate/structured answer contracts, CLI option IDs, mathematical semantics, explicit Schema validation, trustworthy source observations and corroboration, duplicate-file cleanup and current documentation. Author verification now also uses bounded exact rational arithmetic rather than the former global float tolerance.

Already completed: 15 retired copies were verified byte-identical to their archives and removed. data/migrations/quality-v2-cleanup.json records every path, archive, byte count and SHA-256. All 23 active volume sources and the archives remain.

Gate: the audit's wrong-answer and legal-variant cases, CLI path, Schema incompatibilities and false corroboration must be fixed and exercised before the batch is committed.

Acceptance: all 11 release gates passed on 2026-10-26. All 2,257 author-verifiable numeric questions pass the exact/bounded evaluator; all 3,568 reference answers pass the unified student grader. Additional adversarial tests cover undefined powers, concealed unexpanded products, noninteger factors and computed-exponent resource limits. New numeric attempt records use exact rational strings and retain the original input. The three formerly truncated fraction answers were corrected in the active authoring sources and rebuilt.

Reproduce: `python -m pip install -r requirements-dev.txt`, then `python tools/release_check.py`. The release report and each gate's full log are in `build/`.

## Batch 2 — one coherent learning chain

Completed: stable skill IDs, textbook placement mappings, unified task/answer contracts, task-family exposure, saved self-checks and project work, next-step/review planning, and safe continuation of sessions under immutable content versions. The first coherent chain is fractions → ratio/proportion → linear functions, with explicit prerequisite links.

The registry contains 1,058 skills (20 observable, 84 composite, 954 legacy composite), 4,439 tasks and 505 materials. The three extended units contain 60 tasks and 20 new observable objectives, supported by six new mathematical diagrams. Counts describe registered resources; they do not imply that every legacy criterion is atomic or independently validated. All 477 quarantined bank records remain excluded from practice and diagnosis.

Learner drafts and revisions are stored transactionally with conflict detection and idempotent retries. Self-assessment remains learner-reported evidence. Material and reference exposure are recorded, and repeated task families do not become independent evidence. New checks with an exhausted pool offer a working practice route. Stored session content and SVGs preserve the original task version across content updates.

Acceptance: all 13 release gates passed. The full core run contained 543 tests (527 passed; 16 optional external-data tests skipped). A subsequent 25-test planner/HTTP run verified the final recommendation grouping. All six frozen browser journeys passed: self-check autosave, rubric revision, the complete 20-task linear-function unit, project history/export, stage-aware mobile layouts, and exhausted-pool recovery. The immediate retest correctly records that the delay window was not met. These are synthetic functional checks, not real-student outcome evidence.

Evidence: `build/quality-v2/batch2/` preserves the gate report/logs; `build/quality-v2/batch2-browser/` preserves the six browser results and screenshots. Legacy IDs and learning records remain readable.

## Batch 3 — course depth and maintainable delivery

Planned: extend the unified resource protocol across the catalog, add purposeful representations and process/project recording, adapt the home page by stage, consolidate authoring sources, archive retired generators and add reproducible browser journeys and release manifests.

Gate: content resources have a single authoring source, applicable objectives have inspectable tasks and scoring evidence, no duplicate family inflates independent coverage, and the full delivery can be rebuilt and exercised from the documented commands.

Final counts, commands, commit IDs and limitations will be appended from actual results.
