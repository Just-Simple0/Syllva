# Native Notion 대시보드 적용 결과 — 검토용 readback

2026-09-13. Notion MCP의 실제 저장 결과를 다시 조회한 기록이다. 주소만 일관된 별칭으로
비식별화하고 제목·문구·블록 구조·뷰 설정을 보존했다. 브라우저 스크린샷은 아니다.
강의 원문·AI 노트 본문·개인 연락처·원본 서비스 ID는 포함하지 않았다.

## 수용 검사

```json
{
  "section_order": [
    "내 과목",
    "이어서 공부",
    "To DO",
    "캘린더",
    "파일 확인"
  ],
  "course_page_count": 7,
  "course_child_identities_preserved": true,
  "original_calendar_preserved": true,
  "resume_count": 1,
  "resume_limit": 3,
  "shared_todo_calendar_source": true,
  "schema_original_options_preserved": true,
  "added_course_options": 7,
  "added_type_options": [
    "할 일",
    "학사일정"
  ],
  "filename_autolink_removed": true,
  "actual_browser_screenshot_verified": false
}
```

실제 뷰 질의 결과: 과목 수업 목록1개, To DO0개, 캘린더0개, 모두 has_more=false.
새 가짜 수업·할 일·일정은 만들지 않았다. 원래 미완료 수업 상태도 그대로다.
기존 option 10개의 이름·identity·색상·설정을 보존하고 과목7개, 유형2개만 추가했다.
초기 구조 변경 후 두 섹션의 저장 순서가 뒤섞인 결과를 발견해 해당 앞부분만 다시 정렬했으며,
아래는 그 수정 후 readback이다. 파일명이 도메인으로 자동 링크된 것도 inline code로 고쳤다.

## dashboard

```text
## 내 과목
<columns>
	<column ratio="50">
		<page url="redacted://RESOURCE-01">데이터분석과 AI 기초</page>
		<page url="redacted://RESOURCE-02">오픈소스 소프트웨어 기초</page>
		<page url="redacted://RESOURCE-03">알고리즘 1</page>
		<page url="redacted://RESOURCE-04">운영체제</page>
	</column>
	<column ratio="50">
		<page url="redacted://RESOURCE-05">웰니스생활법률</page>
		<page url="redacted://RESOURCE-06">인공지능</page>
		<page url="redacted://RESOURCE-07">현대인의 식생활</page>
	</column>
</columns>
---
## 이어서 공부
현재 학습 바로가기 · 최대 3개
<callout icon="📖" color="green_bg">
	**알고리즘 1 · 3월 6일 수업**
	<mention-page url="redacted://RESOURCE-08"/>
</callout>
---
## To DO
새 항목에 할 일이나 과제를 적으세요. 날짜 없이도 등록할 수 있으며, 끝낸 항목은 상태를 ‘완료’로 바꾸면 목록에서 빠집니다.
<database url="redacted://RESOURCE-09" inline="true" data-source-url="redacted://RESOURCE-10"></database>
---
## 캘린더
학사일정·과제 마감·시험일정을 한곳에서 확인하세요. ‘유형’으로 구분하며, To DO에 날짜를 넣으면 같은 항목이 캘린더에도 표시됩니다.
<database url="redacted://RESOURCE-11" inline="true" icon="🗓️" data-source-url="redacted://RESOURCE-10">강의 일정</database>
---
## 파일 확인
<page url="redacted://RESOURCE-12">파일 확인</page>
자동 파일 접수 연결 전 · 현재 등록된 자료와 확인할 항목
```

## course

```text
<mention-page url="redacted://RESOURCE-13">2026-1</mention-page> · <mention-page url="redacted://RESOURCE-12"/>
## 수업
수업을 열어 전사 원문과 학습 노트를 이어서 보세요.
<database url="redacted://RESOURCE-14" inline="true" data-source-url="redacted://RESOURCE-15"></database>
---
### 연결 설정과 테스트 기록
<page url="redacted://RESOURCE-16">ULS 실제 강의 테스트</page>
```

## file

