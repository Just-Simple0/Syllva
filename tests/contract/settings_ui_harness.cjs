// Minimal DOM harness that runs the real Settings app.js under scripted
// backend responses. Used only by tests/contract/test_settings_ui.py.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const STATIC = process.argv[2];
const SCENARIO = process.argv[3];
const CREDENTIAL_ROLE = SCENARIO === "credential_failure_unconfigured_worker" ? "notion-worker" : "notion-mcp";
const CREDENTIAL_PURPOSE = CREDENTIAL_ROLE.endsWith("-worker") ? "worker" : "mcp";
const roleOverviewRace = new Set([
  "credential_overview_after_b", "credential_overview_inflight_b_success", "credential_overview_inflight_b_failure",
]).has(SCENARIO);
const canvasMutationRace = /^canvas_mutation_race_(connect|replace|forget)_(success|neutral)$/.exec(SCENARIO);
const canvasCredentialRace = SCENARIO === "canvas_notice_connect_credential_b";
const credentialReadbackCanvasRace = SCENARIO === "credential_independent_readback_canvas_b";
const credentialReconcileClearCanvasRace = SCENARIO === "credential_reconcile_clear_canvas_b";
const CSRF = "csrf-test-token-value";
const SECRET = "s3cr3t-value-never-shown";
const VOID = new Set(["meta", "link", "input", "br", "img"]);

function visibleText(node) {
  if (node.nodeType === 3) { return node.text; }
  if (node.nodeType !== 1 || node.hidden) { return ""; }
  return node.children.map(visibleText).join("");
}

class Text { constructor(text) { this.nodeType = 3; this.text = text; this.parent = null; } }

