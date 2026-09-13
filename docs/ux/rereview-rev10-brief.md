# UX rev10 — artifact 재사용의 실행 identity 재검토

집중 재리뷰 대상은 첨부 UX-C1 **§6.5의 분기 2, A45-c, §9 C6**와 그 직접 영향입니다.
이 파일은 전체 실행 계약의 최신 원문입니다. 기존 frozen 명세는 변경하지 않았으며, C6은
이미 별도 note_key/attempt/reference 모델을 채택한 다음 버전의 명시적 개정 후보입니다.
이번에는 source job이나 기존 제품 코드의 구현 적합성을 검토하지 않습니다.

한 가지 수정: active attempt가 없지만 verified artifact가 있으면, artifact 재사용도
**새 attempt_no**를 원자적으로 예약해 current pointer/request reference에 결속합니다.
이 시도는 STAGED에서 시작해 generator를 호출하지 않고 publish/readback/terminal 상태만
소유합니다. old CANCELLED/FAILED attempt는 불변이며 늦은 완료는 current 상태를 바꾸지 못합니다.
현재 영역에 동일 artifact가 이미 있으면 readback으로 무쓰기 완료할 수 있지만 상태 identity는
새 attempt에 귀속됩니다. 같은 key에 이미 active attempt가 있을 때 reference로 attach하는 규칙,
최대 한 active attempt, 최신 request head, source freshness와 USER 쓰기 보호는 유지됩니다.

다음 반례가 닫혔는지 독립적으로 검토해 주세요:
R1/K/attempt1 STAGED(verified)→CANCELLED → R2/K → attempt2/current=2 → generator 추가 호출
0회 → 게시 성공은 attempt2 READY, 게시 실패는 attempt2 retry/FAILED → attempt1 늦은 결과는
과거 이력에만 기록. 동일 artifact 재사용이 current attempt guard를 우회해서는 안 됩니다.

한국어로 **GO 또는 REVISE**와 이유를 주세요. **판정마다 파일/라인/코드조각을 인용하라.**
실제 불변식 모순·실행 차단만 필수 결함으로 구분하고, SQL/SDK 구현 자유는 유지해 주세요.
충분하다면 남는 것이 backend/SQLite/adapter 구현 수용 시험임을 명시해 주세요. 합성 모형은
변경되지 않았고 실제 attempt 회복 시험을 수행했다고 주장하지 않습니다. 이전 리뷰의 결론을
그대로 받아들이지 말고 위 최신 규칙 자체의 일관성을 판단해 주세요.

이 검토는 사용자가 Pro 한도 소진으로 승인한 웹 ‘매우 높음’입니다. 새 AI 공급자 연결,
운영 migration·Drive/Notion 쓰기·제품 배포 승인을 뜻하지 않습니다.
