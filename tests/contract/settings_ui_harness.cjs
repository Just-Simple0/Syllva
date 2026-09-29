// Minimal DOM harness that runs the real Settings app.js under scripted
// backend responses. Used only by tests/contract/test_settings_ui.py.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = process.argv[2];
const SCENARIO = process.argv[3];
const CSRF = "csrf-test-token-value";
const SECRET = "s3cr3t-value-never-shown";
const VOID = new Set(["meta", "link", "input", "br", "img"]);

class Text { constructor(text) { this.nodeType = 3; this.text = text; this.parent = null; } }

class El {
  constructor(doc, tag, attrs) {
    this.doc = doc; this.nodeType = 1; this.tagName = tag.toUpperCase();
    this.attrs = new Map(Object.entries(attrs || {})); this.children = []; this.parent = null;
    this.listeners = {}; this._value = this.attrs.get("value") || "";
    this.hidden = this.attrs.has("hidden"); this.disabled = this.attrs.has("disabled");
    this.readOnly = this.attrs.has("readonly"); this.className = this.attrs.get("class") || "";
  }
  get id() { return this.attrs.get("id") || ""; }
  set id(v) { this.attrs.set("id", v); }
  get name() { return this.attrs.get("name") || ""; }
  get type() { return this.attrs.get("type") || (this.tagName === "BUTTON" ? "submit" : "text"); }
  set type(v) { this.attrs.set("type", v); }
  get value() { return this._value; }
  set value(v) { this._value = String(v); }
  getAttribute(n) { return this.attrs.has(n) ? this.attrs.get(n) : null; }
  setAttribute(n, v) { this.attrs.set(n, String(v)); }
  hasAttribute(n) { return this.attrs.has(n); }
  removeAttribute(n) { this.attrs.delete(n); }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  get parentNode() { return this.parent; }
  get nextSibling() { const s = this.parent ? this.parent.children : []; return s[s.indexOf(this) + 1] || null; }
  insertBefore(c, ref) {
    c.parent = this;
    const i = ref ? this.children.indexOf(ref) : -1;
    if (i < 0) { this.children.push(c); } else { this.children.splice(i, 0, c); }
    return c;
  }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); c.parent = null; return c; }
  replaceChildren(...nodes) { this.children = []; nodes.forEach((n) => this.appendChild(n)); }
  get textContent() { return this.children.map((c) => c.nodeType === 3 ? c.text : c.textContent).join(""); }
  set textContent(v) { this.children = []; this.appendChild(new Text(String(v))); }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  dispatch(type) {
    const event = { type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    (this.listeners[type] || []).forEach((f) => f(event));
    return event;
  }
  click() { if (!this.disabled) { this.dispatch("click"); } }
  focus() { this.doc.activeElement = this; }
  descendants() { const out = []; const walk = (n) => n.children.forEach((c) => { if (c.nodeType === 1) { out.push(c); walk(c); } }); walk(this); return out; }
  querySelectorAll(sel) { return this.descendants().filter((e) => matches(e, sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function matches(e, sel) {
  const m = /^([a-zA-Z0-9]*)(?:\[([^\]=]+)(?:="([^"]*)")?\])?$/.exec(sel.trim());
  if (!m) { throw new Error("unsupported selector " + sel); }
  if (m[1] && e.tagName !== m[1].toUpperCase()) { return false; }
  if (m[2] && !e.attrs.has(m[2])) { return false; }
  if (m[3] !== undefined && e.attrs.get(m[2]) !== m[3]) { return false; }
  return true;
}

function parse(doc, html) {
  const root = new El(doc, "#root", {});
  let cur = root;
  const re = /<!--[\s\S]*?-->|<!doctype[^>]*>|<\/([a-zA-Z0-9]+)\s*>|<([a-zA-Z0-9]+)((?:\s+[^\s=>\/]+(?:="[^"]*")?)*)\s*\/?>|([^<]+)/gi;
  let m;
  while ((m = re.exec(html))) {
    if (m[1]) { cur = cur.parent || root; }
    else if (m[2]) {
      const attrs = {}; const ar = /([^\s=]+)(?:="([^"]*)")?/g; let a;
      while ((a = ar.exec(m[3] || ""))) { attrs[a[1]] = a[2] === undefined ? "" : a[2]; }
      const e = new El(doc, m[2], attrs); cur.appendChild(e);
      if (!VOID.has(m[2].toLowerCase())) { cur = e; }
    } else if (m[4] && m[4].trim()) { cur.appendChild(new Text(m[4])); }
  }
  return root;
}

function makeDocument() {
  const doc = { activeElement: null };
  const root = parse(doc, fs.readFileSync(path.join(STATIC, "index.html"), "utf8"));
  doc.root = root;
  doc.getElementById = (id) => root.descendants().find((e) => e.id === id) || null;
  doc.querySelectorAll = (s) => root.querySelectorAll(s);
  doc.querySelector = (s) => root.querySelector(s);
  doc.createElement = (t) => new El(doc, t, {});
  return doc;
}

const overview = (steps) => ({
  local_runtime_healthy: true,
  setup_ready: Boolean(steps) && steps.every((s) => s.state === "Ready"),
  readiness_funnel: {
    ai_client: "not_proven", ai_client_note: "requires human confirmation through actual AI client use",
    retrieval_credentials: "not_checked_here", retrieval_credentials_note: "run uls doctor to check credential readiness",
    source_archival: "not_started", text_extraction: "not_started",
  },
  worker_enabled: false,
  remote_enabled: false, doctor: { status: "ok", checks: {} },
  setup_steps: steps || ["Storage", "Canvas", "Academic", "Automation", "Remote", "Check"].map((n) => ({ name: n, state: "Partial", reason: n + " is not proven." })),
  pending_operations: [], session_notice: null,
});

async function run() {
  const doc = makeDocument();
  const windowListeners = {};
  const intervals = [];
  const clock = { t: 1_000_000 };
  const calls = [];
  let mode = "normal";
  let groupTz = "Asia/Seoul";
  let steps = null;
  let releaseSlow = null;
  const respond = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
  async function fetch(rawUrl, opts) {
    // app.js uses prefix-relative URLs ("api/v1/..."); normalize for routing.
    const url = "/" + String(rawUrl).replace(/^\/+/, "");
    calls.push({ url, raw: rawUrl, method: (opts && opts.method) || "GET",
                 headers: Object.assign({}, opts && opts.headers),
                 body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (mode === "unreachable") { throw new TypeError("Failed to fetch"); }
    if (mode === "replaced") { return respond(401, { error: { code: "SESSION_REPLACED" } }); }
    if (mode === "expired") { return respond(401, { error: { code: "SESSION_EXPIRED" } }); }
    if (mode === "not_found") { return respond(404, { error: { code: "SESSION_NOT_FOUND" } }); }
    if (mode === "csrf_rejected" && opts && opts.method === "POST") { return respond(403, { error: { code: "CSRF_REJECTED" } }); }
    if (url === "/api/v1/session/csrf") { return respond(200, { csrf_token: CSRF, idle_seconds: 900 }); }
    if (url === "/api/v1/overview") { return respond(200, overview(steps)); }
    if (url === "/api/v1/settings/general") { return respond(200, { generation: "g-" + groupTz, values: { "system.timezone": groupTz } }); }
    if (url === "/api/v1/settings/advanced") { return respond(200, { generation: "g-adv", values: { "retrieval.max_candidate_entities": 20, "retrieval.max_candidate_chunks": 12 } }); }
    if (url === "/api/v1/settings/advanced/validate") {
      return respond(400, { error: { code: "INVALID_VALUE", message: "Some settings need attention.", fields: [
        { field: "retrieval.max_candidate_chunks", code: "INVALID_VALUE", message: "Enter a whole number from 1 to 1,000." },
        { field: "retrieval.max_candidate_entities", code: "INVALID_VALUE", message: "Enter a whole number from 1 to 500." },
      ] } });
    }
    if (url.endsWith("/validate") && mode === "conflict") { groupTz = "UTC"; return respond(409, { error: { code: "CONFIGURATION_CHANGED", message: "changed" } }); }
    if (url.endsWith("/validate")) {
      if (mode === "slow") { await new Promise((resolve) => { releaseSlow = resolve; }); }
      const values = calls[calls.length - 1].body.values;
      return respond(200, { valid: true, values, generation: "g-" + groupTz, candidate_hash: "c".repeat(64),
                            diff: [{ field: "system.timezone", from: groupTz, to: values["system.timezone"] }] });
    }
    if (url.endsWith("/apply") && mode === "apply_conflict") { return respond(409, { error: { code: "CONFIGURATION_CHANGED", message: "changed" } }); }
    return respond(200, { status: "ok" });
  }
  class FakeDate extends Date { static now() { return clock.t; } }
  const window = {
    addEventListener(t, f) { (windowListeners[t] = windowListeners[t] || []).push(f); },
    removeEventListener(t, f) { windowListeners[t] = (windowListeners[t] || []).filter((x) => x !== f); },
  };
  const context = vm.createContext({
    document: doc, window, fetch, Date: FakeDate,
    setInterval(f) { intervals.push(f); return intervals.length; }, clearInterval(id) { intervals[id - 1] = null; },
    encodeURIComponent,
  });
  vm.runInContext(fs.readFileSync(path.join(STATIC, "app.js"), "utf8"), context);
  const flush = async () => { for (let i = 0; i < 30; i += 1) { await new Promise((r) => setImmediate(r)); } };
  await flush();
  const $ = (id) => doc.getElementById(id);
  const openGeneral = async () => { doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "general").click(); await flush(); };
  const addSecret = () => { const s = doc.createElement("input"); s.type = "password"; s.setAttribute("name", "secret.test"); s.value = SECRET; $("general-form").appendChild(s); return s; };
  const out = { scenario: SCENARIO };
  const observe = (secret) => {
    const every = doc.root.descendants();
    const serialized = every.map((e) => [e.textContent, e.value, ...e.attrs.values()].join("|")).join("\n");
    Object.assign(out, {
      heading: $("connection-heading").textContent, connectionHidden: $("connection-state").hidden,
      connectionRole: $("connection-state").getAttribute("role"), focusedId: doc.activeElement ? doc.activeElement.id : null,
      ordinaryHidden: $("ordinary-header").hidden, timezoneValue: $("system-timezone").value,
      timezoneReadOnly: $("system-timezone").readOnly, secretValue: secret ? secret.value : null,
      draftText: $("draft-list").textContent, draftHelp: $("draft-help").textContent,
      mutationsDisabled: doc.querySelectorAll("[data-mutation]").every((b) => b.disabled),
      beforeunload: (windowListeners.beforeunload || []).length, secretAnywhere: serialized.includes(SECRET),
      notice: $("notice").textContent,
      allButtonsDisabled: doc.querySelectorAll("button").every((b) => b.disabled),
      inertRegions: ["setup-stepper", "overview-panel", "general-panel", "advanced-panel"].filter((id) => $(id).hasAttribute("inert")),
    });
  };
  const tryMutations = async () => {
    const before = calls.length;
    doc.querySelectorAll("[data-apply-group]").forEach((b) => b.click());
    $("general-form").dispatch("submit");
    $("keepalive").click();
    // Force-dispatch even on disabled buttons to prove handlers are guarded too.
    $("recheck-steps").dispatch("click");
    doc.querySelectorAll("[data-panel]").forEach((b) => b.dispatch("click"));
    const stepsBefore = $("setup-steps").textContent;
    $("setup-steps").querySelectorAll("button").forEach((b) => b.dispatch("click"));
    out.stepsUnchanged = $("setup-steps").textContent === stepsBefore;
    out.generalHiddenAfter = $("general-panel").hidden;
    await flush();
    out.callsAfterEnd = calls.length - before;
  };
  out.startedEnabled = doc.querySelectorAll("[data-mutation]").some((b) => !b.disabled);

  if (SCENARIO === "replaced" || SCENARIO === "unreachable" || SCENARIO === "expired") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    const secret = addSecret();
    mode = SCENARIO;
    $("general-form").dispatch("submit");
    await flush();
    observe(secret);
    await tryMutations();
  } else if (SCENARIO === "clean_replaced") {
    await openGeneral();
    mode = "replaced";
    intervals.filter(Boolean).forEach((f) => f());
    await flush();
    observe(null);
  } else if (SCENARIO === "unreachable_poll") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    const secret = addSecret();
    mode = "unreachable";
    intervals.filter(Boolean).forEach((f) => f());
    await flush();
    observe(secret);
    await tryMutations();
  } else if (SCENARIO === "conflict") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    mode = "conflict";
    $("general-form").dispatch("submit");
    await flush();
    observe(null);
  } else if (SCENARIO === "stepper") {
    steps = ["Storage", "Canvas", "Academic", "Automation", "Remote", "Check"].map((n) => ({ name: n, state: "Ready" }));
    // reload overview so the stepper sees all-Ready steps
    $("recheck-steps").click();
    await flush();
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    const buttons = () => $("setup-steps").querySelectorAll("button");
    out.beforeStates = buttons().map((b) => b.querySelector("small").textContent);
    buttons()[4].click();
    await flush();
    out.currentBefore = buttons().findIndex((b) => b.getAttribute("aria-current") === "step");
    out.forwardStates = buttons().map((b) => b.querySelector("small").textContent);
    buttons()[1].click();
    await flush();
    out.afterStates = buttons().map((b) => b.querySelector("small").textContent);
    out.currentAfter = buttons().findIndex((b) => b.getAttribute("aria-current") === "step");
    observe(null);
  } else if (SCENARIO === "not_found" || SCENARIO === "csrf_rejected") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    const secret = addSecret();
    mode = SCENARIO;
    $("general-form").dispatch("submit");
    await flush();
    observe(secret);
    await tryMutations();
  } else if (["edit_after_review", "revert_after_review", "apply_reviewed", "generation_after_review"].includes(SCENARIO)) {
    await openGeneral();
    const tz = $("system-timezone");
    tz.value = "UTC";
    tz.dispatch("input");
    $("general-form").dispatch("submit");
    await flush();
    out.previewShownAfterReview = !$("general-preview").hidden;
    if (SCENARIO === "edit_after_review") { tz.value = "Europe/Paris"; tz.dispatch("input"); }
    if (SCENARIO === "revert_after_review") { tz.value = "Asia/Seoul"; tz.dispatch("input"); }
    if (SCENARIO === "generation_after_review") { mode = "apply_conflict"; }
    out.previewShownBeforeApply = !$("general-preview").hidden;
    doc.querySelectorAll("[data-apply-group]").find((b) => b.getAttribute("data-apply-group") === "general").dispatch("click");
    await flush();
    out.applyBodies = calls.filter((c) => c.url.endsWith("/apply")).map((c) => c.body);
    if (SCENARIO === "generation_after_review") {
      mode = "normal";
      doc.querySelectorAll("[data-apply-group]").find((b) => b.getAttribute("data-apply-group") === "general").dispatch("click");
      await flush();
      out.applyCallsAfterConflict = calls.filter((c) => c.url.endsWith("/apply")).length;
    }
    out.previewShownAfter = !$("general-preview").hidden;
    out.notice = $("notice").textContent;
    out.timezoneValue = tz.value;
  } else if (SCENARIO === "edit_during_review") {
    await openGeneral();
    const tz = $("system-timezone");
    tz.value = "UTC";
    tz.dispatch("input");
    mode = "slow";
    $("general-form").dispatch("submit");
    await flush();
    tz.value = "Europe/Paris";
    tz.dispatch("input");
    releaseSlow();
    await flush();
    out.previewShown = !$("general-preview").hidden;
    out.notice = $("notice").textContent;
    mode = "normal";
    doc.querySelectorAll("[data-apply-group]").find((b) => b.getAttribute("data-apply-group") === "general").dispatch("click");
    await flush();
    out.applyCalls = calls.filter((c) => c.url.endsWith("/apply")).length;
  } else if (SCENARIO === "busy") {
    await openGeneral();
    $("system-timezone").value = "UTC";
    mode = "slow";
    $("general-form").dispatch("submit");
    await flush();
    const submit = $("general-form").querySelector("button");
    out.busyAttr = $("general-form").getAttribute("aria-busy");
    out.submitDisabledWhileBusy = submit.disabled;
    out.applyDisabledWhileBusy = $("general-preview").querySelector("button").disabled;
    out.busyNotice = $("notice").textContent;
    out.noticeLive = $("notice").getAttribute("aria-live");
    $("general-form").dispatch("submit");
    await flush();
    out.validateCallsWhileBusy = calls.filter((c) => c.url.endsWith("/validate")).length;
    releaseSlow();
    await flush();
    out.busyAfter = $("general-form").getAttribute("aria-busy");
    out.submitDisabledAfter = submit.disabled;
    out.doneNotice = $("notice").textContent;
  } else if (SCENARIO === "field_error") {
    doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "advanced").click();
    await flush();
    const input = $("max-candidate-entities");
    input.value = "0";
    $("max-candidate-chunks").value = "0";
    $("advanced-form").dispatch("submit");
    await flush();
    const describedBy = input.getAttribute("aria-describedby");
    const message = describedBy ? $(describedBy) : null;
    Object.assign(out, {
      ariaInvalid: input.getAttribute("aria-invalid"), describedBy,
      messageText: message ? message.textContent : null,
      messageFollowsInput: message ? input.nextSibling === message : false,
      focusedId: doc.activeElement ? doc.activeElement.id : null,
      secondInvalid: $("max-candidate-chunks").getAttribute("aria-invalid"),
      secondDescribedBy: $("max-candidate-chunks").getAttribute("aria-describedby"),
      thirdInvalid: $("context-ttl").getAttribute("aria-invalid"),
      notice: $("notice").textContent,
    });
    input.value = "25";
    input.dispatch("input");
    out.ariaInvalidAfterEdit = input.getAttribute("aria-invalid");
    out.describedByAfterEdit = input.getAttribute("aria-describedby");
    out.messageRemoved = $("max-candidate-entities-error") === null;
    out.secondStillInvalid = $("max-candidate-chunks").getAttribute("aria-invalid");
  } else if (SCENARIO === "readiness") {
    const list = $("readiness-list");
    out.terms = list.querySelectorAll("dt").map((e) => e.textContent);
    out.details = list.querySelectorAll("dd").map((e) => e.textContent);
  } else if (SCENARIO === "close_confirm") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    const before = calls.length;
    $("close-session").click();
    await flush();
    out.dialogVisible = !$("close-confirm").hidden;
    out.dialogRole = $("close-confirm").getAttribute("role");
    out.dialogFocus = doc.activeElement ? doc.activeElement.id : null;
    out.callsWhileAsking = calls.length - before;
    $("close-confirm-cancel").click();
    await flush();
    out.dialogAfterCancel = !$("close-confirm").hidden;
    out.focusAfterCancel = doc.activeElement ? doc.activeElement.id : null;
    out.stillActive = $("connection-state").hidden && !$("close-session").disabled;
    $("close-session").click();
    await flush();
    $("close-confirm-accept").click();
    await flush();
    out.closeCalls = calls.filter((c) => c.url === "/api/v1/session/close").length;
    observe(null);
  } else if (SCENARIO === "close_clean") {
    $("close-session").click();
    await flush();
    out.closeCalls = calls.filter((c) => c.url === "/api/v1/session/close").length;
    out.dialogVisible = !$("close-confirm").hidden;
    observe(null);
  } else if (SCENARIO === "idle") {
    await openGeneral();
    $("system-timezone").value = "Europe/Paris";
    clock.t += (900 - 50) * 1000;
    intervals.filter(Boolean).forEach((f) => f());
    await flush();
    out.warningVisible = !$("idle-warning").hidden;
    out.warningFocused = doc.activeElement ? doc.activeElement.id : null;
    clock.t += 60 * 1000;
    intervals.filter(Boolean).forEach((f) => f());
    await flush();
    observe(null);
  }
  out.urls = calls.map((c) => c.url);
  out.csrfInUrl = calls.some((c) => c.url.includes(CSRF));
  out.mutationCsrfHeaders = calls.filter((c) => c.method === "POST").map((c) => c.headers["X-ULS-CSRF"] || null);
  process.stdout.write(JSON.stringify(out));
}

run().catch((error) => { process.stderr.write(String(error && error.stack || error)); process.exit(1); });
