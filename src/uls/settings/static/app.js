/* Syllva Local Settings shell (GUI-1).
 * The CSRF value lives only in the closure variable state.csrf. It is never
 * written to a URL, cookie, Web Storage, IndexedDB, or the developer log. Secret inputs
 * (type=password or data-secret) are cleared whenever the session ends.
 */
(function () {
  "use strict";

  var IDLE_WARNING_SECONDS = 60;
  var POLL_MS = 30000;
  var ENDED_TEXT = {
    SESSION_REPLACED: {
      heading: "Settings session moved",
      message: "A new Settings window was opened. This window can no longer save.",
      next: "Use the new Settings window to continue."
    },
    SESSION_UNREACHABLE: {
      heading: "Settings session closed",
      message: "This window can no longer connect to Syllva Settings or save changes.",
      next: "Run uls setup to open Settings again."
    },
    SESSION_EXPIRED: {
      heading: "Settings session expired",
      message: "For your security, this local setup session ended after inactivity. Saved configuration was not changed.",
      next: "Run uls setup to open Syllva Settings again. Unsaved entries stay in this tab until it is closed or reloaded."
    },
    SESSION_CLOSED: {
      heading: "Settings session ended",
      message: "You closed Settings. Saved configuration was not changed by closing.",
      next: "Run uls setup to open Settings again."
    }
  };
  var FIELD_KIND = {
    "retrieval.max_candidate_entities": "int",
    "retrieval.max_candidate_chunks": "int",
    "retrieval.context_ttl_seconds": "int",
    "retrieval.resolution_ttl_seconds": "int",
    "retrieval.max_evidence_items": "int",
    "retrieval.max_chars_per_item": "int",
    "retrieval.max_total_chars": "int",
    "retrieval.max_followup_chunks": "int",
    "normalization.goodnotes_visual_fallback": "bool"
  };
  // Responses that prove this tab no longer talks to its own session.
  var ENDING_CODES = {
    SESSION_REPLACED: "SESSION_REPLACED",
    SESSION_EXPIRED: "SESSION_EXPIRED",
    SESSION_NOT_FOUND: "SESSION_UNREACHABLE",
    CSRF_REJECTED: "SESSION_UNREACHABLE",
    HOST_REJECTED: "SESSION_UNREACHABLE"
  };

  var state = {
    csrf: null,
    ended: null,
    idleSeconds: 900,
    lastActivity: Date.now(),
    generations: {},
    baselines: {},
    steps: [],
    stepOverrides: {},
    currentStep: null,
    timers: [],
    guard: false,
    reviewed: {},
    busy: {},
    editEpoch: {}
  };

  function el(id) { return document.getElementById(id); }
  function all(root, selector) { return Array.prototype.slice.call(root.querySelectorAll(selector)); }
  function make(tag, text) {
    var node = document.createElement(tag);
    if (text !== undefined && text !== null) { node.textContent = String(text); }
    return node;
  }
  function notice(text) { el("notice").textContent = text || ""; }
  function explicitActivity() { state.lastActivity = Date.now(); hideIdleWarning(); }

  async function api(path, options) {
    options = options || {};
    if (state.ended) { return { ok: false, code: state.ended }; }
    var headers = { "Accept": "application/json" };
    if (options.mutation) {
      if (!state.csrf) { return { ok: false, code: "SESSION_EXPIRED" }; }
      headers["X-ULS-CSRF"] = state.csrf;
      headers["Content-Type"] = "application/json";
    }
    var response;
    try {
      response = await fetch(path, {
        method: options.method || "GET",
        headers: headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: "same-origin",
        cache: "no-store",
        redirect: "error"
      });
    } catch (_error) {
      endSession("SESSION_UNREACHABLE");
      return { ok: false, code: "SESSION_UNREACHABLE" };
    }
    var data = null;
    try { data = await response.json(); } catch (_error) { data = null; }
    var code = data && data.error ? data.error.code : null;
    if ((code && ENDING_CODES[code]) || response.status === 401) {
      var ending = (code && ENDING_CODES[code]) || "SESSION_UNREACHABLE";
      endSession(ending);
      return { ok: false, code: ending };
    }
    if (!response.ok) {
      return {
        ok: false,
        code: code || "REQUEST_FAILED",
        message: data && data.error ? data.error.message : null,
        fields: data && data.error && Array.isArray(data.error.fields) ? data.error.fields : []
      };
    }
    return { ok: true, data: data };
  }

  function isSecret(input) {
    return input.type === "password" || input.hasAttribute("data-secret");
  }

  function formInputs() {
    var inputs = [];
    all(document, "form").forEach(function (form) {
      all(form, "input").forEach(function (input) { inputs.push({ form: form, input: input }); });
    });
    return inputs;
  }

  function labelFor(input) {
    var found = all(document, "label").filter(function (label) { return label.getAttribute("for") === input.id; });
    if (!found.length) { return input.name; }
    return found[0].textContent.replace(/\s+/g, " ").trim();
  }

  function dirtyEntries() {
    return formInputs().filter(function (item) {
      var group = item.form.getAttribute("data-group");
      var baseline = state.baselines[group] || {};
      return !isSecret(item.input) && item.input.name && baseline[item.input.name] !== undefined &&
        item.input.value !== baseline[item.input.name];
    }).map(function (item) { return { label: labelFor(item.input), value: item.input.value }; });
  }

  function beforeUnload(event) {
    event.preventDefault();
    event.returnValue = "";
    return "";
  }

  function endSession(code) {
    if (state.ended) { return; }
    state.ended = code;
    state.csrf = null;
    state.timers.forEach(function (timer) { clearInterval(timer); });
    state.timers = [];
    hideIdleWarning();
    formInputs().forEach(function (item) {
      if (isSecret(item.input)) { item.input.value = ""; }
      item.input.readOnly = true;
      item.input.setAttribute("aria-readonly", "true");
    });
    hideCloseConfirm();
    notice("");
    // Disable every control that could call the API or change step state,
    // then make the working area inert. Drafts stay copyable in the alert.
    all(document, "button").forEach(function (button) {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });
    all(document, "form").forEach(function (form) { form.setAttribute("aria-disabled", "true"); });
    ["setup-stepper", "overview-panel", "general-panel", "advanced-panel", "notice"].forEach(function (id) {
      var region = el(id);
      if (region) {
        region.setAttribute("inert", "");
        region.setAttribute("aria-disabled", "true");
        region.className = (region.className ? region.className + " " : "") + "ended-inert";
      }
    });
    var text = ENDED_TEXT[code] || ENDED_TEXT.SESSION_UNREACHABLE;
    var dirty = dirtyEntries();
    el("connection-heading").textContent = text.heading;
    el("connection-message").textContent = text.message;
    el("connection-next").textContent = text.next;
    var list = el("draft-list");
    list.replaceChildren();
    if (dirty.length) {
      dirty.forEach(function (entry) {
        list.appendChild(make("dt", entry.label));
        list.appendChild(make("dd", entry.value));
      });
      el("draft-help").textContent = "Unsaved non-secret entries are kept below for reference or copying. They are read-only and are not saved anywhere.";
    } else {
      el("draft-help").textContent = "There were no unsaved entries in this window.";
    }
    el("ordinary-header").hidden = true;
    el("connection-state").hidden = false;
    if (dirty.length && code !== "SESSION_CLOSED") {
      window.addEventListener("beforeunload", beforeUnload);
      state.guard = true;
    }
    el("connection-heading").focus();
  }

  function hideIdleWarning() { var box = el("idle-warning"); if (box) { box.hidden = true; } }

  function checkIdle() {
    if (state.ended) { return; }
    var idleFor = (Date.now() - state.lastActivity) / 1000;
    if (idleFor >= state.idleSeconds) {
      endSession("SESSION_EXPIRED");
    } else if (idleFor >= state.idleSeconds - IDLE_WARNING_SECONDS && el("idle-warning").hidden) {
      el("idle-warning").hidden = false;
      el("idle-warning-heading").focus();
    }
  }

  function toFieldValue(name, raw) {
    var kind = FIELD_KIND[name];
    var text = raw.trim();
    if (kind === "int" && /^[0-9]{1,9}$/.test(text)) { return Number(text); }
    if (kind === "bool" && (text === "true" || text === "false")) { return text === "true"; }
    return text;
  }

  function renderSteps() {
    var list = el("setup-steps");
    list.replaceChildren();
    state.steps.forEach(function (step, index) {
      var shown = state.stepOverrides[step.name] || step.state;
      var item = make("li");
      var button = make("button");
      button.type = "button";
      button.setAttribute("data-step", String(index));
      if (index === state.currentStep) { button.setAttribute("aria-current", "step"); }
      button.appendChild(make("span", (index + 1) + " " + step.name));
      button.appendChild(make("small", shown));
      button.addEventListener("click", function () { selectStep(index); });
      item.appendChild(button);
      list.appendChild(item);
    });
    var current = state.steps[state.currentStep];
    el("step-detail").textContent = current ? current.name + ": " + (current.reason || "") : "";
  }

  function selectStep(index) {
    if (state.ended) { return; }
    if (state.currentStep !== null && index < state.currentStep) {
      // Back-navigation keeps every entered value; later steps depend on this
      // one, so they show Partial until the durable predicates are rechecked.
      state.steps.slice(index + 1).forEach(function (step) { state.stepOverrides[step.name] = "Partial"; });
      notice("Later steps are marked Partial until they are checked again. Nothing was cleared.");
    }
    state.currentStep = index;
    renderSteps();
  }

  function renderDl(target, entries) {
    target.replaceChildren();
    entries.forEach(function (pair) {
      target.appendChild(make("dt", pair[0]));
      target.appendChild(make("dd", pair[1]));
    });
  }

  var READINESS_LABELS = {
    source_archival: "Source files saved",
    text_extraction: "Text extraction",
    retrieval_credentials: "Retrieval credentials",
    ai_client: "AI client"
  };

  function humanize(value) {
    var text = String(value).replace(/_/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

  function renderReadiness(funnel) {
    // *_note keys describe their parent item; they are never shown as rows.
    var target = el("readiness-list");
    target.replaceChildren();
    Object.keys(funnel).filter(function (key) { return !/_note$/.test(key); }).sort().forEach(function (key) {
      target.appendChild(make("dt", READINESS_LABELS[key] || humanize(key)));
      var value = make("dd", humanize(funnel[key]));
      var note = funnel[key + "_note"];
      if (note) {
        var small = make("small", humanize(note));
        small.className = "readiness-note";
        value.appendChild(small);
      }
      target.appendChild(value);
    });
  }

  function renderOverview(data) {
    // Overall Ready only when every setup predicate is proven.
    el("overall-status").textContent = data.setup_ready === true ? "Ready" : "Partial";
    renderReadiness(data.readiness_funnel || {});
    renderDl(el("service-list"), [
      ["Local runtime", data.local_runtime_healthy ? "Healthy" : "Needs attention"],
      ["Automation", data.worker_enabled ? "Enabled" : "Disabled"],
      ["Remote MCP", data.remote_enabled ? "Enabled" : "Disabled"],
      ["Local checks", data.doctor && data.doctor.status === "ok" ? "Passed" : "Needs attention"]
    ]);
    state.steps = data.setup_steps || [];
    if (state.currentStep === null) {
      var firstOpen = state.steps.findIndex(function (step) { return step.state !== "Ready"; });
      state.currentStep = firstOpen === -1 ? Math.max(state.steps.length - 1, 0) : firstOpen;
    }
    renderSteps();
    renderRecovery(data.pending_operations || []);
    if (data.session_notice === "replaced_previous") {
      el("replaced-notice").hidden = false;
    }
  }

  function renderRecovery(items) {
    var box = el("recovery-list");
    box.replaceChildren();
    if (!items.length) { box.appendChild(make("p", "No pending recovery items.")); return; }
    items.forEach(function (item) {
      var card = make("article");
      card.className = "recovery-card";
      var title = make("h3", "Unfinished settings change");
      title.appendChild(make("span", " Partial"));
      card.appendChild(title);
      card.appendChild(make("p", item.fields && item.fields.length ? "Settings: " + item.fields.join(", ") : "Settings change"));
      if (item.same_target === false) {
        card.appendChild(make("p", "This item belongs to another settings file. Open Settings for that file to resolve it."));
        box.appendChild(card);
        return;
      }
      card.appendChild(make("p", "Resume repair checks the settings file and finishes the change only if it already reached disk. It never saves values again."));
      [["resume", "Resume repair"], ["leave", "Leave as-is"]].forEach(function (choice) {
        var button = make("button", choice[1]);
        button.type = "button";
        button.setAttribute("data-mutation", "");
        button.disabled = !state.csrf;
        button.addEventListener("click", function () { recover(item.operation_id, choice[0]); });
        card.appendChild(button);
      });
      box.appendChild(card);
    });
  }

  async function recover(operationId, action) {
    explicitActivity();
    var result = await api("api/v1/recovery/" + encodeURIComponent(operationId) + "/" + action, { method: "POST", mutation: true, body: {} });
    if (state.ended) { return; }
    notice(result.ok ? "Recovery item updated." : (result.message || "This recovery item could not be updated."));
    await loadOverview();
  }

  async function loadOverview() {
    var result = await api("api/v1/overview");
    if (result.ok) { renderOverview(result.data); }
    else if (!state.ended) { notice("The overview could not be loaded. Saved settings were not changed."); }
  }

  async function loadGroup(group, keepDirty) {
    var result = await api("api/v1/settings/" + group);
    if (!result.ok) { return false; }
    var form = el(group + "-form");
    var oldBaseline = state.baselines[group] || {};
    var baseline = {};
    Object.keys(result.data.values).forEach(function (name) { baseline[name] = String(result.data.values[name]); });
    all(form, "input").forEach(function (input) {
      if (baseline[input.name] === undefined) { return; }
      var userChanged = keepDirty && oldBaseline[input.name] !== undefined && input.value !== oldBaseline[input.name];
      if (!userChanged) { input.value = baseline[input.name]; }
    });
    state.baselines[group] = baseline;
    state.generations[group] = result.data.generation;
    return true;
  }

  function changedValues(group) {
    var form = el(group + "-form");
    var baseline = state.baselines[group] || {};
    var values = {};
    all(form, "input").forEach(function (input) {
      if (!isSecret(input) && input.name && input.value !== baseline[input.name]) {
        values[input.name] = toFieldValue(input.name, input.value);
      }
    });
    return values;
  }

  async function reviewGroup(group) {
    if (state.busy[group]) { return; }
    explicitActivity();
    var values = changedValues(group);
    if (!Object.keys(values).length) { notice("There are no changes to review."); return; }
    var generation = state.generations[group];
    var epoch = state.editEpoch[group] || 0;
    setBusy(group, true, "Checking the change…");
    var result = await api("api/v1/settings/" + group + "/validate", {
      method: "POST", mutation: true, body: { values: values, generation: generation }
    });
    setBusy(group, false);
    if (state.ended) { return; }
    if ((state.editEpoch[group] || 0) !== epoch) {
      // An entry changed while this check ran; never publish a stale review.
      notice("An entry changed while it was being checked. Review it again.");
      return;
    }
    if (!result.ok) { return handleSaveError(group, result); }
    clearFieldErrors(group);
    // Keep exactly what the server reviewed; Apply sends only this.
    state.reviewed[group] = {
      values: result.data.values, generation: result.data.generation, candidateHash: result.data.candidate_hash
    };
    var preview = el(group + "-preview");
    var list = preview.querySelector("ul");
    list.replaceChildren();
    result.data.diff.forEach(function (change) {
      list.appendChild(make("li", change.field + ": " + String(change.from) + " → " + String(change.to)));
    });
    preview.hidden = false;
    notice("Review ready. Check the change, then apply it.");
  }

  async function applyGroup(group) {
    if (state.busy[group]) { return; }
    var reviewed = state.reviewed[group];
    if (!reviewed) { el(group + "-preview").hidden = true; notice("Review the change before applying it."); return; }
    explicitActivity();
    setBusy(group, true, "Saving the reviewed change…");
    var result = await api("api/v1/settings/" + group + "/apply", {
      method: "POST", mutation: true,
      body: { values: reviewed.values, generation: reviewed.generation, candidate_hash: reviewed.candidateHash }
    });
    setBusy(group, false);
    delete state.reviewed[group];
    if (state.ended) { return; }
    if (!result.ok) { return handleSaveError(group, result); }
    clearFieldErrors(group);
    el(group + "-preview").hidden = true;
    await loadGroup(group, false);
    notice("Saved. The settings file now contains the reviewed values.");
    await loadOverview();
  }

  function setBusy(group, busy, message) {
    state.busy[group] = busy;
    var form = el(group + "-form");
    form.setAttribute("aria-busy", busy ? "true" : "false");
    var controls = all(form, "button").concat(all(el(group + "-preview"), "button"));
    controls.forEach(function (button) { button.disabled = busy || Boolean(state.ended); });
    if (busy) { notice(message); }
  }

  function invalidateReview(group) {
    if (!state.reviewed[group] && el(group + "-preview").hidden) { return; }
    delete state.reviewed[group];
    el(group + "-preview").hidden = true;
    notice("The entry changed after review. Review it again before applying.");
  }

  async function handleSaveError(group, result) {
    el(group + "-preview").hidden = true;
    delete state.reviewed[group];
    if (result.code === "CONFIGURATION_CHANGED") {
      await loadGroup(group, true);
      notice("Settings changed elsewhere. Your entries are kept. Review them against the saved values and apply again.");
      return;
    }
    if (result.code === "REVIEW_STALE" || result.code === "REVIEW_REQUIRED") {
      notice("The entries differ from the reviewed change. Review again before applying.");
      return;
    }
    if (result.fields && result.fields.length) {
      clearFieldErrors(group);
      var shown = result.fields.filter(function (item) {
        return showFieldError(group, item.field, item.message || "Check this value.");
      });
      if (shown.length) {
        // Focus the first invalid field in visual (DOM) order.
        var firstInvalid = all(el(group + "-form"), "input").filter(function (input) {
          return input.getAttribute("aria-invalid") === "true";
        })[0];
        if (firstInvalid) { firstInvalid.focus(); }
        notice(shown.length === 1 ? "One setting needs attention. It is marked below."
          : shown.length + " settings need attention. Each one is marked below.");
        return;
      }
    }
    notice(result.message || "This change could not be saved. Your entries are kept.");
  }

  function fieldInput(group, name) {
    var form = el(group + "-form");
    return form ? all(form, "input").filter(function (input) { return input.name === name; })[0] || null : null;
  }

  function clearFieldError(input) {
    if (input.getAttribute("aria-invalid") !== "true") { return; }
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
    var message = el(input.id + "-error");
    if (message && message.parentNode) { message.parentNode.removeChild(message); }
  }

  function clearFieldErrors(group) {
    var form = el(group + "-form");
    if (form) { all(form, "input").forEach(clearFieldError); }
  }

  function showFieldError(group, name, text) {
    var input = fieldInput(group, name);
    if (!input || !input.id) { return false; }
    clearFieldError(input);
    var message = make("p", "Error: " + text);
    message.id = input.id + "-error";
    message.className = "field-error";
    input.parentNode.insertBefore(message, input.nextSibling);
    input.setAttribute("aria-invalid", "true");
    input.setAttribute("aria-describedby", message.id);
    return true;
  }

  function showPanel(name) {
    if (state.ended) { return; }
    ["overview", "general", "advanced"].forEach(function (panel) {
      el(panel + "-panel").hidden = panel !== name;
    });
    if (name !== "overview" && !state.baselines[name]) { loadGroup(name, false); }
    var heading = el(name + "-panel").querySelector("h2");
    if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus(); }
  }

  async function keepAlive() {
    explicitActivity();
    var result = await api("api/v1/session/keepalive", { method: "POST", mutation: true, body: {} });
    if (result.ok) { notice("This local session stays open."); }
  }

  function hideCloseConfirm() {
    var box = el("close-confirm");
    if (box) { box.hidden = true; }
  }

  function requestClose() {
    if (state.ended) { return; }
    if (!dirtyEntries().length) { closeSettings(); return; }
    // Unsaved non-secret entries: ask first. Cancel keeps the session.
    el("close-confirm").hidden = false;
    el("close-confirm-heading").focus();
  }

  function cancelClose() {
    hideCloseConfirm();
    el("close-session").focus();
  }

  async function closeSettings() {
    hideCloseConfirm();
    var result = await api("api/v1/session/close", { method: "POST", mutation: true, body: {} });
    if (result.ok || result.code === "SESSION_UNREACHABLE") {
      state.ended = null;
      endSession("SESSION_CLOSED");
    }
  }

  function bind() {
    all(document, "[data-panel]").forEach(function (button) {
      button.addEventListener("click", function () { showPanel(button.getAttribute("data-panel")); });
    });
    all(document, "form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!state.ended) { reviewGroup(form.getAttribute("data-group")); }
      });
    });
    all(document, "[data-apply-group]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!state.ended) { applyGroup(button.getAttribute("data-apply-group")); }
      });
    });
    all(document, "[data-cancel-preview]").forEach(function (button) {
      button.addEventListener("click", function () {
        var group = button.getAttribute("data-cancel-preview");
        delete state.reviewed[group];
        el(group + "-preview").hidden = true;
      });
    });
    el("keepalive").addEventListener("click", keepAlive);
    el("idle-stay").addEventListener("click", keepAlive);
    el("close-session").addEventListener("click", requestClose);
    el("idle-close").addEventListener("click", requestClose);
    el("close-confirm-accept").addEventListener("click", closeSettings);
    el("close-confirm-cancel").addEventListener("click", cancelClose);
    formInputs().forEach(function (item) {
      item.input.addEventListener("input", function () {
        var group = item.form.getAttribute("data-group");
        state.editEpoch[group] = (state.editEpoch[group] || 0) + 1;
        clearFieldError(item.input);
        invalidateReview(group);
      });
    });
    el("recheck-steps").addEventListener("click", function () {
      if (state.ended) { return; }
      state.stepOverrides = {};
      loadOverview();
    });
  }

  async function start() {
    bind();
    var session = await api("api/v1/session/csrf");
    if (!session.ok) {
      if (!state.ended) { endSession(session.code === "SESSION_REPLACED" ? "SESSION_REPLACED" : "SESSION_EXPIRED"); }
      return;
    }
    state.csrf = session.data.csrf_token;
    state.idleSeconds = session.data.idle_seconds || state.idleSeconds;
    state.lastActivity = Date.now();
    all(document, "[data-mutation]").forEach(function (button) { button.disabled = false; });
    await loadOverview();
    state.timers.push(setInterval(checkIdle, 5000));
    state.timers.push(setInterval(function () { if (!state.ended) { loadOverview(); } }, POLL_MS));
  }

  start();
})();
