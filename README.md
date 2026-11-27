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

The performance rollout cut the median inventory discrepancy query from
**201.191 ms to 54.375 ms** with identical results.
[Performance measurements and query plans](docs/006_performance.md)

## Supply the client data

Get the six original CSVs through Kloud Kart's approved file-sharing
channel and place them in `provided_resources/data/` at the repository
root. Git already supplies `schema.sql`, `load.sql`, and the unchanged
`race_test.py`. Use these exact names:

```text
provided_resources/
├── schema.sql
├── load.sql
├── race_test.py
└── data/
    ├── categories.csv
    ├── customers.csv
    ├── inventory_log.csv
    ├── order_items.csv
    ├── orders.csv
    └── products.csv
```

Git ignores `provided_resources/data/`. The rehearsal checks all nine
files and exits before starting Docker if any are missing, unreadable,
or empty.

## Reproduce the lab with one command

With the client CSVs in place and Docker running, run this command from the
repository root. The rehearsal uses Bash, `tar`, `python3`, and `uv` on the host.

```bash
bash scripts/rehearse.sh
```

The command starts a disposable PostgreSQL 18 container with a random local
port and password. It checks that the database is empty, loads the supplied
data, audits the original rows, and applies migrations 001–006. It uses
simulated counts for the test reconciliation. It checks the inventory trail,
category rollup, and Monday report, runs the supplied race suite three times unchanged, and measures
the Monday report and paced checkout both separately and together.

The command prints the evidence directory at startup and on success.
It contains `environment.txt` (source revision, runtime versions, image ID,
and Docker host resources), `audit-summary.txt` (separate audit result tables),
migration and test logs, both Monday CSVs, and checkout, report, and rollup
timings. The checkout scenarios take at least 80 seconds. The initial image and
dependency downloads can add time. The container is removed when
the command exits. Copy the evidence directory out of your system's
temporary directory if you need to retain it. Keep the evidence in approved
storage and share it only with authorized staff. The CSVs contain customer data.

Earlier Git revisions contain the client CSVs. **Keep this repository and
its Git history private.** For submission, commit the intended source
changes, then export a source-only ZIP:

```bash
if [ -n "$(git ls-tree -r --name-only HEAD -- provided_resources/data/)" ]; then
    printf 'Client data is tracked in HEAD. Resolve this before exporting.\n' >&2
else
    git archive --format=zip --output=../kloud-kart-source.zip HEAD
fi
```

The ZIP contains committed files from `HEAD` and excludes Git history,
local credentials, and untracked data. If the portal requires a repository
URL, use a repository whose history has been cleared for sharing.

The optional [pre-commit check](.githooks/pre-commit) rejects commits that
include tracked files under `provided_resources/data/`. If you have no
existing custom hooks, run `git config --local core.hooksPath .githooks`
to enable it in this checkout. If you use existing hooks, run this check
from your existing pre-commit hook.

