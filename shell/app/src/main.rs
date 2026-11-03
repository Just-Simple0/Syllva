//! Syllva desktop shell (design §3.1, rev 5; implementation spec §2, §6, §13.6).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod core;
mod gate;
mod relay;

use std::sync::Arc;
use syllva_shell_proto::secret::ShellSecret;
use tauri::{RunEvent, WebviewUrl, WebviewWindowBuilder};

fn bootstrap_url(token: &str) -> String {
    if cfg!(windows) {
        format!("http://syllva.localhost/bootstrap#{token}")
    } else {
        format!("syllva://localhost/bootstrap#{token}")
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
    let app = tauri::Builder::default()
        .manage(Arc::clone(&core))
        .manage(Arc::clone(&secret))
        .register_asynchronous_uri_scheme_protocol("syllva", move |_ctx, request, responder| {
            std::thread::spawn(move || responder.respond(relay::handle(port, request)));
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
