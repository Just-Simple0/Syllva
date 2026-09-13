# Native Notion 대시보드 적용 완료 기록

2026-09-13. **현재 적용 범위 수용: GO.**

[2026-1 대시보드](https://app.notion.com/p/34154b33957f801cb86ed4435bd80253)에
**내 과목 → 이어서 공부(최대3) → To DO → 캘린더 → 파일 확인** 순서로 직접 적용했다.
[파일 확인](https://app.notion.com/p/3da54b33957f81109144dacc09547940) 페이지를 만들고,
[알고리즘 1](https://app.notion.com/p/34554b33957f80009a5ace971fbd959a)에는 과목별 수업
연결 뷰와 학기 복귀 링크를 추가했다. 실제 학습 노트·수업 상태는 변경하지 않았다.

## 검증 결과

- 실제 Notion 블록을 재조회해 정확한 5개 제목 순서, 기존 과목 7개 ID와 기존 캘린더 보존을 확인했다.
- 이어서 공부에는 확인된 실제 수업 1개를 배치했다. 최대3개 구성이고 자동 최근 방문 순위는 연결하지 않았다.
- To DO와 캘린더의 원본 data source가 같다. To DO는 미완료 할 일·과제·미분류 작업을
  날짜순으로, 캘린더는 날짜 있는 모든 유형을 보여준다. 실제 질의는 수업1개, To DO0개,
  캘린더0개이며 모두 has_more=false다. 가짜 기록을 만들지 않았다.
- schema 변경 직전 조회와 변경 후 재조회로 기존 option 10개의 전체 객체(identity·이름·색상·설정)
  보존을 비교했다. 과목7개 및 할 일·학사일정 유형2개만 추가했다. 다른 속성은 그대로다.
- 첫 배치 후 Notion 저장 순서가 뒤섞인 것을 발견해 해당 앞부분만 다시 정렬하고 재조회했다.
  파일명을 외부 도메인으로 자동 링크한 결과도 inline code로 수정하고 실제 링크 제거를 확인했다.
- 원본 자료·전사 정규화 코드·테스트·두 frozen 문서 해시는 보존했다. 제품 backend 변경,
  Drive 이동, 새 공급자 호출, 승인 상태 승격, 권한 변경, 커밋·푸시는 수행하지 않았다.

## 독립 검토와 판정

웹 실제 모델은 **GPT-5.6 Sol (매우 높음)**으로 전송 전에 UI 검증했다. Pro 한도 소진에
대한 사용자의 대체 모드 승인을 사용했다. [계획 검토](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa6235c-539c-83ee-af23-16c02f90177c)는
option 추가의 fresh 조회·재실행 안전성 보완 1건으로 REVISE였으며, 요구한 문구와 실행 검증을
반영했다. [최종 검토](https://chatgpt.com/g/g-p-6a9fdbd2dc3081919990a6607f8fe7c4-syllva-eeb93c01/c/6aa62624-2154-83e8-bb92-0b9ef724177b)는
그 지적의 해소를 포함해 **GO**, 필수 미해결 지적0건이다. 완료 응답 전체를 회수했다.

독립 **Gemini 3.8 Flash high**는 계획·최종 모두 GO다. 두 검토의 근거는 실제 native
블록·뷰·질의 결과이며, 화면을 보았다는 판정이 아니다. Gemini의 ‘직접 자식’ 표현은
열 컨테이너 아래에 같은 과목 페이지 ID가 보존됐다는 뜻으로 한정하며, 즉시 부모 블록 ID나
브라우저 접근성까지 동일하다는 근거로 쓰지 않는다. 추가 weekly 뷰 등 선택 제안은 확대하지 않았다.

현재 assistant는 요구한 native 화면 적용과 검토·readback을 통합해 위 범위를 수용한다.

## 확인 범위의 한계

Aside CLI는 연결되지 않았고 NAVER Whale 화면 접근은 승인되지 않았다. 승인 우회 없이
Notion MCP로 적용·검증했다. 브라우저 픽셀 배치, 모바일 화면, 실제 사용자 클릭으로 새 항목을
저장하는 검증은 하지 않았다. 최근 학습 자동 갱신과 파일 자동 접수는 아직 연결 전이다.

증거: [주소를 비식별화한 저장 결과](dashboard-native-readback.md), [적용 계획](dashboard-native-application.md),
로컬 `.review/dashboard-native-readback.json`, `dashboard-native-receipts.json`,
`dashboard-native-plan-inputs.json`, `dashboard-native-final-inputs.json` 및 Gemini 검토 기록.
