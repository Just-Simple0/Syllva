"""Context capability issuance and six-check locator authorization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from uls.domain.errors import (
    ContextExpiredError,
    LocatorParseError,
    LocatorNotAllowedError,
)
from uls.domain.models import EvidenceItem, PageLocator, TimeLocator, is_contained, parse_locator
from uls.domain.source_ref import SourceFingerprint
from uls.ephemeral.models import AllowedLocator

from .schemas import CapabilityBinding


class CapabilityManager:
    """Bind exact returned evidence ranges to a Phase 1 context capability.

    Phase 1's ``MemoryEphemeralStore`` owns existence, expiry, caller scope,
    typed containment, and fingerprint checks.  This manager owns the second
    policy layer: the source-class binding and current verified-relation
    recheck that cannot be represented by the generic locator store.
    """

    def __init__(self, ephemeral: Any, *, ttl_seconds: int = 900, max_followup_chunks: int = 8) -> None:
        self.ephemeral = ephemeral
        self.ttl_seconds = ttl_seconds
        self.max_followup_chunks = max_followup_chunks
        self._bindings: dict[str, tuple[CapabilityBinding, ...]] = {}
        self._followups: dict[str, int] = {}

    def issue(
        self,
        bindings: Sequence[CapabilityBinding | EvidenceItem],
        *,
        caller_scope: str | None = None,
    ) -> Any:
        exact_list: list[CapabilityBinding] = []
        for value in bindings:
            if isinstance(value, CapabilityBinding):
                exact_list.append(value)
            elif isinstance(value, EvidenceItem):
                exact_list.append(
                    CapabilityBinding(
                        entity_id=value.entity_id,
                        locator=value.locator,
                        source_hash=value.fingerprint.source_hash,
                        source_version=value.fingerprint.source_version,
                        source_class=value.source_class,
                    )
                )
            else:
                raise TypeError("capability bindings must be CapabilityBinding or EvidenceItem")
        exact = tuple(exact_list)
        specs = [
            {
                "locator": str(binding.locator),
                "entity_id": binding.entity_id,
                "locator_range": str(binding.locator),
                "source_hash": binding.source_hash,
                "source_version": binding.source_version,
                "source_class": binding.source_class,
            }
            for binding in exact
        ]
        try:
            capability = self.ephemeral.create_context_capability(
                specs,
                caller_scope=caller_scope,
                ttl_seconds=self.ttl_seconds,
            )
        except TypeError:
            # Compatibility with a minimal fake that implements the original
            # global-fingerprint signature.  Reject mixed fingerprints rather
            # than broadening one item to another item's version.
            fingerprints = {(b.source_hash, b.source_version) for b in exact}
            if len(fingerprints) > 1:
                raise
            if exact:
                source_hash, source_version = next(iter(fingerprints))
                capability = self.ephemeral.create_context_capability(
                    [str(binding.locator) for binding in exact],
                    caller_scope,
                    self.ttl_seconds,
                    source_hash,
                    source_version,
                )
            else:
                # An empty context has no source fingerprint.  The Phase 1
                # MemoryEphemeralStore accepts it; minimal fakes may not.
                try:
                    capability = self.ephemeral.create_context_capability(
                        [], caller_scope, self.ttl_seconds, "empty", 1
                    )
                except TypeError:
                    capability = self.ephemeral.create_context_capability(
                        [], caller_scope, self.ttl_seconds
                    )
        self._decorate_allowed_locators(capability, exact)
        self._bindings[capability.context_id] = exact
        self._followups[capability.context_id] = 0
        return capability

    @staticmethod
    def _decorate_allowed_locators(
        capability: Any,
        bindings: Sequence[CapabilityBinding],
    ) -> None:
        """Expose the complete per-item binding without changing domain types.

        Phase 1's generic ``AllowedLocator`` stores the locator and its
        fingerprint.  Retrieval needs the additional entity/role binding for
        inspection and for the engine's second authorization layer.  The
        domain object intentionally remains untouched; where its concrete
        implementation is extensible, these are additive metadata attributes.
        The authoritative copy is still ``CapabilityManager._bindings``.
        """

        allowed_values = getattr(capability, "allowed_locators", ())
        used: set[int] = set()
        for allowed in allowed_values:
            for index, binding in enumerate(bindings):
                if index in used:
                    continue
                if (
                    str(getattr(allowed, "locator", "")) == str(binding.locator)
                    and getattr(allowed, "source_hash", None) == binding.source_hash
                    and getattr(allowed, "source_version", None) == binding.source_version
                ):
                    try:
                        object.__setattr__(allowed, "entity_id", binding.entity_id)
                        object.__setattr__(allowed, "locator_range", binding.locator)
                        object.__setattr__(allowed, "source_class", binding.source_class)
                        object.__setattr__(allowed, "role", binding.source_class)
                        object.__setattr__(allowed, "source_ref", binding.source_ref)
                    except (AttributeError, TypeError):
                        pass
                    used.add(index)
                    break

    def issue_for_evidence(
        self,
        items: Iterable[EvidenceItem],
        *,
        caller_scope: str | None = None,
        session_id: str | None = None,
        material_ids: Mapping[str, str] | None = None,
    ) -> Any:
        bindings = []
        for item in items:
            material_id = material_ids.get(item.entity_id) if material_ids else None
            bindings.append(
                CapabilityBinding(
                    entity_id=item.entity_id,
                    locator=item.locator,
                    source_hash=item.fingerprint.source_hash,
                    source_version=item.fingerprint.source_version,
                    source_class=item.source_class,
                    source_ref=getattr(item, "source_ref", None),
                    session_id=session_id,
                    material_id=material_id,
                    relation_required=material_id is not None,
                    provisional=bool(getattr(item, "provisional", False)),
                )
            )
        return self.issue(bindings, caller_scope=caller_scope)

    def bindings_for(self, context_id: str) -> tuple[CapabilityBinding, ...] | None:
        return self._bindings.get(context_id)

    def authorize(
        self,
        context_id: str,
        locator: Any,
        *,
        caller_scope: str | None = None,
        current_fingerprint: SourceFingerprint | None = None,
        role_validator: Callable[[CapabilityBinding], bool] | None = None,
    ) -> CapabilityBinding:
        if role_validator is None:
            raise LocatorNotAllowedError(
                "role_validator is required for source authorization"
            )
        if self.ephemeral.get_context_capability(context_id) is None:
            raise ContextExpiredError(
                "Context capability is missing or expired",
                details={"context_id": context_id},
            )
        try:
            parsed = parse_locator(locator) if isinstance(locator, str) else locator
        except Exception as exc:
            raise LocatorNotAllowedError("requested locator is malformed") from exc
        if not isinstance(parsed, (PageLocator, TimeLocator)):
            raise LocatorNotAllowedError("requested locator is malformed")
        bindings = self._bindings.get(context_id)
        if bindings is None:
            raise LocatorNotAllowedError(
                "context has no source-class binding",
                details={"context_id": context_id},
            )
        try:
            matches = [binding for binding in bindings if is_contained(parsed, binding.locator)]
        except LocatorParseError as exc:
            raise LocatorNotAllowedError("requested locator is malformed") from exc
        if not matches:
            raise LocatorNotAllowedError(
                "requested locator is outside the issued ranges",
                details={"context_id": context_id, "locator": str(parsed)},
            )
        if current_fingerprint is None:
            raise LocatorNotAllowedError(
                "current source fingerprint is required for authorization",
                details={"context_id": context_id},
            )
        # The generic EphemeralStore repeats the scope, containment and
        # fingerprint checks.  A mismatch is allowed to raise LOCATOR_STALE.
        authorized = self.ephemeral.authorize_locator(
            context_id,
            parsed,
            caller_scope,
            current_fingerprint=current_fingerprint,
        )
        if not authorized:
            raise LocatorNotAllowedError(
                "locator capability policy denied the request",
                details={"context_id": context_id, "locator": str(parsed)},
            )
        binding = matches[0]
        if role_validator is not None and not role_validator(binding):
            raise LocatorNotAllowedError(
                "requested source role is no longer allowed",
                details={"context_id": context_id, "source_class": binding.source_class},
            )
        count = self._followups.get(context_id, 0)
        if count >= self.max_followup_chunks:
            raise LocatorNotAllowedError(
                "maximum follow-up chunk budget exceeded",
                details={"context_id": context_id},
            )
        self._followups[context_id] = count + 1
        return binding

    def authorize_locator(
        self,
        context_id: str,
        locator: Any,
        *,
        caller_scope: str | None = None,
        current_fingerprint: SourceFingerprint | None = None,
        role_validator: Callable[[CapabilityBinding], bool] | None = None,
    ) -> bool:
        self.authorize(
            context_id,
            locator,
            caller_scope=caller_scope,
            current_fingerprint=current_fingerprint,
            role_validator=role_validator,
        )
        return True


def issue_context_capability(
    ephemeral: Any,
    bindings: Sequence[CapabilityBinding | EvidenceItem],
    *,
    caller_scope: str | None = None,
    ttl_seconds: int = 900,
) -> Any:
    return CapabilityManager(ephemeral, ttl_seconds=ttl_seconds).issue(
        bindings,
        caller_scope=caller_scope,
    )


create_context_capability = issue_context_capability
issue_capability = issue_context_capability


def authorize_locator(
    ephemeral: Any,
    context_id: str,
    locator: Any,
    caller_scope: str | None,
    current_fingerprint: SourceFingerprint,
    *,
    role_validator: Callable[[CapabilityBinding], bool] | None = None,
) -> bool:
    """Authorize a locator only when a current source-role validator is supplied.

    The old public helper delegated directly to the five generic ephemeral
    checks and could therefore bypass the retrieval role/currentness check.
    Keep the compatibility entry point, but make the sixth check mandatory and
    fail closed when the capability does not carry a decorated source role.
    The engine itself uses :meth:`CapabilityManager.authorize`, which has the
    authoritative binding set and follow-up budget.
    """

    if role_validator is None:
        raise LocatorNotAllowedError(
            "role_validator is required for source authorization"
        )
    capability = ephemeral.get_context_capability(context_id)
    if capability is None:
        raise ContextExpiredError(
            "Context capability is missing or expired",
            details={"context_id": context_id},
        )
    try:
        parsed = parse_locator(locator) if isinstance(locator, str) else locator
    except LocatorParseError as exc:
        raise LocatorNotAllowedError("requested locator is malformed") from exc
    if not isinstance(parsed, (PageLocator, TimeLocator)):
        raise LocatorNotAllowedError("requested locator is malformed")

    matches = []
    for allowed in getattr(capability, "allowed_locators", ()):
        try:
            if is_contained(parsed, allowed.locator):
                matches.append(allowed)
        except LocatorParseError:
            continue
    if not matches:
        raise LocatorNotAllowedError(
            "requested locator is outside the issued ranges",
            details={"context_id": context_id, "locator": str(parsed)},
        )
    allowed = matches[0]
    source_class = getattr(allowed, "source_class", None)
    if not isinstance(source_class, str) or not source_class.strip():
        raise LocatorNotAllowedError(
            "context has no current source-role binding",
            details={"context_id": context_id},
        )
    binding = CapabilityBinding(
        entity_id=getattr(allowed, "entity_id", getattr(parsed, "entity_id", "")),
        locator=allowed.locator,
        source_hash=allowed.source_hash,
        source_version=allowed.source_version,
        source_class=source_class,
        source_ref=getattr(allowed, "source_ref", None),
    )
    if not role_validator(binding):
        raise LocatorNotAllowedError(
            "requested source role is no longer allowed",
            details={"context_id": context_id, "source_class": source_class},
        )
    try:
        authorized = ephemeral.authorize_locator(
            context_id,
            parsed,
            caller_scope,
            current_fingerprint=current_fingerprint,
        )
    except LocatorParseError as exc:
        raise LocatorNotAllowedError("requested locator is malformed") from exc
    if not authorized:
        raise LocatorNotAllowedError(
            "locator capability policy denied the request",
            details={"context_id": context_id, "locator": str(parsed)},
        )
    return True


__all__ = [
    "CapabilityManager",
    "authorize_locator",
    "create_context_capability",
    "issue_capability",
    "issue_context_capability",
]
