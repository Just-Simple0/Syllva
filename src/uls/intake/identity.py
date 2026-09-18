"""Stable hashes and provider identity helpers for intake operations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any


def canonical_json(value: Any) -> str:
    """Serialize an identity tuple exactly once for every hash boundary."""

    _reject_nonfinite(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def provider_binding_id(provider: str, provider_account_stable_id: str, oauth_app_stable_id: str) -> str:
    """Derive the persisted account/app binding without retaining credentials."""

    for value, name in (
        (provider, "provider"),
        (provider_account_stable_id, "provider_account_stable_id"),
        (oauth_app_stable_id, "oauth_app_stable_id"),
    ):
        _require_text(value, name)
    return sha256_hex(
        [
            "intake.account-binding.v1",
            provider,
            provider_account_stable_id,
            oauth_app_stable_id,
        ]
    )


def derive_request_revision(
    *,
    provider: str,
    provider_account_binding_id: str,
    semester: str,
    request_type: str,
    observation_refs: Sequence[Sequence[str] | Mapping[str, Any]],
    config_fingerprint: str,
    target_snapshot_hash: str,
) -> str:
    refs = [_canonical_observation_ref(value) for value in observation_refs]
    refs.sort(key=lambda value: canonical_json(value))
    return sha256_hex(
        [
            "intake.request-revision.v1",
            provider,
            provider_account_binding_id,
            semester,
            request_type,
            refs,
            config_fingerprint,
            target_snapshot_hash,
        ]
    )


def derive_request_key(
    *,
    provider: str,
    provider_account_binding_id: str,
    semester: str,
    request_type: str,
    request_revision_hash: str,
    intake_ids: Sequence[str],
    target_snapshot_hash: str,
) -> str:
    """Derive the immutable R3 Request Key from one accepted tuple form."""

    for value, name in (
        (provider, "provider"),
        (provider_account_binding_id, "provider_account_binding_id"),
        (semester, "semester"),
        (request_type, "request_type"),
        (request_revision_hash, "request_revision_hash"),
        (target_snapshot_hash, "target_snapshot_hash"),
    ):
        _require_text(value, name)
    if isinstance(intake_ids, (str, bytes, bytearray)):
        raise ValueError("intake_ids must be a sequence of IDs")
    canonical_intake_ids = list(intake_ids)
    if any(not isinstance(value, str) or not value for value in canonical_intake_ids):
        raise ValueError("intake_ids must contain non-empty strings")
    if len(set(canonical_intake_ids)) != len(canonical_intake_ids):
        raise ValueError("intake_ids must be unique")
    return sha256_hex([
        "intake.request.v1",
        provider,
        provider_account_binding_id,
        semester,
        request_type,
        sorted(canonical_intake_ids),
        request_revision_hash,
        target_snapshot_hash,
    ])


def derive_plan_revision(
    *,
    provider: str,
    request_key: str,
    normalized_user_hash: str,
    target_snapshot_hash: str,
    workspace_fingerprint: str,
) -> str:
    """Derive a post-claim plan revision from an immutable receipt snapshot."""

    return sha256_hex(
        [
            "intake.plan.v1",
            provider,
            request_key,
            normalized_user_hash,
            target_snapshot_hash,
            workspace_fingerprint,
        ]
    )


def derive_status_revision(
    *,
    intake_id: str,
    source_hash: str,
    source_version: int,
    status: str,
    request_revision_hash: str | None = None,
) -> str:
    """Derive a status-operation revision without inventing a receipt.

    Before a USER request is claimed, ``request_revision_hash`` is the
    durable immutable observation/request generation.  A receipt-derived
    ``plan_revision`` is included only after claim and is never required for
    the initial File Intake projection.
    """

    return sha256_hex(
        [
            "intake.status-revision.v1",
            "preclaim" if request_revision_hash is None else "postclaim",
            intake_id,
            source_hash,
            source_version,
            status,
            request_revision_hash,
        ]
    )


def derive_operation_key(operation: str, *identity_values: Any) -> str:
    """Return the plan's bare lowercase hex operation key."""

    _require_text(operation, "operation")
    return sha256_hex([operation, *identity_values])


