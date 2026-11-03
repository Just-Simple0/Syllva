//! `shell_secret` (spec §2.2): 32 bytes from the OS CSPRNG, kept in memory, sent once on
//! the Core's stdin in `hello`. Never written to disk, argv, env or logs.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;

pub struct ShellSecret([u8; 32]);

impl ShellSecret {
    pub fn generate() -> Self {
        let mut bytes = [0u8; 32];
        getrandom::getrandom(&mut bytes).expect("OS random source");
        ShellSecret(bytes)
    }

    pub fn from_bytes(bytes: [u8; 32]) -> Self {
        ShellSecret(bytes)
    }

    pub fn bytes(&self) -> &[u8; 32] {
        &self.0
    }

    /// The `secret_b64` value of `hello` (base64url, no padding).
    pub fn hello_value(&self) -> String {
        URL_SAFE_NO_PAD.encode(self.0)
    }
}

impl std::fmt::Debug for ShellSecret {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("<secret>")
    }
}
