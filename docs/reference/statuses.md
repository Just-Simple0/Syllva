# Status Model

[한국어](statuses.ko.md) · [Reference](README.md)

This page summarizes user/operator-facing statuses. Internal domain enums may be more detailed; source code remains authoritative.

## Intake request status

| Status | Meaning / operator expectation |
| --- | --- |
| `Draft` | Request exists but has not been submitted for processing. |
| `Submitted` | User explicitly submitted the request. |
| `Claimed` | Worker owns the current processing attempt. |
| `Applied` | Intended processing/mutation completed with required confirmation/readback. |
| `Needs Input` | Required user/operator information is missing. |
| `Reconcile Required` | External outcome is uncertain and must be read back/reconciled. |
| `Cancelled` | User cancellation prevents further normal processing. |
| `Failed` | Processing failed; diagnose before retry. |

`Submitted`/`Cancelled` are user-owned controls in the intake model; `Request Status` is system-owned projection state.

## Source/material completeness

- `Ready` — the required processing/validation for that source state completed.
- `Partial` — some information is available, but completeness failed or remains unknown.
- `Unavailable` / equivalent unavailable state — required source content cannot currently be served.

Never treat `Partial` as `Ready` merely to make downstream retrieval succeed.

## Worker execution

A bounded worker tick can report counts such as discovered, processed, failed, and needs-input. A second local worker can return an `already_running` outcome when the local lock is owned by another process.

## Client support labels

- **Experimental** — implementation/config exists but live end-user domain validation is incomplete.
- **Deployment deferred** — the repository intentionally does not claim the environment/auth/client path is deployable yet.
