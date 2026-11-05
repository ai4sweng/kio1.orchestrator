"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

/** @returns {object} Minimal DOM control for testing the existing UI handlers. */
function control() {
  return {
    disabled: false, dataset: {}, style: {}, value: "", listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
    querySelector() { return control(); },
    querySelectorAll() { return []; },
    setAttribute() {}, focus() {},
  };
}

/** @returns {object} Isolated application handlers with no server or real agent calls. */
function loadUI() {
  const elements = new Map();
  const requests = [];
  const controls = [];
  const catalog = { KIO7: { capabilities: ["generate_vitis_config", "review_config"] } };
  const document = {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, control());
      return elements.get(id);
    },
    querySelectorAll() { return []; },
    addEventListener() {},
  };
  document.getElementById("capability-catalog").textContent = JSON.stringify(catalog);
  document.getElementById("messages").querySelectorAll = () => controls;
  const context = vm.createContext({
    document,
    window: { addEventListener() {}, confirm: () => true },
    fetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({ status: "succeeded" }) };
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  vm.runInContext(source.replace("  initializeConversationHistory();", `
    appendMessage = () => ({ remove() {} });
    renderGenericJson = () => "";
    globalThis.ui = { renderPlanStep, setInterfaceBusy };
  `), context);
  return {
    ...context.ui, requests, controls,
    input: elements.get("query-input"),
    click: elements.get("messages").listeners.click,
  };
}

/** @param {object} plan Workflow fixture. @returns {object} Editable card DOM adapter. */
function planCard(plan) {
  const card = control();
  card.dataset.plan = JSON.stringify(plan);
  const error = control();
  const dispatch = control();
  dispatch.closest = () => card;
  let steps;
  const list = {
    set innerHTML(value) {
      assert.equal((value.match(/class="plan-step"/g) ?? []).length,
        JSON.parse(card.dataset.plan).steps.length);
      rebuild();
    },
  };
  /** @returns {void} Rebuild controls using the updated plan after deletion. */
  function rebuild() {
    steps = JSON.parse(card.dataset.plan).steps.map((step, index) => {
      const element = control();
      element.dataset.stepIndex = String(index);
      const editor = control();
      editor.value = step.task;
      editor.scrollHeight = 56;
      const button = control();
      button.closest = (selector) => selector === ".plan-card" ? card : element;
      const selected = (step.capabilities ?? [step.capability]).map((value) => ({ value }));
      element.querySelector = () => editor;
      element.querySelectorAll = () => selected;
      return { element, editor, button };
    });
  }
  rebuild();
  card.querySelector = (selector) => ({
    ".plan-steps": list, ".plan-error": error, ".dispatch-plan": dispatch,
  })[selector];
  card.querySelectorAll = (selector) => steps.map((step) => ({
    ".plan-step": step.element, ".step-task": step.editor, ".delete-step": step.button,
  })[selector]);
  return { card, error, dispatch, get steps() { return steps; } };
}

/** @returns {object} Three-step plan with explicit dependencies and structured inputs. */
function samplePlan() {
  return {
    workflow_id: "wf-ui", execution_mode: "mixed",
    steps: ["s1", "s2", "s3"].map((step_id, index) => ({
      step_id, agent_id: "KIO7", capability: "generate_vitis_config",
      task: `Task ${index + 1}`, depends_on: index ? ["s1"] : [],
      data: { preserve: index },
    })),
  };
}

test("capability labels are readable but checkbox values stay canonical and escaped", () => {
  const ui = loadUI();
  const html = ui.renderPlanStep(samplePlan().steps[0], 0);
  assert.match(html, /value="generate_vitis_config" checked/);
  assert.match(html, /<span>Generate vitis config<\/span>/);
  assert.match(html, /<span>Review config<\/span>/);
  assert.match(html, /class="delete-step"/);
  assert.match(html, /\/static\/trash-2.svg/);
});

for (const mode of ["mixed", "sequential", "parallel"]) {
  test(`deletion updates ${mode} dispatch, dependencies, numbering, and preserves edits`, async () => {
    const ui = loadUI();
    const plan = samplePlan();
    plan.execution_mode = mode;
    if (mode === "parallel") plan.steps.forEach((step) => { step.depends_on = []; });
    const fixture = planCard(plan);
    fixture.steps[1].editor.value = "Operator edited task";
    fixture.steps[0].button.focus = () => {
      assert.equal(JSON.parse(fixture.card.dataset.plan).steps.length, 3);
    };
    await ui.click({ target: { closest: () => fixture.steps[0].button } });
    assert.deepEqual(JSON.parse(fixture.card.dataset.plan).steps.map((step) => step.step_id), ["s2", "s3"]);
    assert.deepEqual(fixture.steps.map((step) => step.element.dataset.stepIndex), ["0", "1"]);
    assert.equal(ui.requests.length, 0);
    await ui.click({ target: { closest: (selector) => selector === ".dispatch-plan" ? fixture.dispatch : null } });
    assert.equal(ui.requests.length, 1);
    const sent = ui.requests[0].body;
    assert.equal(sent.approved, true);
    assert.equal(sent.plan.steps[0].task, "Operator edited task");
    assert.deepEqual(sent.plan.steps.map((step) => step.depends_on), [[], []]);
    assert.deepEqual(sent.plan.steps.map((step) => step.data), [{ preserve: 1 }, { preserve: 2 }]);
    assert.equal(sent.plan.steps[0].capability, "generate_vitis_config");
    assert.deepEqual(sent.plan.steps[0].capabilities, ["generate_vitis_config"]);
  });
}

test("unrelated dependencies survive deletion and repeated deletion cannot dispatch an empty plan", async () => {
  const ui = loadUI();
  const plan = samplePlan();
  plan.steps[2].depends_on.push("s2");
  const fixture = planCard(plan);
  await ui.click({ target: { closest: () => fixture.steps[0].button } });
  assert.deepEqual(JSON.parse(fixture.card.dataset.plan).steps[1].depends_on, ["s2"]);
  while (fixture.steps.length) {
    await ui.click({ target: { closest: () => fixture.steps[0].button } });
  }
  await ui.click({ target: { closest: (selector) => selector === ".dispatch-plan" ? fixture.dispatch : null } });
  assert.equal(ui.requests.length, 0);
  assert.equal(fixture.error.hidden, false);
  assert.match(fixture.error.textContent, /no entries/);
});

test("busy or disabled deletion controls cannot mutate a plan", async () => {
  const ui = loadUI();
  const fixture = planCard(samplePlan());
  const original = fixture.card.dataset.plan;
  ui.controls.push(fixture.steps[0].button);
  ui.setInterfaceBusy(true);
  assert.equal(fixture.steps[0].button.disabled, true);
  await ui.click({ target: { closest: () => fixture.steps[0].button } });
  assert.equal(fixture.card.dataset.plan, original);
  ui.setInterfaceBusy(false);
  assert.equal(fixture.steps[0].button.disabled, false);
  fixture.steps[0].button.disabled = true;
  await ui.click({ target: { closest: () => fixture.steps[0].button } });
  assert.equal(fixture.card.dataset.plan, original);
});