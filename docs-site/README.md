# Syllva documentation site

[한국어](README.ko.md)

This directory contains the Astro Starlight presentation layer for the official Syllva documentation site.

The public Markdown under `../docs/user-guide`, `../docs/operator-guide`, `../docs/concepts`, and `../docs/reference` remains the source of truth. `scripts/sync-docs.mjs` validates English/Korean pairs, converts those files into Starlight content at build time, and rewrites public-document links without committing a second copy of the documentation.

## Local preview

Requires Node.js 24+.

```bash
cd docs-site
npm install --package-lock=false
npm run check:docs
npm run dev
```

Generated content lives under `src/content/docs/` and is gitignored.

## Production build

```bash
npm run build
```

The site is configured for GitHub Pages at `https://just-simple0.github.io/Syllva/`. The repository workflow builds pull requests for validation and deploys only from `main`.

GitHub Pages may require a one-time repository setting selecting **GitHub Actions** as the Pages source before the first deployment.
