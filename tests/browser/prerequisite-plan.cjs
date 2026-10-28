"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

module.exports = async function prerequisitePlanJourney({page, goto, actionResponse, apiRead,
    startFrom, completeSession, claim, result, noHorizontalOverflow}) {
  const root = path.resolve(__dirname, "../..");
  const read = name => JSON.parse(fs.readFileSync(path.join(root, name), "utf8"));
  const registry = read("data/skills/registry.json");
  const catalog = read("data/catalog.json");
  const sourceId = "sampling_and_variation";
  const targetId = "correlation_and_regression";
  const attemptedSkill = registry.objective_skill_ids[sourceId + ":sampling"];
  const missingSkill = registry.objective_skill_ids[sourceId + ":spread"];
  const taskId = sourceId + ":practice-2";
  assert.ok(attemptedSkill && missingSkill && attemptedSkill !== missingSkill);
  assert.ok(catalog.units[targetId].skill_ids.length);

  // Select one real task through its goal card. A completed focused session
  // must not become evidence for every other goal inside the same unit.
  await goto("?goal=" + attemptedSkill + "&goal_unit=" + sourceId);
  await page.locator("#view-goal").waitFor({state: "visible"});
  const pathway = await apiRead("/api/goal?skill_id=" + attemptedSkill + "&unit_id=" + sourceId);
  const optionIndex = pathway.task_options.findIndex(task => task.task_id === taskId);
  assert.ok(optionIndex >= 0, "The authored single-task route must be present");
  const current = await startFrom(page.locator("#goal-content .goal-task").nth(optionIndex)
    .getByRole("button", {name: "做这道任务", exact: true}), "practice");
  assert.equal(current.item.question.id, taskId);
  const completed = await completeSession(current);
  assert.equal(completed.summary.answers.length, 1);
  assert.ok(completed.summary.answers[0].skill_ids.includes(attemptedSkill));
  assert.ok(!completed.summary.answers[0].skill_ids.includes(missingSkill));
  const savedSessionId = completed.session.id;

  const plan = await apiRead("/api/plan?stage=senior");
  const target = plan.next_steps.find(step => step.unit_id === targetId);
  assert.ok(target, "The senior learning plan must include the regression unit");
  assert.ok(target.prerequisite_skill_ids.includes(missingSkill),
    "Completing the sampling task cannot erase the unattempted spread goal");
  assert.ok(target.prerequisite_unit_ids.includes(sourceId),
    "The source unit must remain a route to its unattempted goal");
  assert.ok(!target.observed_prerequisite_skill_ids.includes(missingSkill));
  assert.equal(target.prerequisite_evidence, "direct_skill_attempts_and_unit_activity_not_mastery");
  assert.ok(!plan.skill_evidence.some(row => row.skill_id === missingSkill && row.observations.length));
  assert.ok(plan.skill_evidence.every(row => row.independent_mastery_evidence === false));

  await goto();
  await actionResponse(() => page.locator("#stage-select").selectOption("senior"), "/api/plan", "GET",
    response => new URL(response.url()).searchParams.get("stage") === "senior");
  const card = page.locator("#learning-plan .plan-row").filter({hasText: catalog.units[targetId].title});
  await card.waitFor({state: "visible"});
  const details = card.locator("details.plan-prerequisites");
  await details.locator("summary").click();
  const goalRow = details.locator(`[data-prerequisite-skill="${missingSkill}"]`);
  await goalRow.waitFor({state: "visible"});
  assert.ok((await goalRow.textContent()).includes(registry.skills[missingSkill].statement));
  const opened = await actionResponse(() => goalRow.getByRole("button", {name: "检查这项前置目标", exact: true}).click(),
    "/api/goal", "GET", response => new URL(response.url()).searchParams.get("skill_id") === missingSkill);
  assert.equal(opened.skill_id, missingSkill);
  await page.locator("#view-goal").waitFor({state: "visible"});
  assert.equal(new URL(page.url()).searchParams.get("goal"), missingSkill);
  assert.equal(await page.locator("#goal-title").textContent(), registry.skills[missingSkill].statement);
  await page.setViewportSize({width: 390, height: 844});
  await noHorizontalOverflow("prerequisite_goal_390");
  const preserved = await apiRead("/api/session/" + savedSessionId);
  assert.deepEqual(preserved.summary.answers, completed.summary.answers,
    "Opening a prerequisite cannot reinterpret the saved single-task observation");

  // A new, real diagnostic result has no generated ancestor evidence. Old
  // inference fields remain visibly labelled as untested historical records.
  await page.setViewportSize({width: 1280, height: 900});
  const diagnostic = await actionResponse(() => goto("?target=m7a.5.2"), "/api/session");
  const finished = await completeSession(diagnostic);
  assert.deepEqual(finished.summary.assumed, []);
  assert.equal(finished.summary.unobserved_prerequisites_inferred, false);
  assert.equal(finished.summary.independent_mastery_evidence, false);
  assert.ok(finished.summary.untested.length > 0, "Unanswered prerequisites must remain untested");
  await page.getByText("本次检查范围", {exact: true}).click();
  const headings = await page.locator("#res-chapters th").allTextContents();
  assert.ok(headings.includes("旧推断（未实测）"));
  assert.ok((await page.locator("#res-candidates").textContent()).includes("旧版先修推断仅保留作历史记录"));
  await page.setViewportSize({width: 390, height: 844});
  await noHorizontalOverflow("prerequisite_result_390");

  result.observations.prerequisite_plan = {source_unit: sourceId, source_session: savedSessionId,
    selected_task_id: taskId, recorded_answer_count: completed.summary.answers.length,
    attempted_skill_id: attemptedSkill, unattempted_skill_id: missingSkill,
    target_unit: targetId, retained_prerequisite_route: true, exact_goal_navigation: true,
    original_answers_preserved: true, new_assumed_count: finished.summary.assumed.length,
    untested_prerequisite_count: finished.summary.untested.length, legacy_inference_label: "旧推断（未实测）"};
  claim("Completing one selected task leaves other goals of its unit unobserved in the real next-step plan.");
  claim("The home plan exposes a precise prerequisite-goal button and preserves the original answers after navigation.");
  claim("New diagnostic results do not infer unanswered ancestors; the existing inference column is explicitly historical and untested.");
  claim("The prerequisite goal and diagnostic evidence table fit a 390px viewport.");
};
