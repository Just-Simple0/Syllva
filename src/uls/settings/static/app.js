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
  var connections = { generation: null, cards: [], canvas: null, dialog: null, returnFocus: null,
    busy: false, courses: [], selected: [], term: null, savedRegistry: null, registryReview: null,
    discoveryFresh: false, discoveryId: 0, discoveryRequest: 0, draftDirty: false,
    pendingTerm: null, epoch: 0, loadToken: 0, requestToken: 0, activeRequest: 0,
    submissionEpoch: 0, pendingFileRead: null, readingFile: false,
    credentialRetry: {}, credentialMessages: {} };

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
    Object.keys(options.headers || {}).forEach(function (key) { headers[key] = options.headers[key]; });
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
        body: options.body === undefined ? undefined : options.raw ? options.body : JSON.stringify(options.body),
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

  function modalOpen() {
    return Boolean((el("credential-dialog") && !el("credential-dialog").hidden) ||
      (el("canvas-term-dialog") && !el("canvas-term-dialog").hidden));
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
    var entries = formInputs().filter(function (item) {
      var group = item.form.getAttribute("data-group");
      var baseline = state.baselines[group] || {};
      return !isSecret(item.input) && item.input.name && baseline[item.input.name] !== undefined &&
        item.input.value !== baseline[item.input.name];
    }).map(function (item) { return { label: labelFor(item.input), value: item.input.value }; });
    if (el("canvas-origin") && el("canvas-origin").value && !(connections.canvas && connections.canvas.profile)) {
      entries.push({ label: "Canvas address", value: el("canvas-origin").value });
    }
    if (connections.draftDirty) {
      var term = connections.term || "not selected";
      var selection = connections.selected.length
        ? connections.selected.length + " course(s): " + connections.selected.join(", ")
        : "0 courses selected";
      entries.push({ label: "Unsaved Canvas course selection", value: "Term " + term + "; " + selection });
    }
    return entries;
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
    connections.submissionEpoch += 1;
    connections.pendingFileRead = null;
    connections.readingFile = false;
    connections.requestToken += 1;
    connections.activeRequest = 0;
    connections.busy = false;
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
    closeCredentialDialog(false);
    closeTermChangeDialog(false);
    ["setup-stepper", "overview-panel", "general-panel", "advanced-panel", "connections-panel", "notice"].forEach(function (id) {
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
    // A modal may have inerted every main child before the session ended.
    // Keep the ended alert itself selectable/focusable while isolating all
    // remaining work areas, regardless of the modal close path above.
    el("topbar").setAttribute("inert", "");
    var main = document.querySelector("main");
    var connectionState = el("connection-state");
    if (main) {
      Array.prototype.slice.call(main.children).forEach(function (region) {
        if (region === connectionState) {
          region.removeAttribute("inert");
          region.removeAttribute("aria-disabled");
        } else {
          region.setAttribute("inert", "");
        }
      });
    }
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
    if (state.ended || modalOpen()) { return; }
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
    if (el("fake-mode")) { el("fake-mode").hidden = !data.fake_mode; }
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
      (item.action_kind && item.action_kind.indexOf("credential_") === 0
        ? [["resume", "Resume repair"], ["restore", "Restore previous credential"], ["leave", "Clean staged copy"], ["retry_delete", "Retry local deletion"]]
        : [["resume", "Resume repair"], ["leave", "Leave as-is"]]).forEach(function (choice) {
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
    if (state.ended || modalOpen()) { return; }
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
    if (state.busy[group] || state.ended || modalOpen()) { return; }
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
    if (state.busy[group] || state.ended || modalOpen()) { return; }
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

  function clearCredentialFileError() {
    var input = el("credential-file"), message = el("credential-file-error");
    if (!input || !message) { return; }
    input.removeAttribute("aria-invalid"); input.removeAttribute("aria-describedby");
    message.hidden = true; message.textContent = "";
  }

  function showCredentialFileError(text) {
    var input = el("credential-file"), message = el("credential-file-error");
    input.setAttribute("aria-invalid", "true"); input.setAttribute("aria-describedby", "credential-file-error");
    message.textContent = "Error: " + text; message.hidden = false; input.focus();
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
    if (state.ended || modalOpen()) { return; }
    ["overview", "general", "advanced", "connections"].forEach(function (panel) {
      el(panel + "-panel").hidden = panel !== name;
    });
    if (name === "connections") { loadConnections(); }
    else if (name !== "overview" && !state.baselines[name]) { loadGroup(name, false); }
    var heading = el(name + "-panel").querySelector("h2");
    if (heading) { heading.setAttribute("tabindex", "-1"); heading.focus(); }
  }

  async function keepAlive() {
    if (state.ended || modalOpen()) { return; }
    explicitActivity();
    var result = await api("api/v1/session/keepalive", { method: "POST", mutation: true, body: {} });
    if (result.ok) { notice("This local session stays open."); }
  }

  function hideCloseConfirm() {
    var box = el("close-confirm");
    if (box) { box.hidden = true; }
  }

  function requestClose() {
    if (state.ended || modalOpen()) { return; }
    if (!dirtyEntries().length) { closeSettings(); return; }
    // Unsaved non-secret entries: ask first. Cancel keeps the session.
    el("close-confirm").hidden = false;
    el("close-confirm-heading").focus();
  }

  function cancelClose() {
    if (state.ended || modalOpen()) { return; }
    hideCloseConfirm();
    el("close-session").focus();
  }

  async function closeSettings() {
    if (state.ended || modalOpen()) { return; }
    hideCloseConfirm();
    var result = await api("api/v1/session/close", { method: "POST", mutation: true, body: {} });
    if (result.ok || result.code === "SESSION_UNREACHABLE") {
      state.ended = null;
      endSession("SESSION_CLOSED");
    }
  }

  function actionButton(parent, label, action, disabled) {
    var button = make("button", label);
    button.type = "button"; button.modelDisabled = Boolean(disabled);
    button.disabled = Boolean(button.modelDisabled || state.ended || connections.busy);
    button.addEventListener("click", function () { if (!state.ended && !connections.busy && !modalOpen()) { action(button); } });
    parent.appendChild(button); return button;
  }

  async function loadConnections() {
    var loadToken = ++connections.loadToken;
    var interactionEpoch = connections.epoch;
    var cards = await api("api/v1/credentials");
    if (loadToken !== connections.loadToken) { return null; }
    if (cards.ok) {
      connections.generation = cards.data.config_generation;
      connections.cards = cards.data.cards; renderCredentialCards();
    }
    var canvas = await api("api/v1/canvas");
    if (loadToken !== connections.loadToken || interactionEpoch !== connections.epoch || !canvas.ok) { return null; }
    var oldProfile = connections.canvas && connections.canvas.profile;
    var newProfile = canvas.data.profile;
    var profileChanged = Boolean(oldProfile && newProfile && oldProfile.id !== newProfile.id);
    if (!newProfile || profileChanged) {
      connections.epoch += 1; connections.discoveryRequest += 1;
      connections.discoveryFresh = false; connections.discoveryId += 1;
      connections.courses = []; connections.selected = []; connections.term = null;
      connections.savedRegistry = null; connections.registryReview = null; connections.draftDirty = false;
      el("canvas-courses").replaceChildren(); el("canvas-term").replaceChildren();
      el("canvas-registry-preview").querySelector("ul").replaceChildren();
      el("canvas-registry-preview").hidden = true; el("canvas-course-area").hidden = true;
      el("canvas-selection-count").textContent = "0 of 20 selected";
      el("canvas-origin").value = "";
    }
    connections.canvas = canvas.data;
    connections.generation = canvas.data.config_generation || canvas.data.generation || connections.generation;
    connections.savedRegistry = canvas.data.registry && Array.isArray(canvas.data.registry.courses)
      ? canvas.data.registry : null;
    if (newProfile && !connections.draftDirty) {
      connections.discoveryFresh = false; connections.registryReview = null;
      connections.courses = connections.savedRegistry ? connections.savedRegistry.courses.slice() : [];
      connections.selected = connections.courses.map(function (course) { return course.course_id; });
      connections.term = connections.savedRegistry && connections.savedRegistry.term_id || null;
      connections.discoveryId += 1;
      renderSavedRegistry();
    }
    renderCanvas();
    return canvas.data;
  }

  function renderSavedRegistry() {
    var registry = connections.savedRegistry;
    if (!registry || !registry.courses || !registry.courses.length) {
      connections.courses = []; connections.selected = []; connections.term = null;
      el("canvas-term").replaceChildren(); el("canvas-courses").replaceChildren();
      el("canvas-selection-count").textContent = "0 of 20 saved";
      el("canvas-course-area").hidden = true;
      return;
    }
    var select = el("canvas-term"); select.replaceChildren();
    var option = make("option", "Saved term " + registry.term_id); option.value = registry.term_id; select.appendChild(option);
    select.value = registry.term_id; select.disabled = true;
    el("canvas-course-area").hidden = false;
    el("canvas-course-status").textContent = "Saved selection. Find courses before making changes.";
    renderCourses();
  }

  function renderCredentialCards() {
    var list = el("credential-cards"); list.replaceChildren();
    connections.cards.forEach(function (card) {
      var box = make("article"); box.className = "summary-card";
      var label = (card.provider === "notion" ? "Notion" : "Google Drive") + " · " + (card.purpose === "mcp" ? "Read-only retrieval" : "Worker");
      var heading = make("h3", label); heading.id = "credential-heading-" + card.role; heading.setAttribute("tabindex", "-1");
      box.appendChild(heading);
      var status = make("p", connections.credentialMessages[card.role] || humanize(card.state));
      status.id = "credential-status-" + card.role; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); status.setAttribute("tabindex", "-1");
      box.appendChild(status);
      box.appendChild(make("p", "Takes effect on next " + card.takes_effect + "."));
      if (card.state === "unsupported_platform") {
        box.appendChild(make("p", "Not available on this computer: Syllva cannot store credentials securely here yet. Use " + card.environment_variable + " instead."));
      } else if (card.source === "environment" && card.state === "external" && card.can_test) {
        box.appendChild(make("p", "Provided by " + card.environment_variable + ". Remove it from the environment to stop using it."));
      }
      if (card.last_check) { box.appendChild(make("p", card.last_check.message || humanize(card.last_check.code))); }
      actionButton(box, "Test connection", function () { testConnection(card); }, !card.can_test);
      if (card.can_mutate) {
        var retryAction = connections.credentialRetry[card.role];
        var defaultAction = card.managed && card.can_test ? "replace" : "set";
        var credentialAction = retryAction || defaultAction;
        var credentialLabel = retryAction ? (retryAction === "replace" ? "Retry replacement" : "Try again")
          : defaultAction === "replace" ? "Replace credential" : "Configure";
        actionButton(box, credentialLabel, function (button) {
          openCredentialDialog({ card: card, label: label, action: credentialAction }, button);
        });
        if (card.managed && card.can_test) { actionButton(box, "Forget local credential", function (button) { openCredentialDialog({ card: card, label: label, action: "forget" }, button); }); }
        if (card.can_detach) { actionButton(box, "Stop using this credential", function (button) { openCredentialDialog({ card: card, label: label, action: "detach" }, button); }); }
      }
      list.appendChild(box);
    });
  }

  function dialogInert(on, activeDialog) {
    var main = document.querySelector("main");
    var regions = [el("topbar")].concat(main ? Array.prototype.slice.call(main.children) : []).filter(Boolean);
    if (on) {
      regions.forEach(function (region) {
        if (region === activeDialog) { region.removeAttribute("inert"); }
        else { region.setAttribute("inert", ""); }
      });
    } else if (!state.ended) {
      regions.forEach(function (region) { region.removeAttribute("inert"); });
    }
  }

  function syncDialogInert() {
    if (el("credential-dialog") && !el("credential-dialog").hidden) {
      dialogInert(true, el("credential-dialog"));
    } else if (el("canvas-term-dialog") && !el("canvas-term-dialog").hidden) {
      dialogInert(true, el("canvas-term-dialog"));
    } else {
      dialogInert(false);
    }
  }

  function closeCredentialDialog(returnFocus) {
    if (!el("credential-dialog")) { return; }
    connections.submissionEpoch += 1;
    connections.pendingFileRead = null;
    connections.readingFile = false;
    el("credential-secret").value = ""; el("credential-file").value = "";
    clearCredentialFileError();
    el("credential-dialog").hidden = true; syncDialogInert();
    connections.dialog = null;
    if (returnFocus && !state.ended && connections.returnFocus) { connections.returnFocus.focus(); }
    connections.returnFocus = null;
  }

  function cancelCredentialSubmission() {
    if (el("credential-dialog").hidden || !el("canvas-term-dialog").hidden) { return; }
    if (connections.readingFile) {
      connections.requestToken += 1;
      connections.activeRequest = 0;
      connections.pendingFileRead = null;
      connections.readingFile = false;
      connectionBusy(false);
    }
    if (!connections.busy) { closeCredentialDialog(true); }
  }

  function dialogKeydown(dialog, event, onEscape) {
    if (event.key === "Escape" && onEscape) { event.preventDefault(); onEscape(); return; }
    if (event.key !== "Tab") { return; }
    var controls = all(dialog, "input, select, button, a[href]").filter(function (control) {
      return !control.hidden && !control.disabled;
    });
    if (!controls.length) { event.preventDefault(); return; }
    var first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  function closeTermChangeDialog(returnFocus) {
    var dialog = el("canvas-term-dialog");
    if (!dialog || dialog.hidden || !el("credential-dialog").hidden) { return; }
    dialog.hidden = true; connections.pendingTerm = null;
    el("canvas-term").value = connections.term || "";
    syncDialogInert();
    if (returnFocus) { el("canvas-term").focus(); }
  }

  function openTermChangeDialog(nextTerm) {
    connections.pendingTerm = nextTerm;
    el("canvas-term").value = connections.term || "";
    el("canvas-term-dialog-help").textContent = "Changing the term clears " + connections.selected.length + " selected courses. You can keep the current term or confirm the change.";
    el("canvas-term-dialog").hidden = false;
    syncDialogInert();
    el("canvas-term-cancel").focus();
  }

  function confirmTermChange() {
    if (connections.pendingTerm === null || connections.busy || state.ended ||
        (el("credential-dialog") && !el("credential-dialog").hidden)) { return; }
    connections.term = connections.pendingTerm;
    connections.selected = []; connections.draftDirty = true;
    closeTermChangeDialog(false);
    renderCourses();
    el("canvas-term").focus();
  }

  function openCredentialDialog(context, button) {
    if (state.ended || connections.busy || modalOpen()) { return; }
    connections.dialog = context; connections.returnFocus = button;
    var deleting = context.action === "forget" || context.action === "detach";
    var google = context.card && context.card.provider === "google";
    var dialog = el("credential-dialog"); dialog.setAttribute("role", deleting ? "alertdialog" : "dialog");
    el("credential-dialog-heading").textContent = (context.action === "detach" ? "Stop using " : deleting ? "Forget " : "Configure ") + context.label;
    el("credential-dialog-help").textContent = context.canvas && context.action === "forget"
      ? "Verified Canvas account: " + (context.maskedAccount || "Verified Canvas account") + ". User ID: " + context.userId + ". Origin: " + context.origin + ". Stored in " + context.storageLabel + ". Forgetting removes the local token and access lease; imported history is kept."
      : context.canvas
      ? "In Canvas, open Account → Settings → Approved Integrations → New Access Token, then paste the token here."
      : context.action === "detach" ? "Syllva will stop using this external credential file. The file is kept unchanged."
      : deleting ? "Role: " + context.card.role + ". Stored in " + (context.card.storage_label || "the local credential store") + ". This removes the local credential and stops using it here. Imported history and provider content are kept."
      : "This field is write-only. Verification only reads provider metadata. A failed replacement keeps the previous credential.";
    el("credential-secret").hidden = deleting || google; el("credential-secret-label").hidden = deleting || google;
    el("credential-file").hidden = deleting || !google; el("credential-file-label").hidden = deleting || !google;
    el("canvas-token-link").hidden = !context.canvas || deleting;
    if (context.canvas && !deleting) { el("canvas-token-link").setAttribute("href", context.origin + "/profile/settings"); }
    el("credential-confirm").textContent = deleting ? (context.action === "detach" ? "Stop using" : "Forget local credential") : context.canvas ? "Verify account" : "Verify and save";
    el("credential-dialog-message").textContent = "";
    clearCredentialFileError();
    el("credential-secret").value = ""; el("credential-file").value = "";
    dialog.hidden = false; syncDialogInert();
    (deleting ? el("credential-cancel") : google ? el("credential-file") : el("credential-secret")).focus();
  }

  function syncCourseControls() {
    var unavailable = Boolean(!connections.discoveryFresh || connections.busy || state.ended);
    var selector = el("canvas-term");
    if (selector) { selector.disabled = unavailable; }
    all(el("canvas-courses"), "input").forEach(function (checkbox) {
      var selected = connections.selected.indexOf(checkbox.value) !== -1;
      checkbox.checked = selected;
      checkbox.disabled = unavailable || (!selected && connections.selected.length >= 20);
    });
    if (el("canvas-review")) {
      el("canvas-review").disabled = unavailable || !connections.discoveryId || connections.selected.length < 1 || connections.selected.length > 20;
    }
    var reviewValid = Boolean(connections.registryReview && connections.discoveryFresh &&
      connections.registryReview.discoveryId === connections.discoveryId && connections.selected.length > 0 && connections.selected.length <= 20);
    if (el("canvas-apply")) { el("canvas-apply").disabled = !reviewValid || connections.busy || state.ended; }
    if (el("canvas-edit")) { el("canvas-edit").disabled = !connections.registryReview || connections.busy || state.ended; }
  }

  function syncConnectionControls() {
    all(el("connections-panel"), "button").forEach(function (button) {
      if (button.modelDisabled !== undefined) {
        button.disabled = Boolean(button.modelDisabled || connections.busy || state.ended);
      }
    });
    if (el("canvas-origin")) { el("canvas-origin").disabled = Boolean(connections.busy || state.ended); }
    syncCourseControls();
    if (el("credential-confirm")) { el("credential-confirm").disabled = Boolean(connections.busy || state.ended); }
    if (el("credential-cancel")) { el("credential-cancel").disabled = Boolean(state.ended || (connections.busy && !connections.readingFile)); }
    if (el("credential-secret")) { el("credential-secret").disabled = Boolean(connections.busy || state.ended); }
    if (el("credential-file")) { el("credential-file").disabled = Boolean(connections.busy || state.ended); }
    if (el("canvas-term-cancel")) { el("canvas-term-cancel").disabled = Boolean(connections.busy || state.ended); }
    if (el("canvas-term-confirm")) { el("canvas-term-confirm").disabled = Boolean(connections.busy || state.ended); }
  }

  function connectionBusy(busy, message) {
    connections.busy = busy;
    el("connections-panel").setAttribute("aria-busy", busy ? "true" : "false");
    el("credential-dialog").setAttribute("aria-busy", busy ? "true" : "false");
    syncConnectionControls();
    notice(message || "");
  }

  function beginConnectionRequest(message) {
    var token = ++connections.requestToken;
    connections.activeRequest = token;
    connectionBusy(true, message);
    return token;
  }

  function finishConnectionRequest(token) {
    if (token !== connections.activeRequest) { return false; }
    connections.activeRequest = 0;
    connectionBusy(false);
    return true;
  }

  function ownsCredentialFileRead(context, submissionEpoch, requestToken, fileRead) {
    return submissionEpoch === connections.submissionEpoch && connections.dialog === context &&
      requestToken === connections.activeRequest && connections.pendingFileRead === fileRead && !state.ended;
  }

  async function refreshAfterCredential(result, options) {
    options = options || {};
    connections.epoch += 1; connections.discoveryRequest += 1; connections.discoveryFresh = false;
    connections.discoveryId += 1; connections.registryReview = null;
    el("canvas-registry-preview").hidden = true;
    if (result.ok && options.applyReadback) { connections.draftDirty = false; }
    ["general", "advanced"].forEach(function (group) { delete state.reviewed[group]; el(group + "-preview").hidden = true; });
    for (var group of ["general", "advanced"]) { if (state.baselines[group]) { await loadGroup(group, true); } }
    var canvasSnapshot = await loadConnections(); await loadOverview();
    syncCourseControls();
    if (options.focusCredentialRole && !state.ended) {
      var focusId = options.focusCredentialHeading ? "credential-heading-" : "credential-status-";
      var credentialTarget = el(focusId + options.focusCredentialRole);
      if (credentialTarget) { credentialTarget.focus(); }
    }
    if (result.ok && options.applyReadback) {
      notice(registryMatchesReadback(canvasSnapshot, options.expectedRegistry)
        ? options.successMessage
        : "The apply request succeeded, but the saved course selection could not be confirmed. Reopen Connections to check it.");
    } else if (result.ok) { notice(options.successMessage || result.data.message || "Saved locally. Running services use the credential when they next start."); }
    else { notice(result.message || "The change could not be completed. The previous credential is kept; inspect unfinished changes if shown."); }
  }

  function registryMatchesReadback(snapshot, expected) {
    var registry = snapshot && snapshot.registry;
    if (!expected || !registry || registry.term_id !== expected.term_id || !Array.isArray(registry.courses)) { return false; }
    var actual = registry.courses.map(function (course) { return course.course_id; }).sort();
    var wanted = expected.course_ids.slice().sort();
    return actual.length === wanted.length && actual.every(function (courseId, index) { return courseId === wanted[index]; });
  }

  function showCourseReadbackPending() {
    connections.epoch += 1;
    connections.discoveryRequest += 1;
    connections.discoveryId += 1;
    connections.discoveryFresh = false;
    connections.registryReview = null;
    connections.savedRegistry = null;
    connections.courses = []; connections.selected = []; connections.term = null;
    connections.draftDirty = false;
    el("canvas-registry-preview").hidden = true;
    el("canvas-term").replaceChildren(); el("canvas-term").disabled = true;
    el("canvas-courses").replaceChildren();
    el("canvas-courses").appendChild(make("p", "Saved courses are being reloaded from Settings."));
    el("canvas-course-area").hidden = false;
    el("canvas-course-status").textContent = "Confirming the saved course selection.";
    el("canvas-selection-count").textContent = "Checking saved selection…";
    el("canvas-review").disabled = true;
  }

  async function submitCredential() {
    var context = connections.dialog;
    if (!context || connections.busy || state.ended || el("credential-dialog").hidden || !el("canvas-term-dialog").hidden) { return; }
    explicitActivity();
    var secret = el("credential-secret").value;
    var file = el("credential-file").files && el("credential-file").files[0];
    var requestToken, submissionEpoch = ++connections.submissionEpoch;
    var readsGoogleFile = context.card && context.card.provider === "google" && (context.action === "set" || context.action === "replace");
    if (readsGoogleFile) {
      if (!file) { showCredentialFileError("Choose a service-account JSON file."); return; }
      if (file.size > 65536) {
        el("credential-file").value = "";
        showCredentialFileError("Key file is larger than 64 KB.");
        return;
      }
      clearCredentialFileError();
      requestToken = beginConnectionRequest("Reading and checking the credential file…");
      connections.readingFile = true;
      syncConnectionControls();
      var fileRead;
      try { fileRead = file.text(); }
      catch (_error) { fileRead = Promise.reject(new Error("file read failed")); }
      file = null;
      connections.pendingFileRead = fileRead;
      el("credential-file").value = "";
      try { secret = await fileRead; }
      catch (_error) {
        var ownsFailedRead = ownsCredentialFileRead(context, submissionEpoch, requestToken, fileRead);
        fileRead = null;
        if (ownsFailedRead) {
          connections.pendingFileRead = null; connections.readingFile = false;
          if (finishConnectionRequest(requestToken)) { showCredentialFileError("The selected service-account file could not be read."); }
        }
        secret = null; file = null;
        return;
      }
      var ownsCompletedRead = ownsCredentialFileRead(context, submissionEpoch, requestToken, fileRead);
      fileRead = null;
      if (!ownsCompletedRead) {
        secret = null; file = null;
        return;
      }
      connections.pendingFileRead = null; connections.readingFile = false;
    } else {
      requestToken = beginConnectionRequest("Checking and saving the credential…");
    }
    el("credential-secret").value = ""; el("credential-file").value = "";
    syncConnectionControls();
    var route, body, headers = {}, raw = false;
    if (context.canvas) {
      route = "api/v1/canvas/" + context.action;
      body = context.action === "forget" ? { generation: connections.generation, confirm_profile_id: context.profileId }
        : context.action === "connect" ? { generation: connections.generation, secret: secret, origin: context.origin }
        : { generation: connections.generation, secret: secret };
    } else {
      route = "api/v1/credentials/" + context.card.role + "/" + context.action;
      if (context.action === "forget" || context.action === "detach") { body = { generation: connections.generation, confirm_role: context.card.role }; }
      else if (context.card.provider === "google") { body = secret; raw = true; headers["X-ULS-Generation"] = connections.generation; }
      else { body = { generation: connections.generation, secret: secret }; }
    }
    var result = await api(route, { method: "POST", mutation: true, body: body, raw: raw, headers: headers });
    secret = null; body = null; file = null;
    if (!finishConnectionRequest(requestToken)) { return; }
    if (!context.canvas && context.card) {
      var credentialRole = context.card.role;
      if (result.ok) {
        delete connections.credentialRetry[credentialRole];
        connections.credentialMessages[credentialRole] = context.action === "forget" || context.action === "detach"
          ? "Credential removed." : "Credential saved and verified.";
      } else if (context.action === "set" || context.action === "replace") {
        connections.credentialRetry[credentialRole] = context.action;
        connections.credentialMessages[credentialRole] = "Credential verification failed. The previous credential is unchanged.";
      }
    }
    closeCredentialDialog(false);
    if (state.ended) { return; }
    if (context.canvas && result.code === "DESTINATION_NOT_ALLOWED") { canvasOriginError("Only public Canvas addresses are supported."); }
    var verificationFailure = !result.ok && (context.action === "set" || context.action === "replace");
    await refreshAfterCredential(result, {
      focusCredentialRole: !context.canvas && context.card ? context.card.role : null,
      focusCredentialHeading: verificationFailure
    });
  }

  async function testConnection(card) {
    if (connections.busy || state.ended || modalOpen()) { return; }
    var requestToken = beginConnectionRequest("Checking the connection with read-only requests…");
    var result = await api("api/v1/connections/" + card.provider + "/" + card.purpose + "/test", { method: "POST", mutation: true, body: {} });
    if (!finishConnectionRequest(requestToken) || state.ended) { return; }
    await loadConnections(); notice(result.ok ? result.data.message : result.message);
  }

  function canvasOriginError(message) {
    el("canvas-origin").setAttribute("aria-invalid", "true");
    el("canvas-origin").setAttribute("aria-describedby", "canvas-origin-error");
    el("canvas-origin-error").hidden = false; el("canvas-origin-error").textContent = "Error: " + message; el("canvas-origin").focus();
  }

  function normalizedCanvasOrigin() {
    try {
      var url = new URL(el("canvas-origin").value);
      if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash || url.pathname !== "/" || url.hostname.indexOf(".") < 0) { throw new Error(); }
      return url.origin;
    } catch (_) { canvasOriginError("Enter your Canvas address, for example https://canvas.example.edu"); return null; }
  }

  function renderCanvas() {
    var data = connections.canvas || {}, profile = data.profile || null;
    el("canvas-status").textContent = humanize(data.state || (profile ? "configured" : "not_checked"));
    var lease = data.lease || { state: "not_issued" };
    var leaseLabels = { not_issued: "Access lease not issued", active: "Active until " + (lease.expires_at || ""),
      expiring: "Access expires soon — renew access", expired: "Access expired — renew access", needs_renewal: "Needs renewal — course selection changed" };
    el("canvas-details").textContent = profile ? profile.origin + " · Canvas user " + profile.user_id + " · " + (leaseLabels[lease.state] || "Needs renewal") : "Connect an account, then select courses. Sync is not available yet.";
    if (profile) { el("canvas-origin").value = profile.origin; }
    el("canvas-origin").readOnly = Boolean(profile);
    var actions = el("canvas-actions"); actions.replaceChildren();
    var unsupported = data.supported === false || data.state === "unsupported_platform";
    if (unsupported) { actions.appendChild(make("p", "Canvas credential storage is not supported on Linux yet.")); return; }
    if (!profile) { actionButton(actions, "Test & connect", function (button) { var origin = normalizedCanvasOrigin(); if (origin) { openCredentialDialog({ canvas: true, label: "Canvas LMS", action: "connect", origin: origin }, button); } }); return; }
    if (data.can_mutate === false) { actions.appendChild(make("p", "Resolve the unfinished local change in Overview first.")); return; }
    actionButton(actions, "Replace token", function (button) { openCredentialDialog({ canvas: true, label: "Canvas LMS", action: "replace", origin: profile.origin }, button); });
    actionButton(actions, "Forget local token", function (button) {
      openCredentialDialog({ canvas: true, label: "Canvas LMS at " + profile.origin, action: "forget",
        profileId: profile.id, origin: profile.origin, userId: profile.user_id,
        maskedAccount: data.masked_account, storageLabel: data.storage_label || "local credential storage" }, button);
    });
    actionButton(actions, "Retry check", function () { canvasRequest("test", {}); });
    actionButton(actions, "Find my courses", function () { canvasRequest("discover", {}); });
    actionButton(actions, "Renew access", function () { canvasRequest("renew", { generation: connections.generation }); });
    actionButton(actions, "Disable Sync", function () { canvasRequest("disable_sync", { generation: connections.generation }); });
    actionButton(actions, "Enable Sync — not available yet", function () {}, true);
  }

  async function canvasRequest(action, body) {
    if (connections.busy || state.ended || modalOpen()) { return; }
    var priorTerm = connections.term;
    var priorSelected = connections.selected.slice();
    var priorDraftDirty = connections.draftDirty;
    var profileId = connections.canvas && connections.canvas.profile && connections.canvas.profile.id;
    var requestToken = beginConnectionRequest(action === "discover" ? "Finding your courses…" : "Checking Canvas…");
    var discoveryRequest = ++connections.discoveryRequest;
    connections.discoveryFresh = false; connections.registryReview = null;
    connections.discoveryId += 1;
    el("canvas-registry-preview").hidden = true;
    renderCourses();
    var requestEpoch = connections.epoch;
    el("canvas-course-area").setAttribute("aria-busy", "true");
    var result = await api("api/v1/canvas/" + action, { method: "POST", mutation: true, body: body });
    if (!finishConnectionRequest(requestToken)) { return; }
    el("canvas-course-area").setAttribute("aria-busy", "false");
    if (state.ended || requestEpoch !== connections.epoch || profileId !== (connections.canvas && connections.canvas.profile && connections.canvas.profile.id)
        || discoveryRequest !== connections.discoveryRequest) { return; }
    if (!result.ok) { el("canvas-status").textContent = "Needs attention"; notice(result.message || "Canvas check failed. Retry check."); return; }
    if (action === "discover") {
      connections.courses = Array.isArray(result.data.courses) ? result.data.courses : [];
      var terms = el("canvas-term"); terms.replaceChildren();
      (result.data.terms || []).forEach(function (term) { var option = make("option", term.name); option.value = term.term_id; terms.appendChild(option); });
      var preferredTerm = priorDraftDirty ? priorTerm : connections.savedRegistry && connections.savedRegistry.term_id;
      var termExists = connections.courses.some(function (course) { return course.term_id === preferredTerm; });
      var firstTerm = result.data.terms && result.data.terms[0];
      connections.term = termExists ? preferredTerm : firstTerm && firstTerm.term_id || terms.value || null;
      terms.value = connections.term || "";
      if (priorDraftDirty) {
        connections.selected = priorSelected;
      } else {
        connections.selected = connections.savedRegistry && connections.savedRegistry.term_id === connections.term
          ? connections.savedRegistry.courses.map(function (course) { return course.course_id; }).filter(function (id) {
            return connections.courses.some(function (course) { return course.course_id === id && course.term_id === connections.term; });
          }) : [];
      }
      connections.discoveryFresh = true; connections.discoveryId += 1;
      connections.draftDirty = priorDraftDirty;
      el("canvas-course-area").hidden = false;
      el("canvas-course-status").textContent = "Current course list was freshly loaded. Changes remain unsaved until Apply.";
      renderCourses();
      notice("Found " + connections.courses.length + " courses in " + (result.data.terms || []).length + " terms.");
    } else { await refreshAfterCredential(result); }
  }

  function renderCourses() {
    connections.epoch += 1; connections.registryReview = null; el("canvas-registry-preview").hidden = true;
    var list = el("canvas-courses"), selector = el("canvas-term");
    var term = connections.term || selector.value; selector.value = term || "";
    selector.disabled = Boolean(!connections.discoveryFresh || connections.busy || state.ended);
    list.replaceChildren();
    var rows = connections.courses.filter(function (course) { return course.term_id === term; });
    if (!rows.length) { list.appendChild(make("p", connections.discoveryFresh ? "No active courses in this term." : "No saved courses are available.")); }
    rows.forEach(function (course) {
      var label = make("label"), input = make("input"); input.type = "checkbox"; input.value = course.course_id;
      input.checked = connections.selected.indexOf(course.course_id) !== -1;
      input.disabled = !connections.discoveryFresh || connections.busy || state.ended || (!input.checked && connections.selected.length >= 20);
      input.addEventListener("change", function () {
        if (connections.busy || !connections.discoveryFresh || state.ended || modalOpen()) { input.checked = connections.selected.indexOf(course.course_id) !== -1; return; }
        if (input.checked) { if (connections.selected.length < 20) { connections.selected.push(course.course_id); } }
        else { connections.selected = connections.selected.filter(function (id) { return id !== course.course_id; }); }
        connections.draftDirty = true;
        renderCourses();
        all(list, "input").filter(function (checkbox) { return checkbox.value === course.course_id; }).forEach(function (checkbox) { checkbox.focus(); });
      });
      label.appendChild(input); label.appendChild(make("span", (course.code ? course.code + " · " : "") + course.name + " · ID " + course.course_id)); list.appendChild(label);
    });
    var selectionState = connections.discoveryFresh ? "selected"
      : connections.draftDirty ? "selected, unsaved; find courses again to review" : "saved";
    el("canvas-selection-count").textContent = connections.selected.length + " of 20 " + selectionState;
    el("canvas-review").disabled = Boolean(!connections.discoveryFresh || !connections.discoveryId ||
      connections.selected.length < 1 || connections.selected.length > 20 || connections.busy || state.ended);
    syncCourseControls();
  }

  async function reviewCanvasSelection() {
    if (connections.busy || !connections.discoveryFresh || !connections.discoveryId || connections.selected.length < 1 || connections.selected.length > 20 || state.ended || modalOpen()) { return; }
    var epoch = connections.epoch, discoveryId = connections.discoveryId;
    var body = { term_id: el("canvas-term").value, course_ids: connections.selected.slice(), generation: connections.generation };
    var requestToken = beginConnectionRequest("Checking selected courses…");
    var result = await api("api/v1/settings/canvas_registry/validate", { method: "POST", mutation: true, body: body });
    if (!finishConnectionRequest(requestToken)) { return; }
    if (state.ended || epoch !== connections.epoch || discoveryId !== connections.discoveryId || !connections.discoveryFresh) { return; }
    if (!result.ok) { notice(result.message); return; }
    body.candidate_hash = result.data.candidate_hash;
    connections.registryReview = { body: body, discoveryId: discoveryId };
    var list = el("canvas-registry-preview").querySelector("ul"); list.replaceChildren();
    list.appendChild(make("li", "Term " + body.term_id + ": " + body.course_ids.length + " course selected. No content is downloaded or imported."));
    el("canvas-registry-preview").hidden = false;
    syncCourseControls();
  }

  async function applyCanvasSelection() {
    var review = connections.registryReview;
    if (connections.busy || !review || !connections.discoveryFresh || review.discoveryId !== connections.discoveryId ||
        !review.body || !Array.isArray(review.body.course_ids) || review.body.course_ids.length < 1 || review.body.course_ids.length > 20 || state.ended || modalOpen()) { return; }
    var epoch = connections.epoch, discoveryId = connections.discoveryId;
    var requestToken = beginConnectionRequest("Saving reviewed course selection…");
    var result = await api("api/v1/settings/canvas_registry/apply", { method: "POST", mutation: true, body: review.body });
    if (!finishConnectionRequest(requestToken)) { return; }
    if (state.ended || epoch !== connections.epoch || discoveryId !== connections.discoveryId) { return; }
    connections.registryReview = null; connections.discoveryFresh = false;
    connections.discoveryId += 1; el("canvas-registry-preview").hidden = true;
    if (!result.ok) {
      renderCourses(); notice(result.message || "The selection was not saved. Find courses again before reviewing it.");
      return;
    }
    connections.draftDirty = false;
    var expectedRegistry = { term_id: review.body.term_id, course_ids: review.body.course_ids.slice() };
    showCourseReadbackPending();
    await refreshAfterCredential(result, { applyReadback: true,
      expectedRegistry: expectedRegistry,
      successMessage: "Saved course selection. The saved term and courses were reloaded from Settings." });
  }

  function bindConnections() {
    if (!el("credential-dialog")) { return; }
    el("credential-cancel").addEventListener("click", cancelCredentialSubmission);
    el("credential-confirm").addEventListener("click", submitCredential);
    el("credential-dialog").addEventListener("keydown", function (event) {
      if (!el("credential-dialog").hidden && el("canvas-term-dialog").hidden) {
        dialogKeydown(el("credential-dialog"), event, cancelCredentialSubmission);
      }
    });
    el("canvas-term-dialog").addEventListener("keydown", function (event) {
      if (!el("canvas-term-dialog").hidden && el("credential-dialog").hidden) {
        dialogKeydown(el("canvas-term-dialog"), event, function () { closeTermChangeDialog(true); });
      }
    });
    el("canvas-term-cancel").addEventListener("click", function () {
      if (!connections.busy && !el("canvas-term-dialog").hidden && el("credential-dialog").hidden) { closeTermChangeDialog(true); }
    });
    el("canvas-term-confirm").addEventListener("click", confirmTermChange);
    el("canvas-origin").addEventListener("input", function () {
      if (modalOpen()) { return; }
      el("canvas-origin").removeAttribute("aria-invalid"); el("canvas-origin").removeAttribute("aria-describedby"); el("canvas-origin-error").hidden = true;
    });
    el("credential-file").addEventListener("change", function () {
      if (!el("credential-dialog").hidden && el("canvas-term-dialog").hidden) { clearCredentialFileError(); }
    });
    el("canvas-term").addEventListener("change", function () {
      var nextTerm = el("canvas-term").value;
      if (connections.busy || !connections.discoveryFresh || state.ended || modalOpen()) { el("canvas-term").value = connections.term || ""; return; }
      if (nextTerm === connections.term) { return; }
      if (connections.selected.length) { openTermChangeDialog(nextTerm); return; }
      connections.term = nextTerm; connections.selected = []; connections.draftDirty = true; renderCourses();
    });
    el("canvas-review").addEventListener("click", reviewCanvasSelection);
    el("canvas-apply").addEventListener("click", applyCanvasSelection);
    el("canvas-edit").addEventListener("click", function () {
      if (connections.busy || state.ended || modalOpen() || !connections.registryReview) { return; }
      connections.registryReview = null; el("canvas-registry-preview").hidden = true; syncCourseControls();
    });
  }

  function bind() {
    bindConnections();
    all(document, "[data-panel]").forEach(function (button) {
      button.addEventListener("click", function () { showPanel(button.getAttribute("data-panel")); });
    });
    all(document, "form").forEach(function (form) {
      form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!state.ended && !modalOpen()) { reviewGroup(form.getAttribute("data-group")); }
      });
    });
    all(document, "[data-apply-group]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (!state.ended && !modalOpen()) { applyGroup(button.getAttribute("data-apply-group")); }
      });
    });
    all(document, "[data-cancel-preview]").forEach(function (button) {
      button.addEventListener("click", function () {
        if (state.ended || modalOpen()) { return; }
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
        if (state.ended || modalOpen()) { return; }
        var group = item.form.getAttribute("data-group");
        state.editEpoch[group] = (state.editEpoch[group] || 0) + 1;
        clearFieldError(item.input);
        invalidateReview(group);
      });
    });
    el("recheck-steps").addEventListener("click", function () {
      if (state.ended || modalOpen()) { return; }
      state.stepOverrides = {};
      loadOverview();
    });
    all(document, "a[href]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        if (!modalOpen()) { return; }
        var activeDialog = !el("credential-dialog").hidden ? el("credential-dialog") : el("canvas-term-dialog");
        var current = link;
        while (current && current !== activeDialog) { current = current.parentNode; }
        if (!current) { event.preventDefault(); }
      });
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
