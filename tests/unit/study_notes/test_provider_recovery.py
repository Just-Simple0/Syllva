from copy import deepcopy

import pytest

from uls.adapters.drive.worker import DriveMetadata, DriveWorkerCapabilities
from uls.domain.errors import ProviderWriteNotAppliedError
from uls.study_notes.drive import StageAmbiguousError, StudyNoteDriveStaging
from uls.study_notes.notion import BlockCreateAmbiguousError, StudyNoteAIBlockBridge
from uls.study_notes.store import DifferentPayloadReplayError, StudyNoteStore

_PRIVATE = {
    "owned_by_me": True,
    "owner_only": True,
    "is_publicly_shared": False,
    "permission_count": 1,
    "permission_types": ("user",),
    "permission_roles": (("user", "owner"),),
    "can_edit": True,
    "can_move": True,
}


class SimulatedCrash(BaseException):
    pass


class LostResponseDrive:
    capabilities = DriveWorkerCapabilities(True, True, True, True, True)

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, dict[str, str], tuple[str, ...]]] = {}
        self.create_calls = 0

    def search_marker(self, marker):
        return [
            DriveMetadata(
                file_id=file_id, name="note.md", mime_type="text/markdown",
                parents=parents, app_properties=stored_marker, **_PRIVATE,
            )
            for file_id, (_, stored_marker, parents) in self.files.items()
            if stored_marker == marker
        ]

    def create_file_with_marker(self, parent_id, name, mime_type, content, marker):
        self.create_calls += 1
        file_id = f"file-{self.create_calls}"
        self.files[file_id] = (content, dict(marker), (parent_id,))
        raise TimeoutError("response lost after commit")

    def read_metadata(self, file_id):
        _, marker, parents = self.files[file_id]
        return DriveMetadata(
            file_id=file_id, name="note.md", mime_type="text/markdown",
            parents=parents, app_properties=marker, **_PRIVATE,
        )

    def download(self, file_id):
        return self.files[file_id][0]


