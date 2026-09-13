# 상태 모델

[English](statuses.md) · [레퍼런스](README.ko.md)

이 문서는 사용자/운영자가 보는 대표 상태를 요약합니다. 내부 domain enum은 더 세밀할 수 있으며 source code가 최종 권위입니다.

## Intake request status

| 상태 | 의미 / 운영 기대 |
| --- | --- |
| `Draft` | 요청은 존재하지만 처리 제출 전. |
| `Submitted` | 사용자가 요청을 명시적으로 제출함. |
| `Claimed` | worker가 현재 processing attempt를 소유함. |
| `Applied` | 필요한 confirmation/readback을 포함해 의도한 처리/mutation 완료. |
| `Needs Input` | 필요한 사용자/운영자 정보가 부족함. |
| `Reconcile Required` | 외부 결과가 불확실해 readback/reconcile 필요. |
| `Cancelled` | 사용자 취소로 정상 processing 진행 중지. |
| `Failed` | 처리 실패; 재시도 전 진단 필요. |

intake 모델에서 `Submitted`/`Cancelled`는 사용자 소유 control이고 `Request Status`는 시스템 소유 projection state입니다.

## Source/material completeness

- `Ready` — 해당 source state에 필요한 processing/validation 완료.
- `Partial` — 일부 정보는 있으나 completeness가 실패했거나 불명확.
- `Unavailable` 또는 동등 상태 — 필요한 source content를 현재 제공할 수 없음.

하위 retrieval을 성공시키기 위해 `Partial`을 `Ready`로 취급하면 안 됩니다.

## Worker 실행

bounded worker tick은 discovered, processed, failed, needs-input 같은 count를 보고할 수 있습니다. 다른 local worker가 lock을 소유하면 두 번째 실행은 `already_running` 결과를 반환할 수 있습니다.

## Client 지원 라벨

- **Experimental** — 구현/설정은 있지만 최종 사용자 domain live validation이 불완전.
- **Deployment deferred** — 환경/auth/client 경로를 아직 deployable이라고 주장하지 않는 상태.
