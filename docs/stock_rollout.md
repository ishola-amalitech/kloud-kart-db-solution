# Stock reconciliation and safe checkout

Rehearse this rollout on the seeded test database. Apply the migrations to the
existing production database after review and during a checkout maintenance
window. Use the supplied seed scripts only on an empty test database.
`schema.sql` drops and recreates the operational tables.

## Contents

- [Start here](#start-here)
- [Connection settings](#connection-settings)
- [Open the seeded test database](#open-the-seeded-test-database)
- [Use the reconciliation app](#use-the-reconciliation-app)
- [Apply to production](#apply-to-production)
- [Migration reference](#migration-reference)
- [Rehearse from an empty test database](#rehearse-from-an-empty-test-database)
- [Run the checkout tests](#run-the-checkout-tests)
- [Rehearsal results](#rehearsal-results)

## Start here

| Goal | Start with |
| --- | --- |
| Use the app on the existing test database | [Use the reconciliation app](#use-the-reconciliation-app) |
| Load a fresh test database from the supplied bundle | [Rehearse from an empty test database](#rehearse-from-an-empty-test-database) |
| Deploy on the live database | [Apply to production](#apply-to-production) |

## Connection settings

Run the Bash commands in this guide from the **repository root in your normal
terminal**. Python, Streamlit, and `uv` run there. For SQL, the commands below
start `psql` inside the test container and pipe SQL to it from your host. You
can also enter SQL manually at the `kloudkart=#` prompt.

The host-side connection string is `DATABASE_URL` in the ignored `.env` file.
Use [`.env.example`](../.env.example) to configure a fresh checkout. Keep the
URL's username, password, and host port in sync with the Compose settings in
that file. Percent-encode reserved characters in the URL credentials.
The `kk-race-test`, `kk-flash-sale-test`, and `kk-app` commands load
the repository's `.env` by default. An already exported `DATABASE_URL`
or one supplied with `uv run --env-file` takes precedence. Unset an exported
value before switching environment files.
Inside the container, `psql` uses the configured `POSTGRES_USER` and
`POSTGRES_DB` over its local socket.

For example, run the worklist against the existing test database from the
repository root:

```bash
uv run kk-test-db exec -T postgres sh -ec \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < scripts/negative_stock.sql
```

## Open the seeded test database

The rehearsal used the project's persistent Compose test container,
`kloud-kart-test-postgres-1`, and its **`kloudkart`** database. PostgreSQL
keeps a separate administrative database named `postgres`. A prompt of
`postgres=#` means your session is in that database.

In an existing session connected to the test container, switch databases and
inspect the tables.

```text
\connect kloudkart
\dt public.*
SELECT count(*) FROM public.products;
```

From the repository root you can also connect directly.

```bash
uv run kk-test-db-exec sh -ec 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

The seeded test database has 4,023 products. To inspect the persisted
rehearsal corrections, query `public.inventory_log` for notes containing
`Test rehearsal only`.

## Use the reconciliation app

Start the operator app from the repository root. Migration 001 enables
reconciliation and availability changes. Migration 003 enables adding stock.

If your test database already has migrations 001 and 002, apply only 003
before launching the updated app. Skip this command if 003 is already applied.

```bash
uv run kk-test-db exec -T postgres sh -ec \
    'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < scripts/migrations/003_restock_paused_products.sql
```

```bash
uv sync
uv run kk-test-db
uv run kk-app
```

Open Kloud Kart Database Solutions at `http://127.0.0.1:8501` and select **Stock reconciliation**.
Choose **Negative stock**, **Paused**, or
**All products**, then select rows directly in the worklist. Each page shows
10 products. Your selection carries across pages within the same view.
Use **Select all** to select every product in the current view across all
pages, or **Clear selection** to start over.
The available actions appear directly below the table and apply to the
entire selection. Select products that share an action when working in batches.

| Task | Steps |
| --- | --- |
| Reconcile negative stock | Select rows with negative balances, choose **Record physical counts**, and enter a verified count for each and a stocktake reference. Corrections leave the products paused. |
| Add stock to paused products | Select rows in **Paused**, then choose **Add stock and keep paused** or **Add stock and resume**. Enter the units received for each and a delivery reference or reason. |
| Change availability without adding stock | Select the rows, then choose **Pause sales** or **Resume sales**. |

For example, adding 8 units to a product with 3 in stock gives it 11.
Every stock update returns inventory log IDs. If any selected product
fails, the whole batch rolls back, including availability changes.
Use **Refresh products** before retrying after a stock or status change.

The existing rehearsal corrected 14 products to zero and left them paused.
The app opens **Paused** when there are no negative balances. A resumed
product at zero remains out of stock until it receives stock.

### Connect the app to production

For live operation, supply the production operator connection through a
protected environment file or secret store and verify the target before editing.
Use a dedicated role with the permissions needed to read products and invoke
the migration functions. Those functions run with the caller's permissions,
so the role also needs the stock and availability updates and the movement-log
insert and returned log-ID read used by those functions.
After migration 005, follow the [inventory trail cutover](inventory_trail.md#production-cutover)
for database roles and controlled writes. The invoker grants above apply
to migrations 001–003.

The app is a local operator tool without its own login. Keep it bound to
loopback or place it behind authenticated internal access.

## Apply to production

Use a host terminal with `psql` installed. Set `DATABASE_URL` through an
approved secret source and verify the target before writing. Run the commands
in order from the project root. Each migration is applied once.

```bash
live_psql() {
    psql "${DATABASE_URL:?Set the production DATABASE_URL}" -X -v ON_ERROR_STOP=1 "$@"
}
live_psql -c 'SELECT current_database(), current_user, inet_server_addr(), inet_server_port();'
```


1. Back up production and arrange a stocktake for the affected products before
   starting the maintenance window. Pause legacy checkout before applying the
   first migration. The new flag only stops orders through paths that check it.
   Coordinate with other stock writers so a product cannot move between the
   physical count and the correction. The database checks the expected
   balance as an additional safeguard against stale corrections.
2. Install the reconciliation controls and list the affected products.

   ```bash
   live_psql -f scripts/migrations/001_stock_reconciliation.sql
   live_psql -f scripts/negative_stock.sql
   ```

3. For each product, review its movement
   history and verify the physical count. Use the recorded balance from the
   worklist when calling the correction function, or use the app.
   Put the count reference and reason in the note. The function returns the correction's log ID and leaves
   the product paused.

   ```sql
   SELECT log_id, change_qty, reason, created_at, note
   FROM public.inventory_log
   WHERE product_id = 5005
   ORDER BY created_at, log_id;

   -- Example with an expected balance of -4 and a verified physical count of 3.
   SELECT public.reconcile_negative_stock(5005, -4, 3, 'Verified recount, ticket INV-123');
   ```

4. Rerun the worklist. Leave products without verified counts paused and
   postpone migration 002 until the worklist is empty. Then apply
   `scripts/migrations/002_safe_checkout.sql`, which validates the stock check
   against all products before installing checkout. An existing NULL stock
   balance remains unknown and is ineligible for sale.

   ```bash
   live_psql -f scripts/negative_stock.sql
   # Continue only if the worklist is empty.
   live_psql -f scripts/migrations/002_safe_checkout.sql
   live_psql -f scripts/migrations/003_restock_paused_products.sql
   ```

5. Route all sales through `public.place_order(customer_id, product_id, quantity)`
   before ending the maintenance window. Use `READ COMMITTED` and commit
   promptly, ideally by making the function call in autocommit mode. Coordinate
   the change with writers that currently assign their own order, order-item,
   or inventory-log IDs. Use the new identity defaults for new rows so IDs
   generated by different writers cannot collide. Apply restocks and returns
   as atomic increments with a corresponding movement in the same transaction.
6. Release reviewed products in the app or through SQL. Choose **Add stock
   and resume** in the app to receive stock and reopen sales together.
   Use the availability function with `false` to pause a product.

   ```sql
   SELECT public.set_product_sale_availability(5005, true);
   ```

The migrations use a five-second lock timeout. A statement aborts if a lock cannot be acquired
in that time. If a migration fails, diagnose the error before retrying
that migration. Resume checkout only after verifying the intended sale path.

## Migration reference

| Migration | What changes | Why it runs in this order |
| --- | --- | --- |
| `001_stock_reconciliation.sql` | Adds the availability flag, pauses negative-stock products, initializes inventory log IDs, and adds functions to pause, resume, and record a correction. | The existing negative balances stay visible for the stocktake. Corrections can be recorded before enforcing the stock rule. |
| `002_safe_checkout.sql` | Validates the nonnegative constraint, initializes order and item IDs, links new sale movements to order items, and installs `place_order`. | The constraint requires the historical negatives to be resolved. Checkout becomes available when all its supporting changes commit. |
| `003_restock_paused_products.sql` | Adds `restock_paused_product`, which increments stock and records a positive movement in one transaction. | Uses the availability flag and inventory log ID default from migration 001. For a database already on migration 002, apply 003 by itself. |

### Checkout decisions

The stock update checks availability and quantity while claiming the product
row. PostgreSQL rechecks those conditions after a competing transaction
commits. The stock deduction, completed order, order item, and linked sale
movement commit together. Affected products can still receive restocks
while paused. The stock constraint applies to all stock writes.

New checkouts use the status `completed` and write UTC timestamps into the
legacy timestamp columns. The order uses the product price at checkout.
Historical movements retain their existing timestamps and have no inferred
order-item links.

## Rehearse from an empty test database

Run these Bash commands from the repository root. If the test database
already contains the tables, use it to inspect the rehearsal or rerun the
tests below. Start from a separate empty test instance for a fresh replay.

```bash
set -euo pipefail
uv run kk-test-db
test_psql() {
    uv run kk-test-db exec -T postgres sh -ec \
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}

# Wait until the server is ready. Abort if the test database contains tables.
table_count=$(test_psql -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public';")
if [ "$table_count" != 0 ]; then
    echo "Test database already contains tables. Seed skipped." >&2
    exit 1
fi

# Seed the empty test database.
tar -C provided_resources -cf - schema.sql load.sql data | \
    uv run kk-test-db exec -T postgres sh -ec '
        dir=$(mktemp -d)
        trap "rm -rf \"$dir\"" EXIT
        tar -xf - -C "$dir"
        cd "$dir"
        psql -X -1 -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
            -f schema.sql -f load.sql
    '

test_psql < scripts/migrations/001_stock_reconciliation.sql
test_psql < scripts/negative_stock.sql

# Test rehearsal only. These zero counts are simulated.
test_psql <<'SQL'
SELECT product_id,
       public.reconcile_negative_stock(
           product_id, stock_quantity, 0, 'Test rehearsal only; simulated count of zero'
       ) AS correction_log_id
FROM public.products
WHERE stock_quantity < 0
ORDER BY product_id;
SQL

test_psql < scripts/negative_stock.sql
test_psql < scripts/migrations/002_safe_checkout.sql
test_psql < scripts/migrations/003_restock_paused_products.sql
```

The first worklist should show 14 products. The second should be empty.
`negative_stock.sql` only reads and lists currently negative products with
their recorded counts and availability. It does not perform corrections.

## Run the checkout tests

Run both Python scripts from the repository root. The commands load the
test connection from `.env`.

```bash
uv sync
uv run kk-race-test
uv run kk-flash-sale-test
```

The supplied harness already has a flash-sale test. Its test 2 opens 400
separate connections and releases those buyers simultaneously for 50 units.
Run only that case with `uv run kk-race-test --test 2`. Give the test instance connection
headroom for that case and restore its previous
setting afterward. In the rehearsal we raised `max_connections` from 100
to 450 on the test server with `ALTER SYSTEM SET max_connections = 450`,
restarted the container, ran the harness, then used
`ALTER SYSTEM RESET max_connections` and restarted to restore the prior
default. Preserve any explicit previous setting when repeating this step.

`kk-flash-sale-test` uses 32 reusable connections and schedules about 40
attempts per second for ten seconds against one product. With 400 units,
every purchase should succeed, exercising all the checkout writes. With
50 units, it also measures how quickly the remaining 350 buyers are told
the product is sold out. It reports how many requests finished in the
ten-second window and how long buyers waited, verifies the stored orders
and stock movements, and removes its fixtures.
Run these fixture-writing tests on test. Local timings describe the test
environment and should be interpreted against the expected checkout setup.

## Rehearsal results

The supplied suite passed all four tests. Its simultaneous burst sold 50
units, refused 350 attempts, and reported zero errors in 1.67 seconds.

| Starting stock | Sold | Refused | Errors | Finished in 10 s | p95 response |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 400 | 400 | 0 | 0 | 400/400 | 5.1 ms |
| 50 | 50 | 350 | 0 | 400/400 | 3.6 ms |
