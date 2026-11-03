//! `confirm_gate(nonce_id)` — the only app command (spec §6.3 (2)-(4), C-19).
//!
//! The webview passes a nonce id and nothing else. The window label comes from Tauri (the
//! window that invoked the command), the dialog text comes from the Core (`gate.view`), the
//! dialog is native (rfd, Rust side — no dialog plugin in any capability), and the MAC is
//! computed with `shell_secret` only after the user pressed Confirm. JS receives only
//! `{status, reason?}`.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use serde::Serialize;
use serde_json::{json, Map, Value};
use std::sync::Arc;
use syllva_shell_proto::{mac, secret::ShellSecret};

use crate::core::Core;

#[derive(Serialize)]
pub struct GateResult {
    status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    reason: Option<String>,
}

fn object(value: Value) -> Map<String, Value> {
    match value {
        Value::Object(map) => map,
        _ => Map::new(),
    }
}

fn text(view: &Map<String, Value>, key: &str) -> String {
    view.get(key).and_then(Value::as_str).unwrap_or("").chars().filter(|c| !c.is_control() || *c == '\n').collect()
}

pub fn dialog_text(view: &Map<String, Value>) -> String {
    let clean = |items: Option<&Value>| -> Vec<String> {
        items
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(Value::as_str).map(|s| s.chars().filter(|c| !c.is_control()).take(80).collect()).collect())
            .unwrap_or_default()
    };
    let (ids, prefixes) = (clean(view.get("source_ids")), clean(view.get("source_hash_prefixes")));
    let sources: Vec<String> = ids.iter().zip(prefixes.iter()).take(10).map(|(id, p)| format!("{id} (sha256 {p})")).collect();
    let seconds = view.get("expires_in_ms").and_then(Value::as_u64).unwrap_or(0) / 1000;
    format!(
        "{}\n\n대상: {}\n원본: {}\n남은 시간: 약 {}초",
        text(view, "summary"),
        text(view, "target_id"),
        sources.join(", "),
        seconds
    )
}

fn result(reply: &Map<String, Value>) -> GateResult {
    GateResult {
        status: reply.get("status").and_then(Value::as_str).unwrap_or("REJECTED").to_string(),
        reason: reply.get("reason").and_then(Value::as_str).map(str::to_string),
    }
}

fn valid_nonce(nonce_id: &str) -> bool {
    nonce_id.len() == 32 && nonce_id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[tauri::command]
pub async fn confirm_gate(
    webview: tauri::Webview,
    core: tauri::State<'_, Arc<Core>>,
    secret: tauri::State<'_, Arc<ShellSecret>>,
    nonce_id: String,
) -> Result<GateResult, String> {
    if !valid_nonce(&nonce_id) {
        return Ok(GateResult { status: "REJECTED".into(), reason: Some("BAD_NONCE".into()) });
    }
    // the same label namespace the relay uses (ctx.webview_label()); for the main WebviewWindow they coincide
    let label = webview.label().to_string();
    let core = Arc::clone(&core);
    let get = object(json!({"t": "gate.get", "nonce_id": nonce_id, "window_label": label}));
    let view = {
        let core = Arc::clone(&core);
        tauri::async_runtime::spawn_blocking(move || core.request(get)).await.map_err(|e| e.to_string())??
    };
    if view.get("t").and_then(Value::as_str) != Some("gate.view") {
        let reason = view.get("reason").and_then(Value::as_str).unwrap_or("UNAVAILABLE").to_string();
        return Ok(GateResult { status: "REJECTED".into(), reason: Some(reason) });
    }
    let answer = rfd::AsyncMessageDialog::new()
        .set_level(rfd::MessageLevel::Warning)
        .set_title("Syllva — 확인")
        .set_description(dialog_text(&view))
        .set_buttons(rfd::MessageButtons::OkCancelCustom("확인".into(), "취소".into()))
        .show()
        .await;
    let confirmed = matches!(answer, rfd::MessageDialogResult::Ok)
        || matches!(&answer, rfd::MessageDialogResult::Custom(text) if text == "확인");
    let message = if confirmed {
        let request_hash = view.get("request_hash").and_then(Value::as_str).unwrap_or("");
        let tag = mac::compute(secret.bytes(), &nonce_id, request_hash);
        object(json!({"t": "gate.confirm", "nonce_id": nonce_id, "window_label": label, "mac_b64": URL_SAFE_NO_PAD.encode(tag)}))
    } else {
        object(json!({"t": "gate.deny", "nonce_id": nonce_id}))
    };
    let reply = tauri::async_runtime::spawn_blocking(move || core.request(message)).await.map_err(|e| e.to_string())??;
    Ok(result(&reply))
}
