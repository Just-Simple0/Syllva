# 학기 Intake와 Notion

[English](intake-and-notion.md) · [운영자 가이드](README.ko.md)

v0.1.3 beta에는 **preview** 학기 intake lane이 포함되어 있습니다. Preview는 저장소에 해당 code path가 존재하고 내부 테스트가 있다는 뜻이며, 실제 Drive/Notion workspace를 자동 provisioning한다는 뜻은 아닙니다.

## 현재 학기 모델

intake preview는 설정된 학기마다 다섯 개의 운영 Notion data source를 사용합니다.

1. Academic Courses
2. Sessions
3. Materials
4. File Intake
5. Input Request

이는 legacy read-only retrieval lane에서 사용하는 기존 global-ID data source와 별개입니다.

## 사용자 접수 흐름

```text
Drive 학기 업로드 위치
  → File Intake 관찰
  → Input Request
  → 사용자 Submitted=true
  → worker claim/process
  → Applied / Needs Input / Reconcile Required / Failed / Cancelled
```

`Submitted`, `Cancelled`는 사용자 소유 체크박스이고 `Request Status`는 시스템 소유입니다. 실제 processing/readback 대신 운영자 shortcut으로 `Request Status=Applied`를 쓰지 마세요.

## Worker 명령

```bash
uls sync --max-jobs 20
uls process --max-jobs 20
uls run --max-jobs 20
uls jobs --limit 20
```

- `sync`: 현재 작업을 발견/projection하지만 처리하지 않습니다.
- `process`: 이미 발견된 durable work를 처리합니다.
- `run`: 두 단계를 한 번의 bounded tick으로 수행합니다.
- 모든 worker 경로는 local single-active-worker lock을 공유합니다.

첫 live validation에서는 작은 batch를 사용하세요.

## Notion 안전 기준

write 활성화 전 다음을 검증합니다.

- 정확한 parent/data-source ID;
- 예상 schema/property 이름과 relation target;
- 자동화 경로에서 human-owned field를 쓸 수 없는지;
- 생성 페이지의 privacy/ownership 조건;
- mutation 후 성공 주장 전에 readback.

provider 응답이 유실되거나 결과가 모호하면 외부 상태를 다시 읽고 reconcile하세요. 결과가 불명확한 mutation을 무작정 반복하거나 중복 page를 만들지 않습니다.

## 다음 버전 준비 사항: Automation Queue `Proposal Envelope`

수용된 다음 버전 설계(`docs/ux/intake-execution-contract.md`, rev10, §5.3)는 v1.2 §14.7 현재
스키마(`implementation-spec-frozen.md`)에 없는 새 SYSTEM 소유 Notion 속성 하나를 `Automation Queue`
데이터베이스에 추가한다:

| 속성 | 타입 | 소유 |
|---|---|---|
| Proposal Envelope | Rich text | SYSTEM |

이 속성은 rev10의 C5(v2 proposal identity/envelope)가 읽고 쓰기 전에 실제 `Automation Queue`
데이터베이스에 미리 존재해야 한다. 이 저장소는 Notion 데이터베이스 스키마를 자동 생성·프로비저닝하지
않으며, `Automation Queue`에는 현재 `INTAKE_SCHEMAS`에 해당하는 선언형 schema/validator가 없다 --
기존 `Proposal ID`/`Proposed Action` 속성과 함께 Notion에서 수동으로 만든다.
기존 Queue `Proposal Type`/`State`/`Decision` enum 값과 현재 쓰기 권한 규칙(§15.1)은 그대로 유지되며,
이는 추가적인 schema 변경일 뿐이며 기존 Queue 속성·enum·권한은 전혀 변경되지 않는다. 실제 v2 envelope
읽기/쓰기/내용 검증과 Reader/HAA guard 로직은 C5가 담당한다.

## 대시보드 UX

학기 대시보드의 의도된 순서는 다음과 같습니다.

1. 내 과목
2. 이어서 공부 (현재 UX 정의에서 실제 바로가기 최대 3개)
3. To DO
4. 캘린더
5. 파일 확인

수업은 전역 navigation이 아니라 각 과목 아래에 둡니다.

## 암묵적 lane fallback 금지

intake-preview identity가 불완전하면 다른 retrieval/legacy write 경로로 조용히 fallback한 뒤 성공으로 표시하지 않습니다. intake readiness와 read-only retrieval readiness를 별도 사실로 유지하세요.
