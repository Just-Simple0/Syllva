# Syllva 문서 사이트

[English](README.md)

이 디렉터리는 Syllva 공식 문서 사이트의 Astro Starlight 표시 계층입니다.

`../docs/user-guide`, `../docs/operator-guide`, `../docs/concepts`, `../docs/reference`의 공개 Markdown을 계속 source of truth로 사용합니다. `scripts/sync-docs.mjs`가 빌드 시 영문/한국어 쌍을 검증하고 Starlight 콘텐츠로 변환하며, 공개 문서 사이 링크도 사이트 경로에 맞게 변환합니다. 따라서 문서 사본을 별도로 커밋하지 않습니다.

## 로컬 미리보기

Node.js 24+가 필요합니다.

```bash
cd docs-site
npm install --package-lock=false
npm run check:docs
npm run dev
```

생성된 콘텐츠는 `src/content/docs/`에 위치하며 gitignore 대상입니다.

## 프로덕션 빌드

```bash
npm run build
```

사이트는 GitHub Pages의 `https://just-simple0.github.io/Syllva/` 경로를 기준으로 설정했습니다. 저장소 workflow는 PR에서 빌드 검증만 하고 `main`에서만 배포합니다.

첫 배포 전 GitHub 저장소의 Pages 설정에서 소스를 **GitHub Actions**로 선택하는 1회 설정이 필요할 수 있습니다.