def test_drive_lost_first_response_reconciles_exact_intent_without_second_create(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    drive = LostResponseDrive()
    staging = StudyNoteDriveStaging(drive, store)
    try:
        first = staging.stage(
            note_key="note", attempt_no=1, parent_folder_id="parent",
            filename="note.md", content=b"body", manifest_hash="manifest",
        )
        assert first["state"] == "UNKNOWN"
        receipt = store.get_stage_receipt("note", 1)
        assert receipt is not None and receipt["content_hash"]
        assert '"uls_p":"parent"' in receipt["marker_json"]

        reconciled = staging.reconcile_unknown(note_key="note", attempt_no=1)
        assert reconciled["state"] == "VERIFIED"
        assert reconciled["file_id"] == "file-1"
        assert drive.create_calls == 1

        content, marker, parents = drive.files["file-1"]
        drive.files["file-1"] = (content + b" edited", marker, parents)
        with pytest.raises(StageAmbiguousError):
            staging.reuse_verified_artifact(
                note_key="note", output_identity="file-1",
                output_hash=reconciled["content_hash"], manifest_hash="manifest",
            )
    finally:
        store.close()


class CrashAfterCommitDrive(LostResponseDrive):
    def search_marker(self, marker):
        return []

    def create_file_with_marker(self, parent_id, name, mime_type, content, marker):
        self.create_calls += 1
        file_id = f"file-{self.create_calls}"
        self.files[file_id] = (content, dict(marker), (parent_id,))
        raise SimulatedCrash("process died after remote commit")


def test_drive_crash_after_remote_commit_is_unknown_before_dispatch_and_never_recreates(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    drive = CrashAfterCommitDrive()
    staging = StudyNoteDriveStaging(drive, store)
    kwargs = {
        "note_key": "note", "attempt_no": 1, "parent_folder_id": "parent",
        "filename": "note.md", "content": b"body", "manifest_hash": "manifest",
    }
    try:
        with pytest.raises(SimulatedCrash):
            staging.stage(**kwargs)
        assert store.get_stage_receipt("note", 1)["state"] == "UNKNOWN"
        with pytest.raises(StageAmbiguousError):
            staging.reconcile_unknown(note_key="note", attempt_no=1)
        with pytest.raises(StageAmbiguousError):
            staging.stage(**kwargs)
        assert drive.create_calls == 1
    finally:
        store.close()


class DownloadFailureDrive(LostResponseDrive):
    def create_file_with_marker(self, parent_id, name, mime_type, content, marker):
        self.create_calls += 1
        file_id = f"file-{self.create_calls}"
        self.files[file_id] = (content, dict(marker), (parent_id,))
        return self.read_metadata(file_id)

    def download(self, file_id):
        raise OSError("post-create content read failed")


def test_drive_post_create_download_failure_stays_unknown_and_never_recreates(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    drive = DownloadFailureDrive()
    staging = StudyNoteDriveStaging(drive, store)
    kwargs = {
        "note_key": "note", "attempt_no": 1, "parent_folder_id": "parent",
        "filename": "note.md", "content": b"body", "manifest_hash": "manifest",
    }
    try:
        first = staging.stage(**kwargs)
        assert first["state"] == "UNKNOWN"
        with pytest.raises(StageAmbiguousError):
            staging.stage(**kwargs)
        assert drive.create_calls == 1
    finally:
        store.close()


class CrashFolderDrive:
    capabilities = DriveWorkerCapabilities(True, True, True, True, True)

    def __init__(self) -> None:
        self.create_calls = 0

    def search_marker(self, marker):
        return []

    def create_folder_with_marker(self, parent_id, name, marker):
        self.create_calls += 1
        raise SimulatedCrash("folder committed before process death")


def test_folder_crash_window_is_unknown_and_zero_search_cannot_duplicate_create(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    drive = CrashFolderDrive()
    staging = StudyNoteDriveStaging(drive, store)
    try:
        with pytest.raises(SimulatedCrash):
            staging.resolve_note_folder(session_derived_folder_id="derived", note_key="note")
        receipt = next(iter(store._connection.execute("SELECT * FROM note_folder_receipts")))
        assert receipt["state"] == "UNKNOWN"
        with pytest.raises(StageAmbiguousError):
            staging.resolve_note_folder(session_derived_folder_id="derived", note_key="note")
        assert drive.create_calls == 1
    finally:
        store.close()


class LostResponseBlocks:
    def __init__(self, *, commit: bool = True) -> None:
        self.commit = commit
        self.blocks: dict[str, dict] = {}
        self.appends = 0

    def append_block_children(self, parent_block_id, children):
        self.appends += 1
        if self.commit:
            self.blocks["block-1"] = deepcopy(children[0])
        raise TimeoutError("response lost")

    def list_block_children(self, block_id, *, start_cursor=None):
        return {
            "results": [{"id": block_id, **deepcopy(block)}
                        for block_id, block in self.blocks.items()],
            "has_more": False,
            "next_cursor": None,
        }

    def retrieve_block(self, block_id):
        return {"id": block_id, **deepcopy(self.blocks[block_id])}

    def update_block(self, block_id, **fields):
        self.blocks[block_id] = deepcopy(fields)
        return self.retrieve_block(block_id)


class CrashResponseBlocks(LostResponseBlocks):
    def append_block_children(self, parent_block_id, children):
        self.appends += 1
        self.blocks["block-1"] = deepcopy(children[0])
        raise SimulatedCrash("process died after append commit")


class SuccessfulBlocks(LostResponseBlocks):
    def append_block_children(self, parent_block_id, children):
        self.appends += 1
        block = deepcopy(children[0])
        self.blocks["block-1"] = block
        return {"results": [{"id": "block-1", **deepcopy(block)}]}


class NoEffectBlocks(SuccessfulBlocks):
    def append_block_children(self, parent_block_id, children):
        self.appends += 1
        raise ProviderWriteNotAppliedError("provider proves append did not apply")


def test_notion_lost_first_append_blocks_retry_and_adopts_only_exact_match(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    port = LostResponseBlocks()
    bridge = StudyNoteAIBlockBridge(port, store)
    pointer = {"note_key": "note", "coverage": "FULL"}
    try:
        first = bridge.write_study_note_ai_block(
            session_id="session", note_key="note", attempt_no=1, pointer=pointer,
        )
        assert first["state"] == "UNKNOWN"
        with pytest.raises(BlockCreateAmbiguousError):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="note", attempt_no=2, pointer=pointer,
            )
        reconciled = bridge.reconcile_unknown_block("session")
        assert reconciled["state"] == "KNOWN"
        assert reconciled["block_id"] == "block-1"
        assert port.appends == 1
    finally:
        store.close()


def test_notion_zero_match_after_ambiguous_append_remains_unknown(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    port = LostResponseBlocks(commit=False)
    bridge = StudyNoteAIBlockBridge(port, store)
    try:
        first = bridge.write_study_note_ai_block(
            session_id="session", note_key="note", attempt_no=1,
            pointer={"note_key": "note"},
        )
        assert first["state"] == "UNKNOWN"
        with pytest.raises(BlockCreateAmbiguousError):
            bridge.reconcile_unknown_block("session")
        assert store.get_ai_block("session")["state"] == "UNKNOWN"
        assert port.appends == 1
    finally:
        store.close()


def test_notion_crash_after_append_leaves_reconcilable_intent_without_second_append(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    port = CrashResponseBlocks()
    bridge = StudyNoteAIBlockBridge(port, store)
    pointer = {"note_key": "note", "coverage": "FULL"}
    try:
        with pytest.raises(SimulatedCrash):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="note", attempt_no=1, pointer=pointer,
            )
        assert store.get_ai_block("session")["state"] == "INTENT"
        with pytest.raises(BlockCreateAmbiguousError):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="note", attempt_no=1, pointer=pointer,
            )
        reconciled = bridge.reconcile_unknown_block("session")
        assert reconciled["state"] == "KNOWN" and reconciled["block_id"] == "block-1"
        assert port.appends == 1
    finally:
        store.close()


def test_notion_prewrite_rejection_is_retryable_only_for_exact_immutable_intent(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    port = SuccessfulBlocks()
    bridge = StudyNoteAIBlockBridge(port, store)
    pointer = {"note_key": "note", "coverage": "FULL"}
    try:
        bridge.before_write = lambda: (_ for _ in ()).throw(RuntimeError("freshness rejected"))
        with pytest.raises(RuntimeError, match="freshness rejected"):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="note", attempt_no=1, pointer=pointer,
            )
        row = store.get_ai_block("session")
        assert row["state"] == "NONE" and row["intent_payload_hash"]
        assert port.appends == 0
        bridge.before_write = lambda: None
        retried = bridge.write_study_note_ai_block(
            session_id="session", note_key="note", attempt_no=1, pointer=pointer,
        )
        assert retried["state"] == "KNOWN" and port.appends == 1
    finally:
        store.close()


def test_notion_typed_no_effect_cannot_rebind_retryable_intent(tmp_path):
    store = StudyNoteStore(tmp_path / "notes.db")
    port = NoEffectBlocks()
    bridge = StudyNoteAIBlockBridge(port, store)
    first_pointer = {"note_key": "note", "coverage": "FULL"}
    try:
        first = bridge.write_study_note_ai_block(
            session_id="session", note_key="note", attempt_no=1, pointer=first_pointer,
        )
        assert first["state"] == "NONE" and port.appends == 1
        with pytest.raises(DifferentPayloadReplayError):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="note", attempt_no=1,
                pointer={"note_key": "note", "coverage": "PARTIAL"},
            )
        with pytest.raises(DifferentPayloadReplayError):
            bridge.write_study_note_ai_block(
                session_id="session", note_key="other", attempt_no=2,
                pointer=first_pointer,
            )
        assert port.appends == 1
    finally:
        store.close()
