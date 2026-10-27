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

// These helpers are exclusive to the exhaustive content preview. Other journeys
// retain their document navigation and interaction/recovery checks.
function previewText(value) {
  return String(value ?? "").replace(/\$\$([\s\S]*?)\$\$|\$([^$\n]*?)\$|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]/g,
    (_, display, inline, parens, brackets) => display ?? inline ?? parens ?? brackets).replace(/\s+/g, "");
}
function previewHash(value) {
  const sorted = item => Array.isArray(item) ? item.map(sorted) : item && typeof item === "object"
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, sorted(item[key])])) : item;
  // Matches server._hash for these JSON contracts (integers, strings, arrays and
  // objects): recursive key sorting, UTF-8 and no formatting whitespace.
  return require("node:crypto").createHash("sha256").update(JSON.stringify(sorted(value)), "utf8").digest("hex");
}
async function openPreviewReference(task, summary, kind, pageStarted) {
  const started = Date.now(), evidence = { path: task.path, material_id: task.material_id,
    started_after_page_ms: started - pageStarted, requests: [], responses: [] };
  result.observations.preview_reference_interactions ||= [];
  result.observations.preview_reference_interactions.push(evidence);
  const requestData = request => {
    try { return { body: request.postDataJSON() }; }
    catch (error) { return { body_error: String(error) }; }
  };
  const observedRequest = request => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/exposure")
      evidence.requests.push({ elapsed_ms: Date.now() - started, url: request.url(), method: request.method(), ...requestData(request) });
  };
  const observedResponse = response => {
    if (isResponse(response, "/api/exposure", "POST")) evidence.responses.push({ elapsed_ms: Date.now() - started,
      url: response.url(), status: response.status(), request: requestData(response.request()) });
  };
  const timed = (name, promise) => promise.then(value => {
    evidence[name] = { status: "fulfilled", elapsed_ms: Date.now() - started }; return value;
  }, error => {
    evidence[name] = { status: "rejected", elapsed_ms: Date.now() - started }; throw error;
  });
  page.on("request", observedRequest); page.on("response", observedResponse);
  try {
    // Arm the same exact POST/resource matcher before the real pointer click.
    // Settling both outcomes preserves a click failure that a response timeout
    // could otherwise mask. No retry or alternative interaction is performed.
    const responseAttempt = timed("response", (async () => {
      const response = await page.waitForResponse(response => isResponse(response, "/api/exposure", "POST") &&
        response.request().postDataJSON().resource_id === task.material_id);
      const body = await response.json();
      evidence.matched_response = { status: response.status(), request: requestData(response.request()), body };
      assert.equal(response.status(), 200, "/api/exposure: " + JSON.stringify(body));
      assert.equal(body.recorded, true, "The reference exposure must actually be recorded: " + task.path);
      return body;
    })());
    const clickAttempt = timed("click", summary.click());
    const [clicked, responded] = await Promise.allSettled([clickAttempt, responseAttempt]);
    evidence.clickError = clicked.status === "rejected" ? String(clicked.reason.stack || clicked.reason) : null;
    evidence.responseError = responded.status === "rejected" ? String(responded.reason.stack || responded.reason) : null;
    if (clicked.status === "rejected" || responded.status === "rejected") {
      try {
        evidence.failure_details = await page.evaluate(kind => {
          const view = document.getElementById("view-" + kind), selector = kind === "teach" ? ".self-check-answer" : ".worked-example";
          const details = view?.querySelector(selector), summary = details?.querySelector("summary"), bounds = summary?.getBoundingClientRect();
          return { url: location.pathname + location.search, visibility: document.visibilityState, view_hidden: view?.hidden,
            details_count: view?.querySelectorAll(selector).length, connected: details?.isConnected, open: details?.open,
            summary: summary?.textContent, summary_bounds: bounds ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } : null };
        }, kind);
      } catch (error) { evidence.failure_details_error = String(error); }
      throw clicked.status === "rejected" ? clicked.reason : responded.reason;
    }
    return responded.value;
  } finally {
    page.off("request", observedRequest); page.off("response", observedResponse);
  }
}
async function openContentPreview(task, first) {
  const target = new URL(task.path, args["base-url"]), entries = [...target.searchParams];
  const routes = { teach: "/api/teach/", unit: "/api/learning/units/", project: "/api/projects/",
    scope: "/api/quality-scope", needs: "/api/plan", history: "/api/history", profile: "/api/profile" };
  assert.equal(target.origin, new URL(args["base-url"]).origin);
  assert.equal(target.pathname, "/"); assert.equal(target.hash, ""); assert.equal(entries.length, 1);
  const [kind, id] = entries[0]; assert.ok(Object.hasOwn(routes, kind) && id, "Unknown preview route: " + task.path);
  const material = ["teach", "unit", "project"].includes(kind);
  assert.equal(task.material_id, material ? (kind === "teach" ? "topic" : kind) + ":" + id : null);
  if (first) await goto();
  const container = { scope: "scope-policy", needs: "needs-list", history: "history-list" }[kind] || kind + "-content";
  // Capture the destination container, not the previous active view: other views
  // intentionally stay attached while hidden by the application's real router.
  const previous = first ? null : await page.$("#" + container + " > :first-child");
  const responses = [], evidence = [];
  const observed = response => responses.push(response);
  page.on("response", observed);
  async function responseBody(pathname, params = {}) {
    const matches = response => {
      const url = new URL(response.url());
      return url.origin === target.origin && isResponse(response, pathname, "GET") &&
        [...url.searchParams].length === Object.keys(params).length &&
        Object.entries(params).every(([key, value]) => url.searchParams.get(key) === String(value));
    };
    const response = responses.find(matches) || await page.waitForResponse(matches);
    const body = await response.json();
    assert.equal(response.status(), 200, response.url() + ": " + JSON.stringify(body));
    assert.ok(body && typeof body === "object" && !Array.isArray(body), "Expected an object: " + response.url());
    evidence.push({ url: new URL(response.url()).pathname + new URL(response.url()).search, status: response.status() });
    return body;
  }
  try {
    const endpoint = routes[kind] + (material ? encodeURIComponent(id) : "");
    const [body, navigated] = await Promise.all([
      responseBody(endpoint, kind === "history" ? { limit: "20", offset: "0" } : {}),
      page.evaluate(async ({ kind, id }) => {
        // Await the production route, including its draft-flush and navigation
        // token checks. No mock, synthetic DOM or direct API replacement is used.
        const route = { teach: () => openTeach(id, "replace"), unit: () => openUnit(id, "replace"),
          project: () => openProject(id, "replace"), scope: () => openScope("replace"),
          needs: () => openNeeds("replace"), history: () => openHistory("replace"), profile: () => openProfile("replace") };
        return route[kind]();
      }, { kind, id }),
    ]);
    assert.equal(navigated, true, "Production route refused navigation: " + task.path);
    assert.equal(page.url(), target.href, "The rendered route must retain the exact requested URL");
    if (material) assert.equal(kind === "teach" ? body.topicId : body.id, id, "Wrong material response: " + task.path);
    const checks = kind === "teach" ? body.objective_coverage : [];
    assert.ok(Array.isArray(checks), "The teaching response must include its explicit self-check set");
    if (kind === "teach") {
      const prefix = `self_check:${id}:`;
      const expectedIndices = Object.keys(authoredCatalog.source_index).filter(key => key.startsWith(prefix))
        .map(key => Number(key.slice(prefix.length))).sort((a, b) => a - b);
      assert.ok(expectedIndices.length, "The previewed teaching material must have catalogued self-check tasks");
      assert.deepEqual(checks.map(row => row.criterion_index).sort((a, b) => a - b), expectedIndices,
        "A missing or empty self-check set cannot pass by vacuous completion: " + task.path);
    }
    const sources = kind === "teach" ? checks.map(row => ({ kind: "self_check", topic_id: id, criterion_index: String(row.criterion_index) }))
      : material ? [{ kind, [kind + "_id"]: id }] : [];
    const works = await Promise.all(sources.map(source => responseBody("/api/work", source)));
    for (const [index, loaded] of works.entries()) {
      const descriptor = loaded.descriptor, source = sources[index];
      assert.equal(descriptor?.kind, source.kind);
      assert.equal(descriptor.source_ref, kind === "teach" ? `topic:${id}:check:${source.criterion_index}` : `${kind}:${id}`);
      assert.match(descriptor.resource_version, /^[a-f0-9]{64}$/);
      assert.ok(Array.isArray(descriptor.resource_snapshot.fields) && descriptor.resource_snapshot.fields.length);
      assert.ok(Array.isArray(descriptor.resource_snapshot.criteria));
      let expectedVersion, expectedResource;
      if (kind === "teach") {
        const taskId = authoredCatalog.source_index[`self_check:${id}:${source.criterion_index}`], task = authoredCatalog.tasks[taskId];
        assert.ok(task && task.id === taskId);
        expectedVersion = previewHash({ task: task.resource_version, source: checks[index] });
        expectedResource = `self_check:${taskId}`;
        assert.deepEqual(descriptor.resource_snapshot.task_ids, [taskId]);
        assert.deepEqual(descriptor.resource_snapshot.fields.map(field => ({ id: field.id, prompt: field.prompt })),
          [{ id: "response", prompt: checks[index].self_check.prompt }]);
        assert.deepEqual(descriptor.resource_snapshot.criteria, checks[index].self_check.criteria);
      } else if (kind === "unit") {
        expectedVersion = authoredCatalog.units[id].resource_version;
        expectedResource = `unit:unit:${id}`;
        assert.equal(descriptor.resource_version, body.resource_version);
        const goalIdentity = ({ id, skill_id, statement, granularity }) => ({ id, skill_id, statement, granularity });
        assert.deepEqual(body.objectives.map(goalIdentity), authoredCatalog.units[id].objectives.map(goalIdentity),
          "The unit response must preserve every authored goal, including retained composite goals");
        assert.deepEqual(descriptor.resource_snapshot.fields.map(field => field.id), ["notes"]);
        assert.deepEqual(descriptor.resource_snapshot.criteria, body.objectives.map(goal => goal.statement));
      } else {
        const project = authoredCatalog.projects[id];
        assert.match(body.content_sha256, /^[a-f0-9]{64}$/);
        expectedVersion = previewHash({ source: body.content_sha256, catalog: project.resource_version });
        expectedResource = `project:project:${id}`;
        assert.deepEqual(descriptor.resource_snapshot.task_ids, project.task_ids);
        assert.deepEqual(descriptor.resource_snapshot.fields.map(field => field.id), project.task_ids);
        assert.deepEqual(descriptor.resource_snapshot.fields.map(field => ({ label: field.label, prompt: field.prompt })),
          body.steps.map(step => ({ label: step.title, prompt: step.instructions })));
        assert.deepEqual(descriptor.resource_snapshot.criteria, body.rubric.map(row => row.criterion));
      }
      assert.equal(descriptor.resource_version, expectedVersion, "Work descriptor version differs from its content/catalog contract");
      assert.equal(descriptor.resource_id, `${expectedResource}@${expectedVersion.slice(0, 16)}`, "Wrong stable work resource identity");
    }
    let manipulation = null, histories = null;
    if (kind === "unit" && body.manipulative) {
      manipulation = await responseBody("/api/manipulative", { unit_id: id });
      assert.equal(manipulation.resource_version, body.resource_version);
      assert.equal(manipulation.representation_version, body.manipulative.representation_version);
      await page.waitForFunction(id => document.getElementById("manip-prediction-" + id)?.disabled === false, id);
      const config = body.manipulative, state = config.state;
      const prediction = config.kind === "fraction_strip" ? "same" : String(config.kind === "ten_frame" ? state.a + state.b : config.kind === "data_chart" ? state.values.reduce((a, b) => a + b, 0) / 4 : state.slope * state.x + state.intercept);
      const field = page.locator("#manip-prediction-" + id);
      if (config.kind === "fraction_strip") await field.selectOption(prediction); else await field.fill(prediction);
      const recorded = await actionResponse(() => page.locator(".prediction-box").getByRole("button").click(), "/api/manipulative/predict");
      assert.equal(recorded.prediction.result.matches_model, true);
      if (config.kind === "ten_frame") {
        await page.locator(".counting-token").first().click();
        assert.equal(await page.locator('.counting-token[aria-pressed="true"]').count(), 1);
      }
      await actionResponse(() => page.locator(".manipulative").getByRole("button", { name: "保存操作和解释", exact: true }).click(), "/api/manipulative");
    }
    if (kind === "history") histories = await responseBody("/api/works", { limit: "20", offset: "0" });
    const readiness = { kind, id, count: works.length, indices: checks.map(row => row.criterion_index), hasManipulative: Boolean(manipulation), hasSteps: kind === "unit" && id === "equation_equivalence" };
    try {
      // Poll asynchronous DOM mounting independently of drawing frames. The
      // original 15-second default and every readiness condition remain intact.
      await page.waitForFunction(({ kind, id, count, indices, hasManipulative, hasSteps }) => {
        const view = document.getElementById("view-" + kind), editors = [...(view?.querySelectorAll(".work-editor") || [])];
        return view && !view.hidden && editors.length === count && editors.every(editor =>
          editor.querySelector(".work-revisions") && [...editor.querySelectorAll("textarea, select")].every(input => !input.disabled)) &&
          indices.every(index => { const input = view.querySelector("#self-check-" + index); return input && !input.disabled; }) &&
          (!hasSteps || (view.querySelectorAll(".step-practice [data-step-case]").length === 3 && [...view.querySelectorAll(".step-practice [data-step-case]")].every(button => !button.disabled))) &&
          (!hasManipulative || (view.querySelector("#manip-explanation-" + id)?.disabled === false &&
            (view.querySelector(".manipulative svg") || view.querySelector(".manipulative .count-frames button")) && view.querySelectorAll(".manip-controls input, .manip-controls select").length > 0 &&
            [...view.querySelectorAll(".manip-controls input, .manip-controls select")].every(input => !input.disabled)));
      }, readiness, { polling: 100 });
    } catch (error) {
      const observed = await page.evaluate(({ kind, id, indices }) => {
        const view = document.getElementById("view-" + kind);
        const controls = root => [...(root?.querySelectorAll("textarea, select, input") || [])]
          .map(input => ({ tag: input.tagName, id: input.id, disabled: input.disabled }));
        const editors = [...(view?.querySelectorAll(".work-editor") || [])];
        const manipulation = view?.querySelector(".manipulative"), explanation = view?.querySelector("#manip-explanation-" + id);
        return { url: location.href, visibility: document.visibilityState, document_hidden: document.hidden,
          view_exists: Boolean(view), view_hidden: view?.hidden, editor_count: editors.length,
          editors: editors.map(editor => ({ history_ready: Boolean(editor.querySelector(".work-revisions")), controls: controls(editor) })),
          self_checks: indices.map(index => { const input = view?.querySelector("#self-check-" + index);
            return { index, exists: Boolean(input), disabled: input?.disabled }; }),
          manipulation: { exists: Boolean(manipulation), explanation_exists: Boolean(explanation), explanation_disabled: explanation?.disabled,
            svg_exists: Boolean(manipulation?.querySelector("svg")), counting_buttons: manipulation?.querySelectorAll(".counting-token").length || 0, controls: controls(manipulation?.querySelector(".manip-controls")) },
          alerts: [...(view?.querySelectorAll('[role="alert"]') || [])].map(element => element.textContent), performance_now: performance.now() };
      }, readiness).catch(snapshotError => ({ snapshot_error: snapshotError.message }));
      result.observations.preview_readiness_failure = { path: task.path, expected: readiness, observed, api_responses: evidence };
      throw error;
    }
    if (previous) assert.equal(await previous.evaluate(element => element.isConnected), false, "Old destination content survived: " + task.path);
    const dom = await page.locator("#view-" + kind).evaluate((view, kind) => {
      // Read the formula's existing KaTeX source once, without its duplicate
      // accessibility/rendering text. Never render expected content into the DOM.
      const sourceText = node => node.nodeType === Node.TEXT_NODE ? node.textContent : node.matches?.(".katex")
        ? node.querySelector('annotation[encoding="application/x-tex"]')?.textContent || node.textContent
        : [...node.childNodes].map(sourceText).join("");
      const text = selector => [...view.querySelectorAll(selector)].map(sourceText);
      const content = view.querySelector("#" + kind + "-content");
      const goalTitle = { teach: "本节目标", unit: "这次逐项练习", project: "活动中需要展示" }[kind];
      const goals = [...(content?.children || [])].find(card => card.querySelector(":scope > h3")?.textContent === goalTitle);
      const unitGoalRows = goals && kind === "unit" ? [...goals.querySelectorAll(":scope > div > article.goal-summary")] : [];
      return { title: view.querySelector("h2")?.textContent,
        goals: goals ? [...goals.querySelectorAll(kind === "unit" ? ":scope > div > article.goal-summary > p" : ":scope > ul > li")].map(sourceText) : [],
        unitGoalRows: unitGoalRows.map(row => ({ paragraphs: row.querySelectorAll(":scope > p").length,
          buttons: [...row.querySelectorAll(":scope > button")].map(button => ({ name: button.textContent, disabled: button.disabled })) })),
        prompts: text(".self-check-prompt"), indices: [...view.querySelectorAll('textarea[id^="self-check-"]')].map(input => input.id),
        editors: [...view.querySelectorAll(".work-editor")].map(editor => ({ prompts: [...editor.querySelectorAll(".work-prompt")].map(sourceText),
          criteria: [...editor.querySelectorAll(".self-assessment-row > label")].map(label => label.textContent),
          fields: editor.querySelectorAll(".work-field textarea").length })),
        scopeIds: text("#scope-goals > article > details > p:first-of-type"), scopeSummary: view.querySelector("#scope-summary")?.textContent,
        needTitles: text("#needs-list > article > h3"), needFilter: view.querySelector("#need-filter")?.value, needsStatus: view.querySelector("#needs-status")?.textContent,
        sessionTitles: text("#history-list > article h3"), workTitles: text("#history-works > article h3"), historyStatus: view.querySelector("#history-status")?.textContent,
        profileTitle: view.querySelector("#profile-content > section > h3")?.textContent,
        profileFields: ["backup-code", "recover-code", "restore-backup"].filter(id => view.querySelector("#" + id)),
      };
    }, kind);
    const canonical = values => values.map(previewText);
    if (material) {
      assert.equal(dom.title, kind === "teach" ? body.topicName : body.title);
      const goals = kind === "teach" ? body.mastery_criteria : kind === "unit" && body.objectives.some(goal => goal.granularity === "observable")
        ? body.objectives.filter(goal => goal.granularity === "observable").map(goal => goal.statement) : body.objectives.map(goal => goal.statement);
      assert.ok(Array.isArray(goals) && goals.length, "The material must provide actual goals");
      assert.deepEqual(canonical(dom.goals), canonical(goals), "Displayed goals differ from this material: " + task.path);
      if (kind === "unit") {
        assert.deepEqual(dom.unitGoalRows, goals.map(() => ({ paragraphs: 1,
          buttons: [{ name: "查看这项目标的基础、帮助与任务", disabled: false }] })),
        "Every displayed unit goal must retain its own available pathway control");
      }
      if (kind === "teach") {
        assert.deepEqual(dom.indices, checks.map(row => "self-check-" + row.criterion_index));
        assert.deepEqual(canonical(dom.prompts), canonical(checks.map(row => row.self_check.prompt)));
      }
      for (const [index, loaded] of works.entries()) {
        const snapshot = loaded.descriptor.resource_snapshot, editor = dom.editors[index];
        assert.deepEqual(editor.criteria, snapshot.criteria);
        assert.equal(editor.fields, kind === "teach" ? 0 : snapshot.fields.length);
        assert.deepEqual(canonical(editor.prompts), kind === "teach" ? [] : canonical(snapshot.fields.map(field => field.prompt)));
      }
    } else if (kind === "scope") {
      assert.ok(Array.isArray(body.goals) && body.goals.length);
      assert.deepEqual(dom.scopeIds, body.goals.slice(0, 30).map(goal => "稳定目标ID：" + goal.goal_id));
      assert.equal(dom.scopeSummary, `当前显示 ${body.goals.length} 项。完整清单：${body.summary.topic_goals} 项教材目标、${body.summary.unit_goals} 项单元目标、${body.summary.project_goals} 项活动目标；这些层级分别统计，不相加为课标覆盖率。`);
    } else if (kind === "needs") {
      assert.ok(Array.isArray(body.learning_needs));
      const shown = body.learning_needs.filter(need => dom.needFilter === "all" || need.status === dom.needFilter);
      assert.deepEqual(dom.needTitles, shown.map(need => need.title));
      assert.equal(dom.needsStatus, `当前显示 ${shown.length} 项；全部 ${body.learning_needs.length} 项均保留原始来源与内容版本。`);
    } else if (kind === "history") {
      assert.ok(Array.isArray(body.sessions) && Array.isArray(histories.works));
      assert.deepEqual(dom.sessionTitles, body.sessions.map(session => session.title || "知识点检查"));
      assert.deepEqual(dom.workTitles, histories.works.map(work => work.title || "学习作品"));
      assert.equal(dom.historyStatus, body.total ? `共 ${body.total} 次作答，已显示 ${body.sessions.length} 次。` : "本浏览器还没有作答记录，可以从目录选择一个目标开始。");
    } else {
      assert.equal(typeof body.enabled, "boolean");
      assert.equal(dom.profileTitle, body.enabled ? body.label : "为当前学习记录设置恢复方式");
      assert.deepEqual(dom.profileFields, ["backup-code", "recover-code", "restore-backup"]);
    }
    return { kind, identity: { requested_url: target.pathname + target.search, rendered_url: new URL(page.url()).pathname + new URL(page.url()).search,
      response_id: material ? kind === "teach" ? body.topicId : body.id : kind, response_title: dom.title,
      old_destination_detached: previous ? true : null, checked_goal_count: dom.goals.length, checked_self_check_count: checks.length,
      work_descriptors: works.map(row => ({ resource_id: row.descriptor.resource_id, source_ref: row.descriptor.source_ref,
        resource_version: row.descriptor.resource_version, field_count: row.descriptor.resource_snapshot.fields.length })),
      manipulation_version: manipulation?.representation_version || null, api_responses: evidence } };
  } finally {
    page.off("response", observed);
    if (previous) await previous.dispose();
  }
}

