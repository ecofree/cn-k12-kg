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
  const [response] = await Promise.all([
    page.waitForResponse(response => isResponse(response, pathname, method) && predicate(response)), action(),
  ]);
  const body = await response.json();
  assert.equal(response.status(), 200, pathname + ": " + JSON.stringify(body));
  return body;
}
async function navigationAction(action, pathname) {
  // Recovery intentionally replaces the page after its own fetch has succeeded.
  // Chromium may discard that response body during navigation; verify the status
  // here and the restored assets through the new page's actual API below.
  const [response] = await Promise.all([
    page.waitForResponse(response => isResponse(response, pathname, "POST")), action(),
  ]);
  assert.equal(response.status(), 200, pathname);
  await visible("#view-home");
  await page.locator("#open-history:not([disabled])").waitFor();
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
    assert.equal(await page.locator("#quiz-diagram .diagram-enlarge").getAttribute("href"), source);
    await page.waitForFunction(() => { const image = document.querySelector("#quiz-diagram img"); return image?.complete && image.naturalWidth > 0; });
    result.observations.snapshotted_diagrams ||= [];
    if (!result.observations.snapshotted_diagrams.includes(source)) result.observations.snapshotted_diagrams.push(source);
  }
  const answer = override ?? (question.type === "rubric"
    ? "合成浏览器验收：列出已知条件，说明使用的方法，并检查结论与单位。" : String(authored.answer));
  if (question.type === "choice") {
    const index = question.choices.findIndex(choice => choice.id === answer);
    assert.ok(index >= 0, "Authored choice ID is absent from the presented options");
    await page.locator('#choices input[type="radio"]').nth(index).check();
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
  async changed_content_preview() {
    const review = read("build/content-review.json");
    assert.ok(review.preview_tasks.length, "A content review must nominate actual pages");
    result.observations.content_fingerprint = review.content_fingerprint;
    result.observations.previews = [];
    const captured = new Set();
    for (const task of review.preview_tasks) {
      await goto(task.path.slice(1));
      const query = new URLSearchParams(new URL(page.url()).search), kind = query.has("teach") ? "teach" : query.has("unit") ? "unit" : query.has("project") ? "project" : query.has("scope") ? "scope" : query.has("needs") ? "needs" : query.has("profile") ? "profile" : "history";
      await visible("#view-" + kind);
      const main = page.locator("#view-" + kind);
      const open = main.locator(kind === "teach" ? ".self-check-answer > summary" : ".worked-example > summary");
      if (await open.count()) await open.first().click();
      await page.evaluate(async () => { await document.fonts.ready; await Promise.all([...document.querySelectorAll(".view:not([hidden]) img")].map(image => image.complete ? Promise.resolve() : new Promise(resolve => { image.onload = image.onerror = resolve; }))); });
      const reading = await main.evaluate(element => ({
        title: element.querySelector("h2")?.textContent,
        mathErrors: [...element.querySelectorAll(".katex-error")].map(e => e.textContent),
        images: [...element.querySelectorAll("img")].map(e => ({ src: e.getAttribute("src"), loaded: e.complete && e.naturalWidth > 0 })),
        emptyButtons: [...element.querySelectorAll("button")].filter(e => !e.textContent.trim() && !e.getAttribute("aria-label")).length,
        viewport: innerWidth, width: document.documentElement.scrollWidth,
        textLength: element.innerText.length,
      }));
      assert.ok(reading.title && reading.textLength > 30, task.path + " did not render its actual material");
      assert.equal(reading.mathErrors.length, 0, task.path + " math errors: " + JSON.stringify(reading.mathErrors));
      assert.ok(reading.images.every(image => image.loaded), task.path + " has an unloaded diagram");
      assert.equal(reading.emptyButtons, 0, task.path + " has an unnamed control");
      assert.ok(reading.width <= reading.viewport + 1, task.path + " overflows on desktop");
      result.observations.previews.push({ path: task.path, material_id: task.material_id, reason: task.reason, ...reading });
      const screenshot = { teach: "primary-entry.png", unit: "desktop-unit.png", project: "desktop-project.png", scope: "desktop-home.png" }[kind];
      if (screenshot && !captured.has(kind)) { await page.screenshot({ path: path.join(output, screenshot), fullPage: true }); captured.add(kind); }
    }
    result.observations.preview_count = result.observations.previews.length;
    claim("Every directly affected or declared dependent material in the change-impact checklist was opened in the actual application.");
    claim("Rendered titles, mathematical markup, image loading, named controls and desktop overflow were checked; representative screenshots were retained.");
    claim("These previews supplement recorded semantic checks and interaction journeys; they are not teacher approval or student-effect evidence.");
  },

  async learning_needs_support() {
    const uid = "data_reading_and_mean", unit = read(`data/learning_units/${uid}.json`);
    await goto("?unit=" + uid);
    let current = await startFrom(page.locator("#unit-actions").getByRole("button", { name: "练习", exact: true }), "practice");
    const origin = current.session.id;
    await fillAnswer(current.item.question, "999");
    current = await actionResponse(() => page.locator("#submit").click(), "/api/answer");
    await page.locator("#next").click();
    current = await completeSession(current);
    let plan = await apiRead("/api/plan");
    const originalNeed = plan.learning_needs.find(n => n.source_ref.session_id === origin && n.kind === "answer_support");
    const processNeed = plan.learning_needs.find(n => n.source_ref.session_id === origin && n.kind === "process_revision");
    assert.ok(originalNeed && processNeed, "Both the bounded wrong answer and unreviewed process must survive completion");
    await goto("?needs=1");
    const card = page.locator("#needs-list > article").filter({ hasText: "作答后需进一步辨别" }).first();
    await card.locator("summary").filter({ hasText: "暂缓这件事" }).click();
    await card.locator("textarea").fill("合成功能验证：保留问题，暂时处理其他任务。");
    await actionResponse(() => card.getByRole("button", { name: "保存原因与状态" }).click(), "/api/learning-needs/state");
    await page.locator("#need-filter").selectOption("deferred");
    const deferred = page.locator("#needs-list > article").first();
    await deferred.locator("summary").filter({ hasText: "重新列入计划" }).click();
    await deferred.locator("textarea").fill("合成功能验证：现在继续辨别。");
    await actionResponse(() => deferred.getByRole("button", { name: "保存原因与状态" }).click(), "/api/learning-needs/state");
    await page.locator("#need-filter").selectOption("open");
    const support = await actionResponse(() => page.locator("#needs-list > article").filter({ hasText: "作答后需进一步辨别" }).first().getByRole("button", { name: "辨别与补学", exact: true }).click(), "/api/support/start");
    await visible("#view-support");
    assert.equal(support.session.kind, "support");
    assert.ok(!("answer" in support.item));
    assert.ok(support.item.choices.every(c => !("feedback" in c)));
    const authored = unit.instructional_support.supports.find(s => s.id === support.support.id);
    async function answerSupport(response, value) {
      if (response.item.type === "choice") await page.locator(`#view-support input[type=radio][value="${value}"]`).check();
      else await page.locator("#support-answer").fill(String(value));
      return actionResponse(() => page.getByRole("button", { name: "记录这一步", exact: true }).click(), "/api/support/answer");
    }
    let guided = await answerSupport(support, authored.diagnostic.choices.find(c => c.id !== authored.diagnostic.answer).id);
    assert.equal(guided.support.step, 1, "A submitted valid probe must advance to guided practice: " + JSON.stringify(guided.feedback));
    await page.getByRole("heading", { name: "按需要打开提示", exact: true }).waitFor();
    await actionResponse(() => page.getByRole("button", { name: "提示2 · 分步引导", exact: true }).click(), "/api/support/hint");
    assert.equal((await apiRead("/api/plan")).learning_needs.find(n => n.need_id === originalNeed.need_id).status, "open");
    guided = await answerSupport(guided, authored.guided_task.answer);
    assert.equal((await apiRead("/api/plan")).learning_needs.find(n => n.need_id === originalNeed.need_id).status, "open");
    assert.notEqual(guided.item.type, "rubric", "This bounded numeric need has an objective follow-up candidate");
    const finished = await answerSupport(guided, answerBank.get(guided.item.id).answer);
    assert.ok(finished.done);
    plan = await apiRead("/api/plan");
    const closed = plan.learning_needs.find(n => n.need_id === originalNeed.need_id);
    assert.equal(closed.status, "resolved");
    assert.equal(closed.resolution.kind, "supported_confirmation");
    assert.equal(closed.resolution.independent_evidence, false);
    assert.equal(plan.learning_needs.find(n => n.need_id === processNeed.need_id).status, "open");
    assert.ok((await apiRead("/api/session/" + origin)).summary.answers.some(a => a.status === "wrong"));
    await goto();
    await page.locator("#stage-select").selectOption("senior");
    await page.waitForFunction(() => document.querySelector("#need-counts").textContent.includes("1 项待处理"));
    await page.locator("#open-needs").click(); await visible("#view-needs");
    assert.ok((await page.locator("#needs-list").textContent()).includes("过程待自查或修订"));
    result.observations.need_id = originalNeed.need_id;
    result.observations.support_session_id = support.session.id;
    claim("Wrong-answer and process needs survive completion, explicit deferral and stage changes.");
    claim("Intermediate help does not resolve the need; final confirmation records help and leaves the original wrong answer intact.");
  },

  async profile_backup_recovery() {
    await goto("?teach=m1a.1.1");
    const input = page.locator("#self-check-0"), editor = page.locator("#teach-content > .card").filter({ has: input });
    const draft1 = "合成恢复验证：我摆了5颗计数片，一颗一颗地数。", draft2 = "合成恢复验证修订：改变摆法仍是5颗，因为没有添减。";
    const first = await saveText(editor, input, draft1);
    await saveText(editor, input, draft2);
    await page.locator("#open-profile").click(); await visible("#view-profile");
    const enabled = await actionResponse(() => page.getByRole("button", { name: "创建恢复码", exact: true }).click(), "/api/profile/enable");
    let code = await page.locator("#new-recovery-code").inputValue();
    assert.ok(code && code === enabled.recovery_code);
    assert.ok(!("cookie_token" in enabled) && !("owner_id" in enabled.profile));
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByText("更换恢复码", { exact: true }).click();
    for (let i = 0; i < 2; i++) {
      const rotated = await actionResponse(() => page.getByRole("button", { name: "生成新恢复码", exact: true }).click(), "/api/profile/rotate");
      const latest = await page.locator("#new-recovery-code").inputValue();
      assert.notEqual(latest, code); assert.equal(latest, rotated.recovery_code);
      assert.equal(await page.locator(".recovery-code").count(), 1);
      code = latest;
    }
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "下载加密备份", exact: true }).click();
    const downloaded = await downloading, filename = path.join(output, "synthetic-encrypted-backup.json");
    await downloaded.saveAs(filename);
    const encrypted = fs.readFileSync(filename, "utf8");
    assert.ok(!encrypted.includes(draft1) && !encrypted.includes(draft2));
    await context.clearCookies();
    await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
    await goto("?profile=1");
    assert.equal((await apiRead("/api/works")).total, 0);
    await page.locator("#recover-code").fill(code);
    await navigationAction(() => page.getByRole("button", { name: "凭恢复码找回", exact: true }).click(), "/api/profile/recover");
    const recovered = await apiRead("/api/work?resource_id=" + encodeURIComponent(first.resource_id));
    assert.equal(recovered.work.payload.draft.response, draft2);
    assert.equal((await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(first.resource_id))).revisions.length, 2);
    const originalBase = args["base-url"];
    args["base-url"] = args["restore-base-url"];
    assert.ok(args["base-url"] && args["base-url"] !== originalBase);
    await context.close();
    context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "zh-CN", acceptDownloads: true });
    page = await context.newPage(); page.on("pageerror", error => scriptErrors.push(error.message));
    await goto("?profile=1");
    assert.equal((await apiRead("/api/works")).total, 0);
    await page.locator("#recover-code").fill(code);
    await page.locator("#restore-backup").setInputFiles(filename);
    await navigationAction(() => page.getByRole("button", { name: "导入备份并恢复", exact: true }).click(), "/api/profile/restore");
    const imported = await apiRead("/api/work?resource_id=" + encodeURIComponent(first.resource_id));
    assert.deepEqual(imported.work.payload, recovered.work.payload);
    const importedRevisions = (await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(first.resource_id))).revisions;
    assert.equal(importedRevisions.length, 2);
    await goto("?teach=m1a.1.1"); await valueEquals("#self-check-0", draft2);
    claim("Creating a recovery code preserves the current anonymous learning records and returns no cookie credential in JSON.");
    claim("Clearing cookies and site data is recoverable using the code; an encrypted backup imports through the UI into a separate fresh SQLite installation.");
    claim("Work identity, original snapshot and both revisions survive; the downloaded envelope does not expose the learner's text.");
    result.observations.restored_revisions = importedRevisions.length;
  },

  async scope_and_manipulatives() {
    await goto("?scope=1"); await visible("#view-scope");
    const report = await apiRead("/api/quality-scope");
    assert.equal(report.summary.core_topics, 342); assert.equal(report.summary.foundation_topics, 135);
    assert.equal(report.summary.incomplete, 0);
    await page.locator("#scope-role").selectOption("foundation_support");
    await page.locator("#scope-stage").selectOption("primary");
    await page.locator("#scope-search").fill("算盘");
    assert.ok(await page.locator("#scope-goals article").count());
    await page.locator("#scope-goals article").first().getByRole("button", { name: "打开学习材料与任务" }).click();
    await visible("#view-teach"); assert.equal(await page.locator(".self-check-prompt").count(), 2);
    await page.setViewportSize({ width: 390, height: 844 });
    for (const [uid, control, action] of [
      ["ten_frame_place_value", "a", "End"], ["fraction_equivalence", "factor", "3"],
      ["data_reading_and_mean", "baseline", "5"], ["linear_function_representations", "slope", "Home"]]) {
      let releaseRead, readCaptured, readContinued;
      const readReady = new Promise(resolve => { readCaptured = resolve; });
      const readFinished = new Promise(resolve => { readContinued = resolve; });
      const readPattern = "**/api/manipulative?unit_id=" + uid;
      if (uid === "ten_frame_place_value") await page.route(readPattern, async route => {
        await new Promise(resolve => { releaseRead = resolve; readCaptured(); });
        await route.continue(); readContinued();
      });
      await goto("?unit=" + uid); await visible(".manipulative");
      const field = page.locator(`#manip-${uid}-${control}`);
      if (uid === "ten_frame_place_value") {
        await readReady;
        assert.ok(await field.isDisabled());
        assert.ok(await page.locator(`#manip-explanation-${uid}`).isDisabled());
        releaseRead(); await readFinished; await page.unroute(readPattern);
      }
      await page.waitForFunction(id => document.getElementById(id)?.disabled === false, `manip-${uid}-${control}`);
      if (action === "Home" || action === "End") { await field.focus(); await field.press(action); }
      else await field.selectOption(action);
      if (uid === "ten_frame_place_value") {
        const second = page.locator("#manip-ten_frame_place_value-b");
        await second.focus(); await second.press("End");
        assert.ok((await page.locator(".manipulative").textContent()).includes("合成2个十和0个一"));
      }
      const reflection = `合成操作验证：${uid}，先预测，再记录表征中的实际变化。`;
      await page.locator(`#manip-explanation-${uid}`).fill(reflection);
      if (uid === "ten_frame_place_value") {
        let releaseSave, saveCaptured;
        const saveReady = new Promise(resolve => { saveCaptured = resolve; });
        await page.route("**/api/manipulative", async route => {
          await new Promise(resolve => { releaseSave = resolve; saveCaptured(); }); await route.continue();
        });
        const saving = actionResponse(() => page.getByRole("button", { name: "保存操作和解释", exact: true }).click(), "/api/manipulative");
        await saveReady;
        assert.ok(await field.isDisabled());
        assert.ok(await page.locator(`#manip-explanation-${uid}`).isDisabled());
        releaseSave(); await saving; await page.unroute("**/api/manipulative");
      } else await actionResponse(() => page.getByRole("button", { name: "保存操作和解释", exact: true }).click(), "/api/manipulative");
      const state = (await apiRead("/api/manipulative?unit_id=" + uid)).saved.payload.state;
      await page.reload({ waitUntil: "domcontentloaded" });
      await valueEquals(`#manip-explanation-${uid}`, reflection);
      assert.deepEqual((await apiRead("/api/manipulative?unit_id=" + uid)).saved.payload.state, state);
      await noHorizontalOverflow(uid + "_manipulative_390");
    }
    await page.screenshot({ path: path.join(output, "primary-entry.png"), fullPage: true });
    const exported = await apiRead("/api/export");
    assert.equal(exported.events.filter(e => e.event_type === "manipulation_saved").length, 4);
    claim("The complete course inventory retains 342 core and 135 foundation topics with explicit materials for every registered goal.");
    claim("Four mathematical representations support keyboard controls, mobile layout and saved action/reflection recovery without assigning mastery.");
  },

  async navigation_recovery() {
    let creates = 0;
    const countSession = request => { if (request.method() === "POST" && new URL(request.url()).pathname === "/api/session") creates++; };
    page.on("request", countSession);
    await goto("?target=m7a.5.2");
    await visible("#view-quiz");
    await page.waitForFunction(() => new URLSearchParams(location.search).has("session"));
    const sid = new URL(page.url()).searchParams.get("session"), first = await apiRead("/api/session/" + sid);
    await page.reload({ waitUntil: "domcontentloaded" });
    await visible("#view-quiz");
    assert.equal(new URL(page.url()).searchParams.get("session"), sid);
    assert.equal(creates, 1, "Refresh must not create another diagnostic session");
    assert.equal((await apiRead("/api/session/" + sid)).item.question.id, first.item.question.id);
    await page.locator("#restart").click(); await visible("#view-home");
    await page.goBack(); await visible("#view-quiz");
    assert.equal(new URL(page.url()).searchParams.get("session"), sid);
    await page.goForward(); await visible("#view-home");
    assert.equal(creates, 1, "Back/forward must reuse the concrete saved session");

    const unavailableRead = route => {
      const url = new URL(route.request().url());
      return url.searchParams.get("criterion_index") === "1" ? route.abort("failed") : route.continue();
    };
    await page.route("**/api/work?**", unavailableRead);
    await goto("?teach=m5b.4.1"); await visible("#view-teach");
    let input = page.locator("#self-check-1"), card = page.locator("#teach-content > .card").filter({ has: input });
    await card.getByRole("button", { name: "重试恢复记录", exact: true }).waitFor();
    assert.equal(await input.isDisabled(), true, "Failed draft recovery must not enable an unsaved editor");
    await page.unroute("**/api/work?**", unavailableRead);
    await actionResponse(() => card.getByRole("button", { name: "重试恢复记录", exact: true }).click(), "/api/work", "GET");
    await input.waitFor();
    await page.waitForFunction(() => !document.querySelector("#self-check-1").disabled);
    const initialText = "合成验收：恢复记录后填写，核对面积和体积的单位。";
    const original = await saveText(card.locator(".work-editor"), input, initialText);

    // Create a real intervening revision in the same isolated owner, like another tab.
    const fresh = await apiRead("/api/work?resource_id=" + encodeURIComponent(original.resource_id));
    const competingText = "合成验收另一处修订：逐个检查六个面的面积。";
    const competing = await context.request.post(args["base-url"] + "/api/work", { data: {
      resource_id: original.resource_id, expected_revision: fresh.work.revision,
      request_id: "browser-conflict-" + Date.now(), draft: { response: competingText },
      reflection: "", self_assessment: [], status: "draft",
    } });
    assert.equal(competing.status(), 200);
    const currentText = "合成验收本页修订：补充展开图，并说明每个面积项。";
    const conflictResponse = page.waitForResponse(response => isResponse(response, "/api/work", "POST") && response.status() === 409);
    await input.fill(currentText); await conflictResponse;
    const conflictControl = card.getByRole("button", { name: "保留两版并保存本页内容", exact: true });
    await conflictControl.waitFor();
    await page.locator("#restart").click();
    await visible("#save-notice"); assert.equal(await page.locator("#view-teach").isVisible(), true);
    assert.equal(await input.inputValue(), currentText);
    await actionResponse(() => conflictControl.click(), "/api/work", "POST");
    await card.locator(".work-status").filter({ hasText: "已保存。" }).waitFor();
    const revisions = await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(original.resource_id));
    assert.ok(revisions.revisions.some(row => row.payload.draft.response === competingText));
    assert.ok(revisions.revisions.some(row => row.payload.draft.response === currentText));

    const topicTitle = (await page.locator("#teach-title").textContent()).trim();
    await page.locator("#restart").click(); await visible("#view-home");
    await page.locator("#search").fill(topicTitle);
    await page.locator(".row").filter({ has: page.getByText(topicTitle, { exact: true }) }).getByRole("button", { name: "讲解", exact: true }).click();
    await visible("#view-teach"); await valueEquals("#self-check-1", currentText);
    input = page.locator("#self-check-1"); card = page.locator("#teach-content > .card").filter({ has: input });
    const renderedUrl = page.url(), routeIndex = await page.evaluate(() => history.state.cnkgIndex);
    const failedWrite = route => route.request().method() === "POST" ? route.abort("failed") : route.continue();
    await page.route("**/api/work", failedWrite);
    const offlineText = "合成验收连接中断草稿：文字必须留在可访问的输入框。";
    await input.fill(offlineText); await card.locator(".work-status").filter({ hasText: "尚未保存" }).waitFor();
    await page.locator("#restart").click(); await visible("#save-notice");
    assert.equal(await page.locator("#view-teach").isVisible(), true); assert.equal(page.url(), renderedUrl);
    await page.goBack();
    await page.waitForFunction(({ url, index }) => location.href === url && history.state?.cnkgIndex === index,
      { url: renderedUrl, index: routeIndex });
    assert.equal(await input.inputValue(), offlineText);
    await page.unroute("**/api/work", failedWrite);
    await actionResponse(() => page.locator("#retry-save").click(), "/api/work", "POST");
    await card.locator(".work-status").filter({ hasText: "已保存。" }).waitFor();
    await page.locator("#restart").click(); await visible("#view-home");
    await page.goBack(); await visible("#view-teach"); await valueEquals("#self-check-1", offlineText);
    page.off("request", countSession);
    result.observations.session_id = sid;
    result.observations.session_creates = creates;
    result.observations.injected_failures = ["draft_read_network_failure", "stale_revision_conflict", "draft_write_network_failure"];
    claim("A diagnostic launch link becomes a saved-session URL; refresh/back/forward never create a duplicate session.");
    claim("A failed draft load disables input and offers a retry that restores the saved editor.");
    claim("Conflicts and failed writes keep the original editor reachable and block page/back navigation until resolved.");
    claim("The conflicting revision, current revision and retried offline draft all remain in the learner history.");
  },

  async keyboard_accessibility() {
    await goto("?unit=common_factors_multiples"); await visible("#view-unit");
    await page.waitForFunction(() => document.activeElement?.id === "unit-title");
    const practice = page.locator("#unit-actions").getByRole("button", { name: "练习", exact: true });
    for (let tabs = 0; !(await practice.evaluate(element => element === document.activeElement)); tabs++) {
      assert.ok(tabs < 40, "Practice must be reachable with a bounded keyboard path"); await page.keyboard.press("Tab");
    }
    const current = await actionResponse(() => page.keyboard.press("Enter"), "/api/session");
    assert.equal(current.item.question.type, "choice");
    await page.waitForFunction(() => document.activeElement?.id === "quiz-prompt");
    const group = page.getByRole("group", { name: "选择一个答案", exact: true });
    assert.equal(await group.count(), 1);
    const radios = group.getByRole("radio"); assert.equal(await radios.count(), current.item.question.choices.length);
    for (let index = 0; index < await radios.count(); index++) assert.ok((await radios.nth(index).getAttribute("value")));
    await page.keyboard.press("Tab"); await page.keyboard.press("Space");
    assert.equal(await radios.first().isChecked(), true);
    await page.keyboard.press("ArrowRight"); assert.equal(await radios.nth(1).isChecked(), true);
    assert.equal(await radios.first().isChecked(), false);
    await page.screenshot({ path: path.join(output, "desktop-unit.png"), fullPage: true });
    const wanted = String(answerBank.get(current.item.question.id).answer);
    for (let steps = 0; (await group.locator("input:checked").getAttribute("value")) !== wanted; steps++) {
      assert.ok(steps < current.item.question.choices.length); await page.keyboard.press("ArrowRight");
    }
    await page.keyboard.press("Tab");
    assert.equal(await page.locator("#submit").evaluate(element => element === document.activeElement), true);
    const answered = await actionResponse(() => page.keyboard.press("Enter"), "/api/answer");
    assert.equal(answered.feedback.status, "correct");
    assert.equal(await page.locator("#next").evaluate(element => element === document.activeElement), true);
    assert.equal(await group.locator("input:enabled").count(), 0, "Submitted choices are frozen on the feedback view");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.activeElement?.id === "quiz-prompt");
    assert.equal(answered.item.question.type, "numeric");
    await page.keyboard.press("Tab");
    assert.equal(await page.getByLabel("你的答案", { exact: true }).evaluate(element => element === document.activeElement), true);
    await page.keyboard.type(String(answerBank.get(answered.item.question.id).answer));
    const numeric = await actionResponse(() => page.keyboard.press("Enter"), "/api/answer");
    assert.equal(numeric.feedback.status, "correct");
    await page.locator("#restart").focus(); await page.keyboard.press("Enter"); await visible("#view-home");
    await page.waitForFunction(() => document.activeElement === document.querySelector("#view-home h2"));
    await page.locator(".skip-link").focus(); await page.keyboard.press("Enter");
    assert.equal(await page.locator("#main-content").evaluate(element => element === document.activeElement), true);
    result.observations.choice_input_type = "native_radio";
    result.observations.keyboard_actions = ["Tab", "Space", "ArrowRight", "Enter"];
    claim("Native radio choices expose selection and support Tab, Space and arrow-key operation.");
    claim("View changes focus their heading; question changes focus the prompt before answer controls.");
    claim("Choice and numeric answers submit through keyboard controls with stable answer IDs and visible labels.");
    claim("A keyboard skip link reaches the main learning content; this is a scoped functional check, not complete WCAG conformance.");
  },

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
    const assessmentComment = "合成验收：我补充了事件独立的依据，还需要检查模型条件。";
    await editor.locator(".self-assessment-comment > summary").first().click();
    let releaseDraft, startedDraft;
    const draftStarted = new Promise(resolve => { startedDraft = resolve; });
    const holdDraft = async route => {
      const request = route.request(), payload = request.method() === "POST" ? request.postDataJSON() : {};
      if (payload.status === "draft" && payload.self_assessment?.some(row => row.comment === assessmentComment)) {
        await new Promise(resolve => { releaseDraft = resolve; startedDraft(); });
      }
      await route.continue();
    };
    await page.route("**/api/work", holdDraft);
    let saved, autosaveTimeout;
    try {
      await editor.locator(".self-assessment-comment textarea").first().fill(assessmentComment);
      await Promise.race([draftStarted, new Promise((_, reject) => { autosaveTimeout = setTimeout(() => reject(new Error("Autosave did not start")), 15000); })]);
      clearTimeout(autosaveTimeout);
      const finish = editor.getByRole("button", { name: "提交我的自查", exact: true });
      assert.equal(await finish.isEnabled(), true, "An in-flight autosave must not swallow explicit submission");
      saved = await actionResponse(async () => { await finish.click(); releaseDraft(); }, "/api/work", "POST",
        response => response.request().postDataJSON().status === "submitted");
    } finally { clearTimeout(autosaveTimeout); releaseDraft?.(); await page.unroute("**/api/work", holdDraft); }
    await editor.locator(".work-status").filter({ hasText: "自查记录已保存" }).waitFor();
    const exported = await apiRead("/api/export");
    const session = exported.sessions.find(session => session.id === sid);
    assert.equal(session.answers.find(answer => answer.question_id === qid).status, "needs_review");
    assert.equal(saved.work.payload.self_assessment[0].status, "met");
    assert.equal(saved.work.payload.self_assessment[0].comment, assessmentComment);
    result.observations.session_id = sid;
    result.observations.retained_grade = "needs_review";
    result.observations.submission_queued_during_autosave = true;
    claim("A rubric draft survives reload and resumes through the visible Continue control.");
    claim("Learner self-assessment creates a work revision while the original grade remains needs_review.");
    claim("Explicit submission queues behind an in-flight autosave instead of being dropped by a disabled button.");
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
    assert.equal(exported.schema, 3);
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
    assert.deepEqual(await page.locator("#data-learning-path .eyebrow").allTextContents(), ["小学"]);
    assert.ok((await page.locator("#chapters .volume-title").allTextContents()).every(text => text.includes("小学")));
    await noHorizontalOverflow("primary_home_390");
    await page.locator("#data-learning-path").getByRole("button", { name: "开始学习", exact: true }).click();
    await visible("#view-unit");
    assert.equal(new URL(page.url()).searchParams.get("unit"), "data_reading_and_mean");
    await page.waitForFunction(() => { const image = document.querySelector("#unit-content .lesson-diagram img"); return image?.complete && image.naturalWidth > 0; });
    await noHorizontalOverflow("primary_data_unit_390");
    const notes = page.locator("#unit-content > details").filter({ has: page.getByText("保存我的解释与疑问", { exact: true }) });
    await notes.locator(":scope > summary").click();
    const noteInput = notes.locator(".work-field textarea").first(), note = "合成验收：放大图表时保留本页读数草稿。";
    const savedNote = page.waitForResponse(response => isResponse(response, "/api/work", "POST") && Object.values(response.request().postDataJSON().draft || {}).includes(note));
    await noteInput.fill(note);
    const enlarge = page.locator("#unit-content .diagram-enlarge").first(), source = await page.locator("#unit-content .lesson-diagram img").first().getAttribute("src");
    assert.equal(await enlarge.getAttribute("href"), source); assert.equal(await enlarge.getAttribute("target"), "_blank");
    assert.ok((await enlarge.getAttribute("rel")).split(" ").includes("noopener"));
    const originalUrl = page.url(), opened = page.waitForEvent("popup");
    await enlarge.click();
    const popup = await opened; await popup.waitForLoadState("domcontentloaded");
    assert.equal(new URL(popup.url()).pathname, source); assert.equal(await popup.locator("svg").count(), 1);
    assert.equal(page.url(), originalUrl); assert.equal(await noteInput.inputValue(), note);
    assert.equal((await savedNote).status(), 200); await popup.close();
    result.observations.mobile_diagram_popup = { source, original_page_preserved: true, draft_preserved: true };
    await goto("?unit=ten_frame_place_value");
    await visible("#view-unit");
    await page.waitForFunction(() => { const image = document.querySelector("#unit-content .lesson-diagram img"); return image?.complete && image.naturalWidth > 0; });
    assert.match(await page.locator("#unit-content").textContent(), /口述|学具|画点/);
    await noHorizontalOverflow("primary_entry_390");
    await page.screenshot({ path: path.join(output, "primary-entry.png"), fullPage: true });
    await goto();
    await page.reload({ waitUntil: "domcontentloaded" });
    await valueEquals("#stage-select", "primary");
    await actionResponse(() => page.locator("#stage-select").selectOption("senior"), "/api/plan", "GET",
      response => new URL(response.url()).searchParams.get("stage") === "senior");
    assert.ok((await page.locator("#learning-units .eyebrow").allTextContents()).every(text => text === "高中"));
    assert.equal(await page.locator("#path-section").isVisible(), false);
    assert.deepEqual(await page.locator("#data-learning-path .eyebrow").allTextContents(), ["高中"]);
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
    claim("The stage-filtered data learning chain is reachable on a 390px screen; the full SVG opens in another tab without discarding the original draft.");
    claim("Home, teaching, unit and project views have no viewport-level horizontal overflow at 390px.");
    claim("The optional primary entry unit displays a working ten-frame diagram and oral/manipulative alternatives on a narrow screen.");
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
