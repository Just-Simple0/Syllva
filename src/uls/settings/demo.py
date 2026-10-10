"""Launch Settings against newly-created temporary fake stores, never real secrets."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml  # type: ignore[import-untyped]

from uls.behavior import asset_root

from .launcher import run_setup


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="syllva-settings-demo-", dir="/tmp"))
    raw = yaml.safe_load((asset_root() / "config.example.yaml").read_text())
    raw["system"]["workspace_dir"] = str(root / "workspace")
    raw["behavior_contract"]["path"] = str(asset_root() / "contracts" / "study-behavior.md")
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    path.chmod(0o600)
    os.environ["ULS_SETTINGS_FAKE_STORES"] = str(root)
    print(f"Fake Settings root: {root}", flush=True)
    run_setup(path, open_browser=False, runtime_dir=root / "runtime")


if __name__ == "__main__":
    main()
