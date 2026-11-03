// Syllva v2 UI (implementation spec §10, design §5.4a).
// Rules: every AI/SOURCE/USER string is inserted with textContent; elements are created with
// createElement only; no HTML sinks, no eval, no web storage; the CSRF token lives in memory only.
"use strict";

const state = { csrf: null, window: null };

function el(tag, text, attrs) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  for (const [name, value] of Object.entries(attrs || {})) {
    // only fixed, inert attribute names; never event handlers, URLs or styles
    if (!/^(type|placeholder|data-[a-z-]+)$/.test(name)) {
      throw new Error("attribute not allowed: " + name);
    }
    node.setAttribute(name, String(value));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

function show(text) {
  document.getElementById("status").textContent = text;
}

async function api(method, path, body, extraHeaders) {
  const headers = Object.assign({}, extraHeaders || {});
  if (method !== "GET") {
    headers["X-Syllva-CSRF"] = state.csrf || "";
  }
  let payload;
  if (body instanceof Blob || body instanceof ArrayBuffer) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  const data = await response.json();
  return { ok: response.ok, status: response.status, data };
}

async function exchange(token) {
  const result = await api("POST", "/api/session", { bootstrap: token });
  if (!result.ok) {
    throw new Error("session refused");
  }
  state.csrf = result.data.csrf;
  state.window = result.data.window_label;
}

function renderReview(candidates) {
  const list = document.getElementById("review-list");
  clear(list);
  for (const c of candidates) {
    const item = el("li");
    item.appendChild(el("span", c.name));
    item.appendChild(el("small", " " + c.state + (c.reason ? " · " + c.reason : "")));
    if (Array.isArray(c.blockers) && c.blockers.length) {
      item.appendChild(el("small", " (" + c.blockers.join(", ") + ")"));
    }
    if (c.state === "NEEDS_REVIEW") {
      const form = el("form");
      const course = el("input", null, { type: "text", placeholder: "과목 키" });
      course.value = c.suggested_course_key || "";
      const kind = el("input", null, { type: "text", placeholder: "종류" });
      kind.value = c.suggested_kind || "";
      const held = el("input", null, { type: "text", placeholder: "새 세션 날짜 YYYY-MM-DD(선택)" });
      form.appendChild(course);
      form.appendChild(kind);
      form.appendChild(held);
      form.appendChild(el("button", "확인", { type: "submit" }));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const body = { course_key: course.value, kind: kind.value };
        if (held.value) {
          body.new_session_held_on = held.value;
        }
        const result = await api("POST", "/api/library/" + encodeURIComponent(c.candidate_id) + "/classify", body);
        show(result.ok ? "확인했습니다." : "확인할 수 없습니다: " + (result.data.message || result.data.error));
        await refresh();
      });
      item.appendChild(form);
    }
    if (c.state === "NEEDS_REVIEW" || c.state === "DUPLICATE" || c.state === "UNSUPPORTED") {
      const dismiss = el("button", "무시", { type: "button" });
      dismiss.addEventListener("click", async () => {
        await api("POST", "/api/library/" + encodeURIComponent(c.candidate_id) + "/dismiss");
        await refresh();
      });
      item.appendChild(dismiss);
    }
    list.appendChild(item);
  }
}

function renderLibrary(items) {
  const list = document.getElementById("library-list");
  clear(list);
  for (const m of items) {
    const item = el("li");
    const open = el("button", m.title, { type: "button", "data-material": m.material_id });
    open.addEventListener("click", () => openMaterial(m));
    item.appendChild(open);
    item.appendChild(el("small", " " + m.material_id + " · " + m.course_name + " · " + m.kind + " · " + m.processing_status +
      (m.verified ? " · 검증됨" : "")));
    list.appendChild(item);
  }
}

async function verify(material) {
  const begun = await api("POST", "/api/gate/begin",
    { action: "VERIFY_MATERIAL", target_id: material.material_id, params: { material_id: material.material_id } });
  if (!begun.ok) {
    show("확인을 시작할 수 없습니다: " + begun.data.error);
    return;
  }
  const tauri = window.__TAURI__;
  if (!tauri || !tauri.core || typeof tauri.core.invoke !== "function") {
    show("검증 표시는 데스크탑 앱의 확인 창에서만 할 수 있습니다.");
    return;
  }
  // The shell shows the Core-made summary in a native dialog; the webview passes only the nonce.
  const result = await tauri.core.invoke("confirm_gate", { nonceId: begun.data.nonce_id });
  const committed = Boolean(result && result.status === "COMMITTED");
  show(committed ? "검증됨으로 표시했습니다." : "표시하지 않았습니다.");
  await refresh();
  if (committed) {
    await openMaterial(Object.assign({}, material, { verified: true }));
  }
}

