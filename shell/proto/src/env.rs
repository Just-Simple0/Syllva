//! Environment of the spawned Core (spec §2.2-2): an allowlist, nothing else.

pub const ALLOWED: &[&str] = &["PATH", "HOME", "USERPROFILE", "LANG", "SYLLVA_DATA_DIR", "SYSTEMROOT"];

/// Keep only allowlisted variables (case-insensitive on Windows names like `Path`).
pub fn core_env<I, K, V>(vars: I) -> Vec<(String, String)>
where
    I: IntoIterator<Item = (K, V)>,
    K: Into<String>,
    V: Into<String>,
{
    vars.into_iter()
        .map(|(k, v)| (k.into(), v.into()))
        .filter(|(k, _)| ALLOWED.iter().any(|allowed| allowed.eq_ignore_ascii_case(k)))
        .collect()
}
