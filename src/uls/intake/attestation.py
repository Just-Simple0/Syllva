"""Per-entry WORKER attestation contract for personal OAuth credentials (P2 plan §5).

An attestor proves, immediately before any public worker entry takes effect,
that the configured ``authorized_user`` grant is still live, still bound to the
configured Desktop client and still the same Google account.  The resulting
snapshot carries no token, secret or raw permission ID: only the role, scope,
a monotonic generation and the existing provider binding digest.

Service-account compositions never install an attestor; the gate is then a
no-op so the existing SA path is unchanged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from uls.domain.errors import UlsError

ATTESTATION_MAX_AGE_SECONDS = 15 * 60.0


class ReconnectRequiredError(UlsError):
    """Fixed fail-closed signal: the personal OAuth connection must be redone."""

    code = "RECONNECT_REQUIRED"

    def __init__(self, message: str | None = None) -> None:
        # Never carry provider diagnostics; the code alone is the contract.
        super().__init__(message or "RECONNECT_REQUIRED")


@dataclass(frozen=True)
class WorkerEntryAttestation:
    """Immutable proof for one public worker entry (role, scope, binding digest only)."""

    attestor_id: str
    role: str
    scope: str
    generation: int
    binding_id: str
    issued_at: float = field(default_factory=time.monotonic)

    def is_fresh(self, *, now: float | None = None, max_age: float = ATTESTATION_MAX_AGE_SECONDS) -> bool:
        current = time.monotonic() if now is None else now
        return 0.0 <= current - self.issued_at <= max_age


class WorkerEntryAttestor(Protocol):
    """Concrete runtime object shared by the worker and its Drive adapter."""

    attestor_id: str
    role: str
    scope: str
    binding_id: str | None
    generation: int

    def attest_entry(self) -> WorkerEntryAttestation: ...


def attestation_matches(attestor: Any, attestation: object) -> bool:
    """Full-field match of an attestation against the attestor that must have issued it."""

    if not isinstance(attestation, WorkerEntryAttestation) or attestor is None:
        return False
    if getattr(attestor, "revoked", False):
        # A refresh/proof failure after issuance invalidates the current entry proof.
        return False
    try:
        return (
            attestation.attestor_id == attestor.attestor_id
            and attestation.role == attestor.role
            and attestation.scope == attestor.scope
            and attestation.binding_id == attestor.binding_id
            and attestation.generation == int(attestor.generation) >= 1
            and attestation.is_fresh()
        )
    except (AttributeError, TypeError, ValueError):
        return False


class StaticAttestor:
    """Test-only attestor producing deterministic attestations or a fixed failure."""

    def __init__(self, *, role: str = "worker", scope: str = "", binding_id: str = "synthetic-binding",
                 failure: Callable[[], BaseException | None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        import secrets

        self.attestor_id = secrets.token_hex(8)
        self.role, self.scope = role, scope
        self.binding_id: str | None = binding_id
        self.failure = failure
        self.clock = clock
        self.generation = 0
        self.calls = 0
        self.revoked = False

    def attest_entry(self) -> WorkerEntryAttestation:
        self.calls += 1
        if self.failure is not None:
            error = self.failure()
            if error is not None:
                raise error
        self.generation += 1
        self.revoked = False
        return WorkerEntryAttestation(
            attestor_id=self.attestor_id, role=self.role, scope=self.scope,
            generation=self.generation, binding_id=self.binding_id or "", issued_at=self.clock(),
        )


__all__ = [
    "ATTESTATION_MAX_AGE_SECONDS",
    "ReconnectRequiredError",
    "StaticAttestor",
    "WorkerEntryAttestation",
    "WorkerEntryAttestor",
    "attestation_matches",
]
