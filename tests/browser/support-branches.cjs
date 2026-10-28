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
  async function holdRequest(pattern) {
    let arrive, release;
    const arrived = new Promise(resolve => { arrive = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    const handler = async route => { arrive(); await gate; await route.continue(); };
    await page.route(pattern, handler);
    return {arrived, release, remove: () => page.unroute(pattern, handler)};
  }
  let data = await start();
  const firstSession = data.session.id;
  for (const branch of authored.guided_branches) assert.ok(!JSON.stringify(data).includes(branch.guided_task.prompt));
  await actionResponse(() => root.locator('input[type="radio"][value="A"]').check(), "/api/work");
  assert.ok((await apiRead("/api/plan")).next_steps.some(step => step.session_id === firstSession && step.continuation === "resume_support"));
  await page.reload({waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => document.querySelector('#support-content input[type="radio"][value="A"]')?.checked);
  assert.equal((await apiRead("/api/session/" + firstSession)).answers.length, 0, "Restoring an unsubmitted choice must not invent an answer");
  data = await submit(data, "A");
  const firstGuide = data.item.id;
  assert.equal(data.support.route.route_id, "scale_per_grid");
  assert.equal(await root.locator(".support-route").getAttribute("data-route-id"), "scale_per_grid");
  assert.equal(data.item.prompt, authored.guided_branches.find(row => row.id === "scale_per_grid").guided_task.prompt);
  await root.locator(".support-task img").waitFor();
  assert.match(await root.locator(".support-task img").getAttribute("src"), /^\/api\/session\/[a-f0-9]+\/assets\//);
  await page.waitForFunction(() => [...document.querySelectorAll("#support-content .support-task img")].every(img => img.complete && img.naturalWidth > 0));
  await actionResponse(() => root.locator("#support-answer").fill("18"), "/api/work");
  const draftResource = await root.locator(".support-draft").getAttribute("data-resource-id");
  await page.reload({waitUntil: "domcontentloaded"});
  await page.waitForFunction(() => document.querySelector("#support-answer")?.value === "18" && !document.querySelector("#support-answer").disabled);
  const hint2 = root.getByRole("button", {name: "提示2 · 分步引导", exact: true});
  const heldHint = await holdRequest("**/api/support/hint");
  await hint2.click(); await heldHint.arrived;
  const failedWrite = route => route.abort("failed");
  await page.route("**/api/work", failedWrite);
  await root.locator("#support-answer").fill("19");
  await root.locator(".work-status").filter({hasText: "尚未保存"}).waitFor();
  const hintResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/support/hint");
  heldHint.release(); assert.equal((await hintResponse).status(), 200);
  await page.locator("#action-notice").filter({hasText: "当前草稿还未保存"}).waitFor();
  assert.equal(await root.locator('.support-hint[data-hint-level="2"]').isVisible(), false);
  assert.equal(await hint2.getAttribute("aria-expanded"), "false");
  assert.equal(await root.locator("#support-answer").inputValue(), "19");
  await heldHint.remove(); await page.unroute("**/api/work", failedWrite);
  await actionResponse(() => root.getByRole("button", {name: "保存记录", exact: true}).click(), "/api/work");
  await hint2.focus();
  await actionResponse(() => hint2.press("Enter"), "/api/support/hint");
  await page.waitForFunction(() => document.querySelector('.support-hint[data-hint-level="2"]')?.hidden === false);
  assert.equal(await hint2.getAttribute("aria-expanded"), "true");
  assert.equal(await hint2.evaluate(element => element === document.activeElement), true, "Hint expansion keeps keyboard focus on its control");
  assert.equal(await hint2.getAttribute("aria-controls"), await root.locator('.support-hint[data-hint-level="2"]').getAttribute("id"));

  const hint3 = root.getByRole("button", {name: "提示3 · 查看例子", exact: true});
  const heldConflictHint = await holdRequest("**/api/support/hint");
  await hint3.click(); await heldConflictHint.arrived;
  const beforeConflict = await apiRead("/api/work?resource_id=" + encodeURIComponent(draftResource));
  const competing = await page.context().request.post(new URL("/api/work", page.url()).href, {data: {
    resource_id: draftResource, expected_revision: beforeConflict.work.revision,
    request_id: "support-draft-conflict-" + Date.now(), ...beforeConflict.work.payload, draft: {response: "20"},
  }});
  assert.equal(competing.status(), 200);
  const conflictResponse = page.waitForResponse(response => new URL(response.url()).pathname === "/api/work" && response.status() === 409);
  await root.locator("#support-answer").fill("21"); await conflictResponse;
  const recordedConflictHint = page.waitForResponse(response => new URL(response.url()).pathname === "/api/support/hint");
  heldConflictHint.release(); assert.equal((await recordedConflictHint).status(), 200);
  await page.waitForFunction(() => document.querySelector('#support-content button[data-hint-level="3"]')?.getAttribute("aria-busy") === "false");
  assert.equal(await root.locator('.support-hint[data-hint-level="3"]').isVisible(), false);
  assert.equal(await root.locator("#support-answer").inputValue(), "21");
  await heldConflictHint.remove();
  await actionResponse(() => root.getByRole("button", {name: "保留两版并保存本页内容", exact: true}).click(), "/api/work");
  await actionResponse(() => hint3.click(), "/api/support/hint");
  await page.waitForFunction(() => document.querySelector('.support-hint[data-hint-level="3"]')?.hidden === false);
  const drafts = await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(draftResource));
  for (const value of ["18", "19", "20", "21"]) assert.ok(drafts.revisions.some(row => row.payload.draft.response === value));
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
  const routeCases = [
    ["independent_events", "conditional", "B", "condition_on_subset"],
    ["sampling_and_variation", "population", "B", "zero_chance_outside_frame"],
    ["correlation_and_regression", "r_squared", "A", "variation_not_case_accuracy"],
    ["data_reading_and_mean", "combine", "A", "expand_group_weights"],
  ];
  for (const unitId of ["ten_frame_place_value", "linear_function_representations"]) {
    const unit = JSON.parse(fs.readFileSync(path.resolve(__dirname, "../../data/learning_units/" + unitId + ".json"), "utf8"));
    const support = unit.instructional_support.supports[0], branch = support.guided_branches?.[0];
    assert.ok(branch, "The newly completed unit must have an executable authored error route");
    routeCases.push([unitId, support.objective_id.slice(unitId.length + 1), branch.choice_ids[0], branch.id]);
  }
  for (const [unitId, goal, wrongChoice, expectedRoute] of routeCases) {
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
    authored_routes: expandedRoutes,
    draft_recovery: {unsubmitted_choice: true, unsubmitted_guide: true, failed_save_kept_hint_closed: true,
      concurrent_409_kept_hint_closed: true, both_versions_preserved: true, keyboard_hint_focus: true}};
  claim("Two actual diagnostic choices lead to different frozen guide questions and real session SVGs; unselected branch content is not returned.");
  claim("A final correct answer cannot erase an incorrect guide or skipped diagnostic. The retry button names the original need and updates the actual session URL.");
  claim("Successful linked retry retains the original failure and creates a separate open follow-up obligation; reload keeps the selected route.");
  claim("Four probability/statistics units and two newly branched arithmetic/function units render the actual authored route, only opened hints, a matching confirmation, and bounded help provenance at mobile width.");
  claim("Unsubmitted diagnostic choices and guide drafts restore without inventing an answer. Failed or conflicting later writes keep requested hints closed and preserve both draft versions.");
  claim("Hint controls name their controlled region, expose expanded state, and retain keyboard focus after successful persistence.");
};
