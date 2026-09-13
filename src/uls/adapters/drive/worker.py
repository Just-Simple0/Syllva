"""Worker-only Drive write port and Google implementation.

Retrieval imports ``GoogleDriveReader`` only.  This module is intentionally a
separate capability boundary: a connected action connector that cannot carry
private markers is represented as unsupported and cannot be upgraded by
matching folder names.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from uls.domain.errors import (
    PolicyDeniedError,
    ProviderUnavailableError,
    SourcePartialError,
    SourceUnavailableError,
)

from uls.intake.identity import validate_private_properties


DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
_FILE_ID = re.compile(r"\A[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class DriveWorkerCapabilities:
    marker_create: bool
    marker_search: bool
    metadata_readback: bool
    file_id_preserving_move: bool
    private_owner_readback: bool
    reason: str = ""

    @property
    def full_intake(self) -> bool:
        return all(
            (
                self.marker_create,
                self.marker_search,
                self.metadata_readback,
                self.file_id_preserving_move,
                self.private_owner_readback,
            )
        )


@dataclass(frozen=True)
class DriveMetadata:
    file_id: str
    name: str
    mime_type: str
    parents: tuple[str, ...] = ()
    modified_time: str | None = None
    size: int | None = None
    trashed: bool = False
    owned_by_me: bool | None = None
    web_view_link: str | None = None
    md5_checksum: str | None = None
    drive_id: str | None = None
    permission_types: tuple[str, ...] = ()
    permission_roles: tuple[tuple[str, str], ...] = ()
    permission_count: int | None = None
    owner_only: bool | None = None
    is_publicly_shared: bool | None = None
    can_edit: bool | None = None
    can_move: bool | None = None
    app_properties: dict[str, str] = field(default_factory=dict)

    @property
    def parent_id(self) -> str | None:
        return self.parents[0] if len(self.parents) == 1 else None

    @property
    def identity(self) -> str:
        """Stable provider/file identity used when one file is listed twice."""

        return self.file_id


@runtime_checkable
class DriveWorkerPort(Protocol):
    """Minimal worker mutation/readback contract, independent of Google SDK."""

    capabilities: DriveWorkerCapabilities

    def list_folder(self, folder_id: str) -> list[DriveMetadata]: ...

    def read_metadata(self, file_id: str) -> DriveMetadata: ...

    def download(self, file_id: str) -> bytes: ...

    def search_marker(self, marker: dict[str, str]) -> list[DriveMetadata]: ...

    def create_folder_with_marker(
        self, parent_id: str, name: str, marker: dict[str, str]
    ) -> DriveMetadata: ...

    def create_file_with_marker(
        self,
        parent_id: str,
        name: str,
        mime_type: str,
        content: bytes,
        marker: dict[str, str],
    ) -> DriveMetadata: ...

    def move_file(self, file_id: str, original_parent_id: str, target_parent_id: str) -> DriveMetadata: ...


class GoogleDriveWorkerAdapter:
    """Authenticated provider-specific worker adapter.

    Credentials are supplied by the runtime composition root.  The adapter
    never loads environment values, logs auth material, or exposes the SDK to
    intake planning code.
    """

    capabilities = DriveWorkerCapabilities(
        marker_create=True,
        marker_search=True,
        metadata_readback=True,
        file_id_preserving_move=True,
        private_owner_readback=True,
    )

    def __init__(self, service: Any, *, max_bytes: int = 20_000_000, max_files: int = 10_000) -> None:
        self._service = service
        self._files = service.files()
        self.max_bytes = max_bytes
        self.max_files = max_files

    def list_folder(self, folder_id: str) -> list[DriveMetadata]:
        _require_id(folder_id)
        files: list[DriveMetadata] = []
        token: str | None = None
        seen: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "pageSize": min(1000, self.max_files),
                "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime,size,md5Checksum,trashed,ownedByMe,webViewLink,driveId,permissions(type,role,allowFileDiscovery),capabilities(canEdit,canMoveItemWithinDrive),appProperties)",
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if token:
                kwargs["pageToken"] = token
            result = self._call(self._files.list, **kwargs)
            values = result.get("files")
            if not isinstance(values, list):
                raise SourceUnavailableError("Drive listing is malformed")
            for value in values:
                files.append(_metadata(value))
                if len(files) > self.max_files:
                    raise SourcePartialError("Drive folder exceeds bounded listing limit")
            token = result.get("nextPageToken")
            if not token:
                return files
            if not isinstance(token, str) or token in seen:
                raise SourcePartialError("Drive pagination is incomplete")
            seen.add(token)

    def read_metadata(self, file_id: str) -> DriveMetadata:
        _require_id(file_id)
        result = self._call(
            self._files.get,
            fileId=file_id,
            fields="id,name,mimeType,parents,modifiedTime,size,md5Checksum,trashed,ownedByMe,webViewLink,driveId,permissions(type,role,allowFileDiscovery),capabilities(canEdit,canMoveItemWithinDrive),appProperties",
            supportsAllDrives=True,
        )
        metadata = _metadata(result)
        if metadata.file_id != file_id or metadata.trashed:
            raise SourceUnavailableError("Drive file identity changed or is trashed")
        return metadata

    def download(self, file_id: str) -> bytes:
        metadata = self.read_metadata(file_id)
        size = metadata.size
        if size is None or size < 0 or size > self.max_bytes:
            raise SourcePartialError("Drive source is outside the bounded download limit")
        try:
            from googleapiclient.http import MediaIoBaseDownload  # type: ignore[import-untyped]

            output = io.BytesIO()
            downloader = MediaIoBaseDownload(
                output,
                self._files.get_media(fileId=file_id, supportsAllDrives=True),
                chunksize=256_000,
            )
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=0)
                if output.tell() > self.max_bytes:
                    raise SourcePartialError("Drive source exceeds byte limit")
            data = output.getvalue()
        except SourcePartialError:
            raise
        except Exception:
            raise ProviderUnavailableError("Drive download failed") from None
        if len(data) != size:
            raise SourceUnavailableError("Drive source changed during download")
        return data

    def search_marker(self, marker: dict[str, str]) -> list[DriveMetadata]:
        validate_private_properties(marker)
        # Search the compact tuple digest, then exact-check every returned
        # property.  A missing/partial search is an error, never zero matches.
        marker_key, marker_value = _search_pair(marker)
        result: list[DriveMetadata] = []
        token: str | None = None
        seen: set[str] = set()
        while True:
            kwargs: dict[str, Any] = {
                "q": f"appProperties has {{ key = '{_quote(marker_key)}' and value = '{_quote(marker_value)}' }} and trashed = false",
                "pageSize": 1000,
                "fields": "nextPageToken,files(id,name,mimeType,parents,modifiedTime,size,md5Checksum,trashed,ownedByMe,webViewLink,driveId,permissions(type,role,allowFileDiscovery),capabilities(canEdit,canMoveItemWithinDrive),appProperties)",
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
            }
            if token:
                kwargs["pageToken"] = token
            payload = self._call(self._files.list, **kwargs)
            values = payload.get("files")
            if not isinstance(values, list):
                raise SourceUnavailableError("Drive marker search is malformed")
            result.extend(_metadata(value) for value in values)
            if len(result) > self.max_files:
                raise SourcePartialError("Drive marker search exceeds bounded limit")
            token = payload.get("nextPageToken")
            if not token:
                break
            if not isinstance(token, str) or token in seen:
                raise SourcePartialError("Drive marker search pagination is incomplete")
            seen.add(token)
        return [item for item in result if item.app_properties == marker]

    def create_folder_with_marker(self, parent_id: str, name: str, marker: dict[str, str]) -> DriveMetadata:
        return self._create_with_marker(parent_id, name, DRIVE_FOLDER_MIME, b"", marker)

    def create_file_with_marker(
        self,
        parent_id: str,
        name: str,
        mime_type: str,
        content: bytes,
        marker: dict[str, str],
    ) -> DriveMetadata:
        if not isinstance(content, bytes):
            raise TypeError("Drive content must be bytes")
        return self._create_with_marker(parent_id, name, mime_type, content, marker)

    def _create_with_marker(
        self, parent_id: str, name: str, mime_type: str, content: bytes, marker: dict[str, str]
    ) -> DriveMetadata:
        _require_id(parent_id)
        if not isinstance(name, str) or not name or len(name) > 512:
            raise ValueError("Drive file name is invalid")
        validate_private_properties(marker)
        body = {"name": name, "parents": [parent_id], "mimeType": mime_type, "appProperties": marker}
        try:
            media = None
            if mime_type != DRIVE_FOLDER_MIME:
                from googleapiclient.http import MediaIoBaseUpload  # type: ignore[import-untyped]

                media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime_type, resumable=False)
            result = self._call(
                self._files.create,
                body=body,
                media_body=media,
                fields="id,name,mimeType,parents,modifiedTime,size,md5Checksum,trashed,ownedByMe,webViewLink,driveId,permissions(type,role,allowFileDiscovery),capabilities(canEdit,canMoveItemWithinDrive),appProperties",
                supportsAllDrives=True,
            )
        except Exception:
            raise ProviderUnavailableError("Drive create failed") from None
        metadata = _metadata(result)
        return self._validate_private_metadata(metadata, parent_id, mime_type, marker)

    def move_file(self, file_id: str, original_parent_id: str, target_parent_id: str) -> DriveMetadata:
        _require_id(file_id)
        _require_id(original_parent_id)
        _require_id(target_parent_id)
        current = self.read_metadata(file_id)
        if current.owned_by_me is not True:
            raise PolicyDeniedError("Drive source is not owned by the configured user")
        self._require_private_metadata(current, require_move=True)
        if len(current.parents) != 1:
            raise SourceUnavailableError("Drive source has ambiguous parents")
        if current.parents[0] == target_parent_id:
            return current
        if current.parents[0] != original_parent_id:
            raise PolicyDeniedError("Drive source moved outside the registered intake parent")
        try:
            result = self._call(
                self._files.update,
                fileId=file_id,
                addParents=target_parent_id,
                removeParents=original_parent_id,
                fields="id,name,mimeType,parents,modifiedTime,size,md5Checksum,trashed,ownedByMe,webViewLink,driveId,permissions(type,role,allowFileDiscovery),capabilities(canEdit,canMoveItemWithinDrive),appProperties",
                supportsAllDrives=True,
            )
        except Exception:
            raise ProviderUnavailableError("Drive move failed") from None
        moved = _metadata(result)
        if moved.file_id != file_id or moved.parents != (target_parent_id,):
            raise SourceUnavailableError("Drive move readback is uncertain")
        if moved.owned_by_me is not True:
            raise PolicyDeniedError("Drive move readback is not USER owned")
        self._require_private_metadata(moved, require_move=True)
        return moved

    def _validate_private_metadata(
        self, metadata: DriveMetadata, parent_id: str, mime_type: str, marker: dict[str, str]
    ) -> DriveMetadata:
        if metadata.parents != (parent_id,) or metadata.mime_type != mime_type:
            raise SourceUnavailableError("Drive create parent or MIME readback mismatch")
        if metadata.app_properties != marker:
            raise SourceUnavailableError("Drive marker readback mismatch")
        if metadata.owned_by_me is not True:
            raise PolicyDeniedError("Drive created item is not USER owned")
        self._require_private_metadata(metadata, require_move=mime_type != DRIVE_FOLDER_MIME)
        return metadata

    @staticmethod
    def _require_private_metadata(metadata: DriveMetadata, *, require_move: bool = False) -> None:
        if metadata.drive_id is not None:
            raise PolicyDeniedError("Drive shared-drive items are outside the owner-only intake scope")
        if metadata.permission_count is None or metadata.owner_only is None:
            raise SourceUnavailableError("Drive owner permission readback is missing")
        if metadata.owner_only is not True:
            raise PolicyDeniedError("Drive item is not solely USER owned")
        if metadata.is_publicly_shared is None:
            raise SourceUnavailableError("Drive permission/privacy readback is missing")
        if metadata.is_publicly_shared:
            raise PolicyDeniedError("Drive item has broad sharing and cannot be used for intake")
        if metadata.can_edit is not True:
            raise PolicyDeniedError("Drive item is not editable by the configured worker")
        if require_move and metadata.can_move is not True:
            raise PolicyDeniedError("Drive item cannot be moved by the configured worker")

    @staticmethod
    def _call(method: Any, **kwargs: Any) -> Any:
        try:
            return method(**kwargs).execute()
        except (SourcePartialError, SourceUnavailableError, PolicyDeniedError):
            raise
        except Exception:
            raise ProviderUnavailableError("Drive worker operation failed") from None


class UnsupportedConnectorDrivePort:
    """Capability description for the connected action connector.

    It may be used by static onboarding outside this worker.  Entity marker
    creation/search and moves are deliberately unavailable here.
    """

    capabilities = DriveWorkerCapabilities(
        marker_create=False,
        marker_search=False,
        metadata_readback=False,
        file_id_preserving_move=False,
        private_owner_readback=False,
        reason="connector exposes no appProperties marker setter/search",
    )

    def __getattr__(self, name: str) -> Any:
        if name in {"list_folder", "read_metadata", "download", "search_marker", "create_folder_with_marker", "create_file_with_marker", "move_file"}:
            def unsupported(*args: Any, **kwargs: Any) -> Any:
                del args, kwargs
                raise NotImplementedError(self.capabilities.reason)

            return unsupported
        raise AttributeError(name)


class InMemoryDriveWorker:
    """Provider-free Drive worker port used by intake acceptance tests.

    The fake deliberately models the durable boundary: creates persist before
    an optional response-loss exception, moves preserve the file ID, and all
    reads return copies of provider metadata.  It has no connector or SDK
    behavior and never represents credentials.
    """

    capabilities = DriveWorkerCapabilities(
        marker_create=True,
        marker_search=True,
        metadata_readback=True,
        file_id_preserving_move=True,
        private_owner_readback=True,
    )

    def __init__(
        self,
        files: Iterable[DriveMetadata] = (),
        contents: Mapping[str, bytes] | None = None,
        *,
        events: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.files: dict[str, DriveMetadata] = {item.file_id: item for item in files}
        self.contents: dict[str, bytes] = dict(contents or {})
        self.events = events if events is not None else []
        self._counter = 0
        self.drop_next_create_response = False
        self.drop_next_move_response = False

    def list_folder(self, folder_id: str) -> list[DriveMetadata]:
        _require_id(folder_id)
        self.events.append(("list", folder_id))
        return [
            replace(item)
            for item in self.files.values()
            if not item.trashed and folder_id in item.parents
        ]

    def read_metadata(self, file_id: str) -> DriveMetadata:
        _require_id(file_id)
        self.events.append(("read", file_id))
        item = self.files.get(file_id)
        if item is None or item.trashed:
            raise SourceUnavailableError("in-memory Drive file is unavailable")
        return replace(item)

    def download(self, file_id: str) -> bytes:
        metadata = self.read_metadata(file_id)
        self.events.append(("download", file_id))
        if metadata.mime_type == DRIVE_FOLDER_MIME:
            return b""
        if file_id not in self.contents:
            raise SourceUnavailableError("in-memory Drive content is unavailable")
        return bytes(self.contents[file_id])

    def search_marker(self, marker: dict[str, str]) -> list[DriveMetadata]:
        validate_private_properties(marker)
        self.events.append(("search", dict(marker)))
        return [
            replace(item)
            for item in self.files.values()
            if not item.trashed and item.app_properties == marker
        ]

    def create_folder_with_marker(
        self, parent_id: str, name: str, marker: dict[str, str]
    ) -> DriveMetadata:
        return self._create(parent_id, name, DRIVE_FOLDER_MIME, b"", marker)

    def create_file_with_marker(
        self,
        parent_id: str,
        name: str,
        mime_type: str,
        content: bytes,
        marker: dict[str, str],
    ) -> DriveMetadata:
        if not isinstance(content, bytes):
            raise TypeError("Drive content must be bytes")
        return self._create(parent_id, name, mime_type, content, marker)

    def _create(
        self,
        parent_id: str,
        name: str,
        mime_type: str,
        content: bytes,
        marker: dict[str, str],
    ) -> DriveMetadata:
        _require_id(parent_id)
        validate_private_properties(marker)
        self._counter += 1
        file_id = f"memory-drive-{self._counter}"
        metadata = DriveMetadata(
            file_id=file_id,
            name=name,
            mime_type=mime_type,
            parents=(parent_id,),
            modified_time=f"2026-01-01T00:00:{self._counter:02d}Z",
            size=len(content),
            owned_by_me=True,
            drive_id=(self.files.get(parent_id).drive_id if self.files.get(parent_id) else None),
            permission_types=("user",),
            permission_roles=(("user", "owner"),),
            permission_count=1,
            owner_only=True,
            is_publicly_shared=False,
            can_edit=True,
            can_move=True,
            web_view_link=(
                f"https://drive.google.com/drive/folders/{file_id}"
                if mime_type == DRIVE_FOLDER_MIME
                else f"https://drive.google.com/file/d/{file_id}/view"
            ),
            app_properties=dict(marker),
        )
        self.files[file_id] = metadata
        self.contents[file_id] = bytes(content)
        self.events.append(("create", file_id, parent_id, name, dict(marker)))
        if self.drop_next_create_response:
            self.drop_next_create_response = False
            raise ProviderUnavailableError("simulated Drive create response loss")
        return replace(metadata)

    def move_file(
        self, file_id: str, original_parent_id: str, target_parent_id: str
    ) -> DriveMetadata:
        current = self.read_metadata(file_id)
        if current.owned_by_me is not True:
            raise PolicyDeniedError("in-memory Drive source is not USER owned")
        if current.parents != (original_parent_id,):
            if current.parents == (target_parent_id,):
                return current
            raise PolicyDeniedError("in-memory Drive source parent is not registered")
        moved = replace(current, parents=(target_parent_id,))
        self.files[file_id] = moved
        self.events.append(("move", file_id, original_parent_id, target_parent_id))
        if self.drop_next_move_response:
            self.drop_next_move_response = False
            raise ProviderUnavailableError("simulated Drive move response loss")
        return replace(moved)


def ensure_marked_folder(
    port: DriveWorkerPort,
    *,
    parent_id: str,
    name: str,
    marker: dict[str, str],
    create_attempted: bool = False,
) -> DriveMetadata:
    """Recover or create one marker-identified entity folder conservatively."""

    if not port.capabilities.marker_create or not port.capabilities.marker_search:
        raise NotImplementedError(port.capabilities.reason or "Drive marker capability is unsupported")
    matches = port.search_marker(marker)
    if len(matches) > 1:
        raise SourceUnavailableError("multiple Drive marker matches require reconciliation")
    if len(matches) == 1:
        item = matches[0]
        if item.mime_type != DRIVE_FOLDER_MIME or item.parents != (parent_id,) or item.app_properties != marker:
            raise SourceUnavailableError("Drive marker match failed exact parent/MIME readback")
        if item.owned_by_me is not True:
            raise PolicyDeniedError("Drive marker item is not USER owned")
        return item
    if create_attempted:
        raise SourceUnavailableError("Drive create response was lost; marker lookup is indeterminate")
    return port.create_folder_with_marker(parent_id, name, marker)


def _metadata(value: Any) -> DriveMetadata:
    if not isinstance(value, dict):
        raise SourceUnavailableError("Drive metadata is malformed")
    file_id = value.get("id")
    name = value.get("name")
    mime_type = value.get("mimeType")
    parents = value.get("parents", [])
    if not isinstance(file_id, str) or not file_id or not isinstance(name, str) or not name or not isinstance(mime_type, str) or not isinstance(parents, list) or any(not isinstance(item, str) for item in parents):
        raise SourceUnavailableError("Drive metadata identity is malformed")
    properties = value.get("appProperties", {})
    if not isinstance(properties, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in properties.items()):
        raise SourceUnavailableError("Drive appProperties are malformed")
    size = value.get("size")
    if size is not None:
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise SourceUnavailableError("Drive size is malformed") from None
    raw_permissions = value.get("permissions")
    permission_types: tuple[str, ...] = ()
    permission_roles: tuple[tuple[str, str], ...] = ()
    permission_count: int | None = None
    owner_only: bool | None = None
    is_publicly_shared: bool | None = None
    if raw_permissions is not None:
        if not isinstance(raw_permissions, list):
            raise SourceUnavailableError("Drive permissions are malformed")
        parsed_permissions: list[str] = []
        parsed_roles: list[tuple[str, str]] = []
        for permission in raw_permissions:
            if (
                not isinstance(permission, dict)
                or not isinstance(permission.get("type"), str)
                or not isinstance(permission.get("role"), str)
            ):
                raise SourceUnavailableError("Drive permission entry is malformed")
            parsed_permissions.append(permission["type"])
            parsed_roles.append((permission["type"], permission["role"]))
        permission_types = tuple(sorted(set(parsed_permissions)))
        permission_roles = tuple(sorted(parsed_roles))
        permission_count = len(parsed_roles)
        owner_only = permission_count == 1 and parsed_roles == [("user", "owner")]
        # Domain sharing is treated as broad sharing for this private intake
        # workspace.  User/group permissions remain represented in the
        # readback without exposing their identities to the ledger.
        is_publicly_shared = any(
            value in {"anyone", "domain"} for value in permission_types
        )
    capabilities = value.get("capabilities")
    can_edit: bool | None = None
    can_move: bool | None = None
    if capabilities is not None:
        if not isinstance(capabilities, dict):
            raise SourceUnavailableError("Drive capabilities are malformed")
        if "canEdit" in capabilities:
            can_edit = capabilities["canEdit"] if isinstance(capabilities["canEdit"], bool) else None
            if can_edit is None:
                raise SourceUnavailableError("Drive canEdit capability is malformed")
        if "canMoveItemWithinDrive" in capabilities:
            can_move = (
                capabilities["canMoveItemWithinDrive"]
                if isinstance(capabilities["canMoveItemWithinDrive"], bool)
                else None
            )
            if can_move is None:
                raise SourceUnavailableError("Drive canMove capability is malformed")
    return DriveMetadata(
        file_id=file_id,
        name=name,
        mime_type=mime_type,
        parents=tuple(parents),
        modified_time=value.get("modifiedTime") if isinstance(value.get("modifiedTime"), str) else None,
        size=size,
        trashed=value.get("trashed") is True,
        owned_by_me=value.get("ownedByMe") if isinstance(value.get("ownedByMe"), bool) else None,
        web_view_link=value.get("webViewLink") if isinstance(value.get("webViewLink"), str) else None,
        md5_checksum=value.get("md5Checksum") if isinstance(value.get("md5Checksum"), str) else None,
        drive_id=value.get("driveId") if isinstance(value.get("driveId"), str) else None,
        permission_types=permission_types,
        permission_roles=permission_roles,
        permission_count=permission_count,
        owner_only=owner_only,
        is_publicly_shared=is_publicly_shared,
        can_edit=can_edit,
        can_move=can_move,
        app_properties=dict(properties),
    )


def _search_pair(marker: dict[str, str]) -> tuple[str, str]:
    if "uls_t" in marker:
        return "uls_t", marker["uls_t"]
    if len(marker) == 1:
        return next(iter(marker.items()))
    raise ValueError("marker search requires a compact uls_t tuple digest")


def _quote(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _require_id(value: Any) -> None:
    if not isinstance(value, str) or _FILE_ID.fullmatch(value) is None:
        raise ValueError("invalid Drive file ID")


__all__ = [
    "DRIVE_FOLDER_MIME",
    "DriveMetadata",
    "DriveWorkerCapabilities",
    "DriveWorkerPort",
    "GoogleDriveWorkerAdapter",
    "InMemoryDriveWorker",
    "UnsupportedConnectorDrivePort",
    "ensure_marked_folder",
]
