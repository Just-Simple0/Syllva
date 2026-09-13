# UX rev9 — 포트 및 active attempt 계약 집중 검토

다음 버전 설계의 수용을 독립 검토해 주세요. 제품 구현/배포 검토가 아닙니다. 사용자가 Pro
한도 소진으로 승인한 웹 ‘매우 높음’입니다. UX-C1은 명시적 개정 후보, frozen 문서는 현행 기준입니다.

이번 변경은 두 계약 연결입니다. HTML과 사용자 동선은 동일하고 기존 34개 합성 검사 증거를
재사용합니다. 실제 backend/provider 시험으로 오인하지 않습니다. 아래 변경과 직접 영향에
구체적인 모순·불변식 위반·실행 경로 차단이 있는지 검토해 주세요.

1. **§3.4 / C2:** 구현 명세 §13 DriveAdapter를 개정 대상으로 명시하고 worker 전용 포트에
   marker 포함 create, exact marker 전체 검색, complete-zero/one/multiple/indeterminate,
   ID 직접 metadata readback의 의미를 고정했습니다. SDK/쿼리는 adapter 안에 두고 core/worker
   우회 호출과 MCP mutating 노출을 금지합니다. 폴더 recovery 정책 자체는 유지합니다.
2. **§6.5 / C6 / A45-b/c:** `(note_key, attempt_no)`와 key당 최대 한 non-terminal attempt,
   실행 중 동일 key의 새 request는 reference 추가, 실행 없음+verified artifact는 재사용,
   둘 다 없음+최초 또는 terminal 실패/취소 뒤 새 요청은 다음 attempt 예약으로 분기합니다.
   request별 취소 격리, 중단 진행 중 새 reference 처리, terminal 이력 불변, current attempt의
   identity가 맞는 완료만 current projection을 바꾸도록 정했습니다. 현재 AI 영역 게시에는
   기존 study_note_head와 근거 freshness 검사도 계속 요구합니다.

그 외 SOURCE/AI/USER, Partial≠Ready, human approval, read-only MCP, exact Session 선택,
실제 차시/내부 ID 분리 등 불변식은 유지합니다. 현재 코드가 이 신규 포트를 아직 구현하지
않았다는 사실이나 모형이 모든 crash/response-loss를 재현하지 않는다는 사실은 결함이 아닙니다.

한국어 GO 또는 REVISE와 이유를 주세요. **판정마다 파일/라인/코드조각을 인용하라.**
필수 결함은 입력→잘못된 결과→최소 수정→수용 사례로 쓰고 선택 개선과 구현 후 시험을
구분해 주세요. 구체적인 모순이 없으면 SQL/SDK 설계 자유를 새 필수 결정으로 바꾸지 말아 주세요.
필수 결함이 없다면 남는 작업이 실제 구현과 수용 시험임을 명시해 주세요.
