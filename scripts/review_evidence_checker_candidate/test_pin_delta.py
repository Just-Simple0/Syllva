"""Private candidate regressions; exact reviewed tuples are filled after freeze."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest
import test_state_check as prior

CANDIDATE = prior.CANDIDATE
BASELINE = Path(__file__).resolve().parent / 'checker_active_baseline.py'
REPLACED = (
    'src/uls/settings/credential_service.py',
    'src/uls/settings/credential_admission.py',
    'tests/contract/test_settings_credential_admission.py',
)
ADDED = (
    'tests/contract/test_settings_credential_journal.py',
    'tests/contract/test_settings_credential_http.py',
)
# This is deliberately not derived from a task record or source inventory.
# Pending values cannot pass acceptance and must be independently fixed after
# the product source and tests have been frozen and reviewed.
EXPECTED_REVIEWED_DELTA = {
    REPLACED[0]: ('ff2c8716b413d484b4bde3e1c70600df395ee0c9703fa2ee479081adeb889a8f', 48443),
    REPLACED[1]: ('725b8098c8653b62853a5b15b1e93a30f2817058d4d25d87ab65e47b23586ff7', 50977),
    REPLACED[2]: ('9a9676ef0d59f3b7d66fb4e80313b6fb1d3e80af98382c4019b856e7794583c3', 76850),
    ADDED[0]: ('c18e9da27c482fd3c794472e2b32091b1a16f08963532d5abfee4ebed3b0583c', 41015),
    ADDED[1]: ('c2c2608c302a30ef6be4ec66c9528ab8d9959297c4eeba4883effc61229f7327', 18390),
}

SYNTHETIC = b'"""Synthetic reviewed test-only source."""\n'


def _baseline_tree():
    return ast.parse(BASELINE.read_text())


def _pin_literal(tree):
    matches = [n for n in tree.body if isinstance(n, ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == 'PINNED_SOURCE_PINS' for t in n.targets)]
    assert len(matches) == 1
    return matches[0], ast.literal_eval(matches[0].value.args[0])


def test_exact_reviewed_map_delta_and_entire_other_module_ast_unchanged():
    old_tree = _baseline_tree()
    new_tree = ast.parse(Path(CANDIDATE.__file__).read_text())
    old_node, old_map = _pin_literal(old_tree)
    new_node, new_map = _pin_literal(new_tree)
    assert all(CANDIDATE.HEX256.fullmatch(digest) and size > 0
               for digest, size in EXPECTED_REVIEWED_DELTA.values())
    expected = dict(old_map)
    expected.update(EXPECTED_REVIEWED_DELTA)
    assert new_map == expected == dict(CANDIDATE.PINNED_SOURCE_PINS)
    assert set(new_map) - set(old_map) == set(ADDED)
    assert {p for p in old_map if new_map[p] != old_map[p]} == set(REPLACED)
    old_tree.body.remove(old_node)
    new_tree.body.remove(new_node)
    assert ast.dump(old_tree, include_attributes=False) == ast.dump(new_tree, include_attributes=False)


def test_review_only_inventory_matches_compiled_literal_and_actual_frozen_source():
    fixture = json.loads(Path(CANDIDATE.__file__).with_name('source_pins.json').read_text())
    entries = fixture['sources']
    assert len(entries) == len({e['path'] for e in entries}) == 11
    assert {e['path']: (e['sha256'], e['size_bytes']) for e in entries} == dict(CANDIDATE.PINNED_SOURCE_PINS)
    assert fixture['purpose'] == 'review-only source inventory; not runtime permission input'
    assert fixture['runtime_authority'].startswith('none;')
    binding = fixture['root_binding']
    assert binding == {'path': CANDIDATE.PINNED_SOURCE_ROOT,
                       'device': CANDIDATE.PINNED_SOURCE_ROOT_DEVICE,
                       'inode': CANDIDATE.PINNED_SOURCE_ROOT_INODE}
    root = Path(binding['path'])
    for path, (sha, size) in EXPECTED_REVIEWED_DELTA.items():
        data = CANDIDATE._read_pinned_source(root, path, sha)
        assert len(data) == size and hashlib.sha256(data).hexdigest() == sha


@pytest.mark.parametrize('path', ADDED)
def test_new_exact_synthetic_source_passes_but_artifact_reuse_fails(path):
    with prior.tempfile.TemporaryDirectory() as temporary:
        root = (Path(temporary) / 'repo').resolve()
        root.mkdir()
        record = prior._make_valid_record(root, source_path=path, source_data=SYNTHETIC)
        digest = hashlib.sha256(SYNTHETIC).hexdigest()
        with prior._bind_candidate(root, {path: (digest, len(SYNTHETIC))}):
            CANDIDATE.validate(str(root), 'task.json')
            forged = copy.deepcopy(record)
            forged['package'] = {'path': path, 'sha256': digest}
            prior._write_json(root, 'task.json', forged)
            with pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE.validate(str(root), 'task.json')
            with pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_relative(root, path)


@pytest.mark.parametrize('path', ADDED)
def test_new_exact_source_forged_digest_size_root_and_bytes_rejected(path):
    with prior.tempfile.TemporaryDirectory() as temporary:
        root = (Path(temporary) / 'repo').resolve()
        root.mkdir()
        digest = hashlib.sha256(SYNTHETIC).hexdigest()
        prior._make_source(root, path, SYNTHETIC)
        with prior._bind_candidate(root, {path: (digest, len(SYNTHETIC))}):
            with prior._spy_open() as opens, pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, path, '0' * 64)
            assert opens == []
            wrong = root.with_name('other-repo')
            wrong.mkdir()
            with prior._spy_open() as opens, pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(wrong, path, digest)
            assert opens == []
            (root / path).write_bytes(SYNTHETIC + b'x')
            with mock.patch.object(CANDIDATE.os, 'read', side_effect=AssertionError('content read before size validation')):
                with pytest.raises(CANDIDATE.InvalidRecord):
                    CANDIDATE._read_pinned_source(root, path, digest)
            (root / path).write_bytes(b'x' * len(SYNTHETIC))
            with pytest.raises(CANDIDATE.InvalidRecord):
                CANDIDATE._read_pinned_source(root, path, digest)


@pytest.mark.parametrize('path', REPLACED)
def test_old_changed_sha_is_not_accepted_as_current_reviewed_source(path):
    old = _pin_literal(_baseline_tree())[1][path][0]
    assert EXPECTED_REVIEWED_DELTA[path][0] != old
    root = Path(CANDIDATE.PINNED_SOURCE_ROOT)
    with prior._spy_open() as opens, pytest.raises(CANDIDATE.InvalidRecord):
        CANDIDATE._read_pinned_source(root, path, old)
    assert opens == []
