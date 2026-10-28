"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const outputIndex = process.argv.indexOf("--output");
const output = outputIndex >= 0 ? process.argv[outputIndex + 1] : null;

module.exports = async function equationStepJourney({page, goto, actionResponse, apiRead, claim, result, noHorizontalOverflow}) {
  await goto("?unit=equation_equivalence");
  const panel = page.locator(".step-practice");
  await panel.locator('[data-step-case="one_solution"]').waitFor();
  let state = await actionResponse(() => panel.locator('[data-step-case="one_solution"]').click(), "/api/steps/start");
  const firstId = state.id;
  await panel.locator('[data-step-action="answer"]:not([disabled])').waitFor();
  if (output) await panel.screenshot({path: path.join(output, "steps-desktop.png")});
  const incomplete = " 3(x− ";
  state = await actionResponse(() => panel.locator("#step-equation").fill(incomplete), "/api/steps/draft");
  assert.equal(state.draft.equation, incomplete);
  assert.equal(state.timeline.length, 0);
  assert.equal("feedback" in state, false);
  await page.waitForFunction(() => !window.CNKGWork.hasUnsaved());
  await goto("?unit=equation_equivalence");
  await panel.locator("#step-equation").waitFor();
  assert.equal(await panel.locator("#step-equation").inputValue(), incomplete);

  // Interrupt draft writes only. The real router must refuse to discard them.
  const interrupted = " 3(x−2)=";
  const failedWrite = route => route.abort("failed");
  await page.route("**/api/steps/draft", failedWrite);
  await panel.locator("#step-equation").fill(interrupted);
  await panel.locator("[data-step-draft-status]").filter({hasText: "尚未保存"}).waitFor();
  const originalUrl = page.url();
  await page.locator("#restart").click();
  await page.locator("#save-notice").waitFor();
  assert.equal(await page.locator("#view-unit").isVisible(), true);
  assert.equal(page.url(), originalUrl);
  assert.equal(await panel.locator("#step-equation").inputValue(), interrupted);
  assert.equal(await page.evaluate(() => {
    const event = new Event("beforeunload", {cancelable: true});
    dispatchEvent(event); return event.defaultPrevented && window.CNKGWork.hasUnsaved();
  }), true);
  await page.unroute("**/api/steps/draft", failedWrite);
  state = await actionResponse(() => panel.locator('[data-step-action="draft"]').click(), "/api/steps/draft");
  assert.equal(state.draft.equation, interrupted);
  assert.equal(state.timeline.length, 0);
  await page.waitForFunction(() => !window.CNKGWork.hasUnsaved());
  await goto("?unit=equation_equivalence");
  await panel.locator("#step-equation").waitFor();
  assert.equal(await panel.locator("#step-equation").inputValue(), interrupted);
  async function step(equation, operation, operand = "") {
    await panel.locator("#step-equation").fill(equation);
    await panel.locator("#step-operation").selectOption(operation);
    await panel.locator("#step-operand").fill(operand);
    const value = await actionResponse(() => panel.locator('[data-step-action="answer"]').click(), "/api/steps/answer");
    await panel.locator('[data-step-action="answer"]:not([disabled])').waitFor();
    return value;
  }
  async function conclude(kind, value = "") {
    await panel.locator("#step-conclusion").selectOption(kind);
    await panel.locator("#step-value").fill(value);
    const data = await actionResponse(() => panel.locator('[data-step-action="conclusion"]').click(), "/api/steps/answer");
    await panel.locator('[data-step-case="identity"]:not([disabled])').waitFor();
    return data;
  }
  state = await step("y=6", "equivalent");
  assert.equal(state.feedback.status, "unsupported");
  assert.equal(state.current_equation, "3(x−2)=12");
  state = await step("0=0", "multiply", "0");
  assert.equal(state.feedback.status, "non_equivalent");
  assert.match(state.feedback.feedback, /x=0/);
  state = await actionResponse(() => panel.locator('[data-step-hint="2"]').click(), "/api/steps/hint");
  await panel.locator('[data-step-action="answer"]:not([disabled])').waitFor();
  assert.equal(state.timeline.at(-1).kind, "hint");
  state = await step("x-2=4", "divide", "3");
  assert.equal(state.feedback.operation_status, "verified");
  state = await step("x=6", "add", "2");
  assert.equal(state.feedback.operation_status, "verified");
  state = await conclude("unique", "6");
  assert.equal(state.completed, true);
  assert.equal(state.may_confirm_mastery, false);
  assert.equal(state.timeline.length, 6);
  const frozenTimeline = state.timeline;

  await goto("?unit=equation_equivalence");
  await panel.getByText("我的原始输入：y=6", {exact: true}).waitFor();
  assert.deepEqual((await apiRead("/api/steps?activity_id=" + firstId)).timeline, frozenTimeline);
  assert.equal(await panel.locator('[data-step-history="true"] > li').count(), 6);

  state = await actionResponse(() => panel.locator('[data-step-case="no_solution"]').click(), "/api/steps/start");
  await panel.locator('[data-step-action="answer"]:not([disabled])').waitFor();
  state = await step("2x+6=2x+7", "simplify");
  state = await step("0=1", "subtract", "2x+6");
  assert.equal(state.feedback.accepted, true);
  state = await conclude("none");
  assert.equal(state.feedback.status, "conclusion_correct");

  state = await actionResponse(() => panel.locator('[data-step-case="identity"]').click(), "/api/steps/start");
  await panel.locator('[data-step-action="answer"]:not([disabled])').waitFor();
  state = await step("2x+6=2x+6", "simplify");
  state = await step("0=0", "subtract", "2x+6");
  state = await conclude("all");
  assert.equal(state.feedback.status, "conclusion_correct");
  await panel.getByText("我的结论：任意实数都是解", {exact: true}).waitFor();
  assert.equal((await apiRead("/api/steps?unit_id=equation_equivalence")).total, 3);
  assert.deepEqual((await apiRead("/api/steps?activity_id=" + firstId)).timeline, frozenTimeline);
  await page.setViewportSize({width: 390, height: 844});
  await noHorizontalOverflow("equation_steps_390");
  if (output) await panel.screenshot({path: path.join(output, "steps-mobile.png")});
  result.observations.equation_step_activity = {cases: 3, preserved_first_attempts: frozenTimeline.length,
    invalid_unknown_preserved: true, zero_multiplication_rejected: true, mastery_confirmed: false,
    unsubmitted_draft_restored: true, failed_draft_write_blocks_navigation: true, dirty_draft_cancels_beforeunload_event: true};
  claim("An incomplete unsubmitted draft is autosaved and restored without grading; failed draft writes preserve the fields, block real in-app navigation, and activate the beforeunload guard.");
  claim("Actual controls save unsupported input, a non-equivalent zero-multiplication attempt, a requested hint, and a different legal solution route.");
  claim("Reloading restores original steps and feedback; completing identity/no-solution cases preserves the earlier activity and never creates mastery evidence.");
  claim("The bounded equation activity has no viewport-level horizontal overflow at 390px.");
};