def derive_job_key(operation: str, *identity_values: Any) -> str:
    """Return the StateStore-compatible deterministic job key."""

    return "sha256:" + derive_operation_key(operation, *identity_values)


def derive_study_note_key(
    *,
    course_key: str,
    session_id: str,
    evidence_manifest_hash: str,
    learner_request_hash: str,
    template_version: str,
    generator_config_version: str,
) -> str:
    """Derive the rev10 study-note container identity."""

    for value, name in (
        (course_key, "course_key"),
        (session_id, "session_id"),
        (evidence_manifest_hash, "evidence_manifest_hash"),
        (learner_request_hash, "learner_request_hash"),
        (template_version, "template_version"),
        (generator_config_version, "generator_config_version"),
    ):
        _require_text(value, name)
    return sha256_hex(
        [
            "study-note.v1",
            course_key,
            session_id,
            evidence_manifest_hash,
            learner_request_hash,
            template_version,
            generator_config_version,
        ]
    )


def folder_marker_key(
    *,
    provider: str,
    provider_account_binding_id: str,
    parent_folder_id: str,
    reservation_id: str,
    source_file_id: str,
    entity_app_id: str,
    folder_role: str,
) -> str:
    return sha256_hex(
        [
            "intake.folder.v1",
            provider,
            provider_account_binding_id,
            parent_folder_id,
            reservation_id,
            source_file_id,
            entity_app_id,
            folder_role,
        ]
    )


def derivative_marker(
    *,
    provider: str,
    source_file_id: str,
    source_hash: str,
    source_version: int,
    entity_app_id: str,
    normalized_schema: str,
    processor_version: str,
    artifact_role: str,
) -> dict[str, str]:
    """Create compact Drive appProperties without storing the full tuple."""

    tuple_hash = sha256_hex(
        [
            "intake.derivative.v1",
            provider,
            source_file_id,
            source_hash,
            source_version,
            entity_app_id,
            normalized_schema,
            processor_version,
            artifact_role,
        ]
    )
    marker = {
        "uls_v": "1",
        "uls_t": tuple_hash,
        "uls_r": artifact_role,
    }
    validate_private_properties(marker)
    return marker


def validate_private_properties(properties: Mapping[str, str]) -> None:
    """Enforce Google Drive private appProperties byte limits.

    Google Drive permits at most 30 private properties per file and at most
    124 UTF-8 bytes for each key+value pair.  The full source tuple belongs in
    the local ledger; only compact deterministic digests cross this boundary.
    """

    if len(properties) > 30:
        raise ValueError("Drive private appProperties permit at most 30 properties")
    for key, value in properties.items():
        if not isinstance(key, str) or not key or not isinstance(value, str):
            raise ValueError("Drive private appProperties require string keys and values")
        if len(key.encode("utf-8")) + len(value.encode("utf-8")) > 124:
            raise ValueError("Drive private appProperty key/value exceeds 124 UTF-8 bytes")


def _canonical_observation_ref(value: Sequence[str] | Mapping[str, Any]) -> list[Any]:
    if isinstance(value, Mapping):
        intake_id = value.get("intake_id")
        observation_hash = value.get("immutable_observation_hash")
        if set(value) != {"intake_id", "immutable_observation_hash"}:
            raise ValueError(
                "observation references require intake_id and immutable_observation_hash"
            )
    else:
        if isinstance(value, (str, bytes, bytearray)) or len(value) != 2:
            raise ValueError("observation references require intake ID and immutable hash")
        intake_id, observation_hash = value
    _require_text(intake_id, "observation intake_id")
    _require_text(observation_hash, "immutable_observation_hash")
    return [intake_id, observation_hash]


def _reject_nonfinite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("identity values cannot contain non-finite numbers")
    if isinstance(value, Mapping):
        for key, nested in value.items():
            _reject_nonfinite(key)
            _reject_nonfinite(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _reject_nonfinite(nested)


def _require_text(value: Any, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


__all__ = [
    "canonical_json",
    "derivative_marker",
    "derive_job_key",
    "derive_operation_key",
    "derive_plan_revision",
    "derive_request_key",
    "derive_request_revision",
    "derive_status_revision",
    "derive_study_note_key",
    "folder_marker_key",
    "provider_binding_id",
    "sha256_hex",
    "validate_private_properties",
]
