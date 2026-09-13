# Syllva 문서 원본

[English](README.md) · **[공식 문서 사이트에서 읽기](https://just-simple0.github.io/Syllva/ko/)**

이 디렉터리는 공개 문서 사이트의 버전 관리 Markdown 원본과 과거 엔지니어링 기록을 함께 보관합니다.

일반적인 문서 읽기와 탐색에는 **공식 Starlight 문서 사이트**를 사용하세요. 검색, 사이드바 탐색, 언어 전환을 제공하고 사용자에게 하나의 계층 구조로 보입니다.

## 공개 문서 원본

| 영역 | 용도 |
| --- | --- |
| [`user-guide/`](user-guide/README.ko.md) | 대시보드, 일상 학습, 파일 접수, AI 사용, 문제 해결 |
| [`operator-guide/`](operator-guide/README.ko.md) | 설치, provider 설정, intake, MCP, LMS, 운영 |
| [`concepts/`](concepts/README.ko.md) | 아키텍처와 신뢰 모델 |
| [`reference/`](reference/README.ko.md) | CLI, 상태, MCP 도구 |

이 공개 문서 영역의 모든 문서는 같은 basename의 `.ko.md` 한국어 버전을 제공합니다. docs-site 빌드는 영문/한국어 쌍을 먼저 검증한 뒤 이 원본에서 다국어 Starlight 콘텐츠 트리를 생성합니다.

## 버전 안내

현재 Syllva는 **0.1.3 beta**이며 공개 패키지 릴리스가 아닙니다. 저장소 루트의 frozen **1.2** 문서는 코어 동작/설계 프로토콜을 설명합니다. 패키지 버전과 프로토콜 버전은 의도적으로 분리합니다.

## 엔지니어링 기록

- `plans/` — 구현 계획, 검증 계획, 운영 증거.
- `ux/` — UX 정의, 리뷰 자료, readback 기록.

이 기록은 유지보수자와 리뷰어를 위해 원래 형태로 보존합니다. 공식 사이트에서는 엔지니어링 archive로 연결하지만 초보자용 기본 탐색에는 섞지 않습니다.
