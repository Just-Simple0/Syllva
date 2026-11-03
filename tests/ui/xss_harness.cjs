// C-18 harness (spec §10.5, design §5.4a): runs the real v2 UI (index.html + app.js) in jsdom
// against a scripted backend whose every string is hostile, then reports what the DOM contains.
// Used only by tests/contract/v2/test_v2_ui_xss.py. Never bundled.
"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const STATIC = process.argv[2];
const PAYLOADS = JSON.parse(process.argv[3]);
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8").replace(/<script[^>]*><\/script>/g, "");
const dom = new JSDOM(html, { url: "http://syllva.localhost/bootstrap#token123", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
window.__xss = [];
window.alert = () => window.__xss.push("alert");
const p = (i) => PAYLOADS[i % PAYLOADS.length];
const items = PAYLOADS.map((_, i) => ({
  material_id: "NET-M0" + (i % 9 + 1), title: p(i), course_key: p(i + 1), course_name: p(i + 2), kind: p(i + 3),
  processing_status: "READY", verified: false, session_id: p(i + 4),
}));
const candidates = PAYLOADS.map((_, i) => ({
  candidate_id: "INT-" + i, state: "NEEDS_REVIEW", name: p(i), reason: p(i + 1), blockers: [p(i + 2)],
  suggested_kind: p(i + 3), material_id: null,
}));
const evidence = { material_id: "NET-M01", warnings: [{ code: p(0) }],
  evidence: PAYLOADS.map((x, i) => ({ locator: p(i + 1), part: 1, parts: 1, text: x })) };
const routes = {
  "POST /api/session": { csrf: "c", window_label: "main" },
  "GET /api/session": { window_label: "main" },
  "GET /api/library/items": { items },
  "GET /api/intake/candidates": { candidates },
  "GET /api/library/NET-M01/context": evidence,
  "GET /api/settings/canvas": { connected: true, needs_token: false, profile_id: "c" + "0".repeat(32), origin: p(5),
    display_name: p(1), token_set_at: p(2), file_hosts: [p(3)], courses: [] },
  "GET /api/activities": { activities: PAYLOADS.map((_, i) => ({ activity_id: "NET-A0" + (i % 9 + 1), title: p(i),
    due_at: p(i + 1), submission_status: p(i + 2), origin: "CANVAS", course_key: p(i + 3), course_name: p(i + 4),
    canvas_active: i % 2 === 0 })) },
  "GET /api/canvas/status": { running: false, error: p(6), courses: PAYLOADS.map((_, i) => ({ canvas_course_id: "1",
    name: p(i), course_key: p(i + 1), last: { status: p(i + 2), reason: p(i + 3), started_at: p(i), finished_at: null } })) },
  "GET /api/canvas/courses": { courses: PAYLOADS.map((_, i) => ({ canvas_course_id: String(100 + i), name: p(i),
    code: p(i + 1), term_name: p(i + 2), linked_course_key: p(i + 3) })) },
};
window.fetch = async (url, init) => {
  const key = ((init && init.method) || "GET") + " " + String(url).split("?")[0];
  const body = routes[key];
  return { ok: body !== undefined, status: body ? 200 : 404, json: async () => body || { error: "NOT_FOUND" } };
};
window.eval(fs.readFileSync(path.join(STATIC, "app.js"), "utf8"));
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));
window.dispatchEvent(new window.Event("DOMContentLoaded"));
setTimeout(async () => {
  const doc = window.document;
  const open = doc.querySelector('[data-material="NET-M01"]');
  if (open) { open.click(); }
  const load = doc.getElementById("canvas-load");
  if (load) { load.click(); }
  await new Promise((r) => setTimeout(r, 50));
  const tags = new Set([...doc.querySelectorAll("*")].map((e) => e.tagName.toLowerCase()));
  const attrs = [];
  for (const el of doc.querySelectorAll("*")) {
    for (const a of el.attributes) { attrs.push(a.name + "=" + a.value); }
  }
  process.stdout.write(JSON.stringify({
    tags: [...tags].sort(), attrs, text: doc.body.textContent, xss: window.__xss, hash: window.location.hash,
  }));
}, 100);
