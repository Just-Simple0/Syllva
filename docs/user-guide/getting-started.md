# 처음 시작하기

이 안내는 로컬 ULS의 첫 설정 경로다. 명령은 저장소 루트의 터미널에서 실행한다.
Drive 업로드와 Notion 기록은 명시적인 설정과 provider 자격증명이 있어야 작동하며,
문서에 실제 ID나 비밀값을 넣지 않는다.

## 설치와 초기화

사용자: Python 3.11 이상으로 전용 가상환경을 만들고 다음을 실행한다.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,mcp,drive,notion,pdf]'
uls init
uls doctor
uls status
uls behavior lint
```

`pdf` extra는 PDF 자료를 다루는 preview 준비 환경에 필요하다. `uls init`은
`config.yaml`, 로컬 상태 저장소, 빈 `sources.json`을 만든다. `.env`를 자동으로
읽지 않으며, 비밀값을 명령 인자·`sources.json`·문서·로그에 넣지 않는다. Drive,
Notion, custom ULS MCP의 자격증명은 서로 다른 설정 경계다.

## v1.3 preview를 켜는 설정

v1.3 intake lane은 `google_drive.semester_registries`와
`notion.semester_workspaces`가 모두 구성되고, 같은 학기의 명시적 과목·provider
ID가 검증될 때 선택된다. 두 목록 중 하나라도 없거나 검증에 실패하면 preview가
작동한다고 가정하지 않는다. preview lane과 legacy MCP reader 사이의 자동 fallback은
없다.

아래는 실제 ID를 넣기 전의 최소 구조 예시다. 모든 `<...>` 값을 운영자가 검증한
값으로 바꾸고, 과목 키와 학기는 registry와 workspace에서 동일하게 유지한다.

```yaml
courses:
  - course_key: "2026-2_COURSE-001-001"
    name: "예시 과목"
    code: "COURSE-001"
    section: "001"
    semester: "2026-2"

google_drive:
  university_root_id: "<school-root-id>"
  semester_registries:
    - semester: "2026-2"
      folder_id: "<semester-folder-id>"
      upload_folder_id: "<upload-folder-id>"
      course_folder_ids:
        "2026-2_COURSE-001-001": "<course-folder-id>"
      course_static_folder_ids:
        "2026-2_COURSE-001-001":
          recordings_folder_id: "<recordings-folder-id>"
          materials_folder_id: "<materials-folder-id>"
      optional_course_upload_folder_ids: {}

notion:
  semester_workspaces:
    - semester: "2026-2"
      connection_settings_files_parent_id: "<notion-parent-id>"
      academic_courses_data_source_id: "<courses-data-source-id>"
      sessions_data_source_id: "<sessions-data-source-id>"
      materials_data_source_id: "<materials-data-source-id>"
      file_intake_data_source_id: "<file-intake-data-source-id>"
      input_requests_data_source_id: "<input-requests-data-source-id>"
      portal_page_ids:
        "2026-2_COURSE-001-001": "<course-portal-page-id>"
```

미리보기에는 학기별로 다음 5개 native data source가 사용된다: Academic Courses,
Sessions, Materials, File Intake, Input Request. 기존 7개 global-ID data source는
custom ULS MCP reader의 legacy read-only 표면이며, 두 체계의 ID를 섞지 않는다.

운영자: `uls doctor`와 설정 검증에서 학기, 과목 키, provider ID, 권한을 확인한다.
자격증명이 없으면 provider 작업을 시작하지 않고 설정 오류로 멈춘다. native Notion
connector의 구조/readback 검증은 별도 운영 경계에서 확인되었지만, custom ULS MCP
client의 전체 연결은 사용자 환경의 config와 자격증명이 필요하며 현재 이 문서가
그 성공을 보장하지 않는다.

## 첫 자료 등록

설정과 검증이 끝난 환경에서 사용자는 다음 순서로 시작한다.

1. 운영자가 지정한 학기 업로드 폴더에 PDF 또는 자료 파일을 넣는다.
2. `FileIntake`에서 파일과 대상 과목을 확인한다.
3. `Input Request`에 과목, 세션/날짜, 자료 종류와 의도를 입력한다.
4. 사용자가 맞는 요청의 `Submitted` 체크박스를 `true`로 바꾼다.
5. `Request Status`에서 시스템 처리 상태를 확인한다.

파일명이나 폴더명만으로 과목·날짜를 확정하지 않는다. `Submitted`와 `Cancelled`는
USER 체크박스이고, `Request Status`는 SYSTEM enum이다. 사용자는 `Request Status`를
승인 수단으로 설정하지 않는다. 중단하려면 `Cancelled=true`로 표시한다.

운영자는 receipt와 지정 위치로의 move/readback을 확인한다. 자동 AI 학습 노트,
파일명 기반 분류, 임의 Drive inbox watching, 원본 archive 보장은 이 preview의
기능으로 안내하지 않는다.

## 실행 명령

운영자: 설정과 provider 연결을 확인한 뒤 로컬 worker를 실행한다.

```sh
uls sync --max-jobs 20     # 새 요청을 발견하고 projection만 갱신
uls process --max-jobs 20  # 이미 발견된 작업을 처리
uls run --max-jobs 20      # 발견과 처리를 한 번에 수행
uls jobs --limit 20
```

`--max-jobs`는 한 번의 실행 상한이다. worker는 local single-active-worker lock을
사용하므로 이미 실행 중이면 두 번째 실행을 겹쳐 시작하지 않는다. preview가 아직
설정·검증되지 않았거나 provider 자격증명이 없으면 명령은 작업을 성공으로 꾸미지
않고 진단 가능한 상태로 멈춘다.
