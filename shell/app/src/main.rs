//! Syllva desktop shell (design §3.1, rev 5; implementation spec §2, §6, §13.6).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod core;
mod gate;
mod relay;

use serde_json::{json, Value};
use std::sync::{Arc, Mutex};
use syllva_shell_proto::relay::Jar;
use syllva_shell_proto::secret::ShellSecret;
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

pub fn bootstrap_url(token: &str) -> String {
    if cfg!(windows) {
        format!("http://syllva.localhost/bootstrap#{token}")
    } else {
        format!("syllva://localhost/bootstrap#{token}")
    }
}

/// The Core restarted or the session idled out: get a fresh bootstrap token for this window and reload it.
fn recover_session(app: &tauri::AppHandle, core: &core::Core, label: &str) {
    let request = json!({"t": "session.bootstrap", "window_label": label});
    let Ok(reply) = core.request(request.as_object().cloned().unwrap_or_default()) else { return };
    let Some(token) = reply.get("bootstrap").and_then(Value::as_str) else { return };
    if let (Some(window), Ok(url)) = (app.get_webview_window(label), bootstrap_url(token).parse()) {
        let _ = window.navigate(url);
    }
}

fn main() {
    let secret = Arc::new(ShellSecret::generate());
    let (core, bootstrap) = match core::Core::start(&secret) {
        Ok(started) => started,
        Err(message) => {
            rfd::MessageDialog::new().set_title("Syllva").set_description(message).show();
            std::process::exit(1);
        }
    };
    let core = Arc::new(core);
    let port = core.port;
    let jar: Arc<Mutex<Jar>> = Arc::new(Mutex::new(Jar::default()));
    let relay_secret = Arc::clone(&secret);
    let relay_jar = Arc::clone(&jar);
    let relay_core = Arc::clone(&core);
    let window_jar = Arc::clone(&jar);
    let app = tauri::Builder::default()
        .manage(Arc::clone(&core))
        .manage(Arc::clone(&secret))
        .register_asynchronous_uri_scheme_protocol("syllva", move |ctx, request, responder| {
            // the window identity comes from Tauri (the webview that made the request), never from page content
            let label = ctx.webview_label().to_string();
            let app = ctx.app_handle().clone();
            let (secret, jar, core) = (Arc::clone(&relay_secret), Arc::clone(&relay_jar), Arc::clone(&relay_core));
            std::thread::spawn(move || {
                let relayed = relay::handle(port, secret.bytes(), &label, &jar, request);
                responder.respond(relayed.response);
                if relayed.session_lost {
                    recover_session(&app, &core, &label);
                }
            });
        })
        .on_window_event(move |window, event| {
            if let WindowEvent::Destroyed = event {
                window_jar.lock().expect("jar lock").forget(window.label()); // the session id dies with the window
            }
        })
        .invoke_handler(tauri::generate_handler![gate::confirm_gate])
        .setup(move |app| {
            let url = WebviewUrl::CustomProtocol(bootstrap_url(&bootstrap).parse()?);
            WebviewWindowBuilder::new(app, "main", url)
                .title("Syllva")
                .inner_size(1100.0, 760.0)
                .on_navigation(relay::allowed_navigation)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Syllva shell");
    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            core.shutdown();
        }
    });
}
