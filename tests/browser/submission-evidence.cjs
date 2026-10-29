/* Actual UI writes and reloads: self-rating cannot replace missing process work. */
"use strict";
const assert = require("node:assert/strict");
module.exports = async ({page, goto, actionResponse, apiRead, saveText, visible, claim, result, noHorizontalOverflow}) => {
  await goto("?teach=m1a.1.1"); await visible("#view-teach");
  let input = page.locator("#self-check-0");
  let card = page.locator("#teach-content > .card").filter({has: input});
  let editor = card.locator(".work-editor");
  await editor.locator(".self-assessment > summary").click();
  const checks = editor.locator(".self-assessment select");
  for (let i = 0; i < await checks.count(); i++) await checks.nth(i).selectOption("met");
  let saved = await actionResponse(() => editor.getByRole("button", {name: "提交我的自查", exact: true}).click(), "/api/work", "POST",
    response => response.request().postDataJSON().status === "submitted");
  const resourceId = saved.work.resource_id;
  assert.deepEqual(saved.submission_evidence.missing_field_ids, ["response"]);
  await editor.locator(".work-evidence-status").filter({hasText: "尚缺实际过程"}).waitFor();
  let plan = await apiRead("/api/plan");
  const originalNeed = plan.learning_needs.find(need => need.work_resource_id === resourceId);
  assert.ok(originalNeed); assert.equal(originalNeed.status, "open");
  assert.equal(originalNeed.revision_basis, "missing_submission");
  await page.reload({waitUntil: "domcontentloaded"});
  await page.locator("#open-history:not([disabled])").waitFor();
  input = page.locator("#self-check-0");
  card = page.locator("#teach-content > .card").filter({has: input});
  editor = card.locator(".work-editor");
  await editor.locator(".work-evidence-status").filter({hasText: "尚缺实际过程"}).waitFor();
  assert.equal(await input.inputValue(), "");
  await goto("?needs=1"); await visible("#view-needs");
  await page.locator("#needs-status").filter({hasText: /^当前显示/}).waitFor();
  await page.locator(".need-missing-work").filter({hasText: "尚缺实际过程"}).first().waitFor();
  await goto("?work=" + encodeURIComponent(resourceId)); await visible("#view-work");
  editor = page.locator("#view-work .work-editor");
  input = editor.locator(".work-field textarea").first();
  await saveText(editor, input, "合成测试：逐一指着三个圆片，记录点数顺序一、二、三。此处只核对提交行为，不代表真实学生表现。");
  plan = await apiRead("/api/plan");
  assert.equal(plan.learning_needs.find(need => need.need_id === originalNeed.need_id).status, "open", "A saved draft is not a submitted self-check");
  saved = await actionResponse(() => editor.getByRole("button", {name: "提交我的自查", exact: true}).click(), "/api/work", "POST",
    response => response.request().postDataJSON().status === "submitted");
  assert.equal(saved.submission_evidence.complete, true);
  plan = await apiRead("/api/plan");
  let need = plan.learning_needs.find(need => need.need_id === originalNeed.need_id);
  assert.equal(need.status, "resolved"); assert.equal(need.resolution.independent_evidence, false);
  await saveText(editor, input, "");
  saved = await actionResponse(() => editor.getByRole("button", {name: "提交我的自查", exact: true}).click(), "/api/work", "POST",
    response => response.request().postDataJSON().status === "submitted");
  assert.equal(saved.submission_evidence.complete, false);
  plan = await apiRead("/api/plan");
  need = plan.learning_needs.find(need => need.need_id === originalNeed.need_id);
  assert.equal(need.status, "open"); assert.equal(need.resolution, null);
  const revisions = (await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(resourceId))).revisions;
  assert.ok(revisions.some(row => row.payload.draft.response?.includes("逐一指着三个圆片")));
  assert.ok(revisions.some(row => !row.payload.draft.response && row.payload.status === "submitted"));
  await page.setViewportSize({width: 390, height: 844});
  await noHorizontalOverflow("missing_process_evidence_390");
  result.observations.submission_presence = {resource_id: resourceId, need_id: originalNeed.need_id,
    transition: ["open_missing_work", "open_draft", "resolved_bounded_self_check", "open_after_removal"],
    revisions: revisions.length, independent_evidence: false};
  claim("Empty process work with all ratings set to met remains open after HTTP persistence and browser reload.");
  claim("Actual work must be supplied and explicitly submitted; removing it reopens the same bounded need while retaining every original revision.");
};
