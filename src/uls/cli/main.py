"""ULS CLI: safe status, explicit worker commands and read-only MCP startup."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from uls.behavior import asset_root, lint_behavior
from uls.config.errors import ConfigurationError
from uls.domain.errors import UlsError
from uls.runtime import build_retrieval, require_mcp_credentials, state_path


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog='uls', description='University Learning System 1.2')
    result.add_argument('--config', type=Path, default=Path('config.yaml'))
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('init', help='Create missing config, state and source registrations')
    doctor_parser = commands.add_parser('doctor', help='Check configuration and credential readiness')
    doctor_parser.add_argument('--live', action='store_true', help='Perform read-only provider checks')
    for name in ('sync', 'process', 'run'):
        commands.add_parser(name).add_argument('--max-jobs', type=int, default=100)
    commands.add_parser('status')
    commands.add_parser('jobs').add_argument('--limit', type=int, default=100)
    commands.add_parser('retry').add_argument('job_id')
    commands.add_parser('reprocess').add_argument('entity_id')
    commands.add_parser('mcp').add_argument('mode', choices=('local', 'remote', 'status'))
    commands.add_parser('behavior').add_argument('action', choices=('lint',))
    return result


def _config(path: Path) -> Any:
    from uls.config.loader import load_config_unvalidated
    from uls.config.validation import validate_config
    config = load_config_unvalidated(path)
    for obj, name in ((config.system, 'workspace_dir'), (config.behavior_contract, 'path'),
                      (config.remote_mcp, 'tls_certfile'), (config.remote_mcp, 'tls_keyfile')):
        value = getattr(obj, name)
        if value:
            candidate = Path(value).expanduser()
            setattr(obj, name, str((path.resolve().parent / candidate).resolve()
                                  if not candidate.is_absolute() else candidate))
    errors = validate_config(config)
    if errors:
        raise ConfigurationError(errors)
    return config


def initialize(path: Path) -> dict[str, Any]:
    from uls.state.sqlite import SQLiteStateStore
    if not path.exists():
        template = yaml.safe_load((asset_root() / 'config.example.yaml').read_text(encoding='utf-8'))
        template['behavior_contract']['path'] = str(asset_root() / 'contracts/study-behavior.md')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as handle:
            yaml.safe_dump(template, handle, allow_unicode=True, sort_keys=False)
    config = _config(path)
    workspace = state_path(config).parent
    workspace.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(state_path(config)):
        pass
    source_path = workspace / 'sources.json'
    if not source_path.exists():
        with source_path.open('x', encoding='utf-8') as handle:
            handle.write('[]\n')
    return {'status': 'initialized', 'config': str(path.resolve()),
            'next': 'Configure provider IDs, credentials and source registrations; run uls doctor.'}


def status(config: Any) -> dict[str, Any]:
    from uls.state.reader import ReadOnlyState
    path = state_path(config)
    if not path.exists():
        return {'status': 'not_initialized', 'worker_enabled': config.worker.enabled}
    state = ReadOnlyState(path)
    counts = {row[0]: row[1] for row in state._rows('SELECT status, COUNT(*) FROM jobs GROUP BY status')}
    return {'status': 'ok' if state.health() else 'unhealthy', 'jobs': counts,
            'readiness_funnel': _readiness_funnel(counts),
            'worker_enabled': config.worker.enabled, 'remote_enabled': config.remote_mcp.enabled,
            'remote_running': 'unknown; use authenticated /health',
            'availability': 'Primary PC must be awake and online'}


def _readiness_funnel(job_counts: dict[str, int]) -> dict[str, Any]:
    """Compute a readiness funnel from job status counts.

    Each stage reports only what the job status evidence can actually prove.
    Labels are deliberately conservative to avoid overclaiming readiness.

    Stage semantics:
    - source_archival: at least one job reached READY or PARTIAL (source bytes
      fetched, hashed, and a normalized derivative was produced or attempted).
    - text_extraction: at least one job reached READY (full text extracted
      without page-level gaps; PARTIAL jobs do not qualify).
    - retrieval_credentials: always 'not_checked_here' — credential presence
      is reported by 'uls doctor', not by the job-count-based status command.
    - ai_client: always 'not_proven' — requires a human to confirm through
      actual use; cannot be proven programmatically.
    """
    ready = job_counts.get('READY', 0)
    partial = job_counts.get('PARTIAL', 0)
    archived = ready + partial
    if archived > 0 and ready > 0:
        source_archival = 'done'
        text_extraction = 'done'
    elif archived > 0:
        source_archival = 'done'
        text_extraction = 'partial'
    else:
        source_archival = 'not_started'
        text_extraction = 'not_started'
    return {
        'source_archival': source_archival,
        'text_extraction': text_extraction,
        'retrieval_credentials': 'not_checked_here',
        'retrieval_credentials_note': 'run uls doctor to check credential readiness',
        'ai_client': 'not_proven',
        'ai_client_note': 'requires human confirmation through actual AI client use',
    }


def _credential_ready(key: str) -> bool:
    value = os.environ.get(key, '')
    return bool(value) and (Path(value).expanduser().is_file() if key.endswith('_FILE') else True)


def doctor(config: Any, *, live: bool = False) -> dict[str, Any]:
    checks: dict[str, Any] = {'behavior_contract': not lint_behavior(Path(config.behavior_contract.path)),
                              'state': status(config)['status'] == 'ok'}
    optional_checks: dict[str, Any] = {}
    # The intake worker's Google/Notion write credentials are only relevant
    # when the worker is actually enabled; requiring them for a read-only,
    # MCP-search-only deployment would fail an otherwise complete minimal
    # configuration (contradicting docs/operator-guide/installation.md,
    # which promises doctor reports only the credentials a selected feature
    # actually needs).
    worker_credential_keys = ('GOOGLE_WORKER_CREDENTIALS_FILE', 'NOTION_WORKER_TOKEN')
    if config.worker.enabled:
        for key in worker_credential_keys:
            checks[key] = _credential_ready(key)
    else:
        for key in worker_credential_keys:
            optional_checks[key] = _credential_ready(key)
    # The read-only MCP search surface is the system's core deliverable and
    # is required regardless of whether the intake worker is enabled.
    for key in ('GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'):
        checks[key] = _credential_ready(key)
    # GitHub is an optional supplemental source: GitHubAPIReader accepts an
    # empty token and simply serves no GitHub content, so a missing token
    # must never fail an otherwise complete minimal configuration.
    optional_checks['GITHUB_READ_TOKEN'] = _credential_ready('GITHUB_READ_TOKEN')
    try:
        require_mcp_credentials(os.environ)
        checks['credential_separation'] = True
    except ConfigurationError:
        checks['credential_separation'] = False
    try:
        from uls.mcp.server import ReadOnlyMCP
        ReadOnlyMCP(None).sdk_server()
        checks['mcp_startable'] = True
    except ImportError:
        checks['mcp_startable'] = False
    if config.remote_mcp.enabled:
        try:
            from uls.mcp.transports.remote import BearerCredential, validate_remote_profile
            validate_remote_profile(config)
            BearerCredential(os.environ.get('REMOTE_MCP_SECRET', ''),
                             float(os.environ.get('REMOTE_MCP_EXPIRES_AT', '0'))).validate()
            checks['remote_profile'] = all(Path(p).is_file() for p in
                                           (config.remote_mcp.tls_certfile, config.remote_mcp.tls_keyfile))
        except (UlsError, ValueError):
            checks['remote_profile'] = False
    else:
        optional_checks['remote_profile'] = 'not_configured'
    if live:
        try:
            from uls.runtime import google_service
            engine = build_retrieval(config)
            checks['live_notion_read'] = engine.notion_reader.get_course_by_alias(config.courses[0].course_key) is not None
            service = google_service(os.environ['GOOGLE_MCP_CREDENTIALS_FILE'], read_only=True)
            root = service.files().get(fileId=config.google_drive.university_root_id, fields='id,trashed').execute()
            checks['live_drive_read'] = root.get('id') == config.google_drive.university_root_id and root.get('trashed') is False
        except Exception:  # noqa: BLE001 - health checks report booleans, never provider payloads
            checks['live_provider_read'] = False
    return {'status': 'ok' if all(checks.values()) else 'needs_configuration',
            'checks': checks, 'optional_checks': optional_checks,
            'client_e2e': 'not_proven_by_doctor'}


def dispatch(args: argparse.Namespace) -> Any:
    if args.command == 'init':
        return initialize(args.config)
    if args.command == 'behavior':
        problems = lint_behavior()
        return {'status': 'failed' if problems else 'ok', 'problems': problems}
    config = _config(args.config)
    if args.command == 'status':
        return status(config)
    if args.command == 'doctor':
        return doctor(config, live=args.live)
    if args.command in {'sync', 'process', 'run'}:
        if not config.worker.enabled:
            return {'status': 'disabled', 'processed': 0}
        from uls.worker import build_worker
        worker = build_worker(config)
        try:
            return worker.runner.run_once(sync=args.command != 'process', process=args.command != 'sync',
                                           max_jobs=args.max_jobs)
        finally:
            worker.state.close()
    if args.command in {'jobs', 'retry', 'reprocess'}:
        from uls.state.sqlite import SQLiteStateStore
        if not state_path(config).is_file():
            raise ConfigurationError('Run uls init before job commands')
        with SQLiteStateStore(state_path(config)) as state:
            if args.command == 'jobs':
                return {'status': 'ok', 'jobs': [
                    {name: getattr(job, name) for name in ('id', 'operation', 'status', 'target_entity_id', 'attempt_count', 'error_class')}
                    for job in state.list_jobs(limit=args.limit)]}
            if not state.acquire_local_worker_lock():
                return {'status': 'already_running'}
            try:
                if args.command == 'retry':
                    job = state.get_job(args.job_id)
                    if job is None:
                        raise ValueError('Unknown job ID')
                    updated = state.requeue_job(job.id, job.error_class or 'PERMANENT')
                else:
                    from uls.ingestion.transcript_ingest import TRANSCRIPT_INGEST_OPERATION
                    jobs = [job for job in state.list_jobs(entity_id=args.entity_id, limit=1000)
                            if job.operation == TRANSCRIPT_INGEST_OPERATION]
                    if not jobs:
                        raise ValueError('No supported ingestion job for this entity')
                    updated = state.request_reprocess(jobs[0].id)
                return {'status': 'queued', 'job_id': updated.id}
            finally:
                state.release_local_worker_lock()
    if args.command == 'mcp':
        if args.mode == 'status':
            return status(config)
        from uls.mcp.server import ReadOnlyMCP
        registry = ReadOnlyMCP(build_retrieval(config))
        if args.mode == 'local':
            from uls.mcp.transports.local import run_local
            run_local(registry)
        else:
            from uls.mcp.transports.remote import BearerCredential, run_remote
            credential = BearerCredential(os.environ.get('REMOTE_MCP_SECRET', ''),
                                           float(os.environ.get('REMOTE_MCP_EXPIRES_AT', '0')))
            run_remote(registry, config, credential)
        return None
    raise ValueError('Unsupported command')


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = dispatch(args)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False))
        return 1 if result and result.get('status') in {'failed', 'unhealthy', 'needs_configuration', 'not_initialized'} else 0
    except Exception as exc:  # noqa: BLE001 - CLI must not print provider payloads or raw tracebacks
        error = {'error': {'code': getattr(exc, 'code', 'CONFIGURATION_INVALID'),
                           'message': 'ULS could not complete this command; check configuration and dependencies.'}}
        print(json.dumps(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
