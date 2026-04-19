# thalos-shopify

**The open source Shopify store-to-store sync tool.**
Clone, migrate, and mirror any Shopify store into another — products,
variants, inventory, media, metafields, metaobjects, collections, pages,
blogs, redirects, menus, discounts, translations, markets, orders, themes.

Built for developers, ops, and agencies who need a real, scriptable
solution — not another clunky app. Battle-tested, idempotent, resumable.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org)

---

## What it does

A CLI that synchronises **one Shopify store into another** with production
semantics:

- **19 resource types** covered: files, locations, pages, blogs, products
  (with variants, inventory, media, SEO, metafields), collections (smart
  + custom), metaobjects, publications, menus, stock, discounts
  (automatic + code), metafield definitions & values, translations,
  redirects, markets, orders, themes.
- **Diff-based, idempotent** — re-running is a no-op when nothing changed.
- **Incremental mode** — `--orders-since 2025-01-01` + `updated_at_min`
  filtering on supported resources.
- **Resumable** — state + id-maps are checkpointed to disk; `--resume`
  picks up where a previous run stopped (network blip, API quota hit, etc).
- **Parallel workers** auto-tuned to the destination plan.
- **URL rewriting** — `body_html`, metaobject GIDs, and internal references
  are remapped from source to destination domain.
- **Dry-run** — preview every operation without mutating the destination.
- **Rate-limited + retry-aware** — backs off on Shopify's REST and
  GraphQL throttles automatically.

Not a scheduler. Not a UI. Not a team tool. A *focused* sync engine you
run from a terminal, a cron, a CI pipeline, or a Docker container.

## Installation

### Docker (recommended)

```bash
git clone https://github.com/GitDFreak/thalos-shopify.git
cd thalos-shopify
cp .env.example .env          # fill your tokens
docker build -t thalos-shopify .
docker run --rm --env-file .env -v "$PWD/snapshot_store:/app/snapshot_store" thalos-shopify
```

### From source

```bash
git clone https://github.com/GitDFreak/thalos-shopify.git
cd thalos-shopify
pip install -r requirements.txt
cp .env.example .env          # fill your tokens
python -m thalos
```

## Usage

```bash
# Full sync
python -m thalos

# Only specific sections
python -m thalos --sections products,theme,metaobjects

# Preview without writing
python -m thalos --dry-run

# Remove resources on destination that no longer exist on source
python -m thalos --delete-orphans

# Only sync orders since a given date
python -m thalos --orders-since 2025-01-01

# Resume a previously interrupted sync
python -m thalos --resume
```

## Configuration

All options are read from environment variables (or a `.env` file — see
`.env.example`):

| Variable                | Required | Description                                     |
|-------------------------|----------|-------------------------------------------------|
| `SHOPIFY_SOURCE_STORE`  | yes      | Source store slug (no `.myshopify.com` suffix) — read-only |
| `SHOPIFY_SOURCE_TOKEN`  | yes      | Admin API token on source                       |
| `SHOPIFY_DEST_STORE`    | yes      | Destination store slug                          |
| `SHOPIFY_DEST_TOKEN`    | yes      | Admin API token on destination (write scope)    |
| `SHOPIFY_API_VERSION`   | no       | default: `2026-01`                              |
| `SNAPSHOT_DIR`          | no       | default: `./snapshot_store`                     |
| `MAX_WORKERS`           | no       | `0` = auto-detect from destination plan         |
| `ORDERS_SINCE`          | no       | ISO date to filter orders from                  |

## How it compares

|                             | `thalos-shopify` (OSS)    | THALOS (hosted)   | Typical migration app |
|-----------------------------|:---:|:---:|:---:|
| Self-hosted, CLI-first      | yes | —   | no  |
| Full resource coverage      | yes | yes | partial |
| Idempotent, resumable       | yes | yes | no  |
| Scriptable / CI-friendly    | yes | via API | no |
| Web dashboard               | —   | yes | yes |
| Multi-store management      | —   | yes | partial |
| Team access + RBAC          | —   | yes | —   |
| Scheduled recurring syncs   | —   | yes | —   |
| Multi-connector (Amazon, Woo, ERP) | — | yes (roadmap) | — |
| Managed hosting + SLA       | —   | yes | vendor-dep. |
| License                     | AGPL-3.0 | Commercial | Proprietary |

## THALOS — the hosted platform

Need a web dashboard, team access, scheduling, multi-store dashboards, or
connectors beyond Shopify (Amazon, WooCommerce, ERPs)?

**→ [thalos.freaklabs.io](https://thalos.freaklabs.io)** — hosted by the
maintainers of this project. `thalos-shopify` is the open-source
connector that powers part of it.

## Safety notes

- The **source store is never written to.** The tool issues only GET
  requests against `SOURCE_STORE`.
- `--delete-orphans` is destructive on the destination. Always run with
  `--dry-run` first on a fresh target.
- Orders sync copies order data *as-is* — customer PII included. Make
  sure the destination store complies with your data-handling policy.
- Store access tokens live in `.env`, which is gitignored. Never commit it.

## Contributing

Pull requests welcome. For substantive changes, open an issue first to
discuss what you have in mind.

```bash
pytest -q        # 45 tests, ~0.1s
bandit -c bandit.yaml -r .
pip-audit -r requirements.txt
```

## Security

See [SECURITY.md](SECURITY.md). Report vulnerabilities privately to
`security@freaklabs.io`.

## License

[AGPL-3.0](LICENSE). If you run this as a network service, any
modifications you make must be released under the same license.
For commercial licensing without the AGPL reciprocity, contact
`hello@freaklabs.io`.

---

Built by [FREAKLABS](https://freaklabs.io). Part of the THALOS ecosystem.
