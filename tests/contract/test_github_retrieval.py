"""Exact-ref, bounded code and Activity integration acceptance (§47)."""
from __future__ import annotations

import base64
import hashlib

import pytest

from syllva.adapters.github.api import GitHubAPIReader, repository_name, repository_path
from syllva.domain.errors import (
    InvalidSubmissionRefError,
    SourcePartialError,
    SourceUnavailableError,
)

pytestmark = pytest.mark.contract
COMMIT, TREE = 'a' * 40, 'b' * 40
CONTENT = b'def solve():\n    return 42\n'
BLOB = hashlib.sha1(b'blob ' + str(len(CONTENT)).encode() + b'\0' + CONTENT).hexdigest()


def api():
    routes = {
        '/repos/student/project': {'full_name': 'student/project'},
        '/repos/student/project/commits/' + COMMIT: {'sha': COMMIT, 'commit': {'tree': {'sha': TREE}}},
        '/repos/student/project/git/ref/tags/v1': {'ref': 'refs/tags/v1', 'object': {'type': 'commit', 'sha': COMMIT}},
        '/repos/student/project/git/trees/' + TREE + '?recursive=1': {
            'sha': TREE, 'truncated': False, 'tree': [
                {'path': 'src/solve.py', 'type': 'blob', 'mode': '100644', 'sha': BLOB, 'size': len(CONTENT)},
                {'path': 'shortcut', 'type': 'blob', 'mode': '120000', 'sha': BLOB, 'size': len(CONTENT)},
                {'path': 'vendor', 'type': 'commit', 'mode': '160000', 'sha': COMMIT},
            ]},
        '/repos/student/project/git/blobs/' + BLOB: {
            'sha': BLOB, 'encoding': 'base64', 'content': base64.b64encode(CONTENT).decode()},
    }
    calls = []
    def get(route):
        calls.append(route)
        if route not in routes:
            raise SourceUnavailableError('not found')
        return routes[route]
    return GitHubAPIReader(get_json=get), routes, calls


@pytest.mark.parametrize('ref', [COMMIT, 'v1', 'refs/tags/v1'])
def test_reads_pinned_commit_never_default_branch(ref):
    reader, _, calls = api()
    code = reader.read_at_ref('https://github.com/student/project', 'src/solve.py', ref)
    assert code.content == CONTENT.decode()
    assert code.ref.commit_sha == COMMIT
    assert code.ref.submission_ref == ref
    assert all('main' not in call and 'heads/' not in call for call in calls)


@pytest.mark.parametrize('ref', ['', 'main', 'refs/heads/main', 'missing-tag', 'abc1234'])
def test_invalid_ref_never_substitutes_branch(ref):
    reader, _, calls = api()
    with pytest.raises(InvalidSubmissionRefError):
        reader.read_at_ref('student/project', 'src/solve.py', ref)
    assert not any('/git/trees/' in call for call in calls)


@pytest.mark.parametrize('value', ['https://github.com.evil/x/y', 'https://token@github.com/x/y',
                                   'https://github.com/x/y?token=secret', '../x', 'x/..', 'x/y/z'])
def test_repository_rejects_redirect_or_credential_paths(value):
    with pytest.raises(SourceUnavailableError):
        repository_name(value)


@pytest.mark.parametrize('value', ['../a.py', '/tmp/a.py', 'src//a.py', 'src\\a.py', 'a\0.py'])
def test_paths_are_not_traversal(value):
    with pytest.raises(SourceUnavailableError):
        repository_path(value)


def test_tree_truncation_and_non_regular_files():
    reader, routes, _ = api()
    ref = reader.validate_ref('student/project', COMMIT)
    assert [e.path for e in reader.list_tree(ref)] == ['src/solve.py']
    routes['/repos/student/project/git/trees/' + TREE + '?recursive=1']['truncated'] = True
    with pytest.raises(SourcePartialError):
        reader.list_tree(ref)


def test_blob_integrity_and_size_limit():
    reader, routes, _ = api()
    ref = reader.validate_ref('student/project', COMMIT)
    reader.max_file_bytes = 1
    with pytest.raises(SourcePartialError):
        reader.read_file(ref, 'src/solve.py')
    reader.max_file_bytes = 256_000
    routes['/repos/student/project/git/blobs/' + BLOB]['content'] = base64.b64encode(b'x' * len(CONTENT)).decode()
    with pytest.raises(SourceUnavailableError):
        reader.read_file(ref, 'src/solve.py')


# The Activity-integration tests ran over the v1.2 RetrievalEngine, removed in R1c (errata E19).
# They return with the Activity operations of the v2 RetrievalService in R3 (errata E13).