const journeys = {
  async support_branches() {
    await require("./support-branches.cjs")({ page, goto, actionResponse, apiRead, claim, result, noHorizontalOverflow });
  },
  async equation_steps() {
    await require("./step-practice.cjs")({ page, goto, actionResponse, apiRead, claim, result, noHorizontalOverflow });
  },
  async goal_alignment() {
    const registry = read("data/skills/registry.json");
    const unitId = "common_factors_multiples";
    const unitGoals = authoredCatalog.units[unitId].objectives;
    const displayed = unitGoals.some(goal => goal.granularity === "observable")
      ? unitGoals.filter(goal => goal.granularity === "observable") : unitGoals;
    result.observations.unit_goal_routes = [];
    for (const [index, goal] of displayed.entries()) {
      await goto("?unit=" + unitId); await visible("#view-unit");
      const row = page.locator("#view-unit .goal-summary").nth(index);
      assert.equal(previewText(await row.locator(":scope > p").textContent()), previewText(goal.statement));
      const pathway = await actionResponse(() => row.getByRole("button", { name: "查看这项目标的基础、帮助与任务", exact: true }).click(),
        "/api/goal", "GET", response => {
          const query = new URL(response.url()).searchParams;
          return query.get("skill_id") === goal.skill_id && query.get("unit_id") === unitId;
        });
      assert.equal(pathway.skill_id, goal.skill_id);
      assert.equal(pathway.statement, goal.statement);
      await visible("#view-goal");
      const query = new URL(page.url()).searchParams;
      assert.equal(query.get("goal"), goal.skill_id); assert.equal(query.get("goal_unit"), unitId);
      result.observations.unit_goal_routes.push({ unit_id: unitId, skill_id: goal.skill_id, statement: goal.statement });
    }
    const whole = registry.objective_skill_ids["fraction_equivalence:whole"];
    await goto("?goal=" + whole + "&goal_unit=fraction_equivalence");
    await visible("#view-goal");
    const pathway = await apiRead("/api/goal?skill_id=" + whole + "&unit_id=fraction_equivalence");
    const commonFactorSkills = authoredCatalog.units.common_factors_multiples.skill_ids;
    assert.ok(!pathway.prerequisite_skill_ids.some(id => commonFactorSkills.includes(id)), "Identifying the whole must not inherit every unit-level GCD/LCM prerequisite");
    assert.ok((await page.locator("#goal-title").textContent()).includes("单位"));
    for (const option of pathway.task_options) assert.ok(!("answer" in option) && !("explanation" in option));
    const sid = registry.placements["m1a.1.1"].criterion_skill_ids[2];
    await goto("?goal=" + sid); await visible("#view-goal");
    const counting = await apiRead("/api/goal?skill_id=" + sid);
    const index = counting.task_options.findIndex(option => option.task_id === "m1a.1.1-q301");
    assert.ok(index >= 0, "A real counting task must remain linked to its exact target");
    const started = await startFrom(page.locator(".goal-task").nth(index).getByRole("button", { name: "做这道任务", exact: true }), "practice");
    assert.equal(started.item.question.id, "m1a.1.1-q301", "The chosen route must not silently substitute a random task");
    const lineGoal = registry.placements["m5b.5.1"].criterion_skill_ids[0];
    await goto("?teach=m5b.5.1&goal=" + lineGoal); await visible("#view-teach .selected-goal");
    assert.equal(await page.locator("#view-teach .selected-goal").getAttribute("data-goal-id"), lineGoal);
    const image = page.locator("#view-teach .selected-goal img");
    await image.waitFor(); assert.ok((await image.getAttribute("src")).includes("statistics-v5-line-self-check.svg"));
    await page.waitForFunction(() => [...document.querySelectorAll(".selected-goal img")].every(image => image.complete && image.naturalWidth > 0));
    result.observations.selected_task_id = started.item.question.id;
    result.observations.goal_id = lineGoal;
    claim("Specific goals open their own prerequisites, help and tasks without exposing answers.");
    claim("Each displayed common-factor/multiple unit goal opens its own stable goal identity and unit context through the actual button.");
    claim("A selected bank task starts exactly that task, and a deep link focuses the corresponding self-check with its actual graph.");
  },
  async changed_content_preview() {
    // Exhaustive previews retain the real DOM assertions and representative PNGs.
    // Automatic full-DOM snapshots also traverse the hidden whole-site catalogue
    // before/after locator/evaluate calls; the other journeys keep that tracing.
    // Playwright couples its built-in HAR to snapshots, so network evidence here
    // is the actual HTTP log plus the exact API response records below, not HAR.
    await context.tracing.stop();
    // Per-frame trace screenshots add capture work; explicit representative and
    // failure PNGs remain unchanged. This is not a claim about a timeout cause.
    await context.tracing.start({ screenshots: false, snapshots: false, sources: true });
    result.observations.tracing = { screenshots: false, snapshots: false, sources: true, built_in_har: false,
      network_evidence: ["http.log", "previews[].identity.api_responses", "preview_reference_interactions"] };
    const review = read("build/content-review.json");
    assert.ok(review.preview_tasks.length, "A content review must nominate actual pages");
    result.observations.content_fingerprint = review.content_fingerprint;
    result.observations.previews = [];
    result.observations.expected_preview_count = review.preview_tasks.length;
    result.observations.document_navigations = [];
    result.observations.bootstrap_requests = {};
    assert.equal(new Set(review.preview_tasks.map(task => task.path)).size, review.preview_tasks.length, "Preview paths must be unique");
    const observeRequest = request => {
      if (request.isNavigationRequest() && request.frame() === page.mainFrame() && request.resourceType() === "document")
        result.observations.document_navigations.push({ url: request.url(), elapsed_ms: Date.now() - started });
      const pathname = new URL(request.url()).pathname;
      if (request.method() === "GET" && ["/api/topics", "/api/learning/units", "/api/projects"].includes(pathname))
        result.observations.bootstrap_requests[pathname] = (result.observations.bootstrap_requests[pathname] || 0) + 1;
    };
    const started = Date.now(), captured = new Set();
    page.on("request", observeRequest);
    for (const task of review.preview_tasks) {
      const pageStarted = Date.now(), { kind, identity } = await openContentPreview(task, !result.observations.previews.length);
      const main = page.locator("#view-" + kind);
      const open = main.locator(kind === "teach" ? ".self-check-answer > summary" : ".worked-example > summary");
      if (await open.count()) await openPreviewReference(task, open.first(), kind, pageStarted);
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
      result.observations.previews.push({ path: task.path, material_id: task.material_id, reason: task.reason,
        ...reading, identity, elapsed_ms: Date.now() - pageStarted });
      result.observations.preview_count = result.observations.previews.length;
      if (result.observations.preview_count % 50 === 0) console.log(`[preview] ${result.observations.preview_count}/${review.preview_tasks.length} pages, ${Date.now() - started} ms`);
      const screenshot = { teach: "primary-entry.png", unit: "desktop-unit.png", project: "desktop-project.png", scope: "desktop-home.png" }[kind];
      if (screenshot && !captured.has(kind)) { await page.screenshot({ path: path.join(output, screenshot), fullPage: true }); captured.add(kind); }
    }
    page.off("request", observeRequest);
    result.observations.elapsed_ms = Date.now() - started;
    assert.equal(result.observations.previews.length, review.preview_tasks.length);
    assert.deepEqual(result.observations.previews.map(row => row.path), review.preview_tasks.map(row => row.path));
    assert.equal(result.observations.document_navigations.length, 1, "Exhaustive preview must use one real document startup and subsequent production SPA routes");
    claim("Every directly affected or declared dependent material in the change-impact checklist was opened in the actual application.");
    claim("Each preview matched its exact URL, response identity, displayed goals and prompts; all associated work editors and operation widgets completed their real loads.");
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
    const routeId = guided.support.route.route_id;
    const shownGuide = routeId === "default" ? authored.guided_task
      : (authored.guided_branches || []).find(branch => branch.id === routeId)?.guided_task;
    assert.ok(shownGuide, "Answer the selected frozen guide, never the default guide from a different route");
    assert.equal(guided.item.prompt, shownGuide.prompt);
    guided = await answerSupport(guided, shownGuide.answer);
    assert.equal((await apiRead("/api/plan")).learning_needs.find(n => n.need_id === originalNeed.need_id).status, "open");
    assert.notEqual(guided.item.type, "rubric", "This bounded numeric need has an objective follow-up candidate");
    const finished = await answerSupport(guided, answerBank.get(guided.item.id).answer);
    assert.ok(finished.done);
    plan = await apiRead("/api/plan");
    const closed = plan.learning_needs.find(n => n.need_id === originalNeed.need_id);
    assert.equal(closed.status, "resolved");
    assert.equal(closed.resolution.kind, "supported_confirmation");
    assert.equal(closed.resolution.independent_evidence, false);
    const followup = plan.learning_needs.find(n => n.parent_need_id === originalNeed.need_id && n.kind === "independent_followup");
    assert.ok(followup, "Supported success must retain an explicit goal-linked follow-up need");
    assert.equal(followup.status, "open");
    assert.equal(followup.resource_version, originalNeed.resource_version);
    assert.equal(followup.followup.ready, false, "Just-finished support cannot provide a real 24-hour interval");
    assert.equal(plan.learning_needs.find(n => n.need_id === processNeed.need_id).status, "open");
    assert.ok((await apiRead("/api/session/" + origin)).summary.answers.some(a => a.status === "wrong"));
    await goto();
    await page.locator("#stage-select").selectOption("senior");
    await page.waitForFunction(count => document.querySelector("#need-counts").textContent.includes(`${count} 项待处理`), plan.need_counts.open);
    await page.locator("#open-needs").click(); await visible("#view-needs");
    await page.locator("#needs-status").filter({ hasText: /^当前显示/ }).waitFor();
    assert.ok((await page.locator("#needs-list").textContent()).includes("过程待自查或修订"));
    assert.ok((await page.locator("#needs-list").textContent()).includes("修订后待独立确认"));
    const pendingFollowup = page.locator("#needs-list > article").filter({ hasText: "修订后待独立确认" }).first();
    await pendingFollowup.getByRole("button", { name: "查看并继续确认", exact: true }).click();
    await visible("#action-notice");
    assert.ok((await page.locator("#action-notice").textContent()).includes("24小时"));
    assert.equal((await apiRead("/api/plan")).learning_needs.find(n => n.need_id === followup.need_id).status, "open");
    result.observations.need_id = originalNeed.need_id;
    result.observations.support_session_id = support.session.id;
    claim("Wrong-answer and process needs survive completion, explicit deferral and stage changes.");
    claim("Intermediate help does not resolve the need; final confirmation records help and leaves the original wrong answer intact.");
    claim("Supported success leaves a separate persistent goal-linked follow-up with an honest interval and executable practice route.");
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
    let health = (await apiRead("/api/profile")).backup_health;
    assert.equal(health.generated.freshness, "matches_current_assets");
    assert.equal(health.verified_file, null, "Generating a response is not proof of a saved file.");
    await page.locator("#verify-backup-file").setInputFiles(filename);
    await page.locator("#verify-backup-code").fill(code);
    const verified = await actionResponse(() => page.getByRole("button", { name: "核验文件，不导入", exact: true }).click(), "/api/profile/verify-backup");
    assert.equal(verified.matches_current_assets, true);
    assert.equal(verified.identity_changed, false); assert.equal(verified.assets_imported, false);
    health = (await apiRead("/api/profile")).backup_health;
    assert.equal(health.verified_file.freshness, "matches_current_assets");
    // Reading material after backup is itself an asset/exposure change, even
    // when no work is edited and the visit occurs within the same clock second.
    await goto("?teach=m1a.1.1"); await valueEquals("#self-check-0", draft2);
    await page.locator("#open-profile").click(); await visible("#view-profile");
    health = (await apiRead("/api/profile")).backup_health;
    assert.equal(health.generated.freshness, "changed");
    assert.equal(health.verified_file.freshness, "changed");
    assert.ok((await page.locator("#backup-health").textContent()).includes("已有差异"));
    await page.setViewportSize({ width: 390, height: 844 });
    await noHorizontalOverflow("profile_backup_health_390");
    await page.screenshot({ path: path.join(output, "profile-health.png"), fullPage: true });
    await page.setViewportSize({ width: 1280, height: 900 });
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
    health = (await apiRead("/api/profile")).backup_health;
    assert.equal(health.restored_snapshot.freshness, "matches_current_assets");
    assert.equal(health.verified_file, null, "A restore receipt must not be renamed as a later selected-file check.");
    const imported = await apiRead("/api/work?resource_id=" + encodeURIComponent(first.resource_id));
    assert.deepEqual(imported.work.payload, recovered.work.payload);
    const importedRevisions = (await apiRead("/api/work/revisions?resource_id=" + encodeURIComponent(first.resource_id))).revisions;
    assert.equal(importedRevisions.length, 2);
    health = (await apiRead("/api/profile")).backup_health;
    assert.equal(health.restored_snapshot.freshness, "changed", "Reopening frozen work records fresh exposure after the imported snapshot.");
    await goto("?teach=m1a.1.1"); await valueEquals("#self-check-0", draft2);
    claim("Creating a recovery code preserves the current anonymous learning records and returns no cookie credential in JSON.");
    claim("Clearing cookies and site data is recoverable using the code; an encrypted backup imports through the UI into a separate fresh SQLite installation.");
    claim("Work identity, original snapshot and both revisions survive; the downloaded envelope does not expose the learner's text.");
    claim("The actual downloaded file can be selected and checked without importing or switching identity; later material exposure makes both backup and verification freshness stale.");
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
    await page.locator("#scope-goals article").first().getByRole("button", { name: "查看目标、材料与后续任务" }).click();
    await visible("#view-goal");
    await page.locator(".goal-route").first().getByRole("button", { name: "打开对应材料与自检" }).click();
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
      }
      assert.equal(await page.locator(".manipulative svg, .counting-token").count(), 0, "Do not show the result before the learner records a prediction");
      const predictionField = page.locator(`#manip-prediction-${uid}`);
      if (uid === "fraction_equivalence") await predictionField.selectOption("longer"); else await predictionField.fill("0");
      const predicted = await actionResponse(() => page.locator(".prediction-box").getByRole("button").click(), "/api/manipulative/predict");
      assert.equal(predicted.prediction.result.matches_model, false, "Keep a deliberately incorrect prediction for comparison");
      if (uid === "ten_frame_place_value") {
        assert.equal(await page.locator(".counting-token").count(), 20);
        await page.locator(".counting-token").first().click();
        await page.locator(".counting-token").first().click();
        assert.equal(await page.locator('.counting-token[aria-pressed="true"]').count(), 1, "A repeated tap must not count a second object");
        const repeatedPrediction = await actionResponse(() => page.locator(".prediction-box").getByRole("button").click(), "/api/manipulative/predict");
        assert.equal(repeatedPrediction.duplicate, true);
        assert.equal(repeatedPrediction.prediction.prediction_id, predicted.prediction.prediction_id);
        assert.equal(await page.locator('.counting-token[aria-pressed="true"]').count(), 1, "Retrying the same prediction must preserve existing taps");
        for (let i = 1; i < 20; i++) await page.locator(`.counting-token[data-token-index="${i}"]`).click();
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
      const saved = (await apiRead("/api/manipulative?unit_id=" + uid)).saved.payload;
      const state = saved.state;
      assert.equal(saved.prediction.prediction_id, predicted.prediction.prediction_id);
      assert.equal(saved.sequence_basis, "server_prediction_before_saved_operation");
      if (uid === "ten_frame_place_value") { assert.equal(saved.interaction.unique_count, 20); assert.equal(saved.interaction.repeated_taps, 1); }
      await page.reload({ waitUntil: "domcontentloaded" });
      await valueEquals(`#manip-explanation-${uid}`, reflection);
      assert.deepEqual((await apiRead("/api/manipulative?unit_id=" + uid)).saved.payload.state, state);
      if (uid === "ten_frame_place_value") assert.equal(await page.locator('.counting-token[aria-pressed="true"]').count(), 20);
      await noHorizontalOverflow(uid + "_manipulative_390");
      if (uid === "ten_frame_place_value") await page.locator(".manipulative").screenshot({ path: path.join(output, "counting-prediction.png") });
    }
    await page.screenshot({ path: path.join(output, "primary-entry.png"), fullPage: true });
    const exported = await apiRead("/api/export");
    const operations = exported.events.filter(e => e.event_type === "manipulation_saved" && e.payload.prediction);
    assert.equal(new Set(operations.map(event => event.resource_id)).size, 4);
    assert.ok(operations.every(event => event.payload.independent_evidence === false));
    claim("The complete course inventory retains 342 core and 135 foundation topics with explicit materials for every registered goal.");
    claim("Four mathematical representations save an actual prediction before showing results; original wrong predictions and operation order survive reload without assigning mastery.");
    claim("Ten-frame tokens support 44-pixel keyboard/touch targets; repeated tapping cannot add another object to the count.");
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
    const help = page.getByRole("checkbox", { name: "这道题使用了提示、参考答案或他人的帮助", exact: true });
    assert.equal(await help.evaluate(element => element === document.activeElement), true);
    await page.keyboard.press("Space"); assert.equal(await help.isChecked(), true);
    await page.keyboard.press("Tab");
    assert.equal(await page.locator("#submit").evaluate(element => element === document.activeElement), true);
    const answered = await actionResponse(() => page.keyboard.press("Enter"), "/api/answer");
    assert.equal(answered.feedback.status, "correct");
    assert.equal(await page.locator("#next").evaluate(element => element === document.activeElement), true);
    assert.equal(await group.locator("input:enabled").count(), 0, "Submitted choices are frozen on the feedback view");
    assert.equal(await help.isDisabled(), true, "Submitted help declaration is frozen with the answer");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.activeElement?.id === "quiz-prompt");
    assert.equal(answered.item.question.type, "numeric");
    assert.equal(await help.isChecked(), false, "The next task asks for its own help declaration");
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
    // Counting practice no longer claims to confirm oral reading or handwriting.
    const practiceOnly = page.waitForResponse(response => isResponse(response, "/api/session", "POST"));
    await goto("?target=m1a.1.1");
    assert.equal((await practiceOnly).status(), 409);
    await visible("#action-notice");
    const waiting = page.waitForResponse(response => isResponse(response, "/api/session", "POST"));
    await goto("?target=m2a.1.1");
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
    await goto("?target=m2a.1.1");
    const unavailable = await (await exhausted).json();
    assert.equal(unavailable.code, "no_fresh_tasks");
    await visible("#action-notice");
    const practice = await startFrom(page.locator("#action-notice").getByRole("button", { name: "继续复习练习", exact: true }), "practice");
    const answered = await submitAnswer(practice);
    assert.equal(answered.feedback.status, "correct");
    result.observations.completed_checks_before_exhaustion = checks;
    claim("An exhausted check pool provides a visible, working practice alternative.");
    claim("Practice-only early counting tasks cannot re-enter diagnostic screening through a direct launch URL.");
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
