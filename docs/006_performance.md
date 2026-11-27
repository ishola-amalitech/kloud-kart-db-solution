# Make it fast, and prove it

| Benchmark | Before | After or comparison | Tested | Decision |
| --- | --- | --- | --- | --- |
| [Monday report baseline](#monday-report-baseline) | 2,655.217 ms | Baseline measurement | Yes | Reference timing |
| [Order-status statistics](#order-status-statistics) | 2,655.217 ms initially, 1,774.970 ms on repeat | 1,335.419 ms initially, 2,014.128 ms on repeat | Yes | Rolled back |
| [Monday report sort memory](#monday-report-sort-memory) | 1,150.375 ms at 4 MB | 1,216.976 ms at 64 MB | Yes | Kept 4 MB |
| [Inventory-history index](#inventory-history-index) | 9.551 ms without index | 0.133 ms with index | Yes | Kept existing index |
| [Low-stock views](#low-stock-views) | 2.285 ms rollup, 2.219 ms worklist, 1.017 ms branch | Existing views measured | Yes | Kept regular views |
| [Inventory discrepancy worklist](#inventory-discrepancy-worklist) | 201.191 ms | 54.375 ms | Yes | Kept rewrite, migration 006 |
| [Monday report cutoffs](#monday-report-cutoff-comparison) | 1,207.290 ms at broad cutoff | 1,092.960 ms at midpoint Monday | Yes | Kept cutoff rules |
| [Completed-orders index](#completed-orders-index) | 1,087.107 ms June, 1,489.523 ms August | 1,108.168 ms June, 1,420.682 ms August | Yes | Rolled back |
| [Materialized Monday report](#materialized-monday-report) | 1,070.458 ms direct read | 134.223 ms stored read, 1,220.029 ms refresh | Yes | Kept direct query |
| [Monday report during a flash sale](#monday-report-during-a-flash-sale) | 3.3–6.0 ms checkout p95 without report | 17.4–18.9 ms checkout p95 with report | Yes | All attempts finished within ten seconds in test |

Timings are medians unless shown as ranges or individual measurements.
Each benchmark below records its test conditions and the evidence available.

## Contents

- [Performance work already in place](#performance-work-already-in-place)
- [Monday report baseline](#monday-report-baseline)
- [Order-status statistics](#order-status-statistics)
- [Monday report sort memory](#monday-report-sort-memory)
- [Inventory-history index](#inventory-history-index)
- [Low-stock views](#low-stock-views)
- [Inventory discrepancy worklist](#inventory-discrepancy-worklist)
- [Monday report cutoff comparison](#monday-report-cutoff-comparison)
- [Completed-orders index](#completed-orders-index)
- [Materialized Monday report](#materialized-monday-report)
- [Monday report during a flash sale](#monday-report-during-a-flash-sale)
- [Rollout status](#rollout-status)

## Performance work already in place

- **Monday report aggregation.** The query scans orders once and uses
  `GROUPING SETS` to calculate customer totals and monthly buckets from
  that stream. Window functions calculate running spend and ranking from
  grouped data. [Report SQL](../scripts/monday_report.sql)
- **Low-stock rollup.** The recursive query groups reorder candidates by
  their assigned category before rolling the counts up to ancestors. This
  avoids repeating ancestor work for every product in the same category.
  [Low-stock view definitions](../scripts/migrations/004_low_stock_rollup.sql)
- **Inventory indexes.** Existing indexes support looking up movements by
  product and finding verified counts or order-item links. This stage
  measured the product-history index against the same query without it.
  [Inventory benchmark](#inventory-history-index) · [Index inventory](#indexes-on-the-test-database)

Migration 006 is the single new database change retained for the lab.
It groups movement rows by product before joining them to current stock.
[Worklist benchmark](#inventory-discrepancy-worklist)

[ADR-009](adr/006-adr-009-aggregate-inventory-before-joining.md) records
why we kept the rewrite and what would cause us to revisit it.

## Monday report baseline

A baseline tells us how long today's report takes. We can rerun it after one
change and see what that change bought Adwoa. The lab brief's roughly 40-minute
runtime gives the business context. These timings are for the current report
on the local test database.

### What we timed

On 2026-11-26, we ran the existing report from commit `6069418` on the seeded
Compose test database. PostgreSQL 18.7 had `work_mem` set to `4MB`. The database
had 40,420 customers and 159,384 orders. We used cutoff **2027-08-21** to cover
the broad seed workload. Each measurement ran five times in sequence, with no
cache reset or discarded runs.

We recorded PostgreSQL's `Execution Time` from `EXPLAIN ANALYZE`. This
measures how long the database took to run the query.

| Run | SQL execution (ms) |
| ---: | ---: |
| 1 | 3,596.512 |
| 2 | 2,655.217 |
| 3 | 2,649.671 |
| 4 | 2,543.545 |
| 5 | 3,361.372 |
| **Median** | **2,655.217** |

### What the plan shows

- PostgreSQL read all 159,384 orders once and kept 111,351 for this cutoff.
  A broad scan makes sense to test against because most orders qualify.
- PostgreSQL estimated 555 eligible rows at the customer join and got 111,351.
  It looked up a customer in the primary-key index for each qualifying order.
  With a more accurate estimate, PostgreSQL might choose to build a lookup
  table of customers in memory and reuse it for the join.
- The final sort handled 525,461 rows and used about 23 MiB of temporary disk
  space in run 3. This is a second candidate to investigate in its own
  experiment. [Execution plan from run 3](query_plans/006_monday_report_baseline_plan.txt)

### What the results must preserve

| Output | Baseline |
| --- | --- |
| Customer totals | 40,420 rows, 111,207 orders, 339,220,585.14 in recorded spend |
| Monthly history | 485,040 rows, 25,304 orders, 76,700,175.24 in recorded spend |
| Unmatched orders | 1 row, 144 orders, 388,780.89 in recorded spend |
| Top 100 | 100 customers |

Compare every SQL output column in both directions with `EXCEPT ALL`. This
catches changed amounts, changed ranks, missing rows, and duplicate rows.

### Reproduce the baseline

Run from the repository root with the seeded Compose test database running.

```bash
set -o pipefail
bench_dir=$(mktemp -d)

{
  printf '%s\n' 'BEGIN READ ONLY;' "SET LOCAL DateStyle = 'ISO, YMD';" \
    "SELECT set_config('kloud_kart.report_cutoff', '2027-08-21', true);"
  for run in 1 2 3 4 5; do
    printf '\\echo Run %s\nEXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)\n' "$run"
    cat scripts/monday_report.sql
  done
  printf '%s\n' 'ROLLBACK;'
} | docker compose exec -T postgres sh -ec \
  'exec psql -X -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$bench_dir/plans.txt"

rg '^(Run| Planning Time:| Execution Time:)' "$bench_dir/plans.txt"
```

## Order-status statistics

The report uses `lower(btrim(status))` to match completed orders. PostgreSQL
estimated far fewer matches than it found. We created one statistics object
for that expression and ran `ANALYZE` to fill it. Think of statistics as a
summary PostgreSQL uses to plan its work.

### Before and after

Times are PostgreSQL execution times in milliseconds. The first trial used
the [original report baseline](#monday-report-baseline). We repeated the comparison back-to-back in a single
session to see whether the improvement held.

| Run | First baseline | First trial with stats | Repeat without stats | Repeat with stats |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 3,596.512 | 1,443.206 | 1,669.328 | 1,683.363 |
| 2 | 2,655.217 | 1,348.561 | 1,340.673 | 1,724.002 |
| 3 | 2,649.671 | 1,335.419 | 1,774.970 | 2,143.238 |
| 4 | 2,543.545 | 1,307.493 | 1,905.205 | 2,075.486 |
| 5 | 3,361.372 | 1,320.008 | 1,800.480 | 2,014.128 |
| **Median** | **2,655.217** | **1,335.419** | **1,774.970** | **2,014.128** |

The stats improved the customer join estimate from **531 to 36,848** in
the repeat. The actual count was **111,351**. With the stats, PostgreSQL
built an in-memory lookup of customers once instead of probing the customer
index for every qualifying order. The final sort still used about 23 MiB of
temporary disk space. [Plan from the first trial with stats](query_plans/006_monday_report_status_statistics_plan.txt)

Both versions returned **525,461 rows**. We captured the SQL results in
temporary tables and compared them with `EXCEPT ALL` in both directions.
There were **zero missing or extra rows**.

**Decision:** Leave this change out for now. The first trial was about 50%
faster than the initial baseline. In the back-to-back repeat, the median
was about 13% slower with the stats. This shows a better estimate and a
different join, but the timings do not establish a repeatable speedup.
Both experiments ran on test and rolled back.

### Reproduce the timing comparison on test

Run this from the repository root against the seeded Compose test database.
The before runs use the current planner settings. The after runs add the
statistics object and collect its data inside the transaction. The rollback
removes the experiment.

```bash
set -o pipefail
bench_dir=$(mktemp -d)

{
  printf '%s\n' 'BEGIN;' "SET LOCAL DateStyle = 'ISO, YMD';" \
    "SELECT set_config('kloud_kart.report_cutoff', '2027-08-21', true);"
  for phase in before after; do
    if [ "$phase" = after ]; then
      printf '%s\n' \
        'CREATE STATISTICS public.kk_bench_orders_normalized_status ON (lower(btrim(status))) FROM public.orders;' \
        'ANALYZE public.orders;'
    fi
    for run in 1 2 3 4 5; do
      printf '\\echo %s %s\nEXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)\n' "$phase" "$run"
      cat scripts/monday_report.sql
    done
  done
  printf '%s\n' 'ROLLBACK;'
} | docker compose exec -T postgres sh -ec \
  'exec psql -X -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  > "$bench_dir/plans.txt"

rg '^(before|after| Execution Time:)' "$bench_dir/plans.txt"
```

For the result check, capture the original query with `CREATE TEMP TABLE
baseline_report AS` before creating the statistics. Capture it as
`candidate_report` after `ANALYZE`. Run this comparison before rollback.

```sql
SELECT
    (SELECT count(*) FROM (
        TABLE baseline_report EXCEPT ALL TABLE candidate_report
    ) AS missing) AS missing_rows,
    (SELECT count(*) FROM (
        TABLE candidate_report EXCEPT ALL TABLE baseline_report
    ) AS extra) AS extra_rows;
```

## Monday report sort memory

The report sorts 525,461 rows before returning them. At `4MB` of
`work_mem`, PostgreSQL writes sorted chunks to temporary files and merges
them. We tried `64MB` to see whether keeping those chunks in RAM saves time.
`SET LOCAL` applies the setting only inside the test transaction. PostgreSQL
can use that budget for each sort or similar operation, so it also can affect
other parts of the same query.

We ran five pairs in one session using the same cutoff and query. Odd pairs
ran `4MB` first, and even pairs ran `64MB` first. This gives each setting a
chance to run second, when some data may already be in memory.

### Reproduce on test

Use the [statistics benchmark command](#reproduce-the-timing-comparison-on-test) with a single transaction and the same cutoff.
Replace its before/after loop with the loop below. Keep the surrounding
`BEGIN`, `ROLLBACK`, and test-container command, and change the final
`rg` pattern to `^(Pair| Execution Time:|   Sort Method:)`.

```bash
for run in 1 2 3 4 5; do
  if (( run % 2 )); then
    settings=(4MB 64MB)
  else
    settings=(64MB 4MB)
  fi
  for memory in "${settings[@]}"; do
    printf '\\echo Pair %s %s\n' "$run" "$memory"
    printf "SET LOCAL work_mem = '%s';\n" "$memory"
    printf 'EXPLAIN (ANALYZE, BUFFERS, TIMING OFF, SUMMARY ON)\n'
    cat scripts/monday_report.sql
  done
done
```

Use the [two-way SQL comparison](#reproduce-the-timing-comparison-on-test) with a temp table from each memory setting.

### Results

| Pair | 4 MB (ms) | 64 MB (ms) |
| ---: | ---: | ---: |
| 1 | 1,150.375 | 1,165.452 |
| 2 | 1,134.636 | 1,203.062 |
| 3 | 1,175.837 | 1,310.489 |
| 4 | 1,155.603 | 1,239.473 |
| 5 | 1,128.576 | 1,216.976 |
| **Median** | **1,150.375** | **1,216.976** |

At `4MB`, the final sort used about **23 MiB** of temporary disk space
in all five runs. At `64MB`, it used about **48 MiB** of RAM and
the query plan reported no temporary block reads or writes in pair 3.
Both settings returned **525,461 rows** with zero missing or extra rows
in the SQL comparison. [Both plans from pair 3](query_plans/006_monday_report_work_mem_plans.txt)

**Decision:** Keep the existing setting. Although the report stopped
spilling to disk, each paired run took longer with more memory, and
the median rose by about **5.8%**. The test session returned to `4MB`
after rollback.

## Inventory-history index

Looking up recent stock movements for one product is a selective query.
Migration 005 already provides an index on `(product_id, log_id)`. PostgreSQL
can find the product in the first part of the index, then read the second
part backwards to pick out its newest movements. We measured the first page
of the same SQL lookup with and without that index.

The benchmark used product **8174**, which had the most movements on the test
database (**101**). It ran five pairs, alternating which version ran first.
Each removal used a savepoint so the index could be restored before the next
indexed run. All other indexes stayed in place.

| Pair | Without index (ms) | With index (ms) |
| ---: | ---: | ---: |
| 1 | 9.255 | 0.520 |
| 2 | 9.551 | 0.114 |
| 3 | 9.787 | 0.119 |
| 4 | 11.301 | 0.155 |
| 5 | 9.441 | 0.133 |
| **Median** | **9.551** | **0.133** |

Without the index, PostgreSQL scanned the movement table and sorted the
matching rows. With it, the plan read backwards through the index and took
the first 26 matches. Both versions returned exactly the same 26 rows.
The median dropped by **9.418 ms**, about **72×** faster for this lookup.
The index was confirmed present after rollback. This measures the read
benefit on test. Maintaining the index still adds work to movement writes.
**Decision** Keep the existing index.
[Execution plans from pair 3](query_plans/006_inventory_history_index_plans.txt)

### Reproduce on test

Connect to the seeded test database with the terminal command in the
[stock rollout](002_stock_rollout.md#open-the-seeded-test-database). Run this
in `psql`. For repeated pairs, rerun the two plans with their order
alternated, restoring the index after each unindexed run. Dropping the
index takes locks, so use the test database for this comparison.

```sql
BEGIN;
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT * FROM public.inventory_movement_history
WHERE product_id = 8174 ORDER BY log_id DESC LIMIT 26;

SAVEPOINT without_index;
DROP INDEX public.inventory_log_product_log_idx;

EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT * FROM public.inventory_movement_history
WHERE product_id = 8174 ORDER BY log_id DESC LIMIT 26;

ROLLBACK TO SAVEPOINT without_index;
ROLLBACK;
```

For result parity, capture the query into temporary tables before and after
the drop and use the two-way `EXCEPT ALL` check from the [statistics benchmark](#reproduce-the-timing-comparison-on-test) before rolling
back.

## Low-stock views

Migration 004 already supplied `stock_category_paths`,
`low_stock_worklist`, and `low_stock_category_rollup`. The benchmark measured these existing views. A materialized low-stock
view was outside the experiment. [Migration 004](../scripts/migrations/004_low_stock_rollup.sql)

A regular view saves the SQL. Each read walks the category tree and
calculates counts from current data. We measured the complete category
rollup, the worklist including exceptions, and a filtered worklist
for Electronics (category 101). Electronics was the root branch with
the most reorder candidates on this test snapshot.

| Run | Category rollup (ms) | Whole worklist (ms) | Electronics candidates (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 2.408 | 2.465 | 1.027 |
| 2 | 2.285 | 2.417 | 1.017 |
| 3 | 2.255 | 2.104 | 1.004 |
| 4 | 2.362 | 2.219 | 1.017 |
| 5 | 2.226 | 2.210 | 1.084 |
| **Median** | **2.285** | **2.219** | **1.017** |
| **Rows returned** | **91** | **1,713** | **514** |

**Decision** Keep the regular views. The test queries all finished in
under 2.5 ms. A materialized view would need a refresh policy and could
show an old reorder list after deliveries or sales. Revisit if production
measurements show that the query time affects the reorder workflow.
[Execution plans from run 3](query_plans/006_low_stock_view_plans.txt)

### Reproduce on test

Run each query five times in the same test session. Use a read-only
`REPEATABLE READ` transaction so every run sees the same data.

```sql
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;

EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT * FROM public.low_stock_category_rollup
ORDER BY category_path, category_id;

EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT * FROM public.low_stock_worklist
ORDER BY stock_quantity NULLS LAST, product_id;

EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT * FROM public.low_stock_worklist
WHERE 101 = ANY (ancestor_ids) AND hierarchy_issue IS NULL AND needs_reorder
ORDER BY stock_quantity NULLS LAST, product_id;

ROLLBACK;
```

### Indexes on the test database

The `public` schema had nine B-tree indexes at the time of measurement.
Six back primary keys. The other three support inventory lookups.

| Table | Index | Columns and filter |
| --- | --- | --- |
| categories | `categories_pkey` | `category_id` |
| customers | `customers_pkey` | `customer_id` |
| inventory_log | `inventory_log_pkey` | `log_id` |
| inventory_log | `inventory_log_order_item_idx` | `order_item_id` where it is present |
| inventory_log | `inventory_log_product_log_idx` | `product_id, log_id` |
| inventory_log | `inventory_log_verified_count_idx` | `product_id, log_id DESC` for `stock_take` rows with a recorded `stock_after` |
| order_items | `order_items_pkey` | `order_item_id` |
| orders | `orders_pkey` | `order_id` |
| products | `products_pkey` | `product_id` |

```sql
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;
```

## Inventory discrepancy worklist

The discrepancy worklist compares each product's stock balance with its
movement history. The original plan joined almost every movement to its
product, then added up the changes. That meant passing about 282,000 rows
through the join and sort. The revised query adds up movements first, so
the final join to products works with about 4,000 movement totals.

The first five baseline times were **259.229, 190.067, 208.118, 199.193,
and 200.870 ms**. The worklist showed 142 products to review, with 18
flagged for possible oversell and 36 whose low-stock classification differed.

### Before and after

We alternated which query ran first in each pair on the same test snapshot.

| Pair | Original (ms) | Sum before joining (ms) |
| ---: | ---: | ---: |
| 1 | 205.538 | 52.809 |
| 2 | 200.293 | 54.375 |
| 3 | 201.191 | 54.954 |
| 4 | 213.494 | 56.853 |
| 5 | 195.815 | 52.932 |
| **Median** | **201.191** | **54.375** |

The median fell **73%**. The new plan scanned the movement log and used
an in-memory hash table to group it by product. It still calculates
from current data. [Execution plans from pair 3](query_plans/006_inventory_discrepancy_plans.txt)

**Decision** Keep the rewrite. On test, we applied migration 006 and
verified all columns across all 142 rows with a two-way `EXCEPT ALL`
comparison before committing. The inventory-trail verification suite
passed, including verified counts, later movements, and role permissions.
The migration preserves the existing view's columns and grants.
[Migration 006](../scripts/migrations/006_optimize_inventory_discrepancy_worklist.sql)

### Reproduce and roll out

On a seeded test database with migration 005 applied, run this query
five times before migration 006, then repeat it on the same data after
migration 006. Save each plan. For a paired test, use the original and
revised definitions side by side in one test session. Compare their
results in both directions using the `EXCEPT ALL` check from the [statistics benchmark](#reproduce-the-timing-comparison-on-test).

```sql
EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)
SELECT product_id, product_name, stock_quantity, is_available, verified_count_id,
       logged_net, comparison_stock, possible_oversell, low_stock_differs
FROM public.inventory_discrepancy_worklist
ORDER BY possible_oversell DESC, low_stock_differs DESC, product_id;
```

After production approval, apply migration 006 during a quiet period.
It replaces one view and aborts if it cannot acquire the lock in five
seconds. Verify the worklist can be read by the reporting and operator
roles. To revert, replace just this view with its definition from
[migration 005](../scripts/migrations/005_inventory_trail.sql), using
`CREATE OR REPLACE VIEW` in a transaction.

## Monday report cutoff comparison

The cutoff is the date before which orders qualify. For a second workload
we found the median order date among dated orders with a completed status,
then used the Monday of that week, **2026-06-08**. This models the midpoint
of seeded history. The production cutoff mix remains to be measured.

| Cutoff | Qualifying orders | Share of 159,384 order rows | Report rows |
| --- | ---: | ---: | ---: |
| 2026-06-08 | 54,975 | 34.5% | 525,461 |
| 2027-08-21 | 111,351 | 69.9% | 525,461 |

We ran five pairs in a single read-only test snapshot, alternating which
cutoff went first. The SQL and database settings stayed the same.

| Pair | Midpoint Monday (ms) | Broad cutoff (ms) |
| ---: | ---: | ---: |
| 1 | 1,069.515 | 1,207.290 |
| 2 | 1,034.552 | 1,187.835 |
| 3 | 1,092.960 | 1,180.378 |
| 4 | 1,146.267 | 1,259.936 |
| 5 | 1,231.503 | 1,240.962 |
| **Median** | **1,092.960** | **1,207.290** |

The earlier cutoff roughly halved qualifying orders, while median runtime
fell about 9.5%. Both plans scanned the orders table and sorted the same
number of report rows. The report generates one summary and 12 monthly
rows per customer, including months with no orders.
[Plans from pair 3](query_plans/006_monday_report_cutoff_plans.txt)

Both cutoffs qualify a substantial share of the table. An index could make
qualifying orders cheaper to find, while leaving the fixed customer-month
output work in place. The follow-up below measures that trade-off.

### Reproduce the cutoff comparison

Use the [baseline command](#reproduce-the-baseline) inside a read-only `REPEATABLE READ` transaction.
Before each execution plan, set the cutoff with
`SELECT set_config('kloud_kart.report_cutoff', '2026-06-08', true);`
or the same statement with `2027-08-21`. Run each cutoff five times and
alternate which goes first in each pair.

## Completed-orders index

We tested a partial B-tree index so PostgreSQL could look up orders by
date within the completed orders only. The index also carries the customer
ID and recorded amount, so the planner can read those fields from the index.
The index occupied **4,424 kB** on the seeded test database.

```sql
CREATE INDEX kk_bench_completed_order_date_idx ON public.orders (order_date)
INCLUDE (customer_id, total_amount)
WHERE lower(btrim(status)) IN ('complete', 'completed');
```

We ran five pairs at each cutoff on the same test snapshot, alternating
which version went first. The outer transaction created the index. For
each baseline run we dropped it under a savepoint and rolled back to
that savepoint afterward. The outer rollback removed the test index.

| Pair | June without (ms) | June with (ms) | August without (ms) | August with (ms) |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1,152.610 | 1,108.168 | 1,538.124 | 1,558.895 |
| 2 | 1,143.909 | 1,115.906 | 1,489.523 | 1,470.377 |
| 3 | 1,053.134 | 1,166.539 | 1,303.418 | 1,326.220 |
| 4 | 1,087.107 | 1,095.751 | 1,421.059 | 1,290.290 |
| 5 | 1,040.129 | 993.133 | 1,573.289 | 1,420.682 |
| **Median** | **1,087.107** | **1,108.168** | **1,489.523** | **1,420.682** |

PostgreSQL switched from scanning the orders table to an index-only scan
at both cutoffs. It still had to build and sort 525,461 result rows.
A two-way `EXCEPT ALL` comparison found zero missing or extra rows at
either cutoff. [Plans from pair 3](query_plans/006_monday_report_orders_index_plans.txt)

**Decision** Leave the index out. The June median rose about 2% and the
August median fell about 5%. Both cutoffs had a mix of faster and slower
paired runs. That result gives little reason to add storage and index
maintenance to qualifying order writes. Rollback removed the index from
test. There was no production change.

To reproduce, use the [cutoff comparison commands](#reproduce-the-cutoff-comparison) and create the index inside
a test transaction. For each unindexed run, execute `SAVEPOINT no_index`,
drop the benchmark index, run the plan, and execute
`ROLLBACK TO SAVEPOINT no_index` followed by `RELEASE SAVEPOINT no_index`.
For each indexed run, execute the plan with the index present. Roll back
the outer transaction at the end.

## Materialized Monday report

A regular view runs its query when asked. A materialized view stores the
result rows when refreshed. Later reads can reuse those rows, but changes to
the orders appear only after the next refresh. We tested a saved copy of the
report for **2026-06-08**. Cached reads used the same ordering as the report.

On one seeded test snapshot, we refreshed the stored copy five times,
then alternated the order of direct and cached reads across five pairs.
The refresh column records the separate refresh series. Refresh times are
elapsed `psql` statement times. The read columns use PostgreSQL execution
times from `EXPLAIN ANALYZE`.

| Run | Refresh (ms) | Direct query (ms) | Cached read (ms) |
| ---: | ---: | ---: | ---: |
| 1 | 1,205.690 | 1,122.358 | 147.835 |
| 2 | 1,162.470 | 1,070.458 | 134.223 |
| 3 | 1,220.029 | 1,031.300 | 122.494 |
| 4 | 1,231.180 | 1,036.594 | 120.890 |
| 5 | 1,453.894 | 1,074.734 | 146.738 |
| **Median** | **1,220.029** | **1,070.458** | **134.223** |

The stored copy occupied **33 MB**. All **525,461 rows** matched in the
two-way SQL comparison. Cached reads were about **8× faster**, since they
read and sorted stored rows without rebuilding the report. One refresh
plus one cached read took about **1.35 seconds** using the medians.
[Plans from pair 3](query_plans/006_monday_report_materialized_view_plans.txt)

**Decision** Keep the direct report. A cached copy makes sense if people
repeatedly request the same cutoff and can work with results as of the last
refresh. We have yet to measure that usage. A standard refresh can block
readers, and the refresh must set the intended cutoff explicitly. Rollback
removed the test view.

### Reproduce on test

Start a writable `REPEATABLE READ` transaction and set
`kloud_kart.report_cutoff` to `2026-06-08` as in the [cutoff comparison](#reproduce-the-cutoff-comparison). Prefix the
contents of `scripts/monday_report.sql` with
`CREATE MATERIALIZED VIEW public.kk_bench_monday_report AS` and replace
the query's final semicolon with ` WITH NO DATA;`. In the same session:

1. Enable `\timing` and run
   `REFRESH MATERIALIZED VIEW public.kk_bench_monday_report;` five times.
2. Run five alternating pairs of the direct report and the query below
   with `EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)`.
3. Capture the direct result in a temporary table and compare it with the
   materialized view using the [two-way `EXCEPT ALL` check](#reproduce-the-timing-comparison-on-test). Roll back.

```sql
SELECT *
FROM public.kk_bench_monday_report
ORDER BY customer_id NULLS LAST, month_start NULLS FIRST;
```

## Monday report during a flash sale

All 400 attempts finished within ten seconds in every run, with zero
checkout errors. The 400-unit scenario sold 400 units. The 50-unit
scenario sold 50 and refused 350. Running the report raised checkout
p95, the time within which 95% of buyers heard back, in all four
comparisons.

| Pair | Starting stock | Checkout p95 alone | Checkout p95 with report | Report alone | Concurrent report |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 400 | 6.0 ms | 18.9 ms | 5.686 s | 4.756 s |
| 1 | 50 | 3.3 ms | 18.6 ms | 5.686 s | 4.134 s |
| 2 | 400 | 5.9 ms | 17.4 ms | 4.246 s | 2.802 s |
| 2 | 50 | 4.1 ms | 17.5 ms | 4.246 s | 3.000 s |

These two local pairs show sufficient throughput for the simulated sale.
The report timings varied between runs. The checkout latency increase
makes it worth scheduling routine exports outside the first live sale,
then checking response times on the live setup before relying on overlap.

On 27 November 2026, a separate PostgreSQL 18 test instance used the
supplied data and migrations 001–006. Each comparison ran the report
alone, both paced checkout scenarios alone, and the same checkout
scenarios with one report started one second into each sale. The paced
test scheduled 400 attempts over ten seconds using 32 reusable
connections. The report ran with cutoff `2027-08-21` and generated both
CSVs in memory. Checkout latency was measured from each buyer's
scheduled arrival, including time spent waiting for a worker.

The timed report used the same loader as the app. The report started
while purchases were being committed, so the result included the fixture
orders visible in its snapshot. We checked that the extra recorded spend
matched the count of visible fixture purchases at 100.00 each and that
other customers' counts and spending matched the report-only run. Each
concurrent report saw 41 fixture purchases and finished while checkout
was still accepting attempts. The paced test
also independently verified the final stock, orders, lines, and
inventory movements.

Repeat both pairs with the [one-command rehearsal](../README.md#reproduce-the-lab-with-one-command).
Read `environment.txt` for the source revision and runtime configuration
and `workloads.log` for the checkout timings, visible fixture sales,
and actual start and end of each report on the sale clock. The command
uses the later of `2027-08-21` and tomorrow in UTC as the report cutoff,
so checkout purchases are eligible even when the rehearsal is repeated
after the seed's last order date.

## Rollout status

Migration 006 is running on the test database. The inventory-trail and role
checks passed. The change affects the worklist read query, so the planned
checkout comparison for changes to writes was not triggered. Production
rollout awaits approval. [Application and verification](#reproduce-and-roll-out)
