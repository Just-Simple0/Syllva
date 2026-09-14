# Installation

[한국어](installation.ko.md) · [Operator Guide](README.md)

## Requirements

- Python 3.11+
- macOS or Windows for the checked-in desktop scheduler profiles
- Git
- External provider accounts only for the features you intend to enable

## Clone and create an environment

```bash
git clone https://github.com/Just-Simple0/Syllva.git
cd Syllva
python -m venv .venv
```

Activate it:

```bash
# macOS / Linux shell
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install the editable beta with the features needed for local development/operator validation:

```bash
pip install -e '.[dev,mcp,drive,notion,pdf]'
```

The `pdf` extra is required for PDF preview/text extraction paths. Optional provider adapters remain behind their respective extras.

## Initialize local state

```bash
uls init
uls doctor
uls status
uls behavior lint
```

`uls init` creates only missing local configuration/state files. It does not provision a live Notion workspace, watch Drive, enroll credentials, or enable a scheduler.

`uls doctor` is expected to report incomplete configuration until the provider IDs and credentials needed by your selected features are supplied.

## Repository checks

Before opening an operational PR or enabling a scheduler, run:

```bash
python -m pytest -q
python scripts/lint_behavior_projection.py
python -m compileall -q src
```

The repository CI runs on macOS and Windows. Passing automated checks does not prove live provider/client E2E.

## Next

Continue with [Configuration](configuration.md), then configure only the lanes you intend to use.
