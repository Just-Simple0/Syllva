# LMS 동기화

[English](lms-sync.md) · [운영자 가이드](README.ko.md)

LMS 지원은 선택 기능이며 코어 Syllva retrieval의 필수조건이 아니라 별도 gate가 있는 sidecar로 다뤄야 합니다.

저장소에는 `scripts/` 아래 KNU/Canvas 지향 probe/sync helper와 `docs/plans/` 아래 관련 engineering/acceptance 기록이 있습니다.

## 안전 규칙: 기본은 paused

다음 조건을 모두 만족하기 전까지 LMS scheduling을 **paused** 상태로 유지하세요.

- 사용자가 credential 사용을 명시적으로 승인함;
- 예상 active account/course scope가 검증됨;
- credential이 현재 시점과 의도한 시간 범위에서 유효함;
- 설정된 scope로 read-only connection/probe가 성공함;
- target Notion/application mapping이 검증됨;
- 반복 실행 활성화 전에 운영자가 첫 결과를 검토함.

저장소에 script가 존재한다는 이유만으로 credential을 등록하거나 heartbeat를 활성화하지 않습니다.

## Credential

활성화 시 Canvas/KNU sidecar는 process environment의 별도 `CANVAS_ACCESS_TOKEN` 경계를 사용합니다. repository file이나 log에 넣지 마세요.

secure storage에서 secret을 꺼내거나 provider call을 하기 **전에** credential/config scope를 검증해야 합니다. config가 다른 소유자/범위로 바뀌었는데 이전 active-owner scope의 secret을 재사용하도록 허용하면 안 됩니다.

## 데이터 동작

- course identity는 명시적으로 유지합니다. course name은 후보 탐색 보조일 뿐 durable binding으로 충분하지 않습니다.
- partial/failed course 결과를 보존하고 한 과목의 불완전 상태를 성공한 학기 결과로 바꾸지 않습니다.
- 날짜 없는 assignment에 날짜를 임의 생성하지 않습니다.
- 명시적으로 승인된 integration이 소유하지 않는 한 사용자 완료/제출 상태를 조용히 바꾸지 않습니다.

## 운영 증거

상세 connection-validation, hourly-sync plan/evidence는 `docs/plans/`에 있습니다. 이는 engineering record이며 해당 lane 운영 시 현재 수용된 상태를 따라야 합니다.