async function openMaterial(material) {
  const section = document.getElementById("material-section");
  section.hidden = false;
  document.getElementById("material-title").textContent = material.title;
  document.getElementById("material-meta").textContent =
    material.material_id + " · " + material.course_name + " · " + material.processing_status;
  const actions = document.getElementById("material-actions");
  clear(actions);
  if (!material.verified) {
    const button = el("button", "검증됨으로 표시", { type: "button" });
    button.addEventListener("click", () => verify(material));
    actions.appendChild(button);
  }
  const list = document.getElementById("evidence-list");
  clear(list);
  const result = await api("GET", "/api/library/" + encodeURIComponent(material.material_id) + "/context");
  if (!result.ok) {
    list.appendChild(el("li", "원문을 불러올 수 없습니다."));
    return;
  }
  for (const warning of result.data.warnings || []) {
    list.appendChild(el("li", "알림: " + warning.code));
  }
  for (const item of result.data.evidence || []) {
    const entry = el("li");
    entry.appendChild(el("small", item.locator + (item.parts > 1 ? " (" + item.part + "/" + item.parts + ")" : "")));
    entry.appendChild(el("pre", item.text));
    list.appendChild(entry);
  }
}

async function refresh() {
  const [library, review] = await Promise.all([api("GET", "/api/library/items"), api("GET", "/api/intake/candidates")]);
  if (library.ok) {
    renderLibrary(library.data.items || []);
  }
  if (review.ok) {
    renderReview(review.data.candidates || []);
  }
}

function bindUpload() {
  const form = document.getElementById("upload-form");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("upload-file");
    const file = input.files && input.files[0];
    if (!file) {
      return;
    }
    const course = document.getElementById("upload-course").value;
    const query = course ? "?course=" + encodeURIComponent(course) : "";
    const result = await api("POST", "/api/library/upload" + query, file,
      { "X-Syllva-Filename": encodeURIComponent(file.name), "Content-Type": "application/octet-stream" });
    show(result.ok ? "추가했습니다: " + result.data.state : "추가할 수 없습니다: " + result.data.error);
    await refresh();
  });
}

// --- Canvas settings (R3-1): the token is sent once and never shown again ---------------------

function renderCanvas(status) {
  const line = document.getElementById("canvas-status");
  const connected = document.getElementById("canvas-connected");
  if (status.connected) {
    line.textContent = "연결됨: " + status.display_name + " (" + status.origin + ")";
  } else if (status.needs_token) {
    line.textContent = "토큰을 다시 입력해야 합니다 (이전 연결이 끝나지 않았습니다).";
  } else {
    line.textContent = "연결되지 않음";
  }
  connected.hidden = !status.connected;
  if (status.connected) {
    document.getElementById("canvas-hosts").value = (status.file_hosts || []).join(", ");
  }
}

async function loadCanvas() {
  const result = await api("GET", "/api/settings/canvas");
  if (result.ok) {
    renderCanvas(result.data);
  }
}

function renderCanvasCourses(courses) {
  const list = document.getElementById("canvas-courses");
  clear(list);
  for (const course of courses) {
    const entry = el("li");
    entry.appendChild(el("span", course.name + " · " + course.code + " · " + course.term_name));
    const form = el("form");
    const key = el("input", null, { type: "text", placeholder: "과목 키" });
    key.value = course.linked_course_key || "";
    form.appendChild(key);
    form.appendChild(el("button", course.linked_course_key ? "연결 변경" : "연결", { type: "submit" }));
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = key.value.trim();
      const result = await api("PUT", "/api/canvas/courses/" + encodeURIComponent(course.canvas_course_id),
        { course_key: value === "" ? null : value });
      show(result.ok ? (value === "" ? "연결을 해제했습니다." : "과목을 연결했습니다.") : "연결할 수 없습니다: " + result.data.error);
    });
    entry.appendChild(form);
    list.appendChild(entry);
  }
}

