# Security Policy

## Reporting a vulnerability

Please report security issues privately — **do not open a public GitHub issue**.

Email: `security@freaklabs.io`

Include:
- Description of the issue and its impact
- Steps to reproduce (or proof-of-concept)
- Affected version (git commit hash if possible)
- Your name/handle for attribution (optional)

You'll receive an acknowledgement within 72 hours. We aim to ship a fix
within 30 days for high-severity issues.

## Supported versions

Only the `main` branch receives security patches. Tagged releases are
snapshots.

## Handling Shopify tokens

`thalos-shopify` is a CLI tool that reads Shopify access tokens from
environment variables (`SOURCE_TOKEN`, `DEST_TOKEN`). No tokens are
persisted to disk by the tool itself.

**Best practice:**

- Store tokens in `.env` (already in `.gitignore` + `.dockerignore`).
- Never commit `.env` — verify before any `git add -A`.
- Use narrowly-scoped custom Shopify apps for sync, not admin-wide tokens.
- Rotate tokens regularly; revoke any token that might have leaked.
- Treat the destination-store token as write-privileged — sync is destructive
  when `--delete-orphans` is enabled.

## Known non-goals

This tool is intentionally **single-user**. If you need multi-user, RBAC,
audit logs, SSO, or managed hosting, use the THALOS hosted platform.
