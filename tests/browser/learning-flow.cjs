/* Synthetic UI journeys. Answers come only from local authoring JSON. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ROOT = path.resolve(__dirname, "../..");
const args = {};
for (let i = 2; i < process.argv.length; i += 2) args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];
const output = path.resolve(args.output || path.join(ROOT, "build/browser-check", args.journey || "unknown"));
fs.mkdirSync(output, { recursive: true });
const result = { schema: 1, evidence_type: "synthetic_browser_test", journey: args.journey, ok: false,
  claims: [], fixture_source: "local_authoring_json", browser_context: "fresh", observations: {} };
const read = name => JSON.parse(fs.readFileSync(path.join(ROOT, name), "utf8"));
const answerBank = new Map(read("data/questions.json").map(question => [question.id, question]));
const authoredCatalog = read("data/catalog.json");
for (const filename of fs.readdirSync(path.join(ROOT, "data/learning_units")).filter(name => name.endsWith(".json"))) {
  for (const task of read("data/learning_units/" + filename).tasks) answerBank.set(task.id, task);
}
const UNIT = "independent_events";
let browser, context, page;
const scriptErrors = [], serverErrors = [], dialogs = [];
process.once("SIGTERM", async () => {
  result.ok = false;
  result.error = "Browser journey terminated by its timeout supervisor.";
  fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
  if (browser) await browser.close().catch(() => {});
  process.exit(143);
});

function claim(text) { result.claims.push(text); }
function apiPath(response) { return new URL(response.url()).pathname; }
function isResponse(response, pathname, method = "GET") { return apiPath(response) === pathname && response.request().method() === method; }
async function actionResponse(action, pathname, method = "POST", predicate = () => true) {
  const waiting = page.waitForResponse(response => isResponse(response, pathname, method) && predicate(response));
  await action();
  const response = await waiting;
  const body = await response.json();
  assert.equal(response.status(), 200, pathname + ": " + JSON.stringify(body));
  return body;
}
async function visible(selector) { await page.locator(selector).waitFor({ state: "visible" }); }
async function valueEquals(selector, value) {
  await page.waitForFunction(({ selector, value }) => document.querySelector(selector)?.value === value, { selector, value });
}
async function apiRead(pathname) {
  const response = await context.request.get(args["base-url"] + pathname);
  assert.equal(response.status(), 200, "Observation request failed: " + pathname);
  return response.json();
}
async function goto(query = "") {
  await page.goto(args["base-url"] + "/" + query, { waitUntil: "domcontentloaded" });
  await page.locator("#open-history:not([disabled])").waitFor();
  assert.ok(!(await page.locator("#stats").textContent()).includes("加载失败"));
}
async function saveText(editor, input, text) {
  const body = await actionResponse(() => input.fill(text), "/api/work", "POST", response => {
    const submitted = response.request().postDataJSON();
    return Object.values(submitted.draft || {}).includes(text);
  });
  await editor.locator(".work-status").filter({ hasText: /^(已保存。|自查记录已保存)/ }).waitFor();
  return body.work;
}
async function startFrom(button, phase) {
  const response = await actionResponse(() => button.click(), "/api/session");
  assert.equal(response.session.mode, phase);
  await visible("#view-quiz");
  return response;
}
async function fillAnswer(question, override) {
  const authored = answerBank.get(question.id);
  assert.ok(authored, "No local authored fixture for " + question.id);
  const contract = authoredCatalog.tasks[question.id]?.answer_spec || authored.answer_spec;
  if (contract) {
    result.observations.answer_contracts ||= [];
    const signature = contract.kind + ":" + (contract.representation || "any");
    if (!result.observations.answer_contracts.includes(signature)) result.observations.answer_contracts.push(signature);
  }
  if (question.diagram) {
    const image = page.locator("#quiz-diagram img");
    await image.waitFor();
    const source = await image.getAttribute("src");
    assert.match(source, /^\/api\/session\/[a-f0-9]{32}\/assets\//, "Task SVG must load from the frozen session asset");
    await page.waitForFunction(() => { const image = document.querySelector("#quiz-diagram img"); return image?.complete && image.naturalWidth > 0; });
    result.observations.snapshotted_diagrams ||= [];
    if (!result.observations.snapshotted_diagrams.includes(source)) result.observations.snapshotted_diagrams.push(source);
  }
  const answer = override ?? (question.type === "rubric"
    ? "合成浏览器验收：列出已知条件，说明使用的方法，并检查结论与单位。" : String(authored.answer));
  if (question.type === "choice") {
    const index = question.choices.findIndex(choice => choice.id === answer);
    assert.ok(index >= 0, "Authored choice ID is absent from the presented options");
    await page.locator("#choices button").nth(index).click();
  } else if (question.type === "rubric") await page.locator("#process-input").fill(answer);
  else await page.locator("#numeric-input").fill(answer);
}
async function submitAnswer(current, override) {
  await fillAnswer(current.item.question, override);
  const response = await actionResponse(() => page.locator("#submit").click(), "/api/answer");
  await visible("#feedback");
  if (current.session.feedback_policy === "deferred") {
    assert.deepEqual(response.feedback, { deferred: true });
    assert.equal((await page.locator("#fb-title").textContent()).trim(), "答案已记录");
    assert.equal((await page.locator("#fb-answer").textContent()).trim(), "");
    assert.equal((await page.locator("#fb-rubric").textContent()).trim(), "");
    if (!response.done) assert.equal(response.summary, undefined);
  } else if (current.item.question.type === "rubric") assert.equal(response.feedback.status, "needs_review");
  else assert.equal(response.feedback.status, "correct", "Authored fixture was not graded correctly");
  return response;
}
async function completeSession(current) {
  for (let count = 0; !current.done; count++) {
    assert.ok(count < 150, "Session exceeded the synthetic journey bound");
    current = await submitAnswer(current);
    await page.locator("#next").click();
  }
  await visible("#view-result");
  return current;
}
async function noHorizontalOverflow(label) {
  const measurement = await page.evaluate(() => ({ viewport: innerWidth,
    width: document.documentElement.scrollWidth,
    offenders: [...document.querySelectorAll("body *")].map(element => {
      const bounds = element.getBoundingClientRect();
      return { tag: element.tagName, id: element.id, className: String(element.className), left: bounds.left, right: bounds.right, width: bounds.width };
    }).filter(bounds => bounds.width > 0 && (bounds.right > innerWidth + 1 || bounds.left < -1)).slice(0, 12) }));
  result.observations.layout ||= [];
  result.observations.layout.push({ label, ...measurement });
  assert.ok(measurement.width <= measurement.viewport + 1, label + " overflows: " + JSON.stringify(measurement));
}

const journeys = {
  async self_check_autosave() {
    await goto("?teach=m5b.4.1");
    await visible("#view-teach");
    const input = page.locator("#self-check-1");
    let card = page.locator("#teach-content > .card").filter({ has: input });
    const draft = "合成验收：先求每个面的面积，再核对六个面与体积所用的单位。";
    const saved = await saveText(card.locator(".work-editor"), input, draft);
    await page.reload({ waitUntil: "domcontentloaded" });
    await valueEquals("#self-check-1", draft);
    card = page.locator("#teach-content > .card").filter({ has: page.locator("#self-check-1") });
    await actionResponse(() => card.locator(".self-check-answer > summary").click(), "/api/exposure");
    const exported = await apiRead("/api/export");
    assert.ok(exported.events.some(event => event.event_type === "reference_opened" && event.resource_id === "topic:m5b.4.1"));
    const work = exported.works.find(work => work.resource_id === saved.resource_id);
    assert.ok(work.revisions.some(revision => revision.payload.draft.response === draft));
    result.observations.resource_id = saved.resource_id;
    claim("Self-check text autosaves and restores after a page reload.");
    claim("Opening the reference records reference exposure without asserting understanding.");
  },

  async rubric_revision() {
    await goto("?unit=" + UNIT);
    let current = await startFrom(page.locator("#unit-actions").getByRole("button", { name: "迁移任务", exact: true }), "transfer");
    while (current.item.question.type !== "rubric") {
      current = await submitAnswer(current);
      assert.equal(current.done, false, "Fixture needs an open-ended task");
      await page.locator("#next").click();
    }
    const sid = current.session.id, qid = current.item.question.id;
    const draft = "合成验收草稿：先区分独立事件与互斥事件，列出概率关系并补充理由。";
    await saveText(page.locator("#process-answer .work-editor"), page.locator("#process-input"), draft);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator("#open-history:not([disabled])").waitFor();
    await page.locator("#restart").click();
    current = await actionResponse(() => page.locator("#resume").click(), "/api/session/" + sid, "GET");
    await valueEquals("#process-input", draft);
    assert.equal(current.item.question.id, qid);
    current = await submitAnswer(current, draft);
    await page.locator("#next").click();
    if (!current.done) current = await completeSession(current);
    await visible("#view-result");
    const entry = page.locator("#answer-review > details").filter({ hasText: "过程已记录，可按量规自查" }).first();
    await entry.locator(":scope > summary").click();
    const editor = entry.locator(".work-editor");
    await editor.locator(".self-assessment > summary").click();
    await editor.locator(".self-assessment select").first().selectOption("met");
    const saved = await actionResponse(() => editor.getByRole("button", { name: "提交我的自查", exact: true }).click(), "/api/work", "POST",
      response => response.request().postDataJSON().status === "submitted");
    await editor.locator(".work-status").filter({ hasText: "自查记录已保存" }).waitFor();
    const exported = await apiRead("/api/export");
    const session = exported.sessions.find(session => session.id === sid);
    assert.equal(session.answers.find(answer => answer.question_id === qid).status, "needs_review");
    assert.equal(saved.work.payload.self_assessment[0].status, "met");
    result.observations.session_id = sid;
    result.observations.retained_grade = "needs_review";
    claim("A rubric draft survives reload and resumes through the visible Continue control.");
    claim("Learner self-assessment creates a work revision while the original grade remains needs_review.");
  },

  async unit_phases() {
    const chainUnit = "linear_function_representations";
    await goto("?unit=" + chainUnit);
    await page.screenshot({ path: path.join(output, "desktop-unit.png"), fullPage: true });
    let current = await startFrom(page.locator("#unit-actions").getByRole("button", { name: "练习", exact: true }), "practice");
    const practice = await completeSession(current);
    current = await startFrom(page.locator("#result-actions").getByRole("button", { name: "迁移任务", exact: true }), "transfer");
    assert.equal(current.session.context.previous_session_id, practice.session.id);
    assert.equal(current.session.feedback_policy, "deferred");
    const transfer = await completeSession(current);
    current = await startFrom(page.locator("#result-actions").getByRole("button", { name: "复测题（建议稍后）", exact: true }), "delayed");
    assert.equal(current.session.context.previous_session_id, transfer.session.id);
    assert.equal(current.session.context.delay_window_met, false);
    assert.ok(current.session.context.actual_delay_seconds >= 0 && current.session.context.actual_delay_seconds < 86400);
    const delayed = await completeSession(current);
    assert.ok((await page.locator("#result-context").textContent()).includes("尚不计作延迟复测证据"));
    assert.equal(delayed.summary.context.independent_evidence, false);
    result.observations.phases = [practice, transfer, delayed].map(item => ({ mode: item.session.mode,
      answered_count: item.session.answered_count, delay_window_met: item.session.context.delay_window_met }));
    const authored = read("data/learning_units/" + chainUnit + ".json");
    assert.equal(result.observations.phases.reduce((sum, phase) => sum + phase.answered_count, 0), authored.tasks.length);
    assert.ok((result.observations.snapshotted_diagrams || []).length > 0, "The chain journey must exercise session SVG assets");
    assert.ok(result.observations.answer_contracts.length >= 3, "The chain journey must exercise multiple answer contracts");
    result.observations.unit_id = chainUnit;
    claim("Practice, transfer and retest are reachable through the visible learning flow.");
    claim("Transfer/retest withhold per-item reference answers until the group is complete.");
    claim("An immediate retest is labelled as insufficiently delayed, not independent retention evidence.");
    claim("Every authored task in the new chain unit is completed, including frozen SVGs and multiple answer contracts.");
    await page.locator("#again").click();
    await visible("#view-home");
    await page.screenshot({ path: path.join(output, "desktop-home.png") });
    await page.locator("#open-history").click();
    await visible("#view-history");
    await goto("?project=junior_track_design");
    await page.locator("#project-content .work-field textarea").first().waitFor();
    await page.screenshot({ path: path.join(output, "desktop-project.png"), fullPage: true });
  },

  async project_history() {
    await goto("?project=junior_track_design");
    await visible("#view-project");
    let editor = page.locator("#project-content .project-work");
    const firstText = "合成验收第一稿：画出直道和半圆，记录测量条件。";
    const secondText = "合成验收修订稿：补充单位，并核对周长约束与计算结果。";
    const first = await saveText(editor, editor.locator(".work-field textarea").first(), firstText);
    const second = await saveText(editor, editor.locator(".work-field textarea").first(), secondText);
    assert.ok(second.revision > first.revision);
    assert.equal(second.resource_version, first.resource_version);
    await actionResponse(() => editor.locator(".work-revisions > summary").click(), "/api/work/revisions", "GET");
    const historic = editor.locator(".work-revisions details").filter({ has: page.getByText("第 " + first.revision + " 版", { exact: false }) }).first();
    await historic.locator(":scope > summary").click();
    assert.ok((await historic.textContent()).includes(firstText));
    await page.reload({ waitUntil: "domcontentloaded" });
    editor = page.locator("#project-content .project-work");
    await page.waitForFunction(text => document.querySelector("#project-content .work-field textarea")?.value === text, secondText);
    await page.locator("#open-history").click();
    await visible("#view-history");
    const waiting = page.waitForEvent("download");
    await page.locator("#export-history").click();
    const download = await waiting;
    const filename = path.join(output, "synthetic-learning-records.json");
    await download.saveAs(filename);
    const exported = JSON.parse(fs.readFileSync(filename, "utf8"));
    assert.equal(exported.schema, 2);
    const work = exported.works.find(work => work.resource_id === first.resource_id);
    assert.ok(work.revisions.length >= 2);
    assert.ok(work.revisions.every(revision => revision.resource_version === first.resource_version));
    assert.ok(work.revisions.some(revision => Object.values(revision.payload.draft).includes(firstText)));
    assert.ok(work.revisions.some(revision => Object.values(revision.payload.draft).includes(secondText)));
    result.observations.revisions = work.revisions.length;
    result.observations.frozen_content_version = first.resource_version;
    claim("Project revisions retain earlier text and a fixed content version.");
    claim("Reload restores the latest draft; the visible export button downloads complete work history.");
  },

  async mobile_stage() {
    await goto();
    await actionResponse(() => page.locator("#stage-select").selectOption("primary"), "/api/plan", "GET",
      response => new URL(response.url()).searchParams.get("stage") === "primary");
    assert.ok((await page.locator("#learning-units .eyebrow").allTextContents()).every(text => text === "小学"));
    assert.ok((await page.locator("#learning-projects .eyebrow").allTextContents()).every(text => text === "小学"));
    assert.ok((await page.locator("#chapters .volume-title").allTextContents()).every(text => text.includes("小学")));
    await noHorizontalOverflow("primary_home_390");
    await page.reload({ waitUntil: "domcontentloaded" });
    await valueEquals("#stage-select", "primary");
    await actionResponse(() => page.locator("#stage-select").selectOption("senior"), "/api/plan", "GET",
      response => new URL(response.url()).searchParams.get("stage") === "senior");
    assert.ok((await page.locator("#learning-units .eyebrow").allTextContents()).every(text => text === "高中"));
    assert.equal(await page.locator("#path-section").isVisible(), false);
    await noHorizontalOverflow("senior_home_390");
    await goto("?teach=m5b.4.1");
    await page.locator("#self-check-1").waitFor();
    await noHorizontalOverflow("self_check_390");
    await goto("?unit=linear_function_representations");
    await visible("#view-unit");
    await noHorizontalOverflow("linear_unit_390");
    await goto("?project=junior_track_design");
    await page.locator("#project-content .work-field textarea").first().waitFor();
    await noHorizontalOverflow("project_390");
    claim("Home cards and textbook navigation follow the selected school stage and preserve the selection.");
    claim("Home, teaching, unit and project views have no viewport-level horizontal overflow at 390px.");
  },

  async pool_exhaustion() {
    const waiting = page.waitForResponse(response => isResponse(response, "/api/session", "POST"));
    await goto("?target=m1a.1.1");
    let response = await waiting;
    assert.equal(response.status(), 200);
    let current = await response.json();
    let checks = 0;
    for (;;) {
      current = await completeSession(current);
      checks++;
      const check = page.locator("#result-actions").getByRole("button", { name: "未呈现题检查", exact: true });
      if (await check.isDisabled()) break;
      assert.ok(checks < 12, "The selected finite item pool did not exhaust");
      current = await startFrom(check, "diagnose");
    }
    assert.ok((await page.locator("#result-actions").textContent()).includes("复习练习仍可继续"));
    const exhausted = page.waitForResponse(response => isResponse(response, "/api/session", "POST") && response.status() === 409);
    await goto("?target=m1a.1.1");
    const unavailable = await (await exhausted).json();
    assert.equal(unavailable.code, "no_fresh_tasks");
    await visible("#action-notice");
    const practice = await startFrom(page.locator("#action-notice").getByRole("button", { name: "继续复习练习", exact: true }), "practice");
    const answered = await submitAnswer(practice);
    assert.equal(answered.feedback.status, "correct");
    result.observations.completed_checks_before_exhaustion = checks;
    claim("An exhausted check pool provides a visible, working practice alternative.");
  },
};

(async () => {
  try {
    assert.ok(journeys[args.journey], "Choose a supported --journey");
    const modulePath = process.env.CNKG_PLAYWRIGHT_MODULE || "playwright";
    let playwright;
    try { playwright = require(modulePath); }
    catch (error) { throw new Error("Playwright is unavailable. Run npm ci && npx playwright install chromium, or set CNKG_PLAYWRIGHT_MODULE. " + error.message); }
    result.node_version = process.version;
    result.playwright_version = JSON.parse(fs.readFileSync(path.join(path.dirname(require.resolve(modulePath)), "package.json"), "utf8")).version;
    const launch = { headless: true };
    if (process.env.CNKG_CHROMIUM_EXECUTABLE) launch.executablePath = process.env.CNKG_CHROMIUM_EXECUTABLE;
    browser = await playwright.chromium.launch(launch);
    result.browser_version = browser.version();
    context = await browser.newContext({ viewport: args.journey === "mobile_stage" ? { width: 390, height: 844 } : { width: 1280, height: 900 },
      locale: "zh-CN", timezoneId: "Asia/Shanghai", colorScheme: "light", reducedMotion: "reduce", acceptDownloads: true });
    await context.route("**/*", route => {
      const url = new URL(route.request().url());
      return url.origin === new URL(args["base-url"]).origin || ["data:", "blob:"].includes(url.protocol) ? route.continue() : route.abort();
    });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
    page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.setDefaultNavigationTimeout(30000);
    page.on("pageerror", error => scriptErrors.push(error.message));
    page.on("response", response => { if (response.status() >= 500) serverErrors.push({ url: response.url(), status: response.status() }); });
    page.on("dialog", async dialog => { dialogs.push({ type: dialog.type(), message: dialog.message() }); await dialog.accept(); });
    await journeys[args.journey]();
    assert.deepEqual(scriptErrors, [], "Unhandled browser script errors");
    assert.deepEqual(serverErrors, [], "Server errors during the journey");
    assert.deepEqual(dialogs, [], "Unexpected alert or unsaved-change dialog");
    result.ok = true;
    await page.screenshot({ path: path.join(output, "final.png"), fullPage: true });
    await context.tracing.stop();
  } catch (error) {
    result.error = error.stack || String(error);
    console.error(result.error);
    if (page) await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true }).catch(() => {});
    if (context) await context.tracing.stop({ path: path.join(output, "trace.zip") }).catch(() => {});
  } finally {
    result.script_errors = scriptErrors;
    result.server_errors = serverErrors;
    result.dialogs = dialogs;
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify({ journey: result.journey, ok: result.ok, evidence_type: result.evidence_type }));
    process.exitCode = result.ok ? 0 : 1;
  }
})();
