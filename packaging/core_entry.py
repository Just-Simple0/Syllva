"""Entry of the packaged Core sidecar (implementation spec §13.6).

The bundle is the Core only: it accepts ``core --stdio`` / ``core --self-check`` (the
leading ``core`` is optional) and nothing else from the v2 CLI.
"""

from __future__ import annotations

import sys

from syllva.cli.main import main

MODES = {"--stdio", "--self-check"}


def run(argv: list[str]) -> int:
    args = argv[1:] if argv[:1] == ["core"] else argv
    if len(args) != 1 or args[0] not in MODES:
        sys.stderr.write("usage: syllva-core [core] --stdio | --self-check\n")
        return 2
    return main(["core", args[0]])


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
