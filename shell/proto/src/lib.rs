//! Shell side of the Syllva Core protocol (implementation spec §2, §6.3.2, §13.1).
//!
//! Pure code with no Tauri dependency so it is tested on every OS without a webview:
//! JSON Lines framing (64 KiB), the gate MAC (same vectors as the Python Core), the
//! environment allowlist for spawning the Core, the `shell_secret`, and the relay log
//! formatter (method, path without query/fragment, status, size — never headers or bodies).

pub mod env;
pub mod frame;
pub mod mac;
pub mod relay_log;
pub mod secret;
