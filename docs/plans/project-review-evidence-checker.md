# Syllva 프로젝트 전용 리뷰 증거 검사기

## 현재 적용 — 정상 설계 문서 한 개의 예외 추가 (2026-10-04)

사용자 “예외 추가하고 실제 3.11, 3.14는 CI 통해서 할 거지?” 결정에 따라, 독립 검토된 정확한 후보를 이 프로젝트에만 적용했다.

- 현재 checker SHA-256: `dac0e6f7f5b6c54ae0a7bc7b0ba5af1c4575c706d3cc9d79ba2c2c649c7b700d`.
- 기존 정확한 Python source 9개 pin은 그대로이며 별도 immutable document map에 `docs/plans/credential-secret-file-launcher.md` 한 개만 추가했다. 문서 SHA-256 `c94b94b19e98bc928969d359f59ad0f01ba48b527eb751d8f4c75b20f4568fa8`, 48,413 bytes, 기존 canonical root device/inode 제한을 유지한다.
- `_pinned_source_components` 및 `_verify_sources`만 문서 분기에 맞게 변경했다. descriptor-bound reader, artifact 경로 guard, 일반 Markdown 동작, Native/Gemini/identity/argv/model/effort/hash 검증은 불변이다. `.env`나 실제 자격증명 파일, 전역 검사기·hooks·설정 및 다른 프로젝트는 추가 허용하지 않는다.
- 계획의 REQUIRED1(일반 Markdown 동작 유지) 반영 후 Native 최종 후보 GO/REQUIRED0 및 현재 DACL/CI SOURCE GO/REQUIRED0를 받았다. 관련 전체 소스 22개, 원본 identity-bound 응답과 최대 slider 증거는 `.insane-review/gui23-20261002/ci-corrections-20261004/final-corrected-scopes/`에 보존한다.
- 기존 검사와 새 문서 합성 회귀는 36tests/113subtests PASS, 테스트 Ruff PASS, 기존 checker Ruff 진단3 유지·새 진단0이다. 적용 후 원본 gate와 실제 Windows3.11/3.14 CI 판정은 별도 작업 기록으로 이어 확인한다. 이 문서나 checker 성공은 제품 수락·인간 승인 발급을 뜻하지 않는다.

아래의 `a1004f…` 및 9개 source 설명은 2026-10-03 최초 적용 당시의 역사 기록이다. 현재 명령은 동일한 프로젝트 경로에서 위 SHA의 9 Python + 1 document checker를 실행한다.

2026-10-03 사용자 결정: **“아니. 이 프로젝트 내에서만.”** 전역 적용 제안을 거절하고, 이미 검토된 동일 검사기를 Syllva 프로젝트 내부에서만 사용한다. 개인 전역 검사기·hook·설정은 변경하지 않는다.

## 실행

```bash
python3 -B scripts/review_evidence_checker_candidate/state_check.py \
  --root /Users/admin/Project/Syllva --record <relative-task-record-path>
```

검사기는 저장소의 기존 경로에서 그대로 사용한다. 복사·전역 설치·새 wrapper·pin 자동 갱신 없이, 프로젝트 `AGENTS.md`가 이 명령을 지정한다. 파일명에 `candidate`가 남아 있어도 실제 프로젝트 명령은 이 검토된 파일을 사용한다.

- 코드 SHA-256: `a1004ffae36d06378ffe44a9b242699bc2c7c2109272a0ef6f0abe5c5892b836`.
- 기존 정확한 9개 source path/SHA/size 및 canonical Syllva root device/inode 제한 유지.
- 모든 artifact 경로 검사·Native/Gemini 모델·강도·원본 identity·응답/패키지 해시 검사 유지.
- 계획/최종 Native Latest 최대 강도 GO/REQUIRED0, 원본 identity-bound canonical 회수 및 후보 자신의 원본 증거 검사 PASS.
- 담당자 19tests/113subtests, compile PASS; 새 Ruff 진단0 및 테스트 진단0. 기존 baseline 진단3은 그대로이며 전체 lint clean으로 표현하지 않는다.

상세 검토·변경·검사 근거는 `.insane-review/checker-path-20261003/final-disposition.md`, 현재 사용 선택과 실행 결과는 `project-activation.json` 및 `progress.json`에 기록한다. 이전 계획과 리뷰 패키지는 당시 승인 범위의 불변 근거로 보존한다. 이번 채택은 구현 변경이 없는, 이미 검토된 정확한 코드의 프로젝트 한정 사용 선택이므로 유효한 코드 리뷰·검사를 재사용한다.

## 기존 기록 정합성 처분

2026-10-03 사용자 후속 진행에 따라 원본 구형 CLI/peer 기록과 리뷰 원문을 보존하고 별도 파생 정책 기록을 작성했다. 스키마의 Pro 선호 target과 인간의 Latest/최대 요청·실제 extra_high 실행을 구별해 기록했다. 실제 모델/강도/slider 및 원본 결속 응답은 불변이며 Pro 실행을 주장하지 않는다. 파생 두 기록과 unchanged R2/R3 FINAL/corrective PLAN은 이 프로젝트 명령 exit0이다. UI는 변경 없는19소스에 한해 의견을 재사용하고 backend3을 최신27소스 리뷰로 대체했으며 동일 Gemini ultra가 재사용 GO0을 확인했다. 상세 `.insane-review/gui23-20261002/reconciled-20261003/parent-disposition.md`. 프로젝트 전용 사용 선택만으로 수락한 것이 아니라, 현재 리뷰·소스·검사·재사용 범위 확인 후 GUI23 묶음을 부모가 수락했다. 전체 ULS 프로젝트/릴리스 완료는 아니다.

이 검사기는 sandbox·secret detector·승인 발급기가 아니다. 허용된 source path의 bytes는 해시 검증을 위해 읽으므로, 공격자가 일반 파일로 바꾼 내용을 읽기 전 기밀성까지 보장하지 않는다.
