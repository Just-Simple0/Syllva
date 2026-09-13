# 신뢰 모델

[English](trust-model.md) · [개념](README.ko.md)

Syllva는 AI workflow를 거치는 동안 서로 다른 정보가 의미를 조용히 바꾸지 않도록 설계합니다.

## SOURCE / AI / USER

- **SOURCE** — 설정된 authority/freshness 규칙 아래 근거로 취급하는 자료.
- **AI** — 생성된 설명, enrichment, proposal, 학습 보조물.
- **USER** — 사용자 소유 메모, 선택, 완료 상태, 승인, 기타 사람의 상태.

AI 출력이 그럴듯하다는 이유만으로 SOURCE가 되지 않습니다. 사용자 메모가 유용하다는 이유만으로 권위 있는 과목 근거가 되지 않습니다.

## Human gate

verification이나 scope confirmation 같은 필드는 사람이 소유할 수 있습니다. 보호는 “이 필드를 바꾸지 마”라는 prompt instruction 수준이 아니라 write boundary에 존재해야 합니다.

## Partial은 Complete가 아님

extraction, pagination, provider failure, 불완전한 course result 때문에 partial state가 생길 수 있습니다. Syllva는 그 불완전성을 보존하고 완전한 결과처럼 조용히 승격하지 않습니다.

## Provenance

학업 답변은 근거로 돌아갈 수 있어야 유용합니다. 그래서 retrieval result는 client가 주장의 출처를 보여줄 수 있도록 source identity/location 정보를 포함합니다.

## Freshness

오래된 derivative는 변경된 source를 더 이상 대표하지 않을 수 있습니다. freshness 검사는 stale generated/normalized material이 현재 source보다 조용히 우선되는 것을 막습니다.

## Capability 경계

제한된 retrieval capability는 의도한 scope/current source state만 허용합니다. 임의의 source 접근에 재사용하는 master token이 아닙니다.

## Fail closed

identity, authority, scope, 외부 결과가 모호하면 접근 범위를 넓히거나 성공을 추측하기보다 명시적 error/choice/reconciliation 상태를 선호합니다.

단순한 “내 모든 파일과 채팅” 시스템보다 보수적으로 느껴질 수 있지만 의도된 설계입니다. 학업 컨텍스트는 시스템이 실제로 무엇을 알고 있는지 사용자가 구분할 수 있을 때 더 가치가 있습니다.
