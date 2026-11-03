//! Relay shaping (errata E29, R2 plan §7.2-§7.3): what the Shell adds to and removes from
//! every `syllva://` request and response.
//!
//! * request headers are an allowlist; the webview can never send `Cookie` or `X-Syllva-*`
//!   (they are not on it), Origin/Sec-Fetch-* pass through as received;
//! * the Shell adds `X-Syllva-Window: <label>` and `X-Syllva-Relay: <MAC over the label>`,
//!   and `Cookie: syllva_sid=…` from the per-window jar;
//! * `Set-Cookie` never reaches the webview: `syllva_sid` is absorbed into the jar (deletion
//!   honoured), everything else in it is dropped.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::HashMap;

pub const REQUEST_HEADERS: &[&str] = &[
    "accept", "content-type", "origin", "sec-fetch-site", "sec-fetch-mode", "sec-fetch-dest", "x-syllva-csrf", "x-syllva-filename",
];
pub const RESPONSE_HEADERS: &[&str] = &[
    "content-type", "content-security-policy", "x-content-type-options", "referrer-policy", "cache-control",
    "cross-origin-opener-policy",
];
const DOMAIN: &[u8] = b"syllva-relay-v1\x00";
const COOKIE: &str = "syllva_sid";

pub fn relay_mac(key: &[u8; 32], label: &str) -> [u8; 32] {
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(DOMAIN);
    mac.update(&(label.len() as u32).to_be_bytes());
    mac.update(label.as_bytes());
    mac.finalize().into_bytes().into()
}

/// Session ids per window label; memory only, dropped with the window or the Core.
#[derive(Default, Debug)]
pub struct Jar {
    sids: HashMap<String, String>,
}

impl Jar {
    pub fn get(&self, label: &str) -> Option<&str> {
        self.sids.get(label).map(String::as_str)
    }

    pub fn forget(&mut self, label: &str) -> bool {
        self.sids.remove(label).is_some()
    }

    pub fn clear(&mut self) {
        self.sids.clear();
    }

    /// Absorb `Set-Cookie` values of a response to a request of `label`.
    pub fn absorb(&mut self, label: &str, set_cookies: &[String]) {
        for line in set_cookies {
            let mut parts = line.split(';').map(str::trim);
            let Some((name, value)) = parts.next().and_then(|p| p.split_once('=')) else { continue };
            if name != COOKIE {
                continue;
            }
            let deleted = value.is_empty()
                || parts.any(|attr| {
                    let lower = attr.to_ascii_lowercase();
                    lower == "max-age=0" || lower.starts_with("max-age=-") || lower.starts_with("expires=thu, 01 jan 1970")
                });
            if deleted {
                self.sids.remove(label);
            } else {
                self.sids.insert(label.to_string(), value.to_string());
            }
        }
    }
}

/// Outgoing headers for a request of window `label`.
pub fn shape_request(key: &[u8; 32], label: &str, incoming: &[(String, String)], jar: &Jar) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = incoming
        .iter()
        .filter(|(name, _)| REQUEST_HEADERS.iter().any(|allowed| allowed.eq_ignore_ascii_case(name)))
        .map(|(name, value)| (name.to_ascii_lowercase(), value.clone()))
        .collect();
    out.push(("x-syllva-window".into(), label.to_string()));
    out.push(("x-syllva-relay".into(), URL_SAFE_NO_PAD.encode(relay_mac(key, label))));
    if let Some(sid) = jar.get(label) {
        out.push(("cookie".into(), format!("{COOKIE}={sid}")));
    }
    out
}

/// Headers forwarded to the webview (Set-Cookie is never among them).
pub fn shape_response(headers: &[(String, String)]) -> Vec<(String, String)> {
    headers
        .iter()
        .filter(|(name, _)| RESPONSE_HEADERS.iter().any(|allowed| allowed.eq_ignore_ascii_case(name)))
        .cloned()
        .collect()
}
