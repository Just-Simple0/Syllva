"""ULS CLI: safe status, explicit worker commands and read-only MCP startup."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from uls.behavior import asset_root, lint_behavior
from uls.config.credentials import (
    ALLOWED_SOURCES,
    GOOGLE_CREDENTIAL_PATH_NAMES,
    CredentialResolver,
)
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
    credential_parser = commands.add_parser('credential', help='Manage stored credentials')
    credential_sub = credential_parser.add_subparsers(dest='credential_action', required=True)
    credential_set_parser = credential_sub.add_parser(
        'set', help='Interactively register a credential value (never accepted as a CLI argument)')
    credential_set_parser.add_argument('name', help='Credential name (see uls doctor for the list)')
    credential_set_parser.add_argument('--overwrite', action='store_true',
                                       help='Skip the confirmation prompt when a value already exists')
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
    for name in ('google_worker_credentials_path', 'google_mcp_credentials_path'):
        value = getattr(config, name, None)
        if value:
            candidate = Path(value).expanduser()
            setattr(config, name, str((path.resolve().parent / candidate).resolve()
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
        return {'status': 'not_initialized',
                'readiness_funnel': _readiness_funnel({}),
                'worker_enabled': config.worker.enabled}
    state = ReadOnlyState(path)
    counts = {row[0]: row[1] for row in state._rows('SELECT status, COUNT(*) FROM jobs GROUP BY status')}
    return {'status': 'ok' if state.health() else 'unhealthy', 'jobs': counts,
            'readiness_funnel': _readiness_funnel(counts, state=state),
            'worker_enabled': config.worker.enabled, 'remote_enabled': config.remote_mcp.enabled,
            'remote_running': 'unknown; use authenticated /health',
            'availability': 'Primary PC must be awake and online'}


def _readiness_funnel(job_counts: dict[str, int], *, state: Any = None) -> dict[str, Any]:
    """Compute a readiness funnel from state store evidence and job status counts.

    Each stage reports only what durable provenance evidence can actually prove.
    Labels are deliberately conservative to avoid overclaiming readiness.

    Stage semantics:
    - source_archival: confirmed durable source registration and version record
      (source_files joined with current source_versions). Job status alone is
      insufficient because a job can be created or marked PARTIAL/READY without
      durable source bytes having been received and hashed.
    - text_extraction: confirmed normalization completion evidenced by a
      durable processing record with valid output derivative reference linked
      to a matching source_file and READY normalization job (excluding
      enrichment operations like enrich_session and enrich_material).
    - retrieval_credentials: always 'not_checked_here' — credential presence
      is reported by 'uls doctor', not by the job-count-based status command.
    - ai_client: always 'not_proven' — requires a human to confirm through
      actual use; cannot be proven programmatically.
    """
    has_archival = False
    has_extraction = False
    if state is not None:
        try:
            from uls.state.reader import parse_derivative_ref

            archival_rows = state._rows("""
                SELECT COUNT(*) AS count
                FROM source_files sf
                JOIN source_versions sv ON sv.source_file_id = sf.source_file_id
                  AND sv.source_hash = sf.current_hash
            """)
            has_archival = archival_rows[0]['count'] > 0

            norm_rows = state._rows("""
                SELECT pr.output_ref_json
                FROM source_files sf
                JOIN jobs j ON j.source_file_id = sf.source_file_id
                JOIN processing_records pr ON pr.job_id = j.id
                WHERE j.source_hash = sf.current_hash
                  AND pr.operation = j.operation
                  AND pr.operation NOT IN ('enrich_session', 'enrich_material')
                  AND pr.input_hash = sf.current_hash
                  AND j.status = 'READY'
                  AND pr.status = 'READY'
                  AND pr.output_ref_json IS NOT NULL
            """)
            for row in norm_rows:
                try:
                    parse_derivative_ref(row['output_ref_json'])
                    has_extraction = True
                    break
                except (ValueError, TypeError):
                    pass
        except Exception:
            pass

    total = sum(job_counts.values())
    pending_or_active = job_counts.get('PENDING', 0) + job_counts.get('PROCESSING', 0)

    if has_archival:
        source_archival = 'done'
    elif total == 0 or pending_or_active > 0:
        source_archival = 'not_started'
    else:
        source_archival = 'not_proven'

    if has_extraction:
        text_extraction = 'done'
    elif total == 0 or pending_or_active > 0:
        text_extraction = 'not_started'
    else:
        text_extraction = 'not_proven'
    return {
        'source_archival': source_archival,
        'text_extraction': text_extraction,
        'retrieval_credentials': 'not_checked_here',
        'retrieval_credentials_note': 'run uls doctor to check credential readiness',
        'ai_client': 'not_proven',
        'ai_client_note': 'requires human confirmation through actual AI client use',
    }


def doctor(config: Any, *, live: bool = False) -> dict[str, Any]:
    # doctor() is a composition root (see docs/plans/credential-resolver.md):
    # it builds one CredentialResolver from config.credentials and calls
    # diagnose() exactly once, over every known credential name. Every
    # check below, including the separation check and the --live provider
    # calls, slices values out of that same DiagnosticResolution via
    # select()/require() instead of diagnosing or resolving again.
    diagnose_names = frozenset(ALLOWED_SOURCES)
    if config.remote_mcp.enabled and config.remote_mcp.auth_mode == 'oidc':
        diagnose_names = diagnose_names - {'REMOTE_MCP_SECRET', 'REMOTE_MCP_EXPIRES_AT'}
    resolver = CredentialResolver(config.credentials, path_overrides=getattr(config, 'google_path_overrides', {}))
    diagnostic = resolver.diagnose(diagnose_names)

    def _ready(key: str) -> bool:
        result = diagnostic.results.get(key)
        if result is None or result.status != 'ready':
            return False
        if key in GOOGLE_CREDENTIAL_PATH_NAMES:
            return diagnostic.get_google_payload(key) is not None
        if key.endswith('_FILE'):
            return Path(diagnostic._ready_values[key]).expanduser().is_file()
        return True

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
            checks[key] = _ready(key)
    else:
        for key in worker_credential_keys:
            optional_checks[key] = _ready(key)
    # The read-only MCP search surface is the system's core deliverable and
    # is required regardless of whether the intake worker is enabled.
    for key in ('GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'):
        checks[key] = _ready(key)
    # GitHub is an optional supplemental source: GitHubAPIReader accepts an
    # empty token and simply serves no GitHub content, so a missing token
    # must never fail an otherwise complete minimal configuration.
    optional_checks['GITHUB_READ_TOKEN'] = _ready('GITHUB_READ_TOKEN')
    try:
        opt_separation: dict[str, str] = {}
        for w_key in ('NOTION_WORKER_TOKEN', 'GOOGLE_WORKER_CREDENTIALS_FILE'):
            res = diagnostic.results.get(w_key)
            if res is not None and res.status != 'error':
                opt_separation[w_key] = ''
        separation_snapshot = diagnostic.select(
            required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'}),
            optional=opt_separation,
        )
        require_mcp_credentials(separation_snapshot)
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
            from uls.mcp.transports.remote import validate_remote_profile
            validate_remote_profile(config)
            tls_ok = all(Path(p).is_file() for p in (config.remote_mcp.tls_certfile, config.remote_mcp.tls_keyfile))
            if not tls_ok:
                checks['remote_profile'] = False
            elif config.remote_mcp.auth_mode == 'oidc':
                oidc = config.remote_mcp.oidc
                oidc_ok = bool(oidc.issuer and oidc.audience and (oidc.authorized_subject or oidc.authorized_email))
                if live and oidc_ok:
                    from uls.mcp.transports.oidc import JwksKeyManager
                    try:
                        JwksKeyManager(oidc.issuer, oidc.jwks_uri).live_check_sync()
                    except (ConfigurationError, OSError, ValueError):
                        oidc_ok = False
                checks['remote_profile'] = oidc_ok
            elif config.remote_mcp.auth_mode == 'bearer':
                from uls.mcp.transports.remote import BearerCredential
                remote_snapshot = diagnostic.select(
                    required=frozenset(),
                    optional={'REMOTE_MCP_SECRET': '', 'REMOTE_MCP_EXPIRES_AT': '0'},
                )
                BearerCredential(remote_snapshot['REMOTE_MCP_SECRET'],
                                 float(remote_snapshot['REMOTE_MCP_EXPIRES_AT'])).validate()
                checks['remote_profile'] = True
            else:  # oauth_or_bearer
                from uls.mcp.transports.remote import BearerCredential
                remote_snapshot = diagnostic.select(
                    required=frozenset(),
                    optional={'REMOTE_MCP_SECRET': '', 'REMOTE_MCP_EXPIRES_AT': '0'},
                )
                # rev3 plan section 3.1 state machine: a lane that is not
                # configured at all is simply absent from the requirement;
                # a lane that *is* configured (a Bearer secret is present,
                # or oidc.issuer is set) must be fully valid, and at least
                # one lane must end up configured-and-valid. "any lane
                # valid" (the previous `bearer_ok or oidc_ok`) let an
                # invalid configured lane hide behind a valid unrelated
                # one, which could diverge from the runtime dispatch path.
                bearer_configured = bool(remote_snapshot['REMOTE_MCP_SECRET'])
                bearer_valid = False
                if bearer_configured:
                    try:
                        BearerCredential(remote_snapshot['REMOTE_MCP_SECRET'],
                                         float(remote_snapshot['REMOTE_MCP_EXPIRES_AT'])).validate()
                        bearer_valid = True
                    except (ConfigurationError, ValueError):
                        bearer_valid = False
                oidc = config.remote_mcp.oidc
                oidc_configured = bool(oidc.issuer)
                oidc_valid = False
                if oidc_configured:
                    oidc_valid = bool(oidc.audience and (oidc.authorized_subject or oidc.authorized_email))
                    if live and oidc_valid:
                        from uls.mcp.transports.oidc import JwksKeyManager
                        try:
                            JwksKeyManager(oidc.issuer, oidc.jwks_uri).live_check_sync()
                        except (ConfigurationError, OSError, ValueError):
                            oidc_valid = False
                configured_lanes_valid = [
                    valid for configured, valid in
                    ((bearer_configured, bearer_valid), (oidc_configured, oidc_valid))
                    if configured
                ]
                checks['remote_profile'] = bool(configured_lanes_valid) and all(configured_lanes_valid)
        except (UlsError, ValueError):
            checks['remote_profile'] = False
    else:
        optional_checks['remote_profile'] = 'not_configured'
    if live:
        try:
            from uls.runtime import google_service
            live_snapshot = diagnostic.select(
                required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'}),
                optional={'GITHUB_READ_TOKEN': ''},
            )
            engine = build_retrieval(config, live_snapshot)
            checks['live_notion_read'] = engine.notion_reader.get_course_by_alias(config.courses[0].course_key) is not None
            live_mcp = live_snapshot.get_google_payload('GOOGLE_MCP_CREDENTIALS_FILE')
            if live_mcp is None:
                raise ConfigurationError('GOOGLE_MCP_CREDENTIALS_FILE payload is missing')
            service = google_service(live_mcp, read_only=True)
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
        credentials = CredentialResolver(config.credentials, path_overrides=getattr(config, 'google_path_overrides', {})).resolve(
            required=frozenset({'GOOGLE_WORKER_CREDENTIALS_FILE', 'NOTION_WORKER_TOKEN'})
        )
        worker = build_worker(config, credentials)
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
    if args.command == 'credential':
        from uls.cli.credential_set import CredentialSetError, _remediation_for
        from uls.cli.credential_set import run as run_credential_set
        if args.credential_action != 'set':
            raise ValueError('Unsupported credential action')
        try:
            return run_credential_set(config=config, config_path=args.config,
                                      name=args.name, overwrite=args.overwrite)
        except KeyboardInterrupt:
            print('Aborted.', file=sys.stderr)
            raise SystemExit(130) from None
        except CredentialSetError as exc:
            code = exc.args[0] if exc.args else 'credential_set_failed'
            return {'status': 'failed',
                   'error': {'code': code, 'message': _remediation_for(code)}}
    if args.command == 'mcp':
        if args.mode == 'status':
            return status(config)
        optional_creds = {'GITHUB_READ_TOKEN': '', 'NOTION_WORKER_TOKEN': '', 'GOOGLE_WORKER_CREDENTIALS_FILE': ''}
        if args.mode == 'remote' and config.remote_mcp.auth_mode != 'oidc':
            # auth_mode == 'oidc' must resolve zero REMOTE_MCP_SECRET /
            # REMOTE_MCP_EXPIRES_AT credentials (rev3 plan section 5.1):
            # the OIDC-only lane never consults CredentialResolver for
            # them, so they must not even enter this resolve() call's
            # input set.
            optional_creds['REMOTE_MCP_SECRET'] = ''
            optional_creds['REMOTE_MCP_EXPIRES_AT'] = '0'
        credentials = CredentialResolver(config.credentials, path_overrides=getattr(config, 'google_path_overrides', {})).resolve(
            required=frozenset({'GOOGLE_MCP_CREDENTIALS_FILE', 'NOTION_MCP_TOKEN'}),
            optional=optional_creds,
        )
        from uls.mcp.server import ReadOnlyMCP
        registry = ReadOnlyMCP(build_retrieval(config, credentials))
        if args.mode == 'local':
            from uls.mcp.transports.local import run_local
            run_local(registry)
        else:
            from uls.mcp.transports.remote import BearerCredential, run_remote
            credential: BearerCredential | None = None
            verifier = None
            if config.remote_mcp.auth_mode in ('bearer', 'oauth_or_bearer') and credentials.get('REMOTE_MCP_SECRET'):
                credential = BearerCredential(credentials['REMOTE_MCP_SECRET'],
                                               float(credentials['REMOTE_MCP_EXPIRES_AT']))
            if config.remote_mcp.auth_mode in ('oidc', 'oauth_or_bearer'):
                oidc = config.remote_mcp.oidc
                if oidc.issuer:
                    from uls.mcp.transports.oidc import JwksKeyManager, OidcTokenVerifier
                    km = JwksKeyManager(oidc.issuer, oidc.jwks_uri)
                    verifier = OidcTokenVerifier(
                        issuer=oidc.issuer,
                        audience=oidc.audience,
                        authorized_subject=oidc.authorized_subject,
                        authorized_email=oidc.authorized_email,
                        leeway_seconds=oidc.leeway_seconds,
                        key_manager=km,
                    )
            run_remote(registry, config, credential=credential, oidc_verifier=verifier)
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
