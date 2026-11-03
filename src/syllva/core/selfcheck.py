"""Bundle self-check (implementation spec §13.6; R2-5 bundle spike).

``syllva core --self-check`` proves that a packaged Core (PyInstaller onedir) carries
what it needs at run time: the native/optional dependencies (``pypdf``, ``keyring``,
``sqlite3`` with JSON), the UI server stack (``uvicorn`` resolves its loop/protocol
modules lazily, which bundlers miss), and the package resources (schema migrations,
static UI). It reads no library data, touches no keychain entry and opens no socket.
"""

from __future__ import annotations

import io
import sqlite3
from collections.abc import Callable
from importlib import resources
from typing import Any

PDF_TEXT = "Syllva self-check"


def _tiny_pdf(text: str) -> bytes:
    """A one-page PDF with ``text``, built with correct xref offsets."""

    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref))
    return out.getvalue()


def _pypdf() -> str:
    from pypdf import PdfReader

    text = PdfReader(io.BytesIO(_tiny_pdf(PDF_TEXT))).pages[0].extract_text()
    if PDF_TEXT not in text:
        raise RuntimeError("text not extracted")
    import pypdf

    return str(pypdf.__version__)


def _keyring() -> str:
    import keyring

    backend = keyring.get_keyring()  # resolves the platform backend; no entry is read or written
    return f"{type(backend).__module__}.{type(backend).__name__}"


def _sqlite() -> str:
    conn = sqlite3.connect(":memory:")
    try:
        if conn.execute("SELECT json_extract('{\"a\": 1}', '$.a')").fetchone()[0] != 1:
            raise RuntimeError("json1 missing")
    finally:
        conn.close()
    return sqlite3.sqlite_version


def _uvicorn() -> str:
    import uvicorn

    async def app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - never served
        return None

    config = uvicorn.Config(app, lifespan="off", log_config=None)
    config.load()  # imports the loop and http protocol implementations
    return str(uvicorn.__version__)


def _resources() -> str:
    migrations = sorted(p.name for p in resources.files("syllva.state.v2").joinpath("migrations").iterdir()
                        if p.name.endswith(".sql"))
    static = resources.files("syllva.ui").joinpath("static")
    for name in ("index.html", "app.js", "styles.css"):
        if not static.joinpath(name).is_file():
            raise RuntimeError(f"static/{name} missing")
    if not migrations:
        raise RuntimeError("no migrations")
    return f"{len(migrations)} migrations, static ui"


CHECKS: dict[str, Callable[[], str]] = {
    "pypdf": _pypdf, "keyring": _keyring, "sqlite": _sqlite, "uvicorn": _uvicorn, "resources": _resources,
}


def self_check() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, check in CHECKS.items():
        try:
            results[name] = {"ok": True, "detail": check()}
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            results[name] = {"ok": False, "detail": type(exc).__name__}
    return {"ok": all(r["ok"] for r in results.values()), "checks": results}


__all__ = ["self_check"]
