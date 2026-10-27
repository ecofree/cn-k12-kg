# Quality V6 follow-up: unresolved correctness and alignment work

Status: **audit backlog only; no implementation is claimed by this document**.
Baseline: `codex/cnkg-quality-v5` at the currently published head.
Teacher review and real-student outcome validation remain deferred by user request.

## Confirmed gaps from the current published source

1. **Skill granularity is uneven.** `data/skills/registry.json` still contains 954 `legacy_composite` skills, 96 `composite`, and 84 `observable`. Do not bulk-relabel. Split only when the task evidence and teaching material support the narrower behavior.
2. **Question alignment is incomplete.** The published scope report records 1,134 stable target identities, with 198 targets in the deeply aligned set; 936 lack verified task-family and representation mappings. Existing question tags do not establish that the task observes the full target.
3. **Target-specific support is incomplete.** The published report records 510 targets with explicit help/observation-boundary support and 624 without it.
4. **Diagnostic eligibility must remain conservative.** Unknown representation and unknown task family are absence of evidence, not evidence of novelty or independent transfer. Text-only answer checks must not imply full-topic mastery.
5. **Content and representation need targeted semantic review.** Review at least: reverse-operation word problems in early primary math; estimation boundary conditions; spatial-diagram tasks; multi-view solids; geometry volume tasks; conditional probability; grouped-data boundary inclusion; and any task whose solution is supplied in the prompt.
6. **Some content cards and their task list need fine-grained alignment.** For example, `data/teach/mh2.8.3.1.json` includes a frustum-volume objective while its topic-level practice list also includes prism/pyramid/surface-area items. Keep these as practice only for the target they actually observe; do not infer skill evidence from the shared topic ID.
7. **Restore and identity switching need a destructive-boundary regression.** If an anonymous profile has saved work but no recovery method, attempting to switch to another profile must not silently strand the first profile. Preserve access or require a verified recovery path before switching.
8. **Review timing and task-family qualification must share one rule.** Planner recommendations must not advertise a due review that the session-creation endpoint rejects; unknown representation must not count as a new representation.

## Batch A — correctness and evidence boundaries

- Add a regression case for every confirmed content or workflow counterexample before changing implementation.
- Review each affected task's stem, constraints, answer, intermediate reasoning, units, representation, target mapping, and allowed use.
- Keep old IDs and immutable historical attempts.
- Separate observed response, learner self-report, help exposure, inferred prerequisite, and independent follow-up evidence.
- Remove diagnostic or whole-target confirmation permissions when the item only measures a narrower behavior.

Exit criteria:
- All listed counterexamples have tests.
- No unattempted prerequisite is silently inferred as mastered.
- Planner and session endpoint agree on review eligibility.
- Profile switching cannot strand unprotected saved work.
- No content correction silently widens task admission.

## Batch B — skill/task/teaching alignment

- Map tasks to explicit observable skills, not just topic IDs, when the evidence supports that mapping.
- Record task-family identity, representation, cognitive demand, and answer/help exposure.
- Treat `unknown` as unknown. Never fill metadata merely to improve coverage percentages.
- Add a real diagram or interactive representation where the learning objective requires spatial or graphical reasoning.
- Make help branches respond to the diagnosed error and end in a bounded follow-up observation.
- Publish a coverage matrix with separate denominators for core, foundational support, and extension targets.

Exit criteria:
- Every claimed target-to-task mapping is traceable to the actual prompt and expected student behavior.
- Missing mappings remain visible.
- Repeated numeric variants do not inflate independent transfer coverage.
- The affected pages and tasks pass browser preview with version and identity checks.

## Batch C — sustainable authoring and release

- Maintain one authoritative source per resource; generated catalog/database files are not hand-edited.
- Run semantic diff review and impact analysis for target, answer, family, representation, and admission changes.
- Bind reports, screenshots, and test logs to one clean commit.
- Preserve failed candidate logs and rerun the complete release gates after any code or content change.
- Keep teacher review and real-student outcome validation explicitly deferred; do not label AI review as teacher approval or synthetic browser tests as learning-effect evidence.

## Non-goals for this follow-up

- Do not claim the whole K12 curriculum is atomic or fully skill-aligned while legacy composite targets remain.
- Do not infer instructional effectiveness from question counts, passing structural checks, or synthetic student models.
- Do not reduce the declared curriculum scope to make coverage appear complete.
