# P2 검토 기록 검사 경로 제한 — 인간 결정 후보

## 현재 근거

Native Runtime PLAN은 원본 결속 정식 회수와 13파일 전문·현재 SHA 대조를 통과했다. 판정은 REVISE REQUIRED3/OPTIONAL0이다. R1은 raw JSON duplicate 정보 소실에 맞춘 logical-key parser 계약 축소, R2는 OAuth 전용 fresh gate와 SA 현행 동작 분리로 보완 중이다. R3의 Worker 본문은 별도 Worker 묶음으로 검토 예정이며 미송신 구성 코드 두 전문을 추가했다.

필수 Syllva consistency 검사기는 `_verify_sources` → `_safe_relative` → `SECRET_PART`에서 아래 테스트 경로를 거부했다. 실제 비밀 파일 접근, 모델 불일치, 인증 오류나 Native 송신 실패가 아니다.

- 대상 root: `/Users/admin/Project/Syllva`
- 대상 파일: `tests/contract/test_doctor_credential_resolver.py`
- SHA-256: `76571c52fb7aafabe5f67f4faced89a87ff36d7beb13806c8f96a33a4906fe6a`
- 크기: `4845 bytes`
- 내용의 역할: synthetic credential resolver 계약 회귀 테스트. 실제 `.env`나 사용자 자격증명 데이터 파일이 아니다.
- 실패 기록: `.insane-review/drive-oauth-20261005/native-plan-p2-runtime/task-record.json`

현재 approved checker와 source17핀/document1핀은 변경하지 않았다. 이 파일을 이름 변경·alias·기록 누락으로 우회하지 않는다. 이전 테스트 목록을 패킷에 넣을 때 compiled 허용경로까지 사전 확인하지 못한 총괄 준비 누락이다.

## 가능한 최소 후속 범위

인간이 허용하면 Syllva 전용 checker 후보에 위 **정확한 경로·SHA·크기**에 대한 읽기 예외 1건만 제안한다. 기존17핀/document1핀과 root device/inode 결속은 유지한다. 개인 전역 checker, hooks, config, 다른 프로젝트, 실제 비밀 경로, basename/pattern 전체 허용은 변경하지 않는다.

후보와 독립 PLAN/FINAL 검토 근거를 준비한 뒤, 적용할 candidate checker의 정확한 SHA를 제시하여 인간 적용 결정을 별도로 받는다. 현재 checker의 PASS, 제품 수락 또는 미래 핀 갱신 승인으로 취급하지 않는다.

이 후보 준비·검토 범위에 대한 인간 결정 전에는 Runtime consistency 성공을 주장하거나 후속 Worker/새 revision 검토를 송신하지 않는다. 현재 허용된 P2 계획 초안 보완은 계속할 수 있다. 예약 automation은 사용자 지시대로 PAUSED 유지한다.
