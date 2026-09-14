# 설치

[English](installation.md) · [운영자 가이드](README.ko.md)

## 요구사항

- Python 3.11+
- 저장소에 포함된 데스크톱 scheduler profile을 사용할 경우 macOS 또는 Windows
- Git
- 실제로 활성화할 기능에 필요한 외부 provider 계정

## Clone과 가상환경

```bash
git clone https://github.com/Just-Simple0/Syllva.git
cd Syllva
python -m venv .venv
```

활성화:

```bash
# macOS / Linux shell
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

로컬 개발/운영 검증에 필요한 기능을 포함해 editable beta를 설치합니다.

```bash
pip install -e '.[dev,mcp,drive,notion,pdf]'
```

`pdf` extra는 PDF preview/text extraction 경로에 필요합니다. 기타 provider adapter는 각 optional extra 경계 뒤에 있습니다.

## 로컬 상태 초기화

```bash
uls init
uls doctor
uls status
uls behavior lint
```

`uls init`은 없는 로컬 설정/상태 파일만 만듭니다. 실제 Notion workspace provisioning, Drive 감시, credential 등록, scheduler 활성화를 하지 않습니다.

선택한 기능에 필요한 provider ID와 credential을 아직 넣지 않았다면 `uls doctor`가 미완료 설정을 보고하는 것이 정상입니다.

## 저장소 검사

운영 PR을 열거나 scheduler를 활성화하기 전에 다음을 실행하세요.

```bash
python -m pytest -q
python scripts/lint_behavior_projection.py
python -m compileall -q src
```

저장소 CI는 macOS와 Windows에서 실행됩니다. 자동화 검사를 통과해도 실제 provider/client E2E가 증명되는 것은 아닙니다.

## 다음 단계

[설정](configuration.ko.md)으로 이동한 뒤 실제로 사용할 lane만 구성하세요.
