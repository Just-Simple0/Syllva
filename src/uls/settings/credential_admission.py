"""Per-user physical-binding admission shared by GUI, recovery and CLI writers."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from uls.orchestration.locks import LocalFileLock

from .journal import TERMINAL_PHASES, JournalError, JournalStore, OperationInProgress


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _private_dir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise OperationInProgress()
    # A prior creator may have exited after mkdir but before syncing the entry.
    # Revalidate and durably sync both levels on every admission, not only creation.
    _fsync(path)
    _fsync(path.parent)


def _read(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 8192:
            raise OperationInProgress()
        try:
            value = json.loads(os.read(fd, 8193))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise OperationInProgress() from exc
    finally:
        os.close(fd)
    if not isinstance(value, dict) or set(value) != {"schema", "complete", "operation_id", "binding_key", "journal", "config_path", "config_dir_id"}:
        raise OperationInProgress()
    if value["schema"] != 1 or value["complete"] is not True:
        raise OperationInProgress()
    return value


class Admission:
    def __init__(self, directory: Path, key: str, journal: JournalStore | None, config_path: Path | None,
                 *, fault_hook: Callable[[str], None] | None = None) -> None:
        self.directory, self.key = directory, key
        self.journal, self.config_path = journal, config_path
        self.path = directory / f"binding-{key}.reservation"
        self.fault_hook = fault_hook

    def publish(self, operation_id: str, config_dir_id: str) -> None:
        if self.journal is None or self.config_path is None:
            raise ValueError("CLI admission cannot publish a journal reservation")
        record = {"schema": 1, "complete": True, "operation_id": operation_id, "binding_key": self.key,
                  "journal": str(self.journal.directory.resolve()), "config_path": str(self.config_path.resolve()),
                  "config_dir_id": config_dir_id}
        temp = self.directory / f".tmp_binding-{self.key}_{secrets.token_hex(8)}"
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            data = json.dumps(record).encode()
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            if self.fault_hook:
                self.fault_hook("reservation_written")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.link(temp, self.path, follow_symlinks=False)
        if self.fault_hook:
            self.fault_hook("reservation_linked")
        temp.unlink()
        _fsync(self.directory)
        if self.fault_hook:
            self.fault_hook("reservation_published")

    def release(self, operation_id: str) -> None:
        if self.journal is None or self.journal.read(operation_id)["phase"] not in TERMINAL_PHASES:
            raise OperationInProgress()
        if _read(self.path)["operation_id"] != operation_id:
            raise OperationInProgress()
        self.path.unlink()
        _fsync(self.directory)


@contextmanager
def credential_admission(root: Path, locator: str, *, journal: JournalStore | None = None,
                         config_path: Path | None = None, recovery_id: str | None = None,
                         config_dir_id: str | None = None, fault_hook: Callable[[str], None] | None = None) -> Iterator[Admission]:
    """Existing-operation recovery is admitted only for its exact durable reservation."""
    try:
        root.mkdir(parents=True, mode=0o700)
        _fsync(root.parent)
    except FileExistsError:
        pass
    _private_dir(root)
    directory = root / "admission"
    _private_dir(directory)
    key = hashlib.sha256(locator.encode()).hexdigest()
    lock = LocalFileLock(directory / f"binding-{key}.lock")
    if not lock.acquire(timeout=0):
        raise OperationInProgress()
    admission = Admission(directory, key, journal, config_path, fault_hook=fault_hook)
    try:
        if list(directory.glob(f".tmp_binding-{key}_*")):
            raise OperationInProgress()
        if admission.path.exists() or admission.path.is_symlink():
            try:
                reservation = _read(admission.path)
                named_journal = Path(reservation["journal"])
                # Read the named record with the same owner/shape validation.
                other = object.__new__(JournalStore)
                other.directory = named_journal
                other.credential_root = root
                record = other.read(reservation["operation_id"])
                if (reservation["binding_key"] != key or record["binding"]["store_locator"] != locator
                        or record["binding"]["config_path"] != reservation["config_path"]
                        or record["binding"]["config_dir_id"] != reservation["config_dir_id"]):
                    raise OperationInProgress()
                if recovery_id is not None:
                    if (journal is None or config_path is None or record["operation_id"] != recovery_id
                            or named_journal != journal.directory.resolve()
                            or reservation["config_path"] != str(config_path.resolve())
                            or reservation["config_dir_id"] != config_dir_id
                            or record["binding"]["config_path"] != reservation["config_path"]
                            or record["binding"]["config_dir_id"] != config_dir_id):
                        raise OperationInProgress()
                elif record["phase"] not in TERMINAL_PHASES:
                    raise OperationInProgress()
                else:
                    admission.path.unlink()
                    _fsync(directory)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise OperationInProgress() from exc
        elif recovery_id is not None:
            raise OperationInProgress()
        if journal is not None:
            for record in journal.unresolved():
                if record["operation_id"] == recovery_id:
                    continue
                if record["phase"] == "unreadable" or record["binding"].get("store_locator") == locator:
                    raise OperationInProgress()
        yield admission
    finally:
        lock.release()


def credential_recovery_admission(root: Path, locator: str, *, operation_id: str, journal: JournalStore,
                                  config_path: Path, config_dir_id: str) -> Any:
    return credential_admission(root, locator, journal=journal, config_path=config_path,
                                recovery_id=operation_id, config_dir_id=config_dir_id)


_PAIR_FIELDS = {
    "schema", "kind", "complete", "operation_id", "pair_key", "provider", "profile",
    "binding_key", "journal", "config_path", "config_dir_id", "legacy_bridge",
}
_PHYSICAL_FIELDS_V2 = {
    "schema", "kind", "complete", "operation_id", "binding_key", "pair_key",
    "journal", "config_path", "config_dir_id",
}


def _read_json(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size > 8192):
            raise OperationInProgress()
        try:
            value = json.loads(os.read(fd, 8193))
        except (ValueError, TypeError, UnicodeError) as exc:
            raise OperationInProgress() from exc
    finally:
        os.close(fd)
    if not isinstance(value, dict):
        raise OperationInProgress()
    return value


def _read_physical(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if set(value) == {"schema", "complete", "operation_id", "binding_key", "journal", "config_path", "config_dir_id"}:
        if (type(value["schema"]) is not int or value["schema"] != 1 or value["complete"] is not True
                or any(not isinstance(value[key], str) or not value[key]
                       for key in ("operation_id", "binding_key", "journal", "config_path", "config_dir_id"))):
            raise OperationInProgress()
    elif set(value) == _PHYSICAL_FIELDS_V2:
        if (type(value["schema"]) is not int or value["schema"] != 2
                or value["kind"] != "physical_binding" or value["complete"] is not True
                or any(not isinstance(value[key], str) or not value[key]
                       for key in ("operation_id", "binding_key", "pair_key", "journal",
                                   "config_path", "config_dir_id"))):
            raise OperationInProgress()
    else:
        raise OperationInProgress()
    return value


def _read_pair(path: Path) -> dict[str, Any]:
    value = _read_json(path)
    if (set(value) != _PAIR_FIELDS or type(value["schema"]) is not int or value["schema"] != 2
            or value["kind"] != "credential_pair" or value["complete"] is not True
            or type(value["legacy_bridge"]) is not bool
            or any(not isinstance(value[key], str) or not value[key]
                   for key in ("operation_id", "pair_key", "provider", "profile", "binding_key",
                               "journal", "config_path", "config_dir_id"))):
        raise OperationInProgress()
    return value


def _journal_record(root: Path, journal_path: str, operation_id: str) -> dict[str, Any]:
    try:
        path = Path(journal_path)
        if not path.is_absolute() or str(path.resolve()) != journal_path:
            raise OperationInProgress()
        other = object.__new__(JournalStore)
        other.directory = path
        other.credential_root = root
        return other.read(operation_id)
    except OperationInProgress:
        raise
    except (JournalError, OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise OperationInProgress() from exc


def _pair_digest(provider: str, profile: str) -> str:
    return hashlib.sha256(f"{provider}/{profile}".encode()).hexdigest()


def _marker_record(marker: dict[str, Any], *, root: Path, key: str, locator: str,
                   provider: str, profile: str) -> dict[str, Any]:
    if (marker.get("binding_key") != key or not isinstance(marker.get("operation_id"), str)
            or not isinstance(marker.get("journal"), str) or not isinstance(marker.get("config_path"), str)
            or not isinstance(marker.get("config_dir_id"), str)):
        raise OperationInProgress()
    record = _journal_record(root, marker["journal"], marker["operation_id"])
    binding = record["binding"]
    if (record["operation_id"] != marker["operation_id"]
            or binding.get("store_locator") != locator
            or binding.get("provider") != provider or binding.get("profile") != profile
            or binding.get("config_path") != marker["config_path"]
            or binding.get("config_dir_id") != marker["config_dir_id"]):
        raise OperationInProgress()
    return record


def _publish_marker(directory: Path, path: Path, record: dict[str, Any], *,
                    temporary_prefix: str, fault_hook: Callable[[str], None] | None,
                    event_prefix: str) -> None:
    temp = directory / f".{temporary_prefix}_{secrets.token_hex(8)}"
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        data = json.dumps(record, sort_keys=True).encode()
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short reservation write")
            view = view[written:]
        if fault_hook:
            fault_hook(f"{event_prefix}_written")
        os.fsync(fd)
    finally:
        os.close(fd)
    os.link(temp, path, follow_symlinks=False)
    if fault_hook:
        fault_hook(f"{event_prefix}_linked")
    temp.unlink()
    _fsync(directory)
    if fault_hook:
        fault_hook(f"{event_prefix}_published")


class CredentialPairAdmission:
    """One per-user provider/profile admission plus both physical role locks."""

    def __init__(self, root: Path, directory: Path, provider: str, profile: str,
                 locators: dict[str, str], journal: JournalStore, config_path: Path,
                 *, recovery_id: str | None, config_dir_id: str | None,
                 fault_hook: Callable[[str], None] | None = None) -> None:
        self.root, self.directory = root, directory
        self.provider, self.profile = provider, profile
        self.locators = locators
        self.journal, self.config_path = journal, config_path
        self.recovery_id, self.config_dir_id = recovery_id, config_dir_id
        self.fault_hook = fault_hook
        self.pair_key = _pair_digest(provider, profile)
        self.path = directory / f"pair-{self.pair_key}.reservation"
        self.target_key: str | None = None

    def _pair_record(self, operation_id: str, binding_key: str, config_dir_id: str,
                     *, legacy_bridge: bool) -> dict[str, Any]:
        return {
            "schema": 2, "kind": "credential_pair", "complete": True,
            "operation_id": operation_id, "pair_key": self.pair_key,
            "provider": self.provider, "profile": self.profile,
            "binding_key": binding_key,
            "journal": str(self.journal.directory.resolve()),
            "config_path": str(self.config_path.resolve()),
            "config_dir_id": config_dir_id, "legacy_bridge": legacy_bridge,
        }

    def _physical_record(self, operation_id: str, key: str, config_dir_id: str) -> dict[str, Any]:
        return {
            "schema": 2, "kind": "physical_binding", "complete": True,
            "operation_id": operation_id, "binding_key": key, "pair_key": self.pair_key,
            "journal": str(self.journal.directory.resolve()),
            "config_path": str(self.config_path.resolve()),
            "config_dir_id": config_dir_id,
        }

    def publish(self, operation_id: str, binding: dict[str, Any]) -> None:
        """Durably reserve the target physical slot and its shared pair before journal creation."""
        if self.recovery_id is not None:
            raise ValueError("recovery admission cannot publish a new operation")
        locator = binding.get("store_locator")
        if (not isinstance(locator, str) or locator not in self.locators.values()
                or binding.get("provider") != self.provider or binding.get("profile") != self.profile):
            raise OperationInProgress()
        binding_key = hashlib.sha256(locator.encode()).hexdigest()
        config_dir_id = binding.get("config_dir_id")
        config_binding_path = binding.get("config_path")
        if (not isinstance(config_dir_id, str) or config_binding_path != str(self.config_path.resolve())
                or self.path.exists() or self.path.is_symlink()):
            raise OperationInProgress()
        physical_path = self.directory / f"binding-{binding_key}.reservation"
        if physical_path.exists() or physical_path.is_symlink():
            raise OperationInProgress()
        _publish_marker(self.directory, physical_path, self._physical_record(operation_id, binding_key, config_dir_id),
                        temporary_prefix=f"tmp_binding-{binding_key}", fault_hook=self.fault_hook,
                        event_prefix="reservation")
        _publish_marker(self.directory, self.path,
                        self._pair_record(operation_id, binding_key, config_dir_id, legacy_bridge=False),
                        temporary_prefix=f"tmp_pair-{self.pair_key}", fault_hook=self.fault_hook,
                        event_prefix="pair_reservation")
        self.target_key = binding_key

    def _linked_records(self, pair: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], Path]:
        if (pair.get("pair_key") != self.pair_key or pair.get("provider") != self.provider
                or pair.get("profile") != self.profile or pair.get("binding_key") not in self.locators):
            raise OperationInProgress()
        key = pair["binding_key"]
        locator = self.locators[key]
        pair_record = _journal_record(self.root, pair["journal"], pair["operation_id"])
        physical_path = self.directory / f"binding-{key}.reservation"
        physical = _read_physical(physical_path)
        record = _marker_record(physical, root=self.root, key=key, locator=locator,
                                provider=self.provider, profile=self.profile)
        binding = pair_record["binding"]
        if (pair_record["operation_id"] != pair["operation_id"]
                or binding.get("store_locator") != locator
                or binding.get("provider") != self.provider or binding.get("profile") != self.profile
                or binding.get("config_path") != pair["config_path"]
                or binding.get("config_dir_id") != pair["config_dir_id"]
                or str(Path(physical["journal"]).resolve()) != pair["journal"]
                or physical["operation_id"] != pair["operation_id"]
                or physical["config_path"] != pair["config_path"]
                or physical["config_dir_id"] != pair["config_dir_id"]):
            raise OperationInProgress()
        if physical["schema"] == 2:
            if physical["pair_key"] != self.pair_key or pair["legacy_bridge"]:
                raise OperationInProgress()
        elif not pair["legacy_bridge"]:
            raise OperationInProgress()
        return pair_record, record, physical_path

    def _legacy_bridge_record(self, marker: dict[str, Any], record: dict[str, Any],
                              key: str) -> dict[str, Any]:
        operation_id = self.recovery_id
        if (operation_id is None or marker.get("schema") != 1 or marker.get("operation_id") != operation_id
                or self.journal.directory.resolve() != Path(marker["journal"]).resolve()
                or self.config_path.resolve() != Path(marker["config_path"]).resolve()
                or marker.get("config_dir_id") != self.config_dir_id
                or record["operation_id"] != operation_id):
            raise OperationInProgress()
        binding = record["binding"]
        if (binding.get("store_locator") != self.locators[key]
                or binding.get("provider") != self.provider or binding.get("profile") != self.profile
                or binding.get("config_path") != marker["config_path"]
                or binding.get("config_dir_id") != self.config_dir_id):
            raise OperationInProgress()
        return self._pair_record(operation_id, key, self.config_dir_id or "", legacy_bridge=True)

    def _bridge_legacy(self, pair: dict[str, Any], key: str) -> None:
        _publish_marker(self.directory, self.path, pair,
                        temporary_prefix=f"tmp_pair-{self.pair_key}", fault_hook=self.fault_hook,
                        event_prefix="pair_reservation")
        self.target_key = key

    def release(self, operation_id: str) -> None:
        try:
            if not (self.path.exists() or self.path.is_symlink()):
                raise OperationInProgress()
            pair = _read_pair(self.path)
            if pair["operation_id"] != operation_id:
                raise OperationInProgress()
            pair_record, _record, physical_path = self._linked_records(pair)
            if pair_record["phase"] not in TERMINAL_PHASES:
                raise OperationInProgress()
            self.path.unlink()
            _fsync(self.directory)
            physical_path.unlink()
            _fsync(self.directory)
            self.target_key = None
        except OperationInProgress:
            raise
        except (JournalError, OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise OperationInProgress() from exc


@contextmanager
def credential_pair_admission(root: Path, provider: str, profile: str, locators: list[str], *,
                              journal: JournalStore, config_path: Path,
                              recovery_id: str | None = None, config_dir_id: str | None = None,
                              fault_hook: Callable[[str], None] | None = None) -> Iterator[CredentialPairAdmission]:
    """Acquire pair then both physical locks; preserve uncertain evidence for exact recovery."""
    if provider not in {"notion", "google"} or not profile or len(set(locators)) != 2:
        raise ValueError("credential pair identity is invalid")
    try:
        root.mkdir(parents=True, mode=0o700)
        _fsync(root.parent)
    except FileExistsError:
        pass
    _private_dir(root)
    directory = root / "admission"
    _private_dir(directory)
    keyed = {hashlib.sha256(locator.encode()).hexdigest(): locator for locator in locators}
    pair_key = _pair_digest(provider, profile)
    pair_lock = LocalFileLock(directory / f"pair-{pair_key}.lock")
    if not pair_lock.acquire(timeout=0):
        raise OperationInProgress()
    physical_locks: list[LocalFileLock] = []
    admission = CredentialPairAdmission(root, directory, provider, profile, keyed, journal, config_path,
                                        recovery_id=recovery_id, config_dir_id=config_dir_id,
                                        fault_hook=fault_hook)
    try:
        for key in sorted(keyed):
            lock = LocalFileLock(directory / f"binding-{key}.lock")
            if not lock.acquire(timeout=0):
                raise OperationInProgress()
            physical_locks.append(lock)

        if list(directory.glob(f".tmp_pair-{pair_key}_*")):
            raise OperationInProgress()
        for key in keyed:
            if list(directory.glob(f".tmp_binding-{key}_*")):
                raise OperationInProgress()

        pair_present = admission.path.exists() or admission.path.is_symlink()
        pair: dict[str, Any] | None = _read_pair(admission.path) if pair_present else None
        pair_record: dict[str, Any] | None = None
        pair_physical_path: Path | None = None
        retire_pair = False
        if pair is not None:
            pair_record, _record, pair_physical_path = admission._linked_records(pair)
            phase = pair_record["phase"]
            if recovery_id is not None:
                exact_recovery = (
                    pair["operation_id"] == recovery_id
                    and admission.journal.directory.resolve() == Path(pair["journal"]).resolve()
                    and admission.config_path.resolve() == Path(pair["config_path"]).resolve()
                    and pair["config_dir_id"] == config_dir_id
                )
                if not exact_recovery:
                    if phase not in TERMINAL_PHASES:
                        raise OperationInProgress()
                    retire_pair = True
            elif phase in TERMINAL_PHASES:
                retire_pair = True
            else:
                raise OperationInProgress()
            if not retire_pair:
                admission.target_key = pair["binding_key"]

        # Snapshot every physical marker and its named journal before cleanup or bridge publication.
        physical_records: dict[str, tuple[Path, dict[str, Any], dict[str, Any]]] = {}
        for key, locator in keyed.items():
            physical_path = directory / f"binding-{key}.reservation"
            if not (physical_path.exists() or physical_path.is_symlink()):
                continue
            marker = _read_physical(physical_path)
            record = _marker_record(marker, root=root, key=key, locator=locator,
                                    provider=provider, profile=profile)
            physical_records[key] = (physical_path, marker, record)

        stale_paths: list[Path] = []
        bridge_pair: dict[str, Any] | None = None
        bridge_key: str | None = None
        pair_target_key = pair["binding_key"] if pair is not None else None
        pair_target_path: Path | None = None
        for key, (physical_path, marker, record) in physical_records.items():
            if pair is not None and key == pair_target_key:
                pair_target_path = physical_path
                if marker["operation_id"] != pair["operation_id"]:
                    raise OperationInProgress()
                if marker["schema"] == 2:
                    if pair["legacy_bridge"] or marker["pair_key"] != pair_key:
                        raise OperationInProgress()
                elif not pair["legacy_bridge"]:
                    raise OperationInProgress()
                if (retire_pair and (pair_record is None
                                     or pair_record["phase"] not in TERMINAL_PHASES)):
                    raise OperationInProgress()
                continue

            if pair is not None and not retire_pair:
                if (marker["schema"] == 1 and pair["legacy_bridge"]
                        and record["phase"] in TERMINAL_PHASES):
                    stale_paths.append(physical_path)
                    continue
                raise OperationInProgress()

            if retire_pair:
                if recovery_id is not None and marker["schema"] == 1 and marker["operation_id"] == recovery_id:
                    candidate = admission._legacy_bridge_record(marker, record, key)
                    if bridge_pair is not None:
                        raise OperationInProgress()
                    bridge_pair = candidate
                    bridge_key = key
                    continue
                if marker["schema"] == 1 and record["phase"] in TERMINAL_PHASES:
                    stale_paths.append(physical_path)
                    continue
                raise OperationInProgress()

            if marker["schema"] == 2:
                raise OperationInProgress()
            if recovery_id is not None and marker["operation_id"] == recovery_id:
                candidate = admission._legacy_bridge_record(marker, record, key)
                if bridge_pair is not None:
                    raise OperationInProgress()
                bridge_pair = candidate
                bridge_key = key
                continue
            if record["phase"] in TERMINAL_PHASES:
                stale_paths.append(physical_path)
                continue
            raise OperationInProgress()

        # Journal-only blockers must also be known before a legacy pair can be published.
        for record in journal.unresolved():
            if record.get("phase") == "unreadable":
                raise OperationInProgress()
            binding = record.get("binding", {})
            if (record.get("operation_id") != recovery_id
                    and binding.get("provider") == provider and binding.get("profile") == profile):
                raise OperationInProgress()

        if retire_pair:
            if pair is None or pair_physical_path is None or pair_target_path is None:
                raise OperationInProgress()
            admission.path.unlink()
            _fsync(directory)
            pair_physical_path.unlink()
            _fsync(directory)
            pair = None

        for stale_path in stale_paths:
            stale_path.unlink()
            _fsync(directory)

        if bridge_pair is not None:
            if bridge_key is None:
                raise OperationInProgress()
            admission._bridge_legacy(bridge_pair, bridge_key)
            pair = bridge_pair

        if recovery_id is not None and pair is None:
            raise OperationInProgress()

        yield admission
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, OperationInProgress):
            raise
        raise OperationInProgress() from exc
    finally:
        for lock in reversed(physical_locks):
            lock.release()
        pair_lock.release()


def credential_pair_recovery_admission(root: Path, provider: str, profile: str, locators: list[str], *,
                                       operation_id: str, journal: JournalStore,
                                       config_path: Path, config_dir_id: str) -> Any:
    return credential_pair_admission(root, provider, profile, locators, journal=journal,
                                     config_path=config_path, recovery_id=operation_id,
                                     config_dir_id=config_dir_id)
