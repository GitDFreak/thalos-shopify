# Contributing to thalos-shopify

Thanks for wanting to contribute. This guide covers everything you need
to send a good change.

## Scope

`thalos-shopify` is intentionally focused: **a single-user CLI that
syncs one Shopify store into another**. We welcome:

- Bug fixes (wrong data copied, edge cases, API regressions)
- New sections or improved coverage of existing Shopify resources
- Performance improvements (batching, parallelism, pagination)
- Better error messages, docs, and test coverage

We deliberately avoid:

- Multi-user / multi-tenant features — those live in the hosted THALOS
  platform
- A web UI — use the hosted platform if that's what you need
- Shopify-adjacent tools (theme development, product generation, etc.)
- Non-Shopify connectors (Amazon, Woo, ERP) — roadmap item for the
  hosted platform

Unsure? [Open an issue](https://github.com/GitDFreak/thalos-shopify/issues/new/choose)
before starting work.

## Development setup

```bash
git clone https://github.com/GitDFreak/thalos-shopify.git
cd thalos-shopify
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Running the tests

```bash
pytest -q            # 45 tests, ~0.1s
```

## Linting / security

```bash
ruff check .
bandit -c bandit.yaml -r .
pip-audit -r requirements.txt
```

All three run in CI on every push + PR.

## Pull request checklist

- [ ] Tests pass locally (`pytest -q`)
- [ ] Ruff is clean (`ruff check .`)
- [ ] Bandit is clean
- [ ] New behavior has a test; bugfixes have a regression test
- [ ] Public functions changed? Update the docstring
- [ ] CLI flags changed? Update README + `.env.example`
- [ ] Significant change? Add a line to `CHANGELOG.md` under `Unreleased`
- [ ] Commit messages are descriptive (imperative, present tense)

## Reporting bugs

Use the issue template. Always include:

1. Shopify API version (`SHOPIFY_API_VERSION`)
2. Sections run (`--sections`)
3. First error or stack trace
4. Whether the run was fresh or a `--resume`

Do **not** include your access tokens in issue reports. Ever.

## Security issues

Please report privately to `security@freaklabs.io`. See
[SECURITY.md](SECURITY.md). Do not open public issues for security bugs.

## Licensing of contributions

By submitting a PR you agree that your contribution is licensed under
the AGPL-3.0, the same license as the project.

## Commercial use questions

If AGPL-3.0 doesn't fit your use case (e.g. you want to embed this in a
closed-source commercial product), contact `hello@freaklabs.io` to
discuss a commercial license.