class El {
  constructor(doc, tag, attrs) {
    this.doc = doc; this.nodeType = 1; this.tagName = tag.toUpperCase();
    this.attrs = new Map(Object.entries(attrs || {})); this.children = []; this.parent = null;
    this.listeners = {}; this._value = this.attrs.get("value") || "";
    this.files = this.type === "file" ? [] : undefined;
    this.hidden = this.attrs.has("hidden"); this.disabled = this.attrs.has("disabled");
    this.readOnly = this.attrs.has("readonly"); this.className = this.attrs.get("class") || "";
  }
  get id() { return this.attrs.get("id") || ""; }
  set id(v) { this.attrs.set("id", v); }
  get name() { return this.attrs.get("name") || ""; }
  get type() { return this.attrs.get("type") || (this.tagName === "BUTTON" ? "submit" : "text"); }
  set type(v) { this.attrs.set("type", v); }
  get value() { return this._value; }
  set value(v) { this._value = String(v); if (this.type === "file" && !this._value) { this.files = []; } }
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
  dispatch(type, extra) {
    const event = Object.assign({ type, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, extra || {});
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
  if (sel.includes(",")) { return sel.split(",").some(part => matches(e, part.trim())); }
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

const overview = (steps, pendingOperations) => ({
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
  pending_operations: pendingOperations || [], session_notice: null,
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
  let releaseCanvasSnapshot = null;
  let releaseCanvasDiscovery = null;
  let releaseGoogleFile = null;
  let credentialPostMode = null;
  let failCredentialReadback = false;
  let credentialPending = null;
  let credentialSaved = false;
  let credentialForgotten = false;
  let credentialDetached = false;
  let credentialMutationPosted = false;
  let credentialReadbackCount = 0;
  let releaseCredentialReadback = null;
  let releaseOldCredentialReadback = null;
  let releaseIndependentCredentialReadback = null;
  let releaseCredentialClearReadback = null;
  let releaseIndependentCanvas = null;
  let releaseSecondCredentialPost = null;
  let releaseCanvasBPost = null;
  let releaseOldOverview = null;
  let releaseRoleOverview = null;
  let releaseSecondCanvasPost = null;
  let overviewCount = 0;
  let credentialSetPostCount = 0;
  let canvasMutationPostCount = 0;
  let canvasActionFailure = null;
  let canvasForgotten = false;
  let canvasConnected = false;
  let canvasRevision = 0;
  let canvasSnapshotCount = 0;
  let lastCanvasSnapshot = null;
  let failCanvasReadback = false;
  let canvasRegistry = ["canvas_saved", "canvas_apply_readback", "canvas_stale_snapshot", "canvas_notice_replace_success"].includes(SCENARIO)
    ? { term_id: "20", courses: [{ course_id: "10", term_id: "20", name: "Saved course", code: "SAVE" }] }
    : null;
  const sampleCanvasCourses = [
    { course_id: "10", term_id: "20", name: "Demo course", code: "DEMO" },
    { course_id: "12", term_id: "20", name: "Second course", code: "SECOND" },
    { course_id: "11", term_id: "21", name: "Later course", code: "LATER" },
  ];
  const canvasCourses = SCENARIO === "canvas_limit20"
    ? Array.from({ length: 21 }, (_, index) => ({ course_id: String(100 + index), term_id: "20", name: "Course " + (index + 1), code: "C" + (index + 1) }))
    : sampleCanvasCourses;
  const canvasProfileScenarios = new Set(["canvas_forget", "canvas_forget_fake", "canvas_term_dialog",
    "canvas_minimum_one", "canvas_saved", "canvas_apply_readback", "canvas_stale_snapshot",
    "canvas_review_invalidated", "canvas_apply_readback_failed", "canvas_limit20", "canvas_slow_discovery",
    "canvas_failed_discover", "canvas_failed_test", "canvas_failed_renew", "canvas_link_focus",
    "canvas_notice_replace_success", "canvas_notice_replace_failure", "canvas_notice_replace_wrong_profile",
    "canvas_notice_forget_success", "canvas_notice_forget_failure"]);
  if (canvasMutationRace && canvasMutationRace[1] !== "connect") { canvasProfileScenarios.add(SCENARIO); }
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const canvasSnapshot = () => {
    const hasProfile = !canvasForgotten && (canvasConnected || canvasProfileScenarios.has(SCENARIO));
    const profile = hasProfile ? { id: SCENARIO === "canvas_notice_replace_wrong_profile" && canvasRevision > 0 ? "other-profile" : "p1",
      origin: "https://canvas.example.edu", user_id: "12345", display_name: "Student N." } : null;
    const credentialPresent = Boolean(profile) && SCENARIO !== "canvas_notice_connect_missing" &&
      SCENARIO !== "canvas_notice_connect_error";
    const pendingOperations = SCENARIO === "canvas_notice_connect_pending" && profile
      ? [{ operation_id: "fake-canvas-pending", action_kind: "canvas_connect", phase: "verify", next_action: "manual_review" }]
      : [];
    let state = profile ? "partial" : "not_configured";
    if (profile && canvasRegistry && credentialPresent) { state = "ready"; }
    if (pendingOperations.length) { state = "partial"; }
    if (profile && SCENARIO === "canvas_notice_connect_error") { state = "error"; }
    return {
      config_generation: "g-canvas-" + canvasRevision,
      state,
      profile,
      registry: canvasRegistry ? copy(canvasRegistry) : null,
      masked_account: profile ? "S•••••• N••" : null,
      storage_label: SCENARIO === "canvas_forget_fake" ? "the fake test store" : "this Mac's Keychain",
      lease: { state: "not_issued" },
      credential_present: credentialPresent,
      can_mutate: pendingOperations.length === 0,
      pending_operations: pendingOperations,
    };
  };
  const respond = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });
  async function fetch(rawUrl, opts) {
    // app.js uses prefix-relative URLs ("api/v1/..."); normalize for routing.
    const url = "/" + String(rawUrl).replace(/^\/+/, "");
    calls.push({ url, raw: rawUrl, method: (opts && opts.method) || "GET",
                 headers: Object.assign({}, opts && opts.headers),
                 body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (url.startsWith("/api/v1/credentials/") && opts && opts.method === "POST" && credentialPostMode) {
      if (credentialPostMode === "unreachable") { throw new TypeError("Failed to fetch"); }
      const code = credentialPostMode === "replaced" ? "SESSION_REPLACED" : "SESSION_EXPIRED";
      return respond(credentialPostMode === "replaced" ? 401 : 401, { error: { code } });
    }
    if (mode === "unreachable") { throw new TypeError("Failed to fetch"); }
    if (mode === "replaced") { return respond(401, { error: { code: "SESSION_REPLACED" } }); }
    if (mode === "expired") { return respond(401, { error: { code: "SESSION_EXPIRED" } }); }
    if (mode === "not_found") { return respond(404, { error: { code: "SESSION_NOT_FOUND" } }); }
    if (mode === "csrf_rejected" && opts && opts.method === "POST") { return respond(403, { error: { code: "CSRF_REJECTED" } }); }
    if (url === "/api/v1/session/csrf") { return respond(200, { csrf_token: CSRF, idle_seconds: 900 }); }
    if (url === "/api/v1/overview") {
      overviewCount += 1;
      if (SCENARIO === "credential_overview_after_b" && overviewCount === 2) {
        return respond(503, { error: { code: "PROVIDER_UNAVAILABLE" } });
      }
      if (SCENARIO.startsWith("credential_overview_inflight_b_") && overviewCount === 2) {
        return await new Promise((resolve) => {
          releaseRoleOverview = () => resolve(SCENARIO.endsWith("_success")
            ? respond(200, overview(null, [{ operation_id: "stale-role-overview", action_kind: "config_apply",
              fields: ["system.timezone"], same_target: true, choices: [{ id: "resume", label: "Resume repair" }] }]))
            : respond(503, { error: { code: "PROVIDER_UNAVAILABLE" } }));
        });
      }
      if (SCENARIO === "overview_ordering" && overviewCount === 2) {
        return await new Promise((resolve) => {
          releaseOldOverview = () => resolve(respond(200, overview(null, [{
            operation_id: "stale-overview-operation", action_kind: "config_apply", fields: ["stale"],
            same_target: true, choices: [{ id: "resume", label: "Resume repair" }],
          }])));
        });
      }
      if (SCENARIO === "overview_ordering" && overviewCount === 3) {
        return respond(200, overview(null, []));
      }
      const pending = SCENARIO === "recovery_projection" ? [
        { operation_id: "config-op", action_kind: "config_apply", fields: ["system.timezone"], same_target: true,
          choices: [{ id: "resume", label: "Resume repair" }, { id: "leave", label: "Leave as-is" }] },
        { operation_id: "credential-op", action_kind: "credential_replacement", fields: ["notion-mcp"], same_target: true,
          choices: [{ id: "resume", label: "Continue credential replacement" }, { id: "restore", label: "Restore previous credential" }] },
      ] : [];
      return respond(200, overview(steps, pending));
    }
    if (url === "/api/v1/credentials") {
      credentialReadbackCount += 1;
      if (failCredentialReadback) { return respond(503, { error: { code: "PROVIDER_UNAVAILABLE" } }); }
      const fakeForget = SCENARIO === "credential_fake_forget" || SCENARIO === "credential_forget_success";
      const externalCredential = SCENARIO === "credential_detach_external_success" && !credentialDetached;
      const environmentFallback = SCENARIO === "credential_detach_external_success" && credentialDetached;
      const managedReplace = SCENARIO === "credential_replace_failure";
      const configured = !credentialForgotten && (externalCredential || environmentFallback || fakeForget || managedReplace || credentialSaved);
      const source = externalCredential ? "external_file" : environmentFallback ? "environment" : configured ? "keyring" : "environment";
      const notion = { role: CREDENTIAL_ROLE, provider: "notion", purpose: CREDENTIAL_PURPOSE,
        state: credentialPending ? "partial" : configured ? (source === "environment" ? "external" : "configured") : SCENARIO === "linux_card" ? "unsupported_platform" : "not_configured",
        source: source, managed: configured && !externalCredential, can_mutate: SCENARIO !== "linux_card",
        can_test: configured, can_detach: externalCredential, environment_variable: "NOTION_MCP_TOKEN",
        storage_label: configured ? "the fake test store" : "the Settings process environment", takes_effect: "MCP restart",
        pending_operation: credentialPending,
        last_check: SCENARIO === "credential_failure_generic"
          ? { code: "PROVIDER_UNAVAILABLE", message: "The provider could not be reached. Nothing changed." } : null };
      const cards = [notion];
      if (SCENARIO.startsWith("google_file_")) {
        cards.push({ role: "google-drive-mcp", provider: "google", purpose: "mcp", state: "not_configured",
          source: "file", managed: true, can_mutate: true, can_test: false, can_detach: false,
          environment_variable: "GOOGLE_DRIVE_MCP_CREDENTIALS", storage_label: "this computer's protected secrets folder",
          takes_effect: "MCP restart" });
      }
      const payload = { config_generation: "g-canvas-" + canvasRevision, cards };
      if (SCENARIO === "credential_failure_superseded_readback" && credentialMutationPosted && credentialReadbackCount === 2) {
        return await new Promise((resolve) => { releaseCredentialReadback = () => resolve(respond(200, payload)); });
      }
      if ((credentialReadbackCanvasRace || credentialReconcileClearCanvasRace) && credentialMutationPosted && credentialReadbackCount === 2) {
        return await new Promise((resolve) => { releaseOldCredentialReadback = () => resolve(respond(200, payload)); });
      }
      if ((credentialReadbackCanvasRace || credentialReconcileClearCanvasRace) && credentialMutationPosted && credentialReadbackCount === 3) {
        return await new Promise((resolve) => { releaseIndependentCredentialReadback = () => resolve(respond(200, payload)); });
      }
      if (credentialReconcileClearCanvasRace && credentialReadbackCount === 4) {
        return await new Promise((resolve) => { releaseCredentialClearReadback = () => resolve(respond(200, payload)); });
      }
      if (SCENARIO === "credential_generation_race" && credentialMutationPosted && credentialReadbackCount === 2) {
        return await new Promise((resolve) => { releaseOldCredentialReadback = () => resolve(respond(200, payload)); });
      }
      if (SCENARIO === "credential_overview_after_b" && credentialMutationPosted && credentialReadbackCount === 2) {
        return await new Promise((resolve) => { releaseOldCredentialReadback = () => resolve(respond(200, payload)); });
      }
      return respond(200, payload);
    }
    if (url === "/api/v1/canvas") {
      canvasSnapshotCount += 1;
      if (SCENARIO === "canvas_notice_connect_session_ended" && canvasSnapshotCount === 2) {
        return respond(401, { error: { code: "SESSION_EXPIRED" } });
      }
      if (failCanvasReadback) { return respond(503, { error: { code: "PROVIDER_UNAVAILABLE" } }); }
      const snapshot = canvasSnapshot();
      lastCanvasSnapshot = snapshot;
      if (SCENARIO === "credential_independent_refresh" && canvasSnapshotCount === 3) {
        return await new Promise((resolve) => { releaseIndependentCanvas = () => resolve(respond(200, snapshot)); });
      }
      if ((canvasMutationRace && canvasSnapshotCount === 2) ||
          (canvasCredentialRace && canvasSnapshotCount === 2) ||
          (SCENARIO === "canvas_stale_snapshot" && canvasSnapshotCount === 1) ||
          (SCENARIO === "canvas_notice_connect_superseded" && canvasSnapshotCount === 2)) {
        return await new Promise((resolve) => { releaseCanvasSnapshot = () => resolve(respond(200, snapshot)); });
      }
      return respond(200, snapshot);
    }
    if (url.startsWith("/api/v1/canvas/") && opts && opts.method === "POST" && canvasActionFailure === url.split("/").pop()) {
      return respond(503, { error: { code: "PROVIDER_UNAVAILABLE", message: "Canvas check unavailable." } });
    }
    if (url === "/api/v1/canvas/discover") {
      if (SCENARIO === "canvas_slow_discovery") {
        return await new Promise((resolve) => { releaseCanvasDiscovery = () => resolve(respond(200, { courses: copy(canvasCourses), terms: [{ term_id: "20", name: "Demo term" }, { term_id: "21", name: "Later term" }] })); });
      }
      return respond(200, { courses: copy(canvasCourses), terms: [{ term_id: "20", name: "Demo term" }, { term_id: "21", name: "Later term" }] });
    }
    if (url.startsWith("/api/v1/canvas/") && opts && opts.method === "POST" && url !== "/api/v1/canvas/discover") {
      const action = url.split("/").pop();
      if ((credentialReadbackCanvasRace || credentialReconcileClearCanvasRace) && action === "connect") {
        return await new Promise((resolve) => {
          releaseCanvasBPost = () => {
            canvasConnected = true;
            canvasRevision += 1;
            resolve(respond(200, { status: "complete" }));
          };
        });
      }
      if (canvasCredentialRace && action === "connect") {
        canvasConnected = true;
        canvasRevision += 1;
        return respond(200, { status: "complete" });
      }
      if (canvasMutationRace && action === canvasMutationRace[1]) {
        canvasMutationPostCount += 1;
        if (canvasMutationPostCount === 2) {
          return await new Promise((resolve) => {
            releaseSecondCanvasPost = () => {
              if (action === "connect") { canvasConnected = true; }
              if (action === "forget") { canvasForgotten = true; canvasRegistry = null; }
              canvasRevision += 1;
              resolve(respond(200, { status: "complete" }));
            };
          });
        }
        if (canvasMutationRace[2] === "neutral") {
          return respond(503, { error: { code: "PROVIDER_UNAVAILABLE", message: "Nothing changed." } });
        }
        if (action === "connect") { canvasConnected = true; }
        if (action === "forget") { canvasForgotten = true; canvasRegistry = null; }
        canvasRevision += 1;
        return respond(200, { status: "complete" });
      }
      const scenarioAction = SCENARIO.startsWith("canvas_notice_") ? SCENARIO.slice("canvas_notice_".length).split("_")[0] : null;
      const scenarioOutcome = SCENARIO.startsWith("canvas_notice_") ? SCENARIO.slice("canvas_notice_".length).slice(scenarioAction.length + 1) : null;
      if (SCENARIO === "canvas_destination_not_allowed" && action === "connect") {
        return respond(400, { error: { code: "DESTINATION_NOT_ALLOWED", message: "Provider refused the destination." } });
      }
      if (scenarioAction === action && scenarioOutcome === "failure") {
        return respond(503, { error: { code: "PROVIDER_UNAVAILABLE", message: "Nothing changed. The previous Canvas credential is unchanged." } });
      }
      if (scenarioAction === action && ["success", "unconfirmed", "missing", "pending", "error", "superseded", "wrong_profile"].includes(scenarioOutcome)) {
        if (action === "connect") { canvasConnected = true; }
        if (action === "forget") { canvasForgotten = true; canvasRegistry = null; }
        canvasRevision += 1;
        if (scenarioOutcome === "unconfirmed") { failCanvasReadback = true; }
        return respond(200, { status: "complete", message: "The previous credential is unchanged." });
      }
      if (url === "/api/v1/canvas/forget") { canvasForgotten = true; canvasRegistry = null; canvasRevision += 1; return respond(200, { status: "complete" }); }
    }
    if (url === "/api/v1/canvas/forget") { canvasForgotten = true; canvasRegistry = null; canvasRevision += 1; return respond(200, { status: "complete" }); }
    if (url === "/api/v1/settings/canvas_registry/validate") { return respond(200, { candidate_hash: "c".repeat(64) }); }
    if (url === "/api/v1/settings/canvas_registry/apply") {
      const body = calls[calls.length - 1].body;
      canvasRegistry = { term_id: body.term_id, courses: body.course_ids.map((id) => copy(canvasCourses.find((course) => course.course_id === id))) };
      canvasRevision += 1;
      if (SCENARIO === "canvas_apply_readback_failed") { failCanvasReadback = true; }
      return respond(200, { status: "complete", code: "APPLIED", config_generation: "g-canvas-" + canvasRevision });
    }
    if (url === "/api/v1/credentials/" + CREDENTIAL_ROLE + "/set") {
      credentialMutationPosted = true;
      credentialSetPostCount += 1;
      if ((SCENARIO === "credential_generation_race" || roleOverviewRace) && credentialSetPostCount === 2) {
        return await new Promise((resolve) => {
          releaseSecondCredentialPost = () => resolve(respond(409, { error: { code: "INVALID_CREDENTIAL",
            message: "The credential was rejected. The previous credential is kept." } }));
        });
      }
      if (canvasCredentialRace && credentialSetPostCount === 1) {
        return await new Promise((resolve) => {
          releaseSecondCredentialPost = () => resolve(respond(409, { error: { code: "INVALID_CREDENTIAL",
            message: "The credential was rejected. The previous credential is kept." } }));
        });
      }
      if (SCENARIO === "credential_success") { credentialSaved = true; return respond(200, { status: "complete", message: "Credential saved." }); }
      if (SCENARIO === "credential_failure_card_refresh_failed") { failCredentialReadback = true; }
      if (SCENARIO === "credential_failure_pending") {
        credentialPending = { operation_id: "pending-op", phase: "repair_required" };
        return respond(409, { error: { code: "MANUAL_REVIEW", message: "The previous credential is unchanged." } });
      }
      if (SCENARIO === "credential_failure_generic") {
        return respond(503, { error: { code: "PROVIDER_UNAVAILABLE", message: "The previous credential is unchanged." } });
      }
      return respond(409, { error: { code: "INVALID_CREDENTIAL", message: "The credential was rejected. The previous credential is kept." } });
    }
    if (url === "/api/v1/credentials/notion-mcp/forget" && SCENARIO === "credential_forget_success") {
      credentialForgotten = true;
      return respond(200, { status: "complete", message: "Credential removed." });
    }
    if (url === "/api/v1/credentials/notion-mcp/detach" && SCENARIO === "credential_detach_external_success") {
      credentialDetached = true;
      return respond(200, { status: "complete", code: "DETACHED", message: "Credential removed." });
    }
    if (url === "/api/v1/credentials/notion-mcp/replace" && SCENARIO === "credential_replace_failure") {
      return respond(409, { error: { code: "INVALID_CREDENTIAL", message: "The credential was rejected. The previous credential is kept." } });
    }
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
    encodeURIComponent, URL,
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
      connectionStateInert: $("connection-state").hasAttribute("inert"),
      connectionStateAriaDisabled: $("connection-state").getAttribute("aria-disabled"),
      headingAncestorInert: (() => {
        for (let node = $("connection-heading"); node; node = node.parentNode) {
          if (node.hasAttribute && node.hasAttribute("inert")) { return true; }
        }
        return false;
      })(),
      endedWorkAreasInert: ["topbar", "setup-stepper", "overview-panel", "general-panel", "advanced-panel", "connections-panel", "notice"]
        .every((id) => $(id).hasAttribute("inert")),
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

  if (SCENARIO === "overview_ordering") {
    $("recheck-steps").click(); await flush();
    $("recheck-steps").click(); await flush();
    if (releaseOldOverview) { releaseOldOverview(); }
    await flush();
    out.pendingLabels = $("recovery-list").querySelectorAll("button").map(button => button.textContent);
    out.pendingText = visibleText($("recovery-list"));
  } else if (["credential_cancel", "credential_failure", "credential_failure_unconfigured_worker", "credential_failure_superseded_readback", "credential_failure_card_refresh_failed",
       "credential_failure_pending", "credential_failure_generic", "credential_success", "credential_fake_forget", "credential_forget_success", "credential_detach_external_success", "credential_session_replaced",
       "credential_session_expired", "credential_session_unreachable", "credential_replace_failure", "linux_card", "canvas_origin", "google_file_cancel",
       "google_file_cancel_replace_race", "google_file_missing", "google_file_oversize", "google_file_read_error",
       "credential_environment_absent", "credential_independent_refresh", "credential_generation_race",
       "credential_overview_after_b", "credential_overview_inflight_b_success", "credential_overview_inflight_b_failure",
       "credential_independent_readback_canvas_b",
       "credential_reconcile_clear_canvas_b",
       "canvas_notice_connect_success", "canvas_notice_connect_failure", "canvas_notice_connect_unconfirmed",
       "canvas_notice_connect_missing", "canvas_notice_connect_pending", "canvas_notice_connect_error",
       "canvas_notice_connect_superseded", "canvas_notice_connect_session_ended", "canvas_notice_connect_credential_b",
       "canvas_notice_replace_success", "canvas_notice_replace_failure", "canvas_notice_replace_wrong_profile",
       "canvas_notice_forget_success", "canvas_notice_forget_failure",
       "canvas_destination_not_allowed"].includes(SCENARIO) || Boolean(canvasMutationRace) ||
      ["canvas_forget", "canvas_forget_fake", "canvas_term_dialog", "canvas_minimum_one", "canvas_saved", "canvas_apply_readback",
       "canvas_apply_readback_failed", "canvas_stale_snapshot", "canvas_review_invalidated", "canvas_limit20",
       "canvas_slow_discovery", "canvas_failed_discover", "canvas_failed_test", "canvas_failed_renew", "canvas_link_focus",
       "recovery_projection"].includes(SCENARIO)) {
    doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "connections").click();
    await flush();
    if (roleOverviewRace) {
      const submitSet = async (value) => {
        $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click();
        $("credential-secret").value = value;
        $("credential-confirm").click();
        await flush();
      };
      await submitSet("fake-A");
      out.aReadbackHeld = Boolean(releaseOldCredentialReadback);
      out.aOverviewHeld = Boolean(releaseRoleOverview);
      await submitSet("fake-B");
      out.bPostHeld = Boolean(releaseSecondCredentialPost);
      out.pendingNotice = $("notice").textContent;
      out.pendingMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.pendingRetry = $("credential-cards").querySelectorAll("button").map(button => button.textContent)
        .filter(label => label === "Try again" || label === "Retry replacement");
      out.pendingDialogOpen = !$('credential-dialog').hidden;
      out.pendingSecretCleared = $("credential-secret").value === "";
      out.pendingFocus = doc.activeElement ? doc.activeElement.id : null;
      if (releaseOldCredentialReadback) { releaseOldCredentialReadback(); }
      if (releaseRoleOverview) { releaseRoleOverview(); }
      await flush();
      out.afterANotice = $("notice").textContent;
      out.afterAMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.afterARetry = $("credential-cards").querySelectorAll("button").map(button => button.textContent)
        .filter(label => label === "Try again" || label === "Retry replacement");
      out.afterADialogOpen = !$('credential-dialog').hidden;
      out.afterASecretCleared = $("credential-secret").value === "";
      out.afterAFocus = doc.activeElement ? doc.activeElement.id : null;
      out.staleOverviewText = visibleText($("recovery-list"));
      out.staleOverviewLabels = $("recovery-list").querySelectorAll("button").map(button => button.textContent);
      out.overviewCountAfterA = overviewCount;
      if (releaseSecondCredentialPost) { releaseSecondCredentialPost(); }
      await flush();
    } else if (SCENARIO === "credential_generation_race") {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click();
      $("credential-secret").value = "fake-A"; $("credential-confirm").click(); await flush();
      out.aReadbackHeld = Boolean(releaseOldCredentialReadback);
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click();
      $("credential-secret").value = "fake-B"; $("credential-confirm").click(); await flush();
      out.pendingNotice = $("notice").textContent;
      out.pendingMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.pendingVisible = visibleText(doc.root);
      out.pendingRetry = $("credential-cards").querySelectorAll("button").map(button => button.textContent)
        .filter(label => label === "Try again" || label === "Retry replacement");
      out.pendingFocus = doc.activeElement ? doc.activeElement.id : null;
      if (releaseOldCredentialReadback) { releaseOldCredentialReadback(); }
      await flush();
      out.afterLateANotice = $("notice").textContent;
      out.afterLateAMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.afterLateAVisible = visibleText(doc.root);
      out.afterLateARetry = $("credential-cards").querySelectorAll("button").map(button => button.textContent)
        .filter(label => label === "Try again" || label === "Retry replacement");
      out.afterLateAFocus = doc.activeElement ? doc.activeElement.id : null;
      if (releaseSecondCredentialPost) { releaseSecondCredentialPost(); }
      await flush();
      out.secretFieldAfterRace = $("credential-secret").value;
    } else if (SCENARIO === "credential_independent_refresh") {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click();
      $("credential-secret").value = SECRET; $("credential-confirm").click(); await flush();
      out.beforeIndependentMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.beforeIndependentRetry = $("credential-cards").querySelectorAll("button").map(button => button.textContent)
        .filter(label => label === "Try again" || label === "Retry replacement");
      credentialSaved = true;
      doc.querySelectorAll("[data-panel]").find(b => b.getAttribute("data-panel") === "overview").click(); await flush();
      doc.querySelectorAll("[data-panel]").find(b => b.getAttribute("data-panel") === "connections").click(); await flush();
      out.independentCanvasHeld = Boolean(releaseIndependentCanvas);
      out.duringIndependentCanvasMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.duringIndependentCanvasButtons = $("credential-cards").querySelectorAll("button").map(button => button.textContent);
      out.duringIndependentCanvasVisible = visibleText(doc.root);
      if (releaseIndependentCanvas) { releaseIndependentCanvas(); }
      await flush();
      out.afterIndependentCanvasMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.afterIndependentCanvasButtons = $("credential-cards").querySelectorAll("button").map(button => button.textContent);
      out.afterIndependentCanvasVisible = visibleText(doc.root);
    } else if (credentialReadbackCanvasRace) {
      $("credential-cards").querySelectorAll("button").find(button => button.textContent === "Configure").click();
      $("credential-secret").value = "fake-credential-A";
      $("credential-confirm").click(); await flush();
      out.aOwnedReadbackHeld = Boolean(releaseOldCredentialReadback);
      $("connections-panel").querySelectorAll("button").find(button => button.getAttribute("class") === "back-link").click(); await flush();
      $("overview-panel").querySelectorAll("button").find(button => button.getAttribute("data-panel") === "connections").click(); await flush();
      out.independentReadbackHeld = Boolean(releaseIndependentCredentialReadback);
      $("canvas-origin").value = "https://canvas.example.edu";
      $("canvas-actions").querySelectorAll("button").find(button => button.textContent === "Test & connect").click(); await flush();
      $("credential-secret").value = "fake-canvas-B";
      $("credential-confirm").click(); await flush();
      out.canvasBPostHeld = Boolean(releaseCanvasBPost);
      out.pendingNotice = $("notice").textContent;
      out.pendingDialogOpen = !$("credential-dialog").hidden;
      out.pendingSecretCleared = $("credential-secret").value === "";
      out.pendingCanvasAction = $("credential-dialog-heading").textContent;
      if (releaseIndependentCredentialReadback) { releaseIndependentCredentialReadback(); }
      await flush();
      out.afterIndependentNotice = $("notice").textContent;
      out.afterIndependentCredentialMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      out.afterIndependentDialogOpen = !$("credential-dialog").hidden;
      out.afterIndependentSecretCleared = $("credential-secret").value === "";
      if (releaseOldCredentialReadback) { releaseOldCredentialReadback(); }
      await flush();
      out.afterStaleOwnedReadbackNotice = $("notice").textContent;
      if (releaseCanvasBPost) { releaseCanvasBPost(); }
      await flush();
      out.finalNotice = $("notice").textContent;
      out.finalDialogOpen = !$("credential-dialog").hidden;
      out.finalSecretCleared = $("credential-secret").value === "";
      out.canvasReadback = lastCanvasSnapshot ? copy(lastCanvasSnapshot) : null;
    } else if (credentialReconcileClearCanvasRace) {
      $("credential-cards").querySelectorAll("button").find(button => button.textContent === "Configure").click();
      $("credential-secret").value = "fake-credential-A";
      $("credential-confirm").click(); await flush();
      out.aOwnedReadbackHeld = Boolean(releaseOldCredentialReadback);
      $("connections-panel").querySelectorAll("button").find(button => button.getAttribute("class") === "back-link").click(); await flush();
      $("overview-panel").querySelectorAll("button").find(button => button.getAttribute("data-panel") === "connections").click(); await flush();
      out.firstIndependentReadbackHeld = Boolean(releaseIndependentCredentialReadback);
      if (releaseIndependentCredentialReadback) { releaseIndependentCredentialReadback(); }
      await flush();
      out.afterNoBIndependentNotice = $("notice").textContent;
      if (releaseOldCredentialReadback) { releaseOldCredentialReadback(); }
      await flush();
      $("connections-panel").querySelectorAll("button").find(button => button.getAttribute("class") === "back-link").click(); await flush();
      $("overview-panel").querySelectorAll("button").find(button => button.getAttribute("data-panel") === "connections").click(); await flush();
      out.secondIndependentReadbackHeld = Boolean(releaseCredentialClearReadback);
      $("canvas-origin").value = "https://canvas.example.edu";
      $("canvas-actions").querySelectorAll("button").find(button => button.textContent === "Test & connect").click(); await flush();
      $("credential-secret").value = "fake-canvas-B";
      $("credential-confirm").click(); await flush();
      out.canvasBPostHeld = Boolean(releaseCanvasBPost);
      out.pendingNotice = $("notice").textContent;
      out.pendingDialogOpen = !$("credential-dialog").hidden;
      if (releaseCredentialClearReadback) { releaseCredentialClearReadback(); }
      await flush();
      out.afterOutcomeInvalidationNotice = $("notice").textContent;
      out.afterOutcomeInvalidationCardMessage = $("credential-message-" + CREDENTIAL_ROLE).textContent;
      if (releaseCanvasBPost) { releaseCanvasBPost(); }
      await flush();
      out.finalNotice = $("notice").textContent;
      out.finalDialogOpen = !$("credential-dialog").hidden;
      out.finalSecretCleared = $("credential-secret").value === "";
    } else if (canvasMutationRace) {
      const action = canvasMutationRace[1];
      const outcome = canvasMutationRace[2];
      const startCanvasAction = async () => {
        if (action === "connect") {
          $("canvas-origin").value = "https://canvas.example.edu";
          $("canvas-actions").querySelectorAll("button").find(button => button.textContent === "Test & connect").click();
          await flush();
        } else {
          const label = action === "replace" ? "Replace token" : "Forget local token";
          $("canvas-actions").querySelectorAll("button").find(button => button.textContent === label).click();
        }
        if (action !== "forget") { $("credential-secret").value = "fake-canvas-secret"; }
        $("credential-confirm").click();
        await flush();
      };
      await startCanvasAction();
      out.aReadbackHeld = Boolean(releaseCanvasSnapshot);
      await startCanvasAction();
      out.bPostHeld = Boolean(releaseSecondCanvasPost);
      out.pendingNotice = $("notice").textContent;
      out.pendingDialogOpen = !$("credential-dialog").hidden;
      out.pendingSecretCleared = $("credential-secret").value === "";
      out.pendingFocus = doc.activeElement ? doc.activeElement.id : null;
      out.pendingVisibleText = visibleText(doc.root);
      if (releaseCanvasSnapshot) { releaseCanvasSnapshot(); }
      await flush();
      out.afterANotice = $("notice").textContent;
      out.afterADialogOpen = !$("credential-dialog").hidden;
      out.afterASecretCleared = $("credential-secret").value === "";
      out.afterAFocus = doc.activeElement ? doc.activeElement.id : null;
      out.afterAVisibleText = visibleText(doc.root);
      if (releaseSecondCanvasPost) { releaseSecondCanvasPost(); }
      await flush();
      out.finalNotice = $("notice").textContent;
      out.finalDialogOpen = !$("credential-dialog").hidden;
      out.finalSecretCleared = $("credential-secret").value === "";
      out.finalFocus = doc.activeElement ? doc.activeElement.id : null;
      out.visibleText = visibleText(doc.root);
      out.action = action;
      out.oldOutcome = outcome;
    } else if (SCENARIO.startsWith("canvas_notice_") || SCENARIO === "canvas_destination_not_allowed") {
      const action = SCENARIO.startsWith("canvas_notice_") ? SCENARIO.slice("canvas_notice_".length).split("_")[0] : "connect";
      if (action === "connect") {
        $("canvas-origin").value = "https://canvas.example.edu";
        $("canvas-actions").querySelectorAll("button").find(button => button.textContent === "Test & connect").click();
        await flush();
      } else {
        const label = action === "replace" ? "Replace token" : "Forget local token";
        $("canvas-actions").querySelectorAll("button").find(button => button.textContent === label).click();
      }
      if (action !== "forget") { $("credential-secret").value = SECRET; }
      $("credential-confirm").click(); await flush();
      if (SCENARIO === "canvas_notice_connect_superseded") {
        out.supersededReadbackHeld = Boolean(releaseCanvasSnapshot);
        $("connections-panel").querySelectorAll("button").find(button => button.getAttribute("class") === "back-link").click(); await flush();
        $("overview-panel").querySelectorAll("button").find(button => button.getAttribute("data-panel") === "connections").click(); await flush();
        if (releaseCanvasSnapshot) { releaseCanvasSnapshot(); }
        await flush();
      }
      if (canvasCredentialRace) {
        out.aReadbackHeld = Boolean(releaseCanvasSnapshot);
        $("credential-cards").querySelectorAll("button").find(button => button.textContent === "Configure").click();
        $("credential-secret").value = "fake-credential-B";
        $("credential-confirm").click(); await flush();
        out.bPostHeld = Boolean(releaseSecondCredentialPost);
        out.pendingNotice = $("notice").textContent;
        out.pendingDialogOpen = !$("credential-dialog").hidden;
        out.pendingSecretCleared = $("credential-secret").value === "";
        out.pendingFocus = doc.activeElement ? doc.activeElement.id : null;
        if (releaseCanvasSnapshot) { releaseCanvasSnapshot(); }
        await flush();
        out.afterANotice = $("notice").textContent;
        out.afterADialogOpen = !$("credential-dialog").hidden;
        out.afterASecretCleared = $("credential-secret").value === "";
        out.afterAFocus = doc.activeElement ? doc.activeElement.id : null;
        if (releaseSecondCredentialPost) { releaseSecondCredentialPost(); }
        await flush();
        out.finalNotice = $("notice").textContent;
        out.finalDialogOpen = !$("credential-dialog").hidden;
      }
      out.notice = $("notice").textContent;
      out.canvasStatus = $("canvas-status").textContent;
      out.canvasReadback = lastCanvasSnapshot ? copy(lastCanvasSnapshot) : null;
      out.canvasOriginError = $("canvas-origin-error").textContent;
      out.connectionVisible = !$("connection-state").hidden;
      out.endedMessage = $("connection-message").textContent;
      out.ordinaryHeaderHidden = $("ordinary-header").hidden;
      out.visibleTextAfterAction = visibleText(doc.root);
      out.secretCleared = $("credential-secret").value === "";
      out.mutationUrl = calls.filter(call => call.method === "POST" && call.url.startsWith("/api/v1/canvas/"))
        .map(call => call.url.split("/").pop());
    } else if (SCENARIO === "credential_forget_success") {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Forget local credential").click();
      $("credential-confirm").click(); await flush();
      out.notice = $("notice").textContent;
      out.credentialMessage = $("credential-message-notion-mcp").textContent;
      out.credentialState = $("credential-status-notion-mcp").textContent;
    } else if (SCENARIO === "credential_detach_external_success") {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Stop using this credential").click();
      $("credential-confirm").click(); await flush();
      out.notice = $("notice").textContent;
      out.credentialMessage = $("credential-message-notion-mcp").textContent;
      out.credentialState = $("credential-status-notion-mcp").textContent;
      out.credentialSource = calls.filter(call => call.url === "/api/v1/credentials").length;
    } else if (SCENARIO === "credential_failure_superseded_readback") {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click(); await flush();
      $("credential-secret").value = SECRET;
      $("credential-confirm").click(); await flush();
      doc.querySelectorAll("[data-panel]").find(b => b.getAttribute("data-panel") === "overview").click(); await flush();
      doc.querySelectorAll("[data-panel]").find(b => b.getAttribute("data-panel") === "connections").click(); await flush();
      if (releaseCredentialReadback) { releaseCredentialReadback(); }
      await flush();
      out.notice = $("notice").textContent;
      out.credentialMessage = $("credential-message-notion-mcp").textContent;
      out.credentialState = $("credential-status-notion-mcp").textContent;
      out.visibleTextAfterCredential = visibleText(doc.root);
      out.secretValue = $("credential-secret").value;
      out.secretInText = doc.root.textContent.includes(SECRET);
      out.secretInUrl = calls.some(c => c.url.includes(SECRET));
      out.credentialReadbackCount = credentialReadbackCount;
    } else if (SCENARIO === "credential_fake_forget") {
      const button = $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Forget local credential");
      button.click();
      out.forgetHelp = $("credential-dialog-help").textContent;
      out.roleInForget = $("credential-dialog-help").textContent.includes("Role: notion-mcp.");
      out.fakeStoreInForget = $("credential-dialog-help").textContent.includes("the fake test store");
      $("credential-cancel").click();
    } else if (SCENARIO === "credential_environment_absent") {
      out.text = $("credential-cards").textContent;
      out.testDisabled = $("credential-cards").querySelector("button").disabled;
    } else if (["google_file_cancel", "google_file_cancel_replace_race", "google_file_missing", "google_file_oversize", "google_file_read_error"].includes(SCENARIO)) {
      const googleCard = $("credential-cards").querySelectorAll("article").find(card => card.querySelector("h3").textContent.includes("Google Drive"));
      googleCard.querySelectorAll("button").find(b => b.textContent === "Configure").click();
      out.focusOnOpen = doc.activeElement.id;
      if (SCENARIO === "google_file_cancel") {
        const selected = { size: 128, text: () => new Promise(resolve => { releaseGoogleFile = () => resolve("fake-file-content"); }) };
        $("credential-file").files = [selected]; $("credential-confirm").click(); await flush();
        out.cancelEnabledDuringRead = !$("credential-cancel").disabled;
        $("credential-cancel").click();
        out.dialogHiddenAfterCancel = $("credential-dialog").hidden;
        if (releaseGoogleFile) { releaseGoogleFile(); }
        await flush();
        out.filePostsAfterCancel = calls.filter(c => c.url === "/api/v1/credentials/google-drive-mcp/set").length;
        out.secretValueAfterCancel = $("credential-secret").value;
      } else if (SCENARIO === "google_file_cancel_replace_race") {
        let releaseA = null, releaseB = null;
        $("credential-file").files = [{ size: 128, text: () => new Promise(resolve => { releaseA = () => resolve("file-A-secret"); }) }];
        $("credential-confirm").click(); await flush();
        $("credential-cancel").click();
        const retry = $("credential-cards").querySelectorAll("article").find(card => card.querySelector("h3").textContent.includes("Google Drive"))
          .querySelectorAll("button").find(b => b.textContent === "Configure");
        retry.click();
        $("credential-file").files = [{ size: 128, text: () => new Promise(resolve => { releaseB = () => resolve("file-B-secret"); }) }];
        $("credential-confirm").click(); await flush();
        out.cancelEnabledBeforeLateA = !$("credential-cancel").disabled;
        releaseA(); await flush();
        out.cancelEnabledAfterLateA = !$("credential-cancel").disabled;
        out.dialogVisibleAfterLateA = !$("credential-dialog").hidden;
        $("credential-cancel").click();
        out.dialogHiddenAfterCancelB = $("credential-dialog").hidden;
        releaseB(); await flush();
        out.postsAfterBothReads = calls.filter(c => c.url === "/api/v1/credentials/google-drive-mcp/set").length;
        out.secretFieldAfterBothReads = $("credential-secret").value;
      } else {
        if (SCENARIO === "google_file_oversize") { $("credential-file").files = [{ size: 65537, text: async () => "" }]; }
        if (SCENARIO === "google_file_read_error") { $("credential-file").files = [{ size: 128, text: () => Promise.reject(new Error("fake-secret-in-read-error")) }]; }
        $("credential-confirm").click(); await flush();
        out.fileError = $("credential-file-error").textContent;
        out.fileErrorVisible = !$("credential-file-error").hidden;
        out.fileErrorId = $("credential-file").getAttribute("aria-describedby");
        out.fileInvalid = $("credential-file").getAttribute("aria-invalid");
        out.fileFocus = doc.activeElement.id;
        out.filePosts = calls.filter(c => c.url === "/api/v1/credentials/google-drive-mcp/set").length;
        out.fileErrorRedacted = !doc.root.textContent.includes("fake-secret-in-read-error");
      }
    } else if (["credential_session_replaced", "credential_session_expired", "credential_session_unreachable"].includes(SCENARIO)) {
      $("credential-cards").querySelectorAll("button").find(b => b.textContent === "Configure").click();
      $("credential-secret").value = SECRET;
      credentialPostMode = SCENARIO === "credential_session_replaced" ? "replaced"
        : SCENARIO === "credential_session_expired" ? "expired" : "unreachable";
      const beforePost = calls.length;
      $("credential-confirm").click(); await flush();
      out.postCount = calls.filter(c => c.url === "/api/v1/credentials/notion-mcp/set").length;
      out.callsAfterPost = calls.length - beforePost - out.postCount;
      observe($("credential-secret"));
      await tryMutations();
    } else if (SCENARIO === "canvas_forget" || SCENARIO === "canvas_forget_fake") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      const checkbox = $("canvas-courses").querySelector("input"); checkbox.checked = true; checkbox.dispatch("change");
      $("canvas-review").click(); await flush();
      out.previewBefore = !$("canvas-registry-preview").hidden;
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Forget local token").click();
      out.forgetText = $("credential-dialog-help").textContent;
      $("credential-confirm").click(); await flush();
      out.courseAreaHidden = $("canvas-course-area").hidden;
      out.previewHidden = $("canvas-registry-preview").hidden;
      out.courses = $("canvas-courses").textContent; out.terms = $("canvas-term").textContent;
      out.count = $("canvas-selection-count").textContent;
      out.status = $("canvas-status").textContent;
    } else if (SCENARIO === "canvas_term_dialog") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      const checkbox = $("canvas-courses").querySelectorAll("input").find(b => b.value === "10");
      checkbox.checked = true; checkbox.dispatch("change");
      $("canvas-term").value = "21"; $("canvas-term").dispatch("change");
      out.dialogOpen = !$("canvas-term-dialog").hidden;
      out.cancelInitiallyFocused = doc.activeElement.id === "canvas-term-cancel";
      out.previousTermRestored = $("canvas-term").value === "20";
      out.selectionPreservedOnOpen = checkbox.checked && $("canvas-selection-count").textContent.startsWith("1 of");
      out.panelInert = $("connections-panel").hasAttribute("inert");
      out.topbarInert = $("topbar").hasAttribute("inert");
      const mainChildren = doc.querySelector("main").children;
      out.fullBackgroundInert = mainChildren.every(child => child === $("canvas-term-dialog") || child.hasAttribute("inert")) &&
        !$("canvas-term-dialog").hasAttribute("inert");
      const callsBeforeForcedBackground = calls.length;
      $("close-session").dispatch("click"); $("keepalive").dispatch("click");
      doc.root.descendants().find(node => node.tagName === "A" && node.getAttribute("class") === "brand").dispatch("click");
      doc.querySelectorAll("[data-panel]").forEach(button => button.dispatch("click"));
      $("general-form").dispatch("submit"); $("recheck-steps").dispatch("click");
      await flush();
      out.forcedBackgroundCalls = calls.length - callsBeforeForcedBackground;
      out.modalStayedOpenAfterForcedBackground = !$("canvas-term-dialog").hidden;
      out.connectionsStayedVisible = !$("connections-panel").hidden;
      const cancel = $("canvas-term-cancel"), confirm = $("canvas-term-confirm");
      cancel.focus(); const backward = $("canvas-term-dialog").dispatch("keydown", { key: "Tab", shiftKey: true });
      out.shiftTabWrapped = backward.defaultPrevented && doc.activeElement === confirm;
      confirm.focus(); const forward = $("canvas-term-dialog").dispatch("keydown", { key: "Tab", shiftKey: false });
      out.tabWrapped = forward.defaultPrevented && doc.activeElement === cancel;
      cancel.focus(); $("canvas-term-dialog").dispatch("keydown", { key: "Escape" });
      out.cancelRestored = $("canvas-term-dialog").hidden && $("canvas-term").value === "20" &&
        $("canvas-selection-count").textContent.startsWith("1 of") && doc.activeElement === $("canvas-term");
      $("canvas-term").value = "21"; $("canvas-term").dispatch("change");
      $("canvas-term-confirm").click();
      out.confirmedTermValue = $("canvas-term").value;
      out.confirmedTermDisabled = $("canvas-term").disabled;
      out.confirmDialogHidden = $("canvas-term-dialog").hidden;
      out.confirmChangedTerm = $("canvas-term").value === "21" && $("canvas-term").disabled === false;
      out.confirmClearedSelection = $("canvas-selection-count").textContent.startsWith("0 of");
      out.confirmReturnedFocus = doc.activeElement === $("canvas-term");
      mode = "replaced"; intervals.filter(Boolean).forEach(f => f()); await flush();
      out.confirmedTermDraft = $("draft-list").textContent;
      out.confirmedTermBeforeunload = (windowListeners.beforeunload || []).length;
    } else if (SCENARIO === "canvas_minimum_one") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      $("canvas-review").dispatch("click"); await flush();
      out.reviewDisabled = $("canvas-review").disabled;
      out.validateCalls = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/validate").length;
      out.previewHidden = $("canvas-registry-preview").hidden;
      out.previewText = $("canvas-registry-preview").textContent;
    } else if (SCENARIO === "canvas_review_invalidated") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      const checkbox = $("canvas-courses").querySelectorAll("input").find(b => b.value === "10");
      checkbox.checked = true; checkbox.dispatch("change");
      $("canvas-review").click(); await flush();
      out.reviewShown = !$("canvas-registry-preview").hidden;
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      out.reviewInvalidatedAfterDiscovery = $("canvas-registry-preview").hidden;
      $("canvas-apply").click(); await flush();
      out.applyCallsAfterInvalidation = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/apply").length;
    } else if (SCENARIO === "canvas_limit20") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      for (let index = 0; index < 20; index += 1) {
        const checkbox = $("canvas-courses").querySelectorAll("input")[index];
        checkbox.checked = true; checkbox.dispatch("change");
      }
      const last = $("canvas-courses").querySelectorAll("input")[20];
      out.twentySelected = $("canvas-selection-count").textContent.startsWith("20 of 20");
      out.twentyFirstDisabled = last.disabled;
      last.checked = true; last.dispatch("change");
      out.selectedAfterForcedTwentyFirst = $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length;
      out.twentyFirstStillDisabled = $("canvas-courses").querySelectorAll("input")[20].disabled;
      $("canvas-review").click(); await flush();
      canvasActionFailure = "discover";
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      out.disabledAfterFailedRediscovery = $("canvas-term").disabled && $("canvas-review").disabled &&
        $("canvas-courses").querySelectorAll("input").every(b => b.disabled);
      out.twentySelectedAfterFailedRediscovery = $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length;
      const lastAfterFailure = $("canvas-courses").querySelectorAll("input")[20];
      lastAfterFailure.checked = true; lastAfterFailure.dispatch("change");
      out.twentyOneStillBlockedAfterFailure = $("canvas-courses").querySelectorAll("input").length === 21 &&
        $("canvas-courses").querySelectorAll("input")[20].disabled &&
        $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length === 20;
      canvasActionFailure = null;
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      out.selectedAfterFreshRediscovery = $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length;
      out.twentyFirstDisabledAfterFreshRediscovery = $("canvas-courses").querySelectorAll("input")[20].disabled;
      $("canvas-review").click(); await flush();
      out.validationCourseCounts = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/validate").map(c => c.body.course_ids.length);
      $("canvas-apply").click(); await flush();
      out.applyCourseCounts = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/apply").map(c => c.body.course_ids.length);
    } else if (SCENARIO === "canvas_slow_discovery") {
      const discover = $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses");
      discover.click(); await flush();
      out.disabledDuringRequest = $("canvas-term").disabled && $("canvas-review").disabled;
      out.checkboxesDisabledDuringRequest = $("canvas-courses").querySelectorAll("input").every(b => b.disabled);
      releaseCanvasDiscovery(); await flush();
      out.discoveryAccepted = $("canvas-course-area").textContent.includes("DEMO") && !$("canvas-term").disabled;
      out.reviewStillRequiresSelection = $("canvas-review").disabled;
    } else if (["canvas_failed_discover", "canvas_failed_test", "canvas_failed_renew"].includes(SCENARIO)) {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      const first = $("canvas-courses").querySelector("input"); first.checked = true; first.dispatch("change");
      canvasActionFailure = SCENARIO === "canvas_failed_discover" ? "discover"
        : SCENARIO === "canvas_failed_test" ? "test" : "renew";
      const actionLabel = canvasActionFailure === "discover" ? "Find my courses"
        : canvasActionFailure === "test" ? "Retry check" : "Renew access";
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === actionLabel).click(); await flush();
      const row = $("canvas-courses").querySelector("input");
      out.termDisabledAfterFailure = $("canvas-term").disabled;
      out.checkboxDisabledAfterFailure = row.disabled;
      out.reviewDisabledAfterFailure = $("canvas-review").disabled;
      out.selectedAfterFailure = $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length;
      out.selectionCountAfterFailure = $("canvas-selection-count").textContent;
      const validates = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/validate").length;
      row.checked = false; row.dispatch("change"); $("canvas-review").dispatch("click"); await flush();
      out.selectedAfterForcedEdit = $("canvas-courses").querySelectorAll("input").filter(b => b.checked).length;
      out.validateCallsAfterForcedReview = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/validate").length;
      out.validateCallsBeforeForcedEvents = validates;
      out.termAfterFailure = $("canvas-term").value;
    } else if (SCENARIO === "canvas_saved") {
      out.savedTerm = $("canvas-term").value;
      out.savedTermLabel = $("canvas-term").textContent;
      out.savedTermDisabled = $("canvas-term").disabled;
      out.savedRows = $("canvas-courses").textContent;
      out.savedChecks = $("canvas-courses").querySelectorAll("input").map(b => ({ id: b.value, checked: b.checked, disabled: b.disabled }));
      out.savedCount = $("canvas-selection-count").textContent;
      out.reviewDisabled = $("canvas-review").disabled;
      mode = "replaced"; intervals.filter(Boolean).forEach(f => f()); await flush();
      out.savedCleanDraft = $("draft-list").textContent;
      out.savedCleanBeforeunload = (windowListeners.beforeunload || []).length;
    } else if (SCENARIO === "canvas_apply_readback" || SCENARIO === "canvas_apply_readback_failed") {
      await openGeneral(); $("system-timezone").value = "Europe/Paris"; $("system-timezone").dispatch("input");
      doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "connections").click(); await flush();
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Find my courses").click(); await flush();
      const first = $("canvas-courses").querySelectorAll("input").find(b => b.value === "10");
      first.checked = false; first.dispatch("change");
      const second = $("canvas-courses").querySelectorAll("input").find(b => b.value === "12");
      second.checked = true; second.dispatch("change");
      $("canvas-review").click(); await flush(); out.previewShown = !$("canvas-registry-preview").hidden;
      $("canvas-apply").click(); await flush();
      out.applyBodies = calls.filter(c => c.url === "/api/v1/settings/canvas_registry/apply").map(c => c.body);
      out.savedTerm = $("canvas-term").value; out.savedTermLabel = $("canvas-term").textContent;
      out.savedRows = $("canvas-courses").textContent;
      out.savedChecks = $("canvas-courses").querySelectorAll("input").map(b => ({ id: b.value, checked: b.checked, disabled: b.disabled }));
      out.savedCount = $("canvas-selection-count").textContent;
      out.reviewDisabled = $("canvas-review").disabled;
      out.timezoneDraft = $("system-timezone").value;
      out.notice = $("notice").textContent;
      if (SCENARIO === "canvas_apply_readback") {
        doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "general").click(); await flush();
        doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "connections").click(); await flush();
        out.reopenedTerm = $("canvas-term").value;
        out.reopenedRows = $("canvas-courses").textContent;
        out.reopenedCount = $("canvas-selection-count").textContent;
        out.reopenedReviewDisabled = $("canvas-review").disabled;
      } else {
        out.readbackPending = $("canvas-course-status").textContent;
        out.readbackCount = $("canvas-selection-count").textContent;
        out.readbackReviewDisabled = $("canvas-review").disabled;
      }
    } else if (SCENARIO === "canvas_stale_snapshot") {
      doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "general").click(); await flush();
      canvasRegistry = { term_id: "20", courses: [copy(canvasCourses[1])] }; canvasRevision += 1;
      doc.querySelectorAll("[data-panel]").find((b) => b.getAttribute("data-panel") === "connections").click(); await flush();
      out.newerResponseVisible = $("canvas-courses").textContent.includes("Second course");
      releaseCanvasSnapshot(); await flush();
      out.olderResponseIgnored = $("canvas-courses").textContent.includes("Second course") && !$("canvas-courses").textContent.includes("Saved course");
    } else if (SCENARIO === "recovery_projection") {
      const cards = $("recovery-list").querySelectorAll("article");
      out.recoveryCards = cards.map((card) => ({
        text: visibleText(card),
        labels: card.querySelectorAll("button").map((button) => button.textContent),
      }));
    } else if (SCENARIO === "linux_card") {
      out.text = $("credential-cards").textContent;
      out.testDisabled = $("credential-cards").querySelector("button").disabled;
    } else if (SCENARIO === "canvas_link_focus") {
      $("canvas-actions").querySelectorAll("button").find(b => b.textContent === "Replace token").click();
      const link = $("canvas-token-link"), cancel = $("credential-cancel");
      out.dialogVisible = !$("credential-dialog").hidden;
      out.topbarInert = $("topbar").hasAttribute("inert");
      out.fullBackgroundInert = doc.querySelector("main").children.every(child => child === $("credential-dialog") || child.hasAttribute("inert"));
      const callsBeforeBackgroundEvents = calls.length;
      $("close-session").dispatch("click"); $("keepalive").dispatch("click");
      doc.root.descendants().find(node => node.tagName === "A" && node.getAttribute("class") === "brand").dispatch("click");
      doc.querySelectorAll("[data-panel]").forEach(button => button.dispatch("click"));
      $("general-form").dispatch("submit"); $("recheck-steps").dispatch("click");
      $("canvas-term-cancel").dispatch("click"); $("canvas-term-confirm").dispatch("click");
      await flush();
      out.forcedBackgroundCalls = calls.length - callsBeforeBackgroundEvents;
      out.modalStayedOpen = !$("credential-dialog").hidden;
      out.fullBackgroundStillInert = doc.querySelector("main").children.every(child => child === $("credential-dialog") || child.hasAttribute("inert"));
      cancel.focus(); const tab = $("credential-dialog").dispatch("keydown", { key: "Tab" });
      out.tabFromCancelReachedLink = tab.defaultPrevented && doc.activeElement === link;
      const shift = $("credential-dialog").dispatch("keydown", { key: "Tab", shiftKey: true });
      out.shiftTabFromLinkReachedCancel = shift.defaultPrevented && doc.activeElement === cancel;
      out.activeDialogLinkAllowed = !link.dispatch("click").defaultPrevented;
      cancel.click();
    } else if (SCENARIO === "canvas_origin") {
      $("canvas-origin").value = "http://localhost/";
      $("canvas-actions").querySelector("button").click(); await flush();
      out.invalid = $("canvas-origin").getAttribute("aria-invalid"); out.focus = doc.activeElement.id;
      out.dialogHidden = $("credential-dialog").hidden; out.message = $("canvas-origin-error").textContent;
    } else {
      $("credential-cards").querySelectorAll("button").find((b) => ["Configure", "Replace credential"].includes(b.textContent)).click(); await flush();
      out.focusOnOpen = doc.activeElement.id;
      out.dialogHelpBeforePost = $("credential-dialog-help").textContent;
      out.dialogTextBeforePost = visibleText($("credential-dialog"));
      $("credential-secret").value = SECRET;
      $(SCENARIO === "credential_cancel" ? "credential-cancel" : "credential-confirm").click(); await flush();
      out.secretValue = $("credential-secret").value; out.dialogHidden = $("credential-dialog").hidden;
      out.notice = $("notice").textContent; out.secretInUrl = calls.some((c) => c.url.includes(SECRET));
      out.secretPosts = calls.filter((c) => c.body && c.body.secret === SECRET).length;
      out.secretInText = doc.root.textContent.includes(SECRET);
      out.visibleTextAfterCredential = visibleText(doc.root);
      out.credentialState = $("credential-status-" + CREDENTIAL_ROLE).textContent;
      out.credentialMessage = $("credential-message-" + CREDENTIAL_ROLE) ? $("credential-message-" + CREDENTIAL_ROLE).textContent : "";
      out.credentialReadbackFailed = calls.some((c) => c.url === "/api/v1/credentials") && failCredentialReadback;
      out.canvasProfileMissing = Boolean(lastCanvasSnapshot && !lastCanvasSnapshot.profile);
      if (["credential_failure", "credential_failure_unconfigured_worker", "credential_success", "credential_replace_failure"].includes(SCENARIO)) {
        out.focusedAfterCredential = doc.activeElement ? doc.activeElement.id : null;
        out.credentialStatus = $("credential-status-" + CREDENTIAL_ROLE).textContent;
        const retryButton = $("credential-cards").querySelectorAll("button").find(b => ["Try again", "Retry replacement"].includes(b.textContent));
        out.retryLabel = retryButton ? retryButton.textContent : null;
        if (retryButton) {
          retryButton.click();
          out.focusOnRetry = doc.activeElement ? doc.activeElement.id : null;
          out.secretEmptyOnRetry = $("credential-secret").value === "";
          $("credential-cancel").click();
        }
      }
    }
  } else if (SCENARIO === "replaced" || SCENARIO === "unreachable" || SCENARIO === "expired") {
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
