//! `syllva://` relay to the Core's loopback HTTP (design rev 5; errata E29; plan §6 A/B, §7).
//!
//! Fixed target `127.0.0.1:<port from ready>`, no redirects. Header shaping (allowlist,
//! relay MAC, per-window session jar, Set-Cookie absorbed) lives in `syllva_shell_proto::relay`
//! where it is tested on every OS. The log line carries only method, path, status and size.

use std::sync::Mutex;
use std::time::Duration;
use syllva_shell_proto::relay::{shape_request, shape_response, Jar};
use syllva_shell_proto::relay_log;
use tauri::http::{Request, Response, StatusCode};

pub const MAX_BODY: usize = 200 * 1024 * 1024 + 64 * 1024;

pub struct Relayed {
    pub response: Response<Vec<u8>>,
    /// The Core answered 401 although this window had a session: it restarted or the session idled out.
    pub session_lost: bool,
}

fn plain(status: StatusCode, body: &'static str) -> Response<Vec<u8>> {
    Response::builder().status(status).header("content-type", "application/json").body(body.as_bytes().to_vec()).unwrap()
}

pub fn handle(port: u16, key: &[u8; 32], label: &str, jar: &Mutex<Jar>, request: Request<Vec<u8>>) -> Relayed {
    let lost = |response| Relayed { response, session_lost: false };
    let path = request.uri().path_and_query().map(|p| p.as_str().to_string()).unwrap_or_else(|| "/".into());
    if request.body().len() > MAX_BODY {
        return lost(plain(StatusCode::PAYLOAD_TOO_LARGE, "{\"error\":\"BODY_TOO_LARGE\"}"));
    }
    let incoming: Vec<(String, String)> = request
        .headers()
        .iter()
        .filter_map(|(name, value)| value.to_str().ok().map(|v| (name.as_str().to_string(), v.to_string())))
        .collect();
    let (outgoing_headers, had_session) = {
        let jar = jar.lock().expect("jar lock");
        (shape_request(key, label, &incoming, &jar), jar.get(label).is_some())
    };
    let agent = ureq::AgentBuilder::new().redirects(0).timeout_connect(Duration::from_secs(5)).build();
    let mut outgoing = agent.request(request.method().as_str(), &format!("http://127.0.0.1:{port}{path}"));
    for (name, value) in &outgoing_headers {
        outgoing = outgoing.set(name, value);
    }
    let result = if request.body().is_empty() { outgoing.call() } else { outgoing.send_bytes(request.body()) };
    let response = match result {
        Ok(response) | Err(ureq::Error::Status(_, response)) => response,
        Err(_) => return lost(plain(StatusCode::BAD_GATEWAY, "{\"error\":\"CORE_UNREACHABLE\"}")),
    };
    let status = response.status();
    let set_cookies: Vec<String> = response.all("set-cookie").iter().map(|v| v.to_string()).collect();
    let headers: Vec<(String, String)> = response
        .headers_names()
        .iter()
        .flat_map(|name| response.all(name).into_iter().map(move |v| (name.clone(), v.to_string())))
        .collect();
    jar.lock().expect("jar lock").absorb(label, &set_cookies);
    let mut builder = Response::builder().status(status);
    for (name, value) in shape_response(&headers) {
        builder = builder.header(name, value);
    }
    let mut body = Vec::new();
    let _ = std::io::Read::read_to_end(&mut response.into_reader(), &mut body);
    if cfg!(debug_assertions) {
        eprintln!("{}", relay_log::line(request.method().as_str(), &path, status, body.len()));
    }
    let session_lost = status == 401 && had_session;
    if session_lost {
        jar.lock().expect("jar lock").forget(label);
    }
    Relayed {
        response: builder.body(body).unwrap_or_else(|_| plain(StatusCode::BAD_GATEWAY, "{\"error\":\"RELAY\"}")),
        session_lost,
    }
}

/// Navigation stays on the app origin (R2 plan item 5); anything else is refused.
pub fn allowed_navigation(url: &tauri::Url) -> bool {
    match (url.scheme(), url.host_str()) {
        ("syllva", Some("localhost")) => true,
        ("http" | "https", Some("syllva.localhost")) => cfg!(windows),
        _ => false,
    }
}
