"""C6 study-note request/generate/save package.

Owns the local submission service core, its own durable store, evidence
assembly for the three contract evidence modes, structural draft validation,
the dedicated Notion AI-block bridge, and Drive staging -- all local to this
package.  Coordinator/runtime/CLI composition (where this package's
RequestHandler is installed) is owned outside this package.
"""

from __future__ import annotations

__all__: list[str] = []
