# skin_care — Homs Skincare Shopping App (Section 15 First Package)

Original Arabic-first RTL skincare e-commerce system built with **Django + Django REST Framework + PostgreSQL + Next.js PWA + Docker Compose**.

## Repository & Branch Workflow

All development is organized in incremental feature branches and Pull Requests (never pushing experimental work directly to `main`):

1. `main` — Architecture, PostgreSQL schema specification, data invariants, and measurable acceptance criteria (`docs/ARCHITECTURE.md`).
2. `feat/pr1-postgres-schema-and-seed` — Django models, PostgreSQL constraints, Role-Based Access Control (RBAC) in Django Admin, and the 10-SKU Section 15 seed command (`MEDEE`, `JUVELAB`, `FEME Hair`).
3. `feat/pr2-inventory-and-order-service` — Single authoritative `InventoryOrderService` enforcing FEFO batch allocation, PostgreSQL `SELECT ... FOR UPDATE` row locking, idempotent checkout, 12-hour unconfirmed COD hold expiration, guarded confirmation vs. expiration transitions, quarantine returns, and separate customer cash collection vs. courier remittance records.
4. `feat/pr3-drf-api-and-nextjs-rtl-pwa` — Django REST Framework API endpoints, server-enforced staff permissions, Next.js Arabic RTL PWA storefront, and Docker Compose orchestration.
5. `feat/pr4-concurrency-and-acceptance-tests` — Automated `pytest` PostgreSQL integration tests and standalone transactional verification suite covering all 18 launch acceptance scenarios.

## Quick Start (Local Development with Docker Compose)

```bash
# 1. Copy environment variables
cp .env.example .env

# 2. Start PostgreSQL, Django backend, and Next.js Arabic RTL PWA
docker compose up --build -d

# 3. Run migrations and seed the 10 representative SKUs + RBAC groups
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_section15

# 4. Run the automated PostgreSQL pytest suite
docker compose exec backend pytest -v

# 5. Run the standalone transactional verification suite
python3 tests/run_sandbox_verification.py
```
