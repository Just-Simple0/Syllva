# P-E — Canvas 첨부 다운로드 경로 불변식 표 (2026-10-13, rev3: 계획 리뷰 r1·r2 반영, r3 GO)

권위: `intake-classification-v2.md` §2.4, §6.1, §6.2(**r15 개정**), §6.3, §9 P-E. 이 표는 **구현 방식 결정**을 포함한다.
리뷰 REQUIRED = 표 위반이 도달 가능한 경로에서 구체적 트리거로 (a) Canvas 토큰·서명 URL(verifier/pre-signed
Location)이 Canvas origin 밖으로 나가거나 로그·상태·Notion에 남거나, (b) 증명되지 않은 파일이 `PROFESSOR_SOURCE`를
얻거나(제목·이름·marker만으로), (c) 중복 Drive 파일·중복 binding·잘못된 binding 같은 잘못된 영속 상태나 외부 쓰기
(Notion 공지 행 포함)를 만들거나, (d) 표가 첨부 코드에 대해 구현 불가능/모순인 경우뿐이다.
HARDENING(주입된 저장소 실패, 실제 provider가 만들지 못하는 응답, 문구)은 GO를 막지 않는다.

## 사실 확인 (계획 개정 사유)

`scripts/knu_lms_sync.py`(기존 KNU LMS 시간별 수집기)의 실제 소스는 계획 §6.2의 전제와 다르다.
1. 공지 **행**을 만들지 않는다. Notion 행은 과제(`유형=과제`)만이고, 공지는 과목 페이지 본문 `## 수집한 공지` 절의
   "날짜 · [제목](링크)" 목록이다. 공지 키 형식은 `<origin>/courses/<id>/discussion_topics/<id>`.
2. `FORBIDDEN_FIELDS`(`body`, `message`, `download_url`, `file_id`, `url`, …)를 응답에서 만나면 거부한다 — 공지 본문과
   첨부를 의도적으로 수집하지 않는다. `RESOURCE_POLICY`에 `files`가 없다.
3. Canvas → Drive 다운로드 경로는 `knu-lms-api-semester.md`의 설계로만 있고 구현이 없다. `canvas_drive_bindings`를
   쓰는 경로가 없어 §6.3의 `PROFESSOR_SOURCE` S0가 실제로는 도달 불가다.

## 결정 (사용자 결정 2026-10-13 "첨부 다운로드 경로 신설")

- **수집기는 바꾸지 않는다.** v2는 수집기와 별개인 제품 경로(`src/uls`)에 읽기 전용 Canvas 첨부 다운로드 →
  개인 Drive 업로드 → `canvas_drive_bindings` 기록을 신설한다. 수집기와 v2 모두 Notion 공지 행을 쓰지 않으므로
  이중 쓰기 문제가 생기지 않는다.
- **공지 본문은 메타데이터만**(현행 P-B3 관측 그대로: 제목·updated_at·`ANNOUNCEMENT` 분류). 계획 §6.2의
  "LMS canonical 행 읽기·로컬 청크·`수집 범위` Partial" 항목은 읽을 행이 없으므로 **삭제**하고, 공지 본문 청크·검색은
  §6.2의 locator 명세 개정 뒤 후속 범위로 남긴다(M8 비노출 결론은 그대로).
- **대상 첨부**: (A) 공지 listing 응답의 `attachments[]`(같은 요청, 추가 GET 없음), (B) 모듈 항목 `type=File`
  (`content_id`). **과제 첨부는 범위 밖**: Canvas Assignment API는 첨부를 목록으로 주지 않고 설명 HTML 링크로만
  주므로 HTML 파싱이 필요하다(후속). 계획의 "Assignment 첨부 CSV → ASSIGNMENT_RESOURCE" 수용 사례는 P-E에서
  재현하지 않는다(규칙 P4의 `canvas_attachment_of=assignment` 경로는 기존 단위 테스트로만 유지).
- **기본 꺼짐**: `intake.classification.canvas_attachment_download_enabled=false`. 켜려면 `canvas_observation_enabled`
  가 필요하다. 교차 origin 파일 저장소(예: inst-fs, S3)로의 redirect는 `canvas_file_hosts`(정확한 호스트 이름 목록,
  기본 빈 목록)에 있는 호스트만 따른다. 비어 있으면 같은 origin 응답만 받는다(KNU의 실제 저장소 호스트는 P-F에서
  사용자 환경으로 확인; 계획 R-1).

