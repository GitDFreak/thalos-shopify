# Changelog

All notable changes to `thalos-shopify` will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — 2026-04-20

Initial public release. Derived from the internal THALOS platform as a
focused, single-user CLI for Shopify store-to-store sync.

### Features

- CLI entry point (`python -m thalos`) with flags: `--sections`,
  `--dry-run`, `--delete-orphans`, `--orders-since`, `--resume`
- 19 sync sections covering files, locations, pages, blogs, products
  (with variants, inventory, media, metafields), collections (smart +
  custom), metaobjects, publications, menus, stock, discounts,
  metafield definitions + values, translations, redirects, markets,
  orders, themes, delivery profiles, customer segments, webhooks
- Diff-based, idempotent sync — re-running is a no-op when nothing changed
- Incremental mode via `updated_at_min` filtering on supported resources
- Resumable via disk-checkpointed state + id-maps
- Worker count auto-tuned to the destination Shopify plan
- `body_html` + metaobject GID + internal reference URL rewriting
- Rate-limit aware (REST + GraphQL throttle honoured automatically)
- Parallel workers with per-section concurrency
- HTML sync report generation

### Packaging

- AGPL-3.0 license
- Python 3.11 / 3.12 / 3.13 tested in CI
- Docker image (`python:3.13-slim`)
- `pyproject.toml`-based install: `pip install thalos-shopify`

### Security tooling

- Bandit (SAST), pip-audit (CVE), semgrep, trufflehog (secrets),
  dependabot, pre-commit hooks

[Unreleased]: https://github.com/GitDFreak/thalos-shopify/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/GitDFreak/thalos-shopify/releases/tag/v1.0.0
