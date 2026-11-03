//! `syllva://` relay to the Core's loopback HTTP (design rev 5, R2 plan condition A/B).
//!
//! Fixed target `127.0.0.1:<port from ready>`, no redirects followed, request headers
//! forwarded as received (never synthesized), bodies capped. The log line carries only
//! method, path (no query/fragment), status and size — headers and bodies never.

use std::time::Duration;
use syllva_shell_proto::relay_log;
use tauri::http::{Request, Response, StatusCode};

pub const MAX_BODY: usize = 200 * 1024 * 1024 + 64 * 1024;
const REQUEST_HEADERS: &[&str] = &[
    "accept", "content-type", "cookie", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest",
    "x-syllva-csrf", "x-syllva-filename",
];
const RESPONSE_HEADERS: &[&str] = &[
    "content-type", "set-cookie", "content-security-policy", "x-content-type-options", "referrer-policy",
    "cache-control", "cross-origin-opener-policy",
];

fn plain(status: StatusCode, body: &'static str) -> Response<Vec<u8>> {
    Response::builder().status(status).header("content-type", "application/json").body(body.as_bytes().to_vec()).unwrap()
}

pub fn handle(port: u16, request: Request<Vec<u8>>) -> Response<Vec<u8>> {
    let path = request.uri().path_and_query().map(|p| p.as_str().to_string()).unwrap_or_else(|| "/".into());
    if request.body().len() > MAX_BODY {
        return plain(StatusCode::PAYLOAD_TOO_LARGE, "{\"error\":\"BODY_TOO_LARGE\"}");
    }
    let agent = ureq::AgentBuilder::new().redirects(0).timeout_connect(Duration::from_secs(5)).build();
    let mut outgoing = agent.request(request.method().as_str(), &format!("http://127.0.0.1:{port}{path}"));
    for name in REQUEST_HEADERS {
        for value in request.headers().get_all(*name) {
            if let Ok(value) = value.to_str() {
                outgoing = outgoing.set(name, value);
            }
        }
    }
    let result = if request.body().is_empty() { outgoing.call() } else { outgoing.send_bytes(request.body()) };
    let response = match result {
        Ok(response) | Err(ureq::Error::Status(_, response)) => response,
        Err(_) => return plain(StatusCode::BAD_GATEWAY, "{\"error\":\"CORE_UNREACHABLE\"}"),
    };
    let status = response.status();
    let mut builder = Response::builder().status(status);
    for name in RESPONSE_HEADERS {
        for value in response.all(name) {
            builder = builder.header(*name, value);
        }
    }
    let mut body = Vec::new();
    let _ = std::io::Read::read_to_end(&mut response.into_reader(), &mut body);
    if cfg!(debug_assertions) {
        eprintln!("{}", relay_log::line(request.method().as_str(), &path, status, body.len()));
    }
    builder.body(body).unwrap_or_else(|_| plain(StatusCode::BAD_GATEWAY, "{\"error\":\"RELAY\"}"))
}

/// Navigation stays on the app origin (R2 plan item 5); anything else is refused.
pub fn allowed_navigation(url: &tauri::Url) -> bool {
    match (url.scheme(), url.host_str()) {
        ("syllva", Some("localhost")) => true,
        ("http" | "https", Some("syllva.localhost")) => cfg!(windows),
        _ => false,
    }
}
