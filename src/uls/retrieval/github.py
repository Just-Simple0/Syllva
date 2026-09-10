"""Activity result evidence at the exact submitted commit/tag.

Code locations are structured GitHub refs, not fabricated page/time locators.
A follow-up repeats the Activity context query; the frozen get_source_chunk
allowlist continues to authorize only actual page/timestamp evidence.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from uls.adapters.github.api import repository_path
from uls.adapters.github.base import GitHubReader
from uls.domain.academic import ActivityRecord
from uls.domain.errors import InvalidSubmissionRefError, SourcePartialError, SourceUnavailableError
from uls.retrieval.lexical import lexical_score

_TEXT_SUFFIXES = frozenset({
    '.py', '.js', '.ts', '.tsx', '.jsx', '.java', '.c', '.h', '.cpp', '.hpp',
    '.rs', '.go', '.rb', '.sql', '.md', '.txt', '.toml', '.yaml', '.yml',
    '.json', '.html', '.css', '.sh', '.ps1', '.cs', '.swift', '.kt', '.ipynb',
})


def activity_code_context(activity: ActivityRecord, reader: GitHubReader | None, *,
                          query: str | None, max_files: int, max_chars_per_file: int,
                          max_total_chars: int) -> dict[str, Any] | None:
    result = activity.result
    if not result.repository_ref and 'github' not in result.result_type.casefold():
        return None
    if not result.repository_ref or not result.submission_ref:
        raise InvalidSubmissionRefError("GitHub results require Repository and Submission Ref")
    if reader is None:
        raise SourceUnavailableError("GitHub read adapter is not configured")
    ref = reader.validate_ref(result.repository_ref, result.submission_ref)
    root = repository_path(result.repository_path or '', allow_empty=True)
    entries = reader.list_tree(ref)
    candidates = [e for e in entries if (not root or e.path == root or
                  PurePosixPath(root) in PurePosixPath(e.path).parents)]
    if root and not candidates:
        raise SourceUnavailableError("Repository Path is absent from the submitted tree")
    candidates = [e for e in candidates if PurePosixPath(e.path).suffix.casefold()
                  in _TEXT_SUFFIXES or PurePosixPath(e.path).name in {'Makefile', 'Dockerfile'}]
    candidates.sort(key=lambda e: (-lexical_score(query or '', e.path), e.path))
    files: list[dict[str, Any]] = []
    warnings: list[str] = []
    total = 0
    for entry in candidates[:max_files]:
        remaining = max_total_chars - total
        if remaining <= 0:
            break
        try:
            code = reader.read_file(ref, entry.path)
        except (SourcePartialError, SourceUnavailableError) as exc:
            warnings.append(exc.code)
            continue
        if code.ref != ref or code.path != entry.path or code.blob_sha != entry.sha:
            raise SourceUnavailableError("submission file identity mismatch")
        lines = code.content.splitlines(keepends=True)
        start = 0
        if query and lines:
            start = max(0, max(range(len(lines)), key=lambda i: lexical_score(query, lines[i])) - 3)
        content = ''.join(lines[start:])[:min(max_chars_per_file, remaining)]
        if not content:
            continue
        end = start + len(content.splitlines())
        files.append({
            'label': 'SOURCE', 'source_class': 'submitted_code',
            'repository': ref.repository, 'repository_path': code.path,
            'submission_ref': ref.submission_ref, 'commit_sha': ref.commit_sha,
            'blob_sha': code.blob_sha,
            'source_hash': 'sha256:' + hashlib.sha256(code.content.encode()).hexdigest(),
            'line_start': start + 1, 'line_end': end, 'content': content,
            'truncated': content != code.content,
            'web_url': f'https://github.com/{ref.repository}/blob/{ref.commit_sha}/'
                       + quote(code.path, safe='/') + f'#L{start + 1}-L{end}',
        })
        total += len(content)
    # A moved tag is visible instead of mixing two trees or silently switching.
    if reader.validate_ref(result.repository_ref, result.submission_ref) != ref:
        raise InvalidSubmissionRefError("Submission Ref changed during retrieval")
    return {
        'provider': 'github', 'repository': ref.repository,
        'submission_ref': ref.submission_ref, 'commit_sha': ref.commit_sha,
        'files': files, 'warnings': warnings,
        'truncated': len(files) < len(candidates) or any(f['truncated'] for f in files),
        'followup': 'repeat uls.get_activity_context with a narrower query',
        'authority': 'submitted work; official Activity instructions remain governing constraints',
    }
