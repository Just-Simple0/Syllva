"""Exact-ref, bounded code and Activity integration acceptance (§47)."""
from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

import pytest

from uls.adapters.github.api import GitHubAPIReader, repository_name, repository_path
from uls.domain.academic import ActivityResultMetadata
from uls.domain.errors import InvalidSubmissionRefError, SourcePartialError, SourceUnavailableError
from uls.retrieval.github import activity_code_context

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


def test_activity_result_is_bounded_and_official_instructions_survive():
    from test_get_activity_context import _engine
    reader, _, _ = api()
    engine, _, _ = _engine()
    original = engine._get_activity_record('COMP319-A01')
    activity = replace(original, result=ActivityResultMetadata('GitHub', COMMIT, 'student/project', repository_path='src'))
    engine._get_activity_record = lambda _: activity
    engine.github_reader = reader
    package = engine.get_activity_context('COMP319-A01')
    result = package.scope['result_source']
    assert package.sources[0].source_class == 'official_activity'
    assert result['commit_sha'] == COMMIT
    assert result['files'][0]['content'] == CONTENT.decode()
    assert result['files'][0]['line_start'] == 1
    assert COMMIT in result['files'][0]['web_url']
    assert len(result['files'][0]['content']) + sum(len(i.content) for i in package.sources) <= engine.budget.max_total_chars
    bounded = activity_code_context(activity, reader, query=None, max_files=1,
                                    max_chars_per_file=10, max_total_chars=5)
    assert len(bounded['files'][0]['content']) == 5
    assert bounded['truncated']
    # Code paths cannot be presented as an invented get_source_chunk locator.
    assert not any('solve.py' in str(b.locator) for b in engine.capabilities.bindings_for(package.context_id))


def test_activity_invalid_ref_is_visible_without_hiding_official_constraints():
    from test_get_activity_context import _engine
    engine, _, _ = _engine()
    package = engine.get_activity_context('COMP319-A01')
    assert package.scope['result_source']['status'] == 'unavailable'
    assert package.scope['result_source']['error'] == 'INVALID_SUBMISSION_REF'
    assert package.sources[0].source_class == 'official_activity'


def test_activity_repository_change_during_read_is_rejected():
    from test_get_activity_context import _engine
    reader, _, _ = api()
    engine, _, _ = _engine()
    original = engine._get_activity_record('COMP319-A01')
    first = replace(original, result=ActivityResultMetadata('GitHub', COMMIT, 'student/project'))
    calls = [0]
    def current(_):
        calls[0] += 1
        return first if calls[0] == 1 else replace(first, result=ActivityResultMetadata('GitHub', COMMIT, 'student/other'))
    engine._get_activity_record = current
    engine.github_reader = reader
    with pytest.raises(SourceUnavailableError):
        engine.get_activity_context('COMP319-A01')
