//! Where the Shell finds the Core (implementation spec §13.6; R2-5 bundle spike).
//!
//! A packaged app carries the Core as a PyInstaller onedir folder `syllva-core/` beside the
//! Shell executable (`syllva-core/syllva-core[.exe]`). A development build may instead name
//! any Core executable with `SYLLVA_CORE_BIN`; release builds ignore that variable so the
//! Core binary cannot be redirected through the environment.

use std::path::{Path, PathBuf};

pub const SIDECAR_DIR: &str = "syllva-core";

pub fn sidecar_name() -> &'static str {
    if cfg!(windows) {
        "syllva-core.exe"
    } else {
        "syllva-core"
    }
}

/// The bundled Core for a Shell executable at `shell_exe`.
pub fn bundled_core(shell_exe: &Path) -> Option<PathBuf> {
    Some(shell_exe.parent()?.join(SIDECAR_DIR).join(sidecar_name()))
}

/// The Core program: the development override (debug builds only), else the bundled sidecar.
pub fn core_program(dev_override: Option<String>, debug_build: bool, shell_exe: Option<&Path>) -> Option<PathBuf> {
    if debug_build {
        if let Some(program) = dev_override.filter(|p| !p.is_empty()) {
            return Some(PathBuf::from(program));
        }
    }
    bundled_core(shell_exe?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bundled_core_sits_beside_the_shell() {
        let exe = Path::new("/opt/Syllva/syllva-shell");
        assert_eq!(bundled_core(exe).unwrap(), Path::new("/opt/Syllva").join("syllva-core").join(sidecar_name()));
    }

    #[test]
    fn release_builds_ignore_the_override() {
        let exe = Path::new("/opt/Syllva/syllva-shell");
        let bundled = bundled_core(exe).unwrap();
        assert_eq!(core_program(Some("/tmp/evil".into()), false, Some(exe)).unwrap(), bundled);
        assert_eq!(core_program(Some("/dev/syllva".into()), true, Some(exe)).unwrap(), PathBuf::from("/dev/syllva"));
        assert_eq!(core_program(Some(String::new()), true, Some(exe)).unwrap(), bundled);
        assert_eq!(core_program(None, true, None), None);
    }
}
