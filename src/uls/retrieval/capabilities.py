"""Context capability issuance and complete selected-entry authorization."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from uls.domain.errors import (
    ContextExpiredError,
    LocatorNotAllowedError,
    LocatorParseError,
    LocatorStaleError,
    UlsError,
)
from uls.domain.ids import parse_course_key, parse_entity_id
from uls.domain.models import EvidenceItem, PageLocator, TimeLocator, is_contained, parse_locator
from uls.domain.page_range import PageRange
from uls.domain.source_ref import SourceFingerprint, SourceRef
from uls.ephemeral.models import AllowedLocator

from .authority import SUPPORTED_MATERIAL_SOURCE_CLASSES
from .schemas import CapabilityBinding
from .scope import VALID_USAGE_ROLES

BindingValidator = Callable[[CapabilityBinding], Any]


class CapabilityManager:
    """Bind returned ranges and revalidate each candidate independently.

    The lower EphemeralStore proves the selected parsed tuple exists in the
    issued capability.  This upper layer owns Material Usage/Role/Type/Course
    identity and therefore must validate every containing candidate before it
    selects one.
    """

    def __init__(self, ephemeral: Any, *, ttl_seconds: int = 900, max_followup_chunks: int = 8) -> None:
        self.ephemeral = ephemeral
        self.ttl_seconds = ttl_seconds
        self.max_followup_chunks = max_followup_chunks
        self._bindings: dict[str, tuple[CapabilityBinding, ...]] = {}
        self._followups: dict[str, int] = {}
        self._followup_lock = threading.Lock()

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
                        source_ref=getattr(value, "source_ref", None),
                    )
                )
            else:
                raise TypeError("capability bindings must be CapabilityBinding or EvidenceItem")
        exact = tuple(exact_list)
        for binding in exact:
            _validate_binding_for_issue(binding)
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
            # Compatibility is limited to old capability-construction fakes;
            # mixed fingerprints are never collapsed to one global value.
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
                capability = self.ephemeral.create_context_capability(
                    [], caller_scope, self.ttl_seconds, "empty", 1
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
        """Attach complete binding metadata to each lower allowed entry."""

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
                        metadata = {
                            "entity_id": binding.entity_id,
                            "locator_range": binding.locator,
                            "source_class": binding.source_class,
                            "role": binding.usage_role,
                            "usage_role": binding.usage_role,
                            "source_ref": binding.source_ref,
                            "session_id": binding.session_id,
                            "material_id": binding.material_id,
                            "usage_id": binding.usage_id,
                            "provisional": binding.provisional,
                            "relation_required": binding.relation_required,
                            "material_type": binding.material_type,
                            "course_relation_page_id": binding.course_relation_page_id,
                            "course_key": binding.course_key,
                            "usage_range": binding.usage_range,
                        }
                        for key, value in metadata.items():
                            object.__setattr__(allowed, key, value)
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
        role_validator: BindingValidator | None = None,
        binding_validator: BindingValidator | None = None,
        candidate_validator: BindingValidator | None = None,
        fingerprint_resolver: Callable[[CapabilityBinding], SourceFingerprint | None] | None = None,
        current_fingerprint_resolver: Callable[[CapabilityBinding], SourceFingerprint | None] | None = None,
    ) -> CapabilityBinding:
        validator = candidate_validator or binding_validator
        if role_validator is None and validator is None:
            raise LocatorNotAllowedError("role_validator is required for source authorization")
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

        valid: list[tuple[CapabilityBinding, SourceFingerprint]] = []
        stale_seen = False
        for binding in matches:
            try:
                # A role-only callback is sufficient for the original generic
                # source capability, but it is not a Material Usage proof.
                # In particular, merely having a CapabilityManager object in
                # the call path must not replace the current graph/Usage/Type
                # validation that a managed binding requires.
                if validator is None and _is_managed_material_binding(binding):
                    continue
                if role_validator is not None and not bool(role_validator(binding)):
                    continue
                candidate_value = validator(binding) if validator is not None else True
                if candidate_value is False or candidate_value is None:
                    continue
                candidate_fp: SourceFingerprint | None
                if isinstance(candidate_value, SourceFingerprint):
                    candidate_fp = candidate_value
                elif fingerprint_resolver is not None:
                    candidate_fp = fingerprint_resolver(binding)
                elif current_fingerprint_resolver is not None:
                    candidate_fp = current_fingerprint_resolver(binding)
                else:
                    candidate_fp = current_fingerprint
                if not isinstance(candidate_fp, SourceFingerprint):
                    continue
                if candidate_fp != SourceFingerprint(binding.source_version, binding.source_hash):
                    stale_seen = True
                    continue
                valid.append((binding, candidate_fp))
            except LocatorStaleError:
                stale_seen = True
            except Exception:
                # A candidate lookup/graph validation failure cannot authorize
                # that candidate or veto an independently valid sibling.
                continue

        valid = self._reject_ambiguous_bases(valid)
        if not valid:
            if stale_seen:
                raise LocatorStaleError(
                    "all containing locator candidates are stale or unavailable",
                    details={"context_id": context_id, "locator": str(parsed)},
                )
            raise LocatorNotAllowedError(
                "requested source role is no longer allowed",
                details={"context_id": context_id, "locator": str(parsed)},
            )

        valid.sort(key=lambda pair: self._selection_key(pair[0]))
        binding, selected_fingerprint = valid[0]
        issued_entry = AllowedLocator(
            binding.locator,
            binding.source_hash,
            binding.source_version,
        )
        try:
            authorized = self.ephemeral.authorize_locator(
                context_id,
                parsed,
                caller_scope,
                current_fingerprint=selected_fingerprint,
                issued_entry=issued_entry,
            )
        except TypeError as exc:
            # Phase4 managed authorization must not downgrade to a legacy
            # all-matches check when the lower store lacks the selector API.
            raise LocatorNotAllowedError(
                "ephemeral store does not support selected-entry authorization",
                details={"context_id": context_id},
            ) from exc
        if not authorized:
            raise LocatorNotAllowedError(
                "locator capability policy denied the request",
                details={"context_id": context_id, "locator": str(parsed)},
            )
        with self._followup_lock:
            count = self._followups.get(context_id, 0)
            if count >= self.max_followup_chunks:
                raise LocatorNotAllowedError(
                    "maximum follow-up chunk budget exceeded",
                    details={"context_id": context_id},
                )
            self._followups[context_id] = count + 1
        return binding

    @staticmethod
    def _reject_ambiguous_bases(
        values: Sequence[tuple[CapabilityBinding, SourceFingerprint]],
    ) -> list[tuple[CapabilityBinding, SourceFingerprint]]:
        """Reject contradictory Usage bases while preserving valid chunks.

        One physical Usage can legitimately produce multiple returned page
        chunks.  Repeating its ID is therefore safe when the complete Usage
        basis is identical.  A repeated ID with contradictory full bases is
        unsafe, while distinct IDs with the same relation tuple remain
        ambiguous.  Physical-row uniqueness is separately checked by the
        engine's current-graph validator.
        """

        bases_by_id: dict[str, set[tuple[Any, ...]]] = {}
        relation_bases: dict[tuple[Any, ...], set[str]] = {}
        for binding, _ in values:
            if binding.usage_id is None:
                continue
            basis = _full_usage_basis(binding)
            bases_by_id.setdefault(binding.usage_id, set()).add(basis)
            relation_key = (
                binding.session_id,
                binding.material_id,
                binding.usage_role,
                binding.usage_range,
            )
            relation_bases.setdefault(relation_key, set()).add(binding.usage_id)
        contradictory_ids = {
            usage_id for usage_id, bases in bases_by_id.items() if len(bases) > 1
        }
        ambiguous_relations = {
            key for key, usage_ids in relation_bases.items() if len(usage_ids) > 1
        }
        return [
            pair
            for pair in values
            if pair[0].usage_id is None
            or (
                pair[0].usage_id not in contradictory_ids
                and (
                    pair[0].session_id,
                    pair[0].material_id,
                    pair[0].usage_role,
                    pair[0].usage_range,
                ) not in ambiguous_relations
            )
        ]

    @staticmethod
    def _selection_key(binding: CapabilityBinding) -> tuple[Any, ...]:
        locator = binding.locator
        if isinstance(locator, PageLocator):
            span = locator.end_page - locator.start_page
        elif isinstance(locator, TimeLocator):
            span = locator.end_seconds - locator.start_seconds
        else:
            span = 10**18
        stable = binding.usage_id or _source_identity(binding.source_ref) or binding.entity_id
        return (span, stable, str(binding.locator))

    def authorize_locator(
        self,
        context_id: str,
        locator: Any,
        *,
        caller_scope: str | None = None,
        current_fingerprint: SourceFingerprint | None = None,
        role_validator: BindingValidator | None = None,
        binding_validator: BindingValidator | None = None,
        candidate_validator: BindingValidator | None = None,
    ) -> bool:
        self.authorize(
            context_id,
            locator,
            caller_scope=caller_scope,
            current_fingerprint=current_fingerprint,
            role_validator=role_validator,
            binding_validator=binding_validator,
            candidate_validator=candidate_validator,
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
    role_validator: BindingValidator | None = None,
    manager: CapabilityManager | None = None,
    binding_validator: BindingValidator | None = None,
    candidate_validator: BindingValidator | None = None,
) -> bool:
    """Compatibility helper with no Phase4 Material bypass.

    A supplied manager routes through complete candidate-aware validation.  A
    bare legacy call can authorize only a generic decorated source binding;
    Material/Usage metadata requires the manager path.
    """

    if manager is not None:
        manager.authorize(
            context_id,
            locator,
            caller_scope=caller_scope,
            current_fingerprint=current_fingerprint,
            role_validator=role_validator,
            binding_validator=binding_validator,
            candidate_validator=candidate_validator,
        )
        return True
    if role_validator is None:
        raise LocatorNotAllowedError("role_validator is required for source authorization")
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
        raise LocatorNotAllowedError("requested locator is outside the issued ranges")
    allowed = matches[0]
    # Decorated Phase4 entries carry at least one of these identity fields.
    # Refuse to reconstruct a stripped Material binding from an arbitrary
    # first match.
    if any(
        getattr(allowed, name, None) is not None
        for name in (
            "usage_id",
            "usage_role",
            "material_id",
            "usage_range",
            "course_relation_page_id",
            "course_key",
        )
    ):
        raise LocatorNotAllowedError(
            "Phase4 Material authorization requires a full CapabilityManager"
        )
    source_class = getattr(allowed, "source_class", None)
    entity_id = getattr(allowed, "entity_id", getattr(parsed, "entity_id", ""))
    if not isinstance(source_class, str) or not source_class.strip():
        raise LocatorNotAllowedError("context has no current source-role binding")
    if _is_material_identity(entity_id, source_class):
        raise LocatorNotAllowedError(
            "Phase4 Material authorization requires a full CapabilityManager"
        )
    binding = CapabilityBinding(
        entity_id=entity_id,
        locator=allowed.locator,
        source_hash=allowed.source_hash,
        source_version=allowed.source_version,
        source_class=source_class,
        source_ref=getattr(allowed, "source_ref", None),
    )
    if not role_validator(binding):
        raise LocatorNotAllowedError("requested source role is no longer allowed")
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
        raise LocatorNotAllowedError("locator capability policy denied the request")
    return True


def _source_identity(value: Any) -> str | None:
    if value is None:
        return None
    provider = getattr(value, "provider", None)
    file_id = getattr(value, "file_id", None)
    if isinstance(provider, str) and isinstance(file_id, str):
        return f"{provider}:{file_id}"
    if isinstance(value, Mapping):
        provider = value.get("provider")
        file_id = value.get("file_id")
        if isinstance(provider, str) and isinstance(file_id, str):
            return f"{provider}:{file_id}"
    return None


def _is_managed_material_binding(binding: CapabilityBinding) -> bool:
    """Whether a binding carries the Phase4 Material Usage contract."""

    if _is_material_identity(binding.entity_id, binding.source_class):
        return True
    if binding.relation_required is True or any(
        value is not None
        for value in (
            binding.material_id,
            binding.usage_id,
            binding.usage_role,
            binding.material_type,
            binding.usage_range,
        )
    ):
        return True
    # Transcript bindings also retain the current Session Course snapshot,
    # but that shared graph metadata is not Material/Usage metadata.  For
    # every other source identity, a Course-only attachment is treated as a
    # managed graph binding so partial Material metadata cannot hide behind a
    # generic capability.
    return (
        binding.source_class.casefold() not in {"professor_transcript", "transcript"}
        and any(
            value is not None
            for value in (binding.course_relation_page_id, binding.course_key)
        )
    )


_MATERIAL_SOURCE_CLASSES = frozenset(
    set(SUPPORTED_MATERIAL_SOURCE_CLASSES) | {"material"}
)


def _is_material_entity_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return parse_entity_id(value).entity_type == "M"
    except UlsError:
        return False


def _is_material_identity(entity_id: Any, source_class: Any) -> bool:
    return _is_material_entity_id(entity_id) or (
        isinstance(source_class, str)
        and source_class.casefold() in _MATERIAL_SOURCE_CLASSES
    )


def _validate_binding_for_issue(binding: CapabilityBinding) -> None:
    """Reject incomplete Phase4 Material identity before lower issuance."""

    try:
        parsed_locator = (
            parse_locator(binding.locator)
            if isinstance(binding.locator, str)
            else binding.locator
        )
    except Exception as exc:
        raise LocatorNotAllowedError("capability binding locator is invalid") from exc
    if not isinstance(parsed_locator, (PageLocator, TimeLocator)):
        raise LocatorNotAllowedError("capability binding locator is invalid")
    if not isinstance(binding.entity_id, str) or not binding.entity_id.strip():
        raise LocatorNotAllowedError("capability binding entity_id is invalid")
    if parsed_locator.entity_id != binding.entity_id:
        raise LocatorNotAllowedError("capability binding locator/entity identity mismatch")
    if not isinstance(binding.source_class, str) or not binding.source_class.strip():
        raise LocatorNotAllowedError("capability binding source_class is invalid")
    if (
        isinstance(binding.source_version, bool)
        or not isinstance(binding.source_version, int)
        or binding.source_version < 1
        or not isinstance(binding.source_hash, str)
        or not binding.source_hash.strip()
    ):
        raise LocatorNotAllowedError("capability binding fingerprint is invalid")
    if type(binding.relation_required) is not bool:
        raise LocatorNotAllowedError("capability binding relation_required flag is invalid")
    if type(binding.provisional) is not bool:
        raise LocatorNotAllowedError("capability binding provisional flag is invalid")
    if not _is_managed_material_binding(binding):
        return

    if binding.source_class not in SUPPORTED_MATERIAL_SOURCE_CLASSES:
        raise LocatorNotAllowedError("unsupported Material source class")
    if not isinstance(parsed_locator, PageLocator):
        raise LocatorNotAllowedError("Material capability requires a PageLocator")
    if not _is_material_entity_id(binding.entity_id):
        raise LocatorNotAllowedError("Material capability requires a canonical Material entity")
    if not isinstance(binding.material_id, str) or not binding.material_id.strip():
        raise LocatorNotAllowedError("Material capability requires material_id")
    if binding.material_id != binding.entity_id:
        raise LocatorNotAllowedError("Material capability entity/material identity mismatch")
    if not isinstance(binding.material_type, str) or not binding.material_type.strip():
        raise LocatorNotAllowedError("Material capability requires the raw Material Type")
    if (
        not isinstance(binding.course_relation_page_id, str)
        or not binding.course_relation_page_id.strip()
        or not isinstance(binding.course_key, str)
        or not binding.course_key.strip()
    ):
        raise LocatorNotAllowedError("Material capability requires a complete Course identity")
    try:
        parse_course_key(binding.course_key)
    except Exception as exc:
        raise LocatorNotAllowedError("Material capability Course Key is invalid") from exc
    if not isinstance(binding.source_ref, SourceRef) or not _valid_source_ref(binding.source_ref):
        raise LocatorNotAllowedError("Material capability requires a valid originating SourceRef")

    usage_backed = (
        binding.relation_required
        or binding.usage_id is not None
        or binding.usage_role is not None
        or binding.usage_range is not None
    )
    if not usage_backed:
        if binding.session_id is not None:
            raise LocatorNotAllowedError("direct Material capability cannot carry a Session basis")
        return

    if (
        not binding.relation_required
        or not isinstance(binding.session_id, str)
        or not binding.session_id.strip()
        or not isinstance(binding.usage_id, str)
        or not binding.usage_id.strip()
        or not isinstance(binding.usage_role, str)
        or binding.usage_role not in VALID_USAGE_ROLES
        or not isinstance(binding.usage_range, PageRange)
    ):
        raise LocatorNotAllowedError(
            "Usage-backed Material capability requires complete Usage identity and PageRange"
        )


def _valid_source_ref(value: SourceRef) -> bool:
    return (
        isinstance(value.provider, str)
        and bool(value.provider.strip())
        and isinstance(value.file_id, str)
        and bool(value.file_id.strip())
        and not value.file_id.casefold().startswith(("http://", "https://"))
    )


def _full_usage_basis(binding: CapabilityBinding) -> tuple[Any, ...]:
    return (
        binding.entity_id,
        binding.session_id,
        binding.material_id,
        binding.usage_role,
        binding.usage_range,
        binding.provisional,
        binding.material_type,
        binding.source_class,
        binding.course_relation_page_id,
        binding.course_key,
        _source_identity(binding.source_ref),
        binding.source_hash,
        binding.source_version,
    )


__all__ = [
    "CapabilityManager",
    "authorize_locator",
    "create_context_capability",
    "issue_capability",
    "issue_context_capability",
]