## rev2 구조 변경 (계획 리뷰 r1, REQUIRED 9)

r1의 REQUIRED 9건 중 7건이 fetcher 한 컴포넌트(출처 증명·중복 생성·revision·순서·목적지·크기·격리)에 몰렸으므로,
marker 검색 기반 복구를 **영속 업로드 intent 원장**(`canvas_attachment_intents`) 기반 상태기계로 바꾼다.
intent는 Canvas에서 내려받은 바이트의 SHA-256을 Drive create **이전에** 기록한다. 따라서 (R-PE-01) marker만 있는 파일은
출처가 증명되지 않고, (R-PE-02) 결과를 못 본 create는 다시 하지 않는다. 처분: `pe-plan-disposition-r1.json`.

## rev3 보완 (계획 리뷰 r2, REQUIRED 4)

intent 원장 구조는 유지하고 버전·origin 경계를 닫는다: (R-PE2-01) 같은 A의 이전 PENDING/UNCERTAIN intent를 먼저 정리하기 전에는
새 버전을 만들지 않는다; (R-PE2-02) 복구 binding은 intent에 기록된 다운로드 시점 revision을 쓴다; (R-PE2-03) 관측 revision이
첨부 서명(`attachment_signature`: 첨부 id·size·updated_at·이름)까지 비교하므로 첨부만 바뀌어도 revision이 오르고, binding 자리
`(resource, revision, attachment_id)`가 이미 차 있으면 create 전에 `BINDING_CONFLICT`; (R-PE2-04) binding에 `origin` 컬럼을
더하고(기존 행 NULL = 증명 아님) intent·binding 조회와 worker S0를 정확한 origin에 결속한다. 처분: `pe-plan-disposition-r2.json`.

## 용어

- 첨부 키 `A = (origin, canvas_course_id, resource_kind ∈ {announcement, module_item}, resource_id, attachment_id)`.
  `attachment_id` = 공지 첨부의 Canvas file `id` / 모듈 File 항목의 `content_id`.
- 첨부 **버전** 지문 `F(A) = sha256(["canvas-attachment.v2", A…, filename, size, canvas_updated_at])` (filename = Canvas
  `display_name`(없으면 `filename`; `filename`은 URL-escape됨), size = Canvas `size`, canvas_updated_at = File 객체의 `updated_at`).
- intent = `canvas_attachment_intents` 행(PK F(A)): origin, A, 관측 revision, filename, size, canvas_updated_at, **Canvas 다운로드 바이트의
  byte_sha256**, folder_id, status ∈ {PENDING, UNCERTAIN, BOUND, SAME_BYTES, CONFLICT}, drive_file_id.
- marker = Drive 개인 appProperties `{uls_r: "CANVAS_ATTACHMENT", uls_t: F(A)}`(각 쌍 ≤124B; `uls_t`는 기존 marker 검색 키).
- 신뢰 경로 = 이 표의 fetcher. `canvas_drive_bindings`를 쓰는 **유일한** 호출자다.

## 행

