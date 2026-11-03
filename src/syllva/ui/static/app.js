// Syllva v2 UI bootstrap (implementation spec §10.2, design §5.4a).
// Rules: text only via textContent, no HTML sinks, no eval, no web storage; the CSRF token lives in memory only.
"use strict";

const state = { csrf: null, window: null };

function show(text) {
  const status = document.getElementById("status");
  status.textContent = text;
}

async function exchange(token) {
  const response = await fetch("/api/session", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bootstrap: token }),
    credentials: "same-origin",
  });
  if (!response.ok) {
    throw new Error("session refused");
  }
  const body = await response.json();
  state.csrf = body.csrf;
  state.window = body.window_label;
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
  const check = await fetch("/api/session", { credentials: "same-origin" });
  show(check.ok ? "준비됨" : "세션이 없습니다. 앱을 다시 시작하세요.");
}

window.addEventListener("DOMContentLoaded", start);
