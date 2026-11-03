//! Gate MAC (spec §6.3.2):
//! `HMAC-SHA256(shell_secret, "syllva-gate-v1" || 0x00 || len32(nonce_id) || nonce_id || len32(request_hash) || request_hash)`
//! with big-endian 4-byte lengths. The Shell never recomputes `request_hash`; it only
//! MACs what the Core showed in `gate.view` after the user pressed Confirm.

use hmac::{Hmac, Mac};
use sha2::Sha256;

const DOMAIN: &[u8] = b"syllva-gate-v1\x00";

pub fn compute(key: &[u8; 32], nonce_id: &str, request_hash: &str) -> [u8; 32] {
    let mut mac = <Hmac<Sha256> as Mac>::new_from_slice(key).expect("HMAC accepts any key length");
    mac.update(DOMAIN);
    for part in [nonce_id.as_bytes(), request_hash.as_bytes()] {
        mac.update(&(part.len() as u32).to_be_bytes());
        mac.update(part);
    }
    mac.finalize().into_bytes().into()
}
