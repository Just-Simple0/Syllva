//! Relay log line (R2 plan condition A): the `syllva://` relay is on the secret path
//! (cookies, CSRF header, upload bodies), so its log carries only the method, the path
//! without query or fragment, the status and the body size.

pub fn line(method: &str, uri: &str, status: u16, body_bytes: usize) -> String {
    let path = uri.split(['?', '#']).next().unwrap_or("");
    let path: String = path.chars().filter(|c| !c.is_control()).take(200).collect();
    let method: String = method.chars().filter(|c| c.is_ascii_uppercase()).take(10).collect();
    format!("relay {method} {path} {status} {body_bytes}")
}