const SUBMISSION_LABELS = { NOT_SUBMITTED: "제출 안 함", SUBMITTED: "제출함", LATE_SUBMITTED: "늦게 제출함", EXCUSED: "제출 면제" };

function dueLabel(due) {
  // the server stores local time with its offset; show the date and minutes as stored
  return due ? "마감 " + String(due).slice(0, 16).replace("T", " ") : "마감 없음";
}

function renderActivities(activities) {
  const list = document.getElementById("activity-list");
  clear(list);
  for (const a of activities) {
    const entry = el("li");
    entry.appendChild(el("span", a.course_name + " · " + a.title));
    entry.appendChild(el("small", " " + dueLabel(a.due_at) + " · " + (SUBMISSION_LABELS[a.submission_status] || a.submission_status)
      + (a.origin === "CANVAS" ? " · Canvas" : "") + (a.canvas_active ? "" : " · Canvas에서 사라짐")));
    list.appendChild(entry);
  }
}

function renderSyncStatus(status) {
  const list = document.getElementById("canvas-sync-status");
  clear(list);
  if (status.running) {
    list.appendChild(el("li", "동기화하는 중…"));
  }
  if (status.error) {
    list.appendChild(el("li", "동기화할 수 없습니다: " + status.error));
  }
  for (const course of status.courses || []) {
    const last = course.last;
    const text = last ? last.status + (last.reason ? " (" + last.reason + ")" : "") + " · " + (last.finished_at || last.started_at)
      : "아직 동기화하지 않음";
    list.appendChild(el("li", course.name + " → " + course.course_key + ": " + text));
  }
}

async function loadActivities() {
  const [activities, status] = await Promise.all([api("GET", "/api/activities"), api("GET", "/api/canvas/status")]);
  if (activities.ok) {
    renderActivities(activities.data.activities || []);
  }
  if (status.ok) {
    renderSyncStatus(status.data);
  }
  return status.ok && status.data.running;
}

function bindCanvas() {
  document.getElementById("canvas-sync").addEventListener("click", async () => {
    const result = await api("POST", "/api/canvas/sync");
    if (!result.ok) {
      show("동기화를 시작할 수 없습니다: " + result.data.error);
      return;
    }
    show("동기화를 시작했습니다.");
    for (let i = 0; i < 60 && await loadActivities(); i++) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  });
  document.getElementById("canvas-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const origin = document.getElementById("canvas-origin");
    const token = document.getElementById("canvas-token");
    const result = await api("PUT", "/api/settings/canvas/token", { origin: origin.value.trim(), token: token.value });
    token.value = "";  // never kept in the page
    if (result.ok) {
      renderCanvas(result.data);
      show("Canvas에 연결했습니다.");
    } else {
      show("연결할 수 없습니다: " + result.data.error);
      await loadCanvas();
    }
  });
  document.getElementById("canvas-hosts-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const hosts = document.getElementById("canvas-hosts").value.split(",").map((h) => h.trim()).filter((h) => h);
    const result = await api("PUT", "/api/settings/canvas/file-hosts", { hosts });
    show(result.ok ? "파일 호스트를 저장했습니다." : "저장할 수 없습니다: " + result.data.error);
  });
  document.getElementById("canvas-load").addEventListener("click", async () => {
    const result = await api("GET", "/api/canvas/courses");
    if (result.ok) {
      renderCanvasCourses(result.data.courses || []);
    } else {
      show("과목을 불러올 수 없습니다: " + result.data.error);
    }
  });
  document.getElementById("canvas-forget").addEventListener("click", async () => {
    const result = await api("DELETE", "/api/settings/canvas");
    show(result.ok ? "연결을 끊었습니다." : "연결을 끊을 수 없습니다: " + result.data.error);
    await loadCanvas();
  });
}

async function start() {
  const hash = location.hash;
  // Clear the one-time token from the address bar and history before anything else.
  history.replaceState(null, "", location.pathname);
  if (hash.length > 1) {
    try {
      await exchange(hash.slice(1));
    } catch (error) {
      show("세션을 열 수 없습니다. 앱을 다시 시작하세요.");
      return;
    }
  }
  const check = await api("GET", "/api/session");
  if (!check.ok) {
    show("세션이 없습니다. 앱을 다시 시작하세요.");
    return;
  }
  show("준비됨");
  bindUpload();
  bindCanvas();
  await Promise.all([refresh(), loadCanvas(), loadActivities()]);
}

window.addEventListener("DOMContentLoaded", start);