| # | 불변식 | 검증 |
|---|---|---|
| E1 | 후보 수집은 기존 관측(`CanvasObserver._collect`)의 **같은 응답**에서만 한다: 공지 `attachments[]`의 각 객체(id·display_name/filename·size·content-type·updated_at·잠김)와 모듈 항목 `type=File`(**`content_id`와 항목 id만**; R-PE-05). 후보는 관측이 `COMPLETE`인 과목에서만 넘어가며(INCOMPLETE/INELIGIBLE/DISABLED → 후보 0), 메모리로만 전달된다. 공지 본문(`message`)·URL·verifier는 후보·관측 레코드·상태 어디에도 저장하지 않는다. 다운로드가 꺼져 있으면 후보를 만들지 않는다 | 단위 |
| E2 | 적격성(순수 함수): 확장자·Canvas `content-type`이 §6.1 처리 방식 매트릭스의 입력(PDF는 `application/pdf`만; 코드·표 형식은 그 확장자의 허용 MIME 또는 `application/octet-stream` — 바이트 서명은 이후 `handling_mode`가 다시 검사)이고, `size`는 bool 아닌 정수 1..**min(`max_source_bytes`, Drive adapter 다운로드 상한)**(R-PE-08: 복구 시 되읽을 수 있는 크기만), 잠김/숨김 아님, 파일명은 경로 구분자·제어문자 없음(길이 ≤200). 공지 첨부는 listing 값으로 **사전** 판정(요청 0), 모듈 File은 E3(1)의 File 객체로 판정(R-PE-05). 부적격은 다운로드 0, 고정 사유(`UNSUPPORTED_TYPE`, `TOO_LARGE`, `LOCKED`, `BAD_NAME`) | 단위 |
| E3 | Canvas 읽기는 관측 allowlist를 넓히지 않고 **별도 파일 인터페이스**(`CanvasConnection.file_metadata`/`download`, 같은 pinned 연결·같은 요청 상한)로 한다(R-PE-06): (1) `GET /api/v1/courses/{cid}/files/{attachment_id}` — 응답 `id` = attachment_id, 공지 첨부는 display_name·size가 listing과 같아야 함(다르면 `FILE_MISMATCH`, 다운로드 0). (2) 응답 `url`은 **같은 Canvas origin**의 `/files/{id}/download` 또는 `/courses/{cid}/files/{id}/download`이고 query 키는 `{download_frd, verifier}`만(그 밖 `DOWNLOAD_URL_NOT_ALLOWED`). `Authorization`은 Canvas origin 요청에만 붙는다 | 단위 |
| E4 | redirect: 체인 전체에서 **최대 2회**(Canvas 내부 hop과 저장소 hop이 한 예산을 공유). 전송 계층의 파일 GET은 3xx에서 `Location`만 보존한다(본문·다른 헤더 0). 같은 origin이면 E3(2) 경로 규칙 재검사 후 따른다(토큰 유지). 다른 호스트면 `canvas_file_hosts`에 정확히 있고 HTTPS·443일 때만 **새 pinned 연결(DNS 1회·공인 IP만)** 으로 **토큰 없이** GET하며, 그 호스트의 추가 redirect는 같은 호스트 안에서만. 허용 밖이면 `DOWNLOAD_HOST_NOT_ALLOWED`, 바이트 0. `Location`·verifier·서명 query는 예외·로그·상태·카운터에 남지 않는다 | 단위 |
| E5 | 바이트: 파일 GET은 `size`+1 바이트 상한으로 읽고(관측의 1MiB 상한과 별개), 정확히 `size`가 아니면 `SIZE_MISMATCH`. 첨부 1개 총 시간 상한 60s. sha256은 메모리에서 계산, 디스크 임시 파일 없음. 새 고정 코드 `SIZE_MISMATCH`는 `CanvasCheckError` 고정 집합에 추가 | 단위 |
| E6 | Drive 업로드 위치: 과목의 course upload 폴더, 없으면 학기 upload 폴더(등록 ID만). course key는 **현재** `canvas_course_map`의 1:1 매핑으로만 정하며 아니면 다운로드 0. **create 직전에** 목적지 폴더를 다시 읽어 folder MIME·untrashed·ID 일치·owner-only·비공개·편집/이동 권한을 확인하고(R-PE-07; 실패 `FOLDER_NOT_PRIVATE`, intent·create 0), create 응답도 기존 adapter readback(부모·MIME·marker·소유·비공개) 검증을 통과해야 한다. 업로드 MIME은 확장자에서 정한 고정 값, 이름은 E2를 통과한 display_name | 통합 |
| E7 | 순서(첨부 1개; 단일 writer = worker 락; intent·binding 조회는 모두 **origin 포함 A**): ① 공지 첨부는 listing의 (filename, size, updated_at)과 같은 BOUND/SAME_BYTES intent가 있으면, 모듈 File은 A에 BOUND/SAME_BYTES intent 또는 binding이 있으면 **요청 0**. ② File 객체 GET + E2/E3 검사 → F(A). ③ F(A)의 intent가 BOUND/SAME_BYTES → 끝; CONFLICT → `BINDING_CONFLICT`. ④ intent가 PENDING/UNCERTAIN(결과를 못 본 create): `search_marker` 결과가 **정확히 1개**이고 intent 폴더·size·marker·비공개가 맞고 **Drive에서 되읽은 바이트의 SHA-256 = intent의 Canvas SHA-256**일 때만 binding(복구). 0개면 `UPLOAD_UNCERTAIN` 유지 — **두 번째 create 0**(R-PE-02); 2개 이상 `MARKER_CONFLICT`; 바이트 불일치 `PROVENANCE_UNVERIFIED`. 복구 binding의 revision은 **intent에 기록된 다운로드 시점 revision**(R-PE2-02). ⑤ **같은 A의 다른 버전 intent가 PENDING/UNCERTAIN이면 먼저 ④로 정리**하고, 정리되지 않으면 새 버전은 `UPLOAD_UNCERTAIN`으로 멈춘다(R-PE2-01). ⑥ intent 없이 marker 파일이 있으면 `PROVENANCE_UNVERIFIED`(R-PE-01: marker는 출처 증명이 아님). ⑦ 그 외: Canvas 다운로드 → 같은 A의 기존 binding과 SHA-256이 같으면 SAME_BYTES intent만 기록(새 파일·binding 0) → binding 자리 `(resource, revision, attachment_id)`가 (어느 origin이든) 이미 차 있으면 `BINDING_CONFLICT`(create 0) → E6 폴더 검사 → intent **PENDING** 기록 → create → 실패/응답 손실은 UNCERTAIN(이 버전 재생성 0) → E8 → BOUND. marker = `{uls_r: CANVAS_ATTACHMENT, uls_t: F(A)}`(기존 `_search_pair`의 `uls_t` 검색 키, R-PE-03) | 통합 |
| E8 | binding 기록: `record_canvas_drive_binding(drive_file_id, canvas_course_id, resource_kind, resource_id, observation_revision=다운로드 시점의 활성 관측 revision, attachment_id, attachment_filename, attachment_size, byte_sha256=Canvas 다운로드 바이트, origin=다운로드한 Canvas origin)`. drive_file_id 재결속(ValueError)과 `(resource, revision, attachment_id)` UNIQUE 위반(`sqlite3.IntegrityError`)은 모두 `BINDING_CONFLICT` + intent CONFLICT(새 Drive 파일은 marker로 숨겨진 채 남음) | 통합 |
| E9 | 발견 경계: `discover_intake`는 `uls_r=CANVAS_ATTACHMENT` marker 파일 중 **binding이 없는 파일을 `upsert_intake_item` 이전에 제외**한다(IntakeItem·File Intake 행·Input Request 0). binding 조회를 못 하는 store면 숨김(fail closed). binding이 생기면 다음 발견에서 일반 항목. marker 없는 파일은 기존과 동일 | 단위+통합 |
| E10 | revision·버전 모델(R-PE-04, §6.2 r15에 반영): binding은 **불변 이력 증명**("이 바이트는 Canvas 첨부 A를 revision r에서 내려받은 것")이다. S0의 `PROFESSOR_SOURCE`는 (drive_file_id, 바이트 SHA-256) 일치만으로 성립하며 현재 revision을 추론하지 않는다(교수 제공이라는 사실은 이후 공지 수정·첨부 제거로 바뀌지 않음). 현재성이 필요한 값만 현재 상태에서 다시 읽는다: course = 현재 1:1 `canvas_course_map`, week = 그 모듈 항목의 **활성·완전 수집** 관측(E11). 관측 revision은 첨부 서명까지 비교하므로 첨부가 바뀌면 revision도 오른다(R-PE2-03; 다운로드가 꺼져 있는 동안은 저장된 서명을 그대로 이어 쓰므로 기능을 켜고 꺼도 revision 불변, H-PE3-01). binding의 origin이 현재 `canvas_origin`과 다르거나 없으면 그 binding은 S0·course·week 어디에도 쓰이지 않는다(R-PE2-04). 첨부 버전은 F(A)(updated_at 포함)로 구분: 제목만 바뀐 공지(revision↑, 첨부 버전 동일) → 요청·파일·binding 0; 버전↑·바이트 동일 → SAME_BYTES(파일·binding 0); 버전↑·바이트 상이 → 새 Drive 파일·새 binding(현재 revision), 옛 binding 불변. 기존 binding 행은 수정·재지정되지 않는다. **알려진 한계**: 모듈 File 항목은 listing에 버전이 없으므로 같은 file id로 제자리 교체된 파일은 다시 받지 않는다 | 통합 |
| E11 | S0/출처 재사용(§6.3 r14 O1): worker의 모든 binding 조회는 **현재 `canvas_origin`과 같은 origin의 binding만** 본다(`_canvas_binding`). 그 binding이 바이트로 증명되면(`byte_sha256` 일치) (a) origin=`PROFESSOR_SOURCE`, (b) 폴더 근거가 없으면 course key = 현재 1:1 `canvas_course_map[binding.canvas_course_id]`(`course_basis="canvas_binding"`), 폴더 근거가 다르면 기존 `BLOCK_CANVAS_COURSE`, (c) `resource_kind=module_item`이면 `canvas_origin`의 그 항목 최신 관측이 활성·완전 수집일 때 그 `module_week`, 파일명 주차(`_N주차`)가 있고 다르면 week 없음 + `CANVAS_WEEK_CONFLICT` + `AUTO_BLOCK_WEEK_CONFLICT`(→ S3). 공지 첨부는 파일명 주차만. Kind는 실제 파일로 새로 판정. 증명되지 않은 binding은 (a)–(c) 모두 미사용 | 단위+통합 |
| E12 | 쓰기 0: fetcher는 Notion에 아무것도 쓰지 않는다(공지 행 create/update 0, File Intake·Input Request·Material 0). Canvas에는 GET만. Drive 쓰기는 E7⑥의 create 1회/첨부 버전뿐. 테스트: fetch 단계만 돌린 틱의 Notion data source 불변 | 통합 |
| E13 | 예산·격리: 틱당 첨부 처리 상한 `max_canvas_downloads_per_tick`(기본 5, File 객체 GET 포함). 첨부 하나의 실패(응답 손실 포함)는 **그 첨부 버전만** 멈추고 같은 틱의 다른 첨부·Drive 발견·다른 과목은 계속한다(R-PE-09). readiness `canvas_attachments: {status, downloaded, recovered, unchanged, skipped, failed, last_codes, intents: {status: n}}`(파일명·URL·토큰 0). UNCERTAIN은 readiness에 계속 보이며 자동 해제되지 않는다(정리는 후속 reconcile 범위) | 단위+통합 |
| E14 | 수용 시나리오: 공지 첨부 A·B → Drive 2·binding 2; 재관측 → 요청 0·추가 0; 공지 제목만 수정 → 추가 0; B 교체 → Drive +1·binding +1, 옛 binding 불변; 버전만 바뀌고 바이트 동일 → 추가 0; 업로드 응답 손실 → 다음 틱 복구(Drive 2·binding 2, create 총 2); create 전 실패 → 이후 틱 create 0·UNCERTAIN; intent 없는 marker 위조 → binding 0; intent 있고 Drive 바이트 위조 → binding 0; 공유 폴더 → create 0; 허용 밖 redirect → 바이트 0; 미확정 F1 → 같은 바이트 F2 → create 총 1·binding 1; 이전 버전 결과 미확인 → 새 버전 create 0; revision 1 다운로드·응답 손실 → 제목 수정(revision 2) → 복구 binding revision 1; 같은 revision에서 첨부만 바뀜 → 관측 revision 증가, 이미 찬 자리면 create 0·`BINDING_CONFLICT`; origin A→B(숫자 ID 동일) → A의 intent·binding 재사용 0, B 쪽 PROFESSOR_SOURCE·course·week 0; 공지 본문은 ANNOUNCEMENT(관측) + 첨부 PDF는 자체 Kind; 모듈 File `Chapter 03-Address.pdf` + 3주차 → week=3(통합); `강의계획서_4주차.pdf` + Canvas 3주차 → week 없음·`AUTO_BLOCK_WEEK_CONFLICT`(단위) | 단위+통합 |
| E15 | 기존 동작 보존: 첨부 다운로드가 꺼져 있으면 관측 결과·요청 수·분류·발견·AUTO 경로가 이전과 같고 후보 0·Canvas 파일 요청 0. 수집기 `scripts/knu_lms_sync.py` 변경 0. (의도된 변화) 바이트로 증명된 binding의 course가 S2 payload의 `course_key`에 들어간다(P-C2 캐시 키 입력; E11(b)) | 회귀 |
