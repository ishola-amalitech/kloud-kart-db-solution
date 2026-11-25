# Low-stock category rollup

Nana can see where stock is running low, drill into a category, and give
purchasing a list of products to review. The report also shows paused products
and catalog problems that could otherwise hide items from a category rollup.

## Contents

- [Use the app](#use-the-app)
- [Stock and category rules](#stock-and-category-rules)
- [How the views work](#how-the-views-work)
- [Install and verify](#install-and-verify)
- [Seeded test results](#seeded-test-results)

## Use the app

Run `uv run kk-app` from the repository root. Open **Low stock** and select
**Load low-stock report**. The app uses the configured `DATABASE_URL` and
requires migration 004. [Connection settings](stock_rollout.md#connection-settings)

| Task | How to do it |
| --- | --- |
| Decide where to focus | Start with the root categories ranked by **Whole branch**. Select a **Category branch** to see the chosen category and its immediate children. Counts under **Whole branch** include every deeper level. |
| Prioritize empty shelves | Choose **Reorder candidates** and turn on **Out of stock only**. The worklist is ordered by recorded stock, then by product ID. |
| Investigate a specific product | Search by name or exact product ID. Clear a selected branch to search store-wide. |
| Route exceptions | Choose **Exceptions** and filter by paused products, stock issues, or category issues. Use **Stock reconciliation** for paused balances and send category issues for catalog review. |
| Share a list | Use **Download filtered worklist CSV**. It includes every matching product across all pages. |

The headline metrics cover the whole store. The category selection narrows the
reorder worklist to that branch. Exceptions are store-wide because products
with broken category paths may have no valid branch. Choose **Refresh low-stock
report** before acting on an older snapshot, especially during a sale.

## Stock and category rules

| Rule | Meaning |
| --- | --- |
| Reorder candidate | Available for sale with **0–5 recorded units**, inclusive. Five is the working threshold from the [diagnosis](data_integrity_diagnosis.md). Treat it as a review threshold. Order quantities depend on demand, lead times, and incoming stock. |
| Out of stock on sale | A reorder candidate with zero recorded units. |
| Pause or stock issue | Paused products go to Exceptions, including balances provisionally set to zero during reconciliation. Unknown and negative balances also go to Exceptions for review. |
| Category issue | Missing assignment, missing category, missing parent, or a cycle. An otherwise eligible product still counts in the store-wide reorder total and appears in Exceptions. It receives no branch count until its category path is valid. |
| Direct and whole branch | **Direct** counts products assigned to that category. **Whole branch** also includes products in all descendants. Only add branch counts across distinct roots, then add reorder candidates with category issues for the store-wide total. |

## How the views work

1. **`stock_category_paths` walks toward the root.** The recursive CTE starts
   one walk per category. Each step follows `parent_id` and saves the visited
   category IDs. A NULL parent completes a valid path. A missing parent or
   repeated ID ends the walk and returns a category issue. Starting at every
   category also exposes cycles that a root-first walk would miss.
2. **`low_stock_worklist` classifies products.** A left join keeps products
   whose category is absent. The view returns a single row per product that
   qualifies for reordering or review, with the two reasons tracked
   independently.
3. **`low_stock_category_rollup` counts and distributes.** It groups eligible
   products by their valid assigned category. `unnest` expands that category's
   path to one row per ancestor, including itself. Adding the direct counts at
   each ancestor makes a product count exactly once per branch. Valid categories
   with no candidates show zero.

The app reads the two reporting views in one short, read-only
`REPEATABLE READ` transaction. Counts and product detail share a snapshot
even if checkout updates stock between the queries. The transaction ends
before the user interacts with filters. The PostgreSQL views recalculate on
read, so they need no background refresh job.

## Install and verify

From the repository root, start the seeded test database with
`uv run kk-test-db`. For a fresh database, follow the
[seed and migration sequence](stock_rollout.md#rehearse-from-an-empty-test-database)
first. Install [migration 004](../scripts/migrations/004_low_stock_rollup.sql)
once per database after migration 001 has added `is_available`.

```bash
uv run kk-test-db exec -T postgres sh -ec \
    'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < scripts/migrations/004_low_stock_rollup.sql

uv run kk-test-db exec -T postgres sh -ec \
    'exec psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    < tests/verify_low_stock_rollup.sql
```

The [verification script](../tests/verify_low_stock_rollup.sql) compares the
views with the seeded product counts and rolls back all synthetic fixtures.
It checks a 12-level branch, siblings, category name reuse, cycles, missing
links, zero stock, the threshold boundary, paused products, and unknown stock.
Run those fixtures on the test database.

During an authorized production rollout, run migration 004 with the deployment
role. It creates the views atomically and changes no product or category rows.
Grant the reporting role access to the two views the app queries and verify
access using that role. The app's other tabs have their own existing permissions.

```sql
-- Replace reporting_role with the app's actual role.
GRANT SELECT ON public.low_stock_category_rollup, public.low_stock_worklist TO reporting_role;
```

To reproduce the timing, run the following in `psql` against the test database.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM public.low_stock_category_rollup ORDER BY category_path, category_id;

EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM public.low_stock_worklist ORDER BY stock_quantity NULLS LAST, product_id;
```

## Seeded test results

| Measure | Result |
| --- | ---: |
| Reorder candidates | 1,671 |
| In valid root branches | 1,656 |
| Candidates with category issues | 15 |
| Out of stock and still on sale | 577 |
| Products to review | 57 |
| Largest branch, Electronics | 515 |

The 15 candidates outside valid branches include 12 uncategorized products and
3 under a cycle. The 57 exceptions in this snapshot comprise 43 products with
category issues and 14 paused products. PostgreSQL recorded execution times of
about **3.4 ms** for the category rollup and **4.7 ms** for the worklist.
The app's data load, including the connection and both reads, took about
**26 ms** in a local rehearsal. These timings are for the seeded test database.