Use this command for a rehearsal. For an approved live rollout, follow
the [production cutover guide](docs/002_stock_rollout.md#apply-to-production)
using verified physical counts.

## Run locally

You’ll need Docker with Compose and `uv`. Use **Python 3.14.10** to match
the rehearsal. The package accepts Python 3.14 or later. Later versions
remain unverified for this submission.
The `kk-*` commands use paths relative to the source checkout. Install
with `uv sync --locked` in this repository and run them from the checkout.
[Supply the client data](#supply-the-client-data) before seeding or
running the supplied race tests. Run these Bash commands from the repository root.

1. On a fresh checkout, copy the environment template and set a local test
   password in both `POSTGRES_PASSWORD` and `DATABASE_URL`. The default
   test connection is `127.0.0.1:5433`, database `kloudkart`.

   ```bash
   (umask 077; cp -n .env.example .env)
   ```

2. Install dependencies and start PostgreSQL.

   ```bash
   uv sync --locked --python 3.14.10
   uv run kk-test-db
   ```

3. For an empty test database, follow the
   [ordered seed and migration walkthrough](docs/002_stock_rollout.md#rehearse-from-an-empty-test-database)
   through migration 006. The supplied schema drops and recreates tables.
   On an existing test database, skip seeding and apply only pending migrations.

4. Launch the operator app.

   ```bash
   uv run kk-app
   ```

   Open `http://127.0.0.1:8501` for **Stock reconciliation**, **Monday exports**,
   **Low stock**, and **Inventory trail**.
   [Connection settings](docs/002_stock_rollout.md#connection-settings)

## Fresh-load verification

On 27 November 2026, a disposable PostgreSQL 18.7 instance loaded the
supplied data and applied migrations 001–006. The rehearsal used Docker Desktop
29.9.3 on x86-64 with 8 CPUs, about 16 GiB RAM available to Docker, and
Python 3.14.10. It started PostgreSQL with `max_connections=450` for the
400-buyer race case. The stock counts used to reconcile the seed were
simulated. Timings vary with machine resources and concurrent load.

| Check | Result |
| --- | --- |
| Supplied race suite | All four cases passed on three consecutive runs with the supplied script unchanged. Each 400-buyer burst sold 50 units, refused 350, and returned zero errors. |
| Inventory, category, and report fixtures | Passed, including movement links and permissions, a 12-level branch, the exclusive report cutoff, and ranking ties. |
| Monday report, cutoff 2027-08-21 | 2.85–3.37 seconds across three consecutive runs, including generation of both CSVs. Verified 40,420 customer summaries and 100 ranked customers. |
| Low-stock data load | 23–26 ms across three runs, including connection and both view reads. |

To reproduce, start with the [empty test database setup](#run-locally).
After applying migrations 001–006, use the [test connection settings](docs/002_stock_rollout.md#connection-settings)
and [raise the test connection limit for the 400-buyer burst](docs/002_stock_rollout.md#run-the-checkout-tests).
Run from the repository root, then restore the original connection limit.

```bash
(
  set -e
  unset DATABASE_URL
  for run in 1 2 3; do uv run --locked kk-race-test; done
  uv run --locked python -m unittest discover -s tests
)
```

Run just the verification commands from the [category guide](docs/004_low_stock_rollup.md#install-and-verify)
and [inventory guide](docs/005_inventory_trail.md#rehearse-on-test) after the
migrations are applied. For the report, follow the [Monday export steps](docs/003_monday_report.md#run-the-app-and-export)
using cutoff `2027-08-21`. The terminal export also includes time to write the
CSV files. The timings above are from the local rehearsal.

All eight paced scenarios also completed 400 attempts within ten seconds.
The four overlapping reports took 2.61–4.53 seconds. The two report-only
baselines for those pairs took 2.86 and 7.37 seconds.
[Shared-workload method](docs/006_performance.md#monday-report-during-a-flash-sale)

## Useful commands

| Command | Use |
| --- | --- |
| `uv run kk-test-db ps` | Check the test container status. |
| `uv run kk-test-db-exec` | Open a shell in the test container. |
| `uv run kk-race-test --test 1` | Run the supplied race test for the last unit. |
| `uv run kk-race-test` | Run the full supplied suite. The 400-buyer case needs [extra test connections](docs/002_stock_rollout.md#run-the-checkout-tests). |
| `uv run kk-flash-sale-test` | Run the separate test that paces about 40 checkout attempts per second for ten seconds. |

Run checkout tests against the test database. They create and remove fixtures.
An exported `DATABASE_URL` takes precedence over the local `.env` file.
Use the rehearsal command if you want an isolated target.
This command opens `psql` directly in the `kloudkart` test database.

```bash
uv run kk-test-db-exec sh -ec 'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Submission map

| Brief deliverable | Where to find it |
| --- | --- |
| Written diagnosis | [Stage 001 · Ranked data integrity findings and cost estimates](docs/001_data_integrity_diagnosis.md), with [baseline audit SQL](scripts/schema_audit.sql) |
| Working SQL solution | [Migrations 001–006](scripts/migrations/), [Monday report query](scripts/monday_report.sql), and [fresh-load rehearsal](#reproduce-the-lab-with-one-command) |
| Key decisions and trade-offs | [Architecture decision records](docs/adr/) and the rollout guides below |
| One-page operations memo | [Memo to Nana](docs/memo_to_nana.md) |
| Implemented improvement | [Operator app and its business value](docs/improvement_beyond_the_brief.md) |

| Stage | Guide |
| --- | --- |
| 002 | [Stock reconciliation and safe checkout](docs/002_stock_rollout.md) |
| 003 | [Monday report rules, calculations, and exports](docs/003_monday_report.md) |
| 004 | [Low-stock category rollup](docs/004_low_stock_rollup.md) |
| 005 | [Inventory trail and database permissions](docs/005_inventory_trail.md) |
| 006 | [Performance experiments and reproduction steps](docs/006_performance.md) |

Deploy to the existing live database only after approval. Start with the
[stock rollout cutover](docs/002_stock_rollout.md#apply-to-production) and the
[inventory trail cutover](docs/005_inventory_trail.md#production-cutover).
The separate production Compose wrapper, `uv run kk-prod-db`, reads `.env.prod`
if you operate a production container from this repository.
