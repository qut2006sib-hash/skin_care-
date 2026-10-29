# Local MVP acceptance evidence — 29 October 2026

Candidate branch: `feat/complete-local-mvp`. Source repairs start from uploaded archive commit `d308f19`; remote snapshot import provenance is in SOURCE_IMPORT.md.

## Verified application behavior

| Check | Result | Evidence scope |
|---|---|---|
| Django startup checks | PASS | No system-check issues. WSGI/templates/static settings and initial migrations are present. |
| Migration drift | PASS | No model changes missing from committed migrations. |
| Django diagnostic suite | 75 passed, 6 skipped | SQLite diagnostic settings only; skipped tests require PostgreSQL/client tools. |
| Real PostgreSQL suite | 81 passed | GitHub run 40903508998 at commit `0a58de55ef5741183e6c83185068e0feed996b00`, including five concurrency/lock tests and actual backup/restore roundtrip. |
| Production frontend build | PASS | Next.js 16.4.3 + React 19.3.1, locked dependencies; GitHub frontend job at the same commit. |
| Real live browser flow | PASS | Chromium, actual Next.js + Django, disposable SQLite database; mobile purchase → confirm → dispatch → delivery/customer collection → courier remittance → return quarantine → inspection/restock → refund payment. Live private tracking updates correctly. No page errors. |
| UI recovery browser scenarios | PASS | Intercepted API responses verify exact-key/body retry after lost response and reload, explicit changed-price approval, search, recent orders, offline cart, item removal and mobile overflow. This is UI behavior coverage rather than PostgreSQL acceptance. |
| Docker packaging and CI browser jobs | PASS | GitHub run 40904793064 at implementation commit `8b7f351875c4c7513cd8d969f2b0638081671cae`: actual Docker build/start, proxied health/catalog, restart preservation, plus full live and mocked browser acceptance. |

Complete application evidence: [all four jobs passed](https://github.com/qut2006sib-hash/skin_care-/actions/runs/40904793064).

PostgreSQL evidence: [successful workflow](https://github.com/qut2006sib-hash/skin_care-/actions/runs/40903508998). The first workflow run found a test isolation issue: a dotenv-parser fixture expected its own password while CI intentionally provided a process password. The corrected fixture isolates that variable; application precedence was preserved. The subsequent full PostgreSQL run passed.

## Repaired invariants

- Normal startup runs migrations and initializes permissions, never demo stock seeding.
- Repeat demo seeding preserves stock, prices, conditions and delivery configuration.
- Same-key checkout/receipt/return/refund/remittance retries return the original operation; changed payloads conflict.
- Last-item reservation, expiry-versus-confirmation and checkout-versus-return locking are covered on PostgreSQL.
- FEFO uses one effective validity date and deterministic stock locks.
- Returns bind to consumed allocations, enter physical quarantine and have a separate inspection decision. Another SKU/lot cannot be credited.
- Customer collection, courier remittance, authorized refunds and actual refund payments stay separately accountable.
- Operational Admin editing cannot bypass stock/order services; role-scoped actions are tested.

## Live browser reproduction

Install Python requirements and Playwright/Chromium, run `npm ci` in frontend, then:

```bash
python tests/run_browser_checks.py
```

The runner creates a temporary database and owner password, starts both servers and closes them on completion. It never uses the operational PostgreSQL database. For an externally installed Chromium, set `CHROMIUM_EXECUTABLE`; `CHROMIUM_ARGS_JSON` can supply launcher arguments. Browser web security is retained.

Next development uses Webpack, matching the tested build path. Exact local development origins are permitted; extra LAN development hostnames can be set with `NEXT_DEV_ALLOWED_HOSTS`. Production uses the built Next server, without the dev-origin gate.

## Operator acceptance still required

This is a local pilot candidate. Before genuine sales, supply real reviewed labels/images/claims, supplier/validity evidence, stock receiving records, selling prices, actual delivery zones/fees and support/policy details. Create your own staff accounts and rehearse backup/restore on your Ubuntu machine. The ten demo SKUs and demonstration fees used by tests are not real business facts.

Native Android/iOS apps, online payment providers, marketplace sellers, loyalty and AI recommendations are outside this first selling workflow. The responsive PWA is the current customer client. Public internet hosting is a separate deployment task; the supplied setup binds to localhost by default.

No branch was merged into main and no public customer-facing service was deployed.
