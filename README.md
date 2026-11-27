# Kloud Kart Database Solutions

PostgreSQL 18 lab solution for Kloud Kart, an e-commerce company in Accra.
It prevents competing checkouts from selling the same stock, gives Adwoa a
repeatable Monday customer-spending report, and helps Nana review low stock
and trace inventory changes. [Lab brief](lab_brief.md)

## What’s included

| Feature | What it does |
| --- | --- |
| Stock reconciliation and checkout | Operators can pause products and record verified counts. Checkout saves the stock deduction, order, order item, and inventory movement in one transaction. |
| Monday report | Shows customer order counts and recorded spend, 12 complete months of spending, running totals, and a top-100 ranking. The app offers separate CSVs for customer history and the ranking. |
| Low-stock rollup | Counts reorder candidates across nested categories, flags broken category paths for review, and lets operators drill down to the affected products. |
| Inventory trail | Records stock movements through controlled database operations and lets an auditor trace sales, restocks, and corrections. |

The supplied race suite passed all four tests on the seeded test database.
In its 400-buyer burst, checkout sold 50 available units and rejected the
remaining 350 attempts with zero errors. The performance rollout also cut
the median inventory discrepancy query from **201.191 ms to 54.375 ms**
with identical results. [Checkout results](docs/002_stock_rollout.md#rehearsal-results) ·
[Performance measurements and query plans](docs/006_performance.md)

## Run locally

You’ll need Docker with Compose, `uv`, and Python 3.14 or later.
Run these Bash commands from the repository root.

1. On a fresh checkout, copy the environment template and set a local test
   password in both `POSTGRES_PASSWORD` and `DATABASE_URL`. The default
   test connection is `127.0.0.1:5433`, database `kloudkart`.

   ```bash
   cp -n .env.example .env
   ```

2. Install dependencies and start PostgreSQL.

   ```bash
   uv sync --locked
   uv run kk-test-db
   ```

3. For an empty test database, follow the [seed and reconciliation walkthrough](docs/002_stock_rollout.md#rehearse-from-an-empty-test-database)
   through migration 003. Then install [004 for low stock](docs/004_low_stock_rollup.md#install-and-verify),
   [005 for the inventory trail](docs/005_inventory_trail.md#rehearse-on-test),
   and [006 for performance](docs/006_performance.md#reproduce-and-roll-out) in order.
   On an existing test database, apply only pending migrations. The supplied
   seed schema drops and recreates tables, so keep it on an empty test database.

4. Launch the operator app.

   ```bash
   uv run kk-app
   ```

   Open `http://127.0.0.1:8501` for **Stock reconciliation**, **Monday exports**,
   **Low stock**, and **Inventory trail**.
   [Connection settings](docs/002_stock_rollout.md#connection-settings)

## Useful commands

| Command | Use |
| --- | --- |
| `uv run kk-test-db ps` | Check the test container status. |
| `uv run kk-test-db-exec` | Open a shell in the test container. |
| `uv run kk-race-test --test 1` | Run the supplied race test for the last unit. |
| `uv run kk-race-test` | Run the full supplied suite. The 400-buyer case needs [extra test connections](docs/002_stock_rollout.md#run-the-checkout-tests). |
| `uv run kk-flash-sale-test` | Run the separate test that paces about 40 checkout attempts per second for ten seconds. |

Run checkout tests against the test database. They create and remove fixtures.
This command opens `psql` directly in the `kloudkart` test database.

```bash
uv run kk-test-db-exec sh -ec 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Rollout guides

[Memo to Nana](docs/memo_to_nana.md) summarizes the operational decisions,
rehearsal results, and checks before launch.

[Improvement beyond the brief](docs/improvement_beyond_the_brief.md) explains
how Nana and Adwoa can use the Streamlit app in their day-to-day work.

| Stage | Guide |
| --- | --- |
| 001 | [Data integrity diagnosis and ranked findings](docs/001_data_integrity_diagnosis.md) |
| 002 | [Stock reconciliation and safe checkout](docs/002_stock_rollout.md) |
| 003 | [Monday report rules, calculations, and exports](docs/003_monday_report.md) |
| 004 | [Low-stock category rollup](docs/004_low_stock_rollup.md) |
| 005 | [Inventory trail and database permissions](docs/005_inventory_trail.md) |
| 006 | [Performance experiments and reproduction steps](docs/006_performance.md) |

[Architecture decision records](docs/adr/) explain the choices and trade-offs.
Deploy to the existing live database only after approval. Start with the
[stock rollout cutover](docs/002_stock_rollout.md#apply-to-production) and the
[inventory trail cutover](docs/005_inventory_trail.md#production-cutover).
The separate production Compose wrapper, `uv run kk-prod-db`, reads `.env.prod`
if you operate a production container from this repository.