```text
<mention-page url="redacted://RESOURCE-13">2026-1</mention-page> · <mention-page url="redacted://RESOURCE-03"/>
## 자료 폴더
[Drive 자료 폴더 열기](redacted://RESOURCE-17)
<callout icon="ℹ️" color="gray_bg">
	자동 파일 접수 연결 전입니다. 현재 등록된 알고리즘 1 자료와 확인할 항목을 볼 수 있습니다.
</callout>
## 확인할 항목
- **Lecture 2 강의자료:** 3월 6일 수업에서 실제로 사용한 페이지 범위는 아직 확인이 필요합니다.
## 등록된 자료
<table header-row="true">
<tr>
<td>자료</td>
<td>열기</td>
<td>현재 확인 내용</td>
</tr>
<tr>
<td>`1주차.md` · 원본 전사</td>
<td>[원문](redacted://RESOURCE-18)</td>
<td>2026-03-06 수업에 연결</td>
</tr>
<tr>
<td>전사 읽기용 파일</td>
<td>[정규화 전사](redacted://RESOURCE-19)</td>
<td>수업 기록의 전사 상태: Ready</td>
</tr>
<tr>
<td>Lecture 2 · 강의자료</td>
<td>[PDF](redacted://RESOURCE-20)</td>
<td>이 수업의 실제 사용 범위 확인 필요</td>
</tr>
</table>
## 공부로 돌아가기
<mention-page url="redacted://RESOURCE-08"/>
```

## todo-view

```json
{"advancedFilter":{"filters":[{"filters":[{"operator":"enum_is_not","property":"상태","propertyType":"select","type":"property","value":{"type":"exact","value":"완료"}}],"operator":"and","type":"group"},{"filters":[{"filters":[{"operator":"enum_contains","property":"유형","propertyType":"multi_select","type":"property","value":{"type":"exact","value":"할 일"}}],"operator":"and","type":"group"},{"filters":[{"operator":"enum_contains","property":"유형","propertyType":"multi_select","type":"property","value":{"type":"exact","value":"📌과제"}}],"operator":"and","type":"group"},{"filters":[{"operator":"is_empty","property":"유형","propertyType":"multi_select","type":"property"}],"operator":"and","type":"group"}],"operator":"or","type":"group"}],"operator":"and","type":"group"},"dataSourceUrl":"{{redacted://RESOURCE-10}}","displayProperties":["이름","날짜","유형","과목","상태"],"name":"To DO","sorts":[{"direction":"ascending","property":"날짜"}],"type":"table"}
```

## calendar-view

```json
{"advancedFilter":{"filters":[{"operator":"is_not_empty","property":"날짜","propertyType":"date","type":"property"}],"operator":"and","type":"group"},"calendarBy":"날짜","dataSourceUrl":"{{redacted://RESOURCE-10}}","displayProperties":["이름","유형","과목","상태"],"name":"전체 일정","type":"calendar"}
```

## course-view

```json
{"advancedFilter":{"filters":[{"operator":"relation_contains","property":"Course","propertyType":"relation","type":"property","value":{"type":"exact","value":"redacted://RESOURCE-21"}}],"operator":"and","type":"group"},"dataSourceUrl":"{{redacted://RESOURCE-15}}","displayProperties":["Name","Date","Recording Status"],"name":"수업 목록","sorts":[{"direction":"descending","property":"Date"}],"type":"list"}
```

## 운영 범위

직접 적용된 것은 native 대시보드·뷰·파일 확인 페이지와 알고리즘 과목의 탐색이다.
이어서 공부 링크는 현재1개를 배치했고 3개까지 두는 구성이다. 최근 열람 기록 수집이나
자동 링크 순위 갱신은 이번에 연결하지 않았다. 파일 확인은 현재 등록 자료를 보여주며
자동 접수는 연결 전으로 표시한다. To DO와 캘린더에는 새 항목을 직접 입력해 사용한다.
브라우저 접근이 승인되지 않아 픽셀 배치·기기별 렌더링·실제 사용자 클릭 저장 검증은
수행하지 않았다. 원본 뷰 질의와 native 블록/필터/관계 readback이 이번 검증 근거다.
