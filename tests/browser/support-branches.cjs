"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

module.exports = async function supportBranchesJourney({page, goto, actionResponse, apiRead, claim, result, noHorizontalOverflow}) {
  const uid = "data_reading_and_mean";
  const unit = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../data/learning_units/" + uid + ".json"), "utf8"));
  const authored = unit.instructional_support.supports.find(row => row.objective_id === uid + ":read_scale");
  const confirmations = new Map(unit.tasks.map(task => [task.id, task]));
  const root = page.locator("#support-content");
  async function start(unitId = uid, support = authored) {
    await goto("?unit=" + unitId);
    const link = page.locator(".support-link").filter({has: page.getByRole("heading", {name: support.title, exact: true})});
    return actionResponse(() => link.getByRole("button", {name: "辨别困难并跟着练", exact: true}).click(), "/api/support/start");
  }
  async function submit(data, answer, skipped = false) {
    if (!skipped) {
      if (data.item.type === "choice") await root.locator(`input[type="radio"][value="${answer}"]`).check();
      else await root.locator("#support-answer").fill(String(answer));
    }
    const changed = await actionResponse(() => root.getByRole("button", {name: skipped ? "暂时不会" : "记录这一步", exact: true}).click(), "/api/support/answer");
    if (!changed.done) await root.locator(`.support-task[data-task-id="${changed.item.id}"]`).waitFor();
    return changed;
  }
  let data = await start();
  const firstSession = data.session.id;
  for (const branch of authored.guided_branches) assert.ok(!JSON.stringify(data).includes(branch.guided_task.prompt));
  data = await submit(data, "A");
  const firstGuide = data.item.id;
  assert.equal(data.support.route.route_id, "scale_per_grid");
  assert.equal(await root.locator(".support-route").getAttribute("data-route-id"), "scale_per_grid");
  assert.equal(data.item.prompt, authored.guided_branches.find(row => row.id === "scale_per_grid").guided_task.prompt);
  await root.locator(".support-task img").waitFor();
  assert.match(await root.locator(".support-task img").getAttribute("src"), /^\/api\/session\/[a-f0-9]+\/assets\//);
  await page.waitForFunction(() => [...document.querySelectorAll("#support-content .support-task img")].every(img => img.complete && img.naturalWidth > 0));
  await actionResponse(() => root.getByRole("button", {name: "提示2 · 分步引导", exact: true}).click(), "/api/support/hint");
  data = await submit(data, "999");
  data = await submit(data, confirmations.get(data.item.id).answer);
  assert.equal(data.done, true);
  assert.equal(data.answers.at(-1).status, "correct");
  assert.equal(data.answers.at(-1).confirmation_eligible, false, "A last correct response cannot erase the failed guide");
  let plan = await apiRead("/api/plan");
  const original = plan.learning_needs.find(need => need.kind === "support_revision" && need.source_ref.session_id === firstSession);
  assert.ok(original);
  assert.equal(original.status, "open");
  await root.getByRole("button", {name: "关联原事项，重新辨别并练习", exact: true}).waitFor();
  data = await actionResponse(() => root.getByRole("button", {name: "关联原事项，重新辨别并练习", exact: true}).click(), "/api/support/start");
  const retrySession = data.session.id;
  assert.notEqual(retrySession, firstSession);
  await page.waitForFunction(id => new URL(location.href).searchParams.get("session") === id, retrySession);
  assert.deepEqual(data.session.retry_need_ids, [original.need_id]);
  data = await submit(data, "B");
  assert.equal(data.support.route.route_id, "restore_axis_origin");
  assert.notEqual(data.item.id, firstGuide);
  assert.equal(data.item.prompt, authored.guided_branches.find(row => row.id === "restore_axis_origin").guided_task.prompt);
  assert.equal((await apiRead("/api/plan")).learning_needs.find(need => need.need_id === original.need_id).status, "open");
  data = await submit(data, "30");
  data = await submit(data, confirmations.get(data.item.id).answer);
  assert.equal(data.answers.at(-1).confirmation_eligible, true);
  plan = await apiRead("/api/plan");
  assert.equal(plan.learning_needs.find(need => need.need_id === original.need_id).status, "resolved");
  const followup = plan.learning_needs.find(need => need.kind === "independent_followup" && need.parent_need_id === original.need_id);
  assert.ok(followup);
  assert.equal(followup.status, "open");
  assert.equal(followup.resource_version, original.resource_version);
  assert.equal(followup.followup.ready, false);
  const originalRecord = await apiRead("/api/session/" + firstSession);
  assert.ok(originalRecord.answers.some(answer => answer.status === "wrong" && answer.support_kind === "guided"));
  await page.reload({waitUntil: "domcontentloaded"});
  await root.locator('.support-route[data-route-id="restore_axis_origin"]').waitFor();
  assert.ok((await root.textContent()).includes("本条指导与后续确认"));

  const skippedUnit = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../data/learning_units/fraction_equivalence.json"), "utf8"));
  const skippedSupport = skippedUnit.instructional_support.supports.find(row => row.objective_id === "fraction_equivalence:whole");
  const skippedConfirmations = new Map(skippedUnit.tasks.map(task => [task.id, task]));
  data = await start(skippedUnit.id, skippedSupport);
  const skippedSession = data.session.id;
  data = await submit(data, "", true);
  assert.equal(data.support.route.route_id, "skip");
  assert.equal(data.item.prompt, skippedSupport.skip_route.guided_task.prompt);
  data = await submit(data, skippedSupport.skip_route.guided_task.answer);
  data = await submit(data, skippedConfirmations.get(data.item.id).answer);
  assert.equal(data.answers.at(-1).status, "correct");
  assert.equal(data.answers.at(-1).confirmation_eligible, false);
  plan = await apiRead("/api/plan");
  assert.equal(plan.learning_needs.find(need => need.kind === "support_revision" && need.source_ref.session_id === skippedSession).status, "open");
  assert.equal(plan.learning_needs.find(need => need.need_id === followup.need_id).status, "open");
  await page.setViewportSize({width: 390, height: 844});
  await noHorizontalOverflow("support_branches_390");
  const expandedRoutes = [];
  for (const [unitId, goal, wrongChoice, expectedRoute] of [
    ["independent_events", "conditional", "B", "condition_on_subset"],
    ["sampling_and_variation", "population", "B", "zero_chance_outside_frame"],
    ["correlation_and_regression", "r_squared", "A", "variation_not_case_accuracy"],
    ["data_reading_and_mean", "combine", "A", "expand_group_weights"],
  ]) {
    const current = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../data/learning_units/" + unitId + ".json"), "utf8"));
    const support = current.instructional_support.supports.find(row => row.objective_id === unitId + ":" + goal);
    const branch = support.guided_branches.find(row => row.id === expectedRoute);
    data = await start(unitId, support);
    data = await submit(data, wrongChoice);
    assert.equal(data.support.route.route_id, expectedRoute);
    assert.equal(data.item.prompt, branch.guided_task.prompt);
    assert.equal(await root.locator(".support-route").getAttribute("data-route-id"), expectedRoute);
    for (const alternative of support.guided_branches) {
      if (alternative.id !== expectedRoute) assert.ok(!JSON.stringify(data).includes(alternative.guided_task.prompt));
    }
    const hinted = await actionResponse(() => root.getByRole("button", {name: "提示2 · 分步引导", exact: true}).click(), "/api/support/hint");
    assert.deepEqual(hinted.feedback.reference_family_ids, support.hints[1].reference_family_ids);
    assert.equal(hinted.support.hints_opened.length, 1);
    await noHorizontalOverflow("support_" + unitId + "_390");
    data = await submit(data, branch.guided_task.answer);
    const confirmation = current.tasks.find(task => task.id === data.item.id);
    assert.ok(confirmation.objective_ids.includes(support.objective_id));
    const value = confirmation.answer || confirmation.rubric.map(row => row.example).join("；");
    data = await submit(data, value);
    assert.equal(data.done, true);
    assert.equal(data.answers.at(-1).confirmation_eligible, confirmation.type !== "rubric");
    assert.equal(data.session.context.independent_evidence, false);
    expandedRoutes.push({unit_id: unitId, objective_id: support.objective_id,
      route: expectedRoute, confirmation: confirmation.id, help_preserved: true});
  }
  result.observations.support_branches = {different_wrong_choices_changed_real_tasks: true, firstGuide,
    retry_route: "restore_axis_origin", original_need_id: original.need_id, followup_need_id: followup.need_id,
    failed_guide_preserved: true, skipped_diagnostic_preserved: true, reload_kept_selected_route: true,
    probability_statistics_routes: expandedRoutes};
  claim("Two actual diagnostic choices lead to different frozen guide questions and real session SVGs; unselected branch content is not returned.");
  claim("A final correct answer cannot erase an incorrect guide or skipped diagnostic. The retry button names the original need and updates the actual session URL.");
  claim("Successful linked retry retains the original failure and creates a separate open follow-up obligation; reload keeps the selected route.");
  claim("Four probability/statistics units render their authored error route, only the opened hint level, matching confirmation, and bounded help provenance at mobile width.");
};
