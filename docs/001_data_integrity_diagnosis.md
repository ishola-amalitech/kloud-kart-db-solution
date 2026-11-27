# Kloud Kart data integrity diagnosis

| Priority | What changes in the workflow |
|---|---|
| [**1 · Act first**](#rank-1--act-first) | Count **14 products** before selling, surface **15 missed reorder candidates** at the test threshold, and fix the spending report’s status rule and runtime. |
| [**2 · Fix next**](#rank-2--fix-next) | Reconcile stock and orders, keep unmatched spending visible, and review **19 active email groups**. |
| [**3 · Investigate**](#rank-3--investigate) | Recover missing order context and trace sale movements. Confirm whether repeated lines and time zones affect a decision. |
| [**4 · Record only**](#rank-4--record-only) | Keep safeguards for valid exceptions and checks with zero observed violations. |

## Table of contents

- [Business cost estimates](#business-cost-estimates)
- [Ranked impact and proposed fixes](#ranked-impact-and-proposed-fixes)
- [What the audit covered](#what-the-audit-covered)
- [Missing foreign keys](#missing-foreign-keys)
- [Missing values and uniqueness](#missing-values-and-uniqueness)
  - [Missing fields](#missing-fields)
  - [Duplicate values](#duplicate-values)
- [Missing CHECK constraints and cross-row validation](#missing-check-constraints-and-cross-row-validation)
  - [Stock and inventory movements](#stock-and-inventory-movements)
  - [Quantities, prices, and order totals](#quantities-prices-and-order-totals)
  - [Category hierarchy](#category-hierarchy)
  - [Timestamp comparisons](#timestamp-comparisons)
- [Order statuses and inventory reasons](#order-statuses-and-inventory-reasons)
  - [Order statuses](#order-statuses)
  - [Inventory reasons](#inventory-reasons)
- [Overselling, timestamps, and indexes](#overselling-timestamps-and-indexes)
- [Reproduce the audit](#reproduce-the-audit)

## Business cost estimates

| Priority | Finding | Wasted labour or review effort | Direct loss | Risk-weighted exposure |
|---|---|---|---|---|
| 1 | [Negative stock and overselling](#overselling-timestamps-and-indexes) | **20–40 staff-minutes** to handle the two reported oversells, assuming 10–20 minutes each for a refund and a customer message. **70–140 staff-minutes** to count the 14 flagged products, assuming 5–10 minutes each for a count and record. | | |
| 1 | [Status spellings](#order-statuses) | | | |
| 1 | [Category rollup omissions](#category-hierarchy) | | | |
| 1 | [Slow customer-spending report](#overselling-timestamps-and-indexes) | | | |
| 2 | [Stock differs from the movement ledger](#stock-and-inventory-movements) | **2.5–5 staff-hours** for first counts of the 30 products whose reorder flag changes, using the same 5–10 minutes per product. Remove any products already counted for negative stock when budgeting. | | |
| 2 | [Orders point to missing customers](#missing-foreign-keys) | | | |
| 2 | [Stored totals and line sums differ](#quantities-prices-and-order-totals) | | | |
| 2 | [Missing line prices](#missing-fields) | | | |
| 2 | [Duplicate customer emails](#duplicate-values) | | | |
| 3 | [Items point to missing orders](#missing-foreign-keys) | | | |
| 3 | [Missing product and purchase links in the movement log](#inventory-reasons) | | | |
| 3 | [Repeated order/product lines](#duplicate-values) | | | |
| 3 | [Missing time-zone information](#overselling-timestamps-and-indexes) | | | |
| 4 | [Zero-change stock takes and reason codes](#inventory-reasons) | | | |
| 4 | [NULL root parents and optional inventory notes](#missing-fields) | | | |
| 4 | [Checks with zero violations](#missing-check-constraints-and-cross-row-validation) | | | |

Blank cells mean the available evidence cannot support an estimate. The filled cells are planning estimates. They assume routine handling of one refund and one message per incident, and a single employee counting and recording readily accessible stock. They cover those tasks only. Use the staff time as the budget until an hourly rate or time sample is available.

- **Wasted labour.** Multiply the number of occurrences by active staff time, then by the full hourly employment cost if available. At 40 minutes each for 52 weekly runs, the old Monday report would take **34 hours 40 minutes of elapsed run time per year** if every run finished in 40 minutes. The brief says some runs do not finish and Adwoa tracks running totals by hand. There is no measured staff time for either task. [Lab brief](../lab_brief.md)
- **Direct loss.** The brief records **two oversells and two refunds in one month**. The refund amounts, retained fees, and lost profit are unavailable. A refund amount would show cash returned to a customer. Establishing financial loss requires knowing what charges or profit the business actually lost. Differences in recorded spend and order totals below measure reporting impact. [Lab brief](../lab_brief.md)
- **Risk-weighted exposure.** Multiply the cost per incident by its expected frequency over a stated period. The brief gives a recent incident count and an expected flash-sale load. It gives no reliable annual incident rate, margin per lost sale, or cost per missed reorder. The test data can show which products or reported figures are exposed.

## Ranked impact and proposed fixes

The rank reflects which decisions need protection first. Before the flash sale, protect checkout and count stock that may be overstated. Protect the Monday spending figures and reorder list next. Use the measured effect on each workflow to prioritize reviews while the annual financial ranking remains open. [Lab brief](../lab_brief.md)

**Measurement assumptions.** The report scenario covers **21 August 2026 inclusive to 21 August 2027 exclusive**, the trailing 12 months through the extract’s last order date. For comparison, it treats trimmed, lowercased `complete` and `completed` as completed and uses stored order totals. That is a proposed reporting rule. Decide explicitly how to handle shipped, cancelled, and refunded orders before publishing. The reorder scenario uses **5 units or fewer** and traverses categories from valid roots. Monetary values are in the extract’s recorded units. Scenario differences measure what the chosen rule would change. Actual refunds, lost sales, and staff effort need separate evidence.

### Rank 1 · Act first

| Finding | **Operational impact** | **Proposed fix and trade-off** |
|---|---|---|
| [Negative stock and overselling](#overselling-timestamps-and-indexes) | **14 products to count**, with balances a combined **27 units below zero**. The brief reports two oversells last month. The extract cannot link them to these balances or establish refund costs. | Count affected stock and pause those products until verified. Guard checkout with a conditional stock decrement and write the order, line, and movement in the same transaction. Add a nonnegative constraint after reconciling existing balances. Run the supplied race test before the flash sale. Pausing products temporarily reduces availability. |
| [Status spellings](#order-statuses) | For known customers, exact `completed` matching drops **6,888 orders** and **21,243,400.85** in stored totals relative to the chosen completion rule. Switching to that rule changes **82 of the 100 customers** on the audit window’s spending list. | Map raw statuses to an approved vocabulary and enforce it on writes. Preserve raw values while validating the mapping. An incorrect mapping could change who Nana contacts. |
| [Category rollup omissions](#category-hierarchy) | A root-based rollup misses **43 products**: **37 uncategorized** and **6 under the cycle**. Of these, **15** qualify as low stock at the test threshold. The broken parent link affects **0 products** in this traversal. | Show unreachable and uncategorized products in a reorder exception list now. Confirm their categories and the intended parent links, then add the category foreign keys and cycle guard. Keep an explicit uncategorized bucket if classification is optional. A guessed category would move stock into the wrong purchasing group. |
| [Slow customer-spending report](#overselling-timestamps-and-indexes) | About **40 minutes** per run, with some Mondays unfinished, according to the brief. Nana sometimes decides without it. The time attributable to missing indexes is unknown. | Aggregate the spending and counts before computing running totals and ranks. Test candidate indexes and benchmark elapsed time and completion on the same workload using the existing PostgreSQL server. Check the figures match the agreed reporting rules. Indexes add storage and write overhead. |

### Rank 2 · Fix next

| Finding | **Operational impact** | **Proposed fix and trade-off** |
|---|---|---|
| [Stock differs from the movement ledger](#stock-and-inventory-movements) | **144 products** differ. Comparing recorded stock with net movements changes the low-stock flag for **30 products** at the test threshold. Physical counts and opening balances are needed to tell which list is right. | Recount those 30 first, reconcile against opening balances and history, and post documented corrections. Update balances and movements together going forward. Replacing stock with an incomplete ledger could introduce new errors. |
| [Orders point to missing customers](#missing-foreign-keys) | The customer join excludes **13 orders worth 5,554.26** under the proposed report rule. Adwoa cannot attribute that spend to a customer. | Show unmatched spend separately, recover verified customer links, then enforce the foreign key. Guessing a customer could change spend and rank for the wrong person. |
| [Stored totals and line sums differ](#quantities-prices-and-order-totals) | **114 fully priced orders** differ in the report window, by **81,617.63** in summed absolute differences. Taxes, discounts, and shipping can account for differences. The amount needing correction is unknown. | Reconcile against receipts and documented adjustments. Store the inputs needed to explain or recompute totals. Replacing totals with raw line sums could erase valid adjustments. |
| [Missing line prices](#missing-fields) | **6 eligible orders** cannot be fully priced from their lines. Their recorded totals sum to **9,875.04**. The wider extract has **148 missing line prices** across **147 linked orders**. | Recover the price at the time of sale from receipts or backups, flag unresolved orders, then require a price on new lines. Using today’s catalog price would rewrite historical spend. |
| [Duplicate customer emails](#duplicate-values) | Treating matching normalized emails as one person would reduce the modeled purchaser count from **7,730 to 7,711**. The **19 groups** with multiple purchasing IDs warrant review. Top-100 membership did **not** change in that scenario. | Confirm identities before merging and preserve the links to their orders. Enforce normalized uniqueness only if shared addresses are disallowed. An automatic merge could combine different people’s histories. |

### Rank 3 · Investigate

| Finding | **Operational impact** | **Proposed fix and trade-off** |
|---|---|---|
| [Items point to missing orders](#missing-foreign-keys) | **692 lines** carry **826,327.80** in priced line value. Missing order headers leave their date, customer, and status unknown, so their effect on the spend report cannot be bounded from the extract. | Recover verified order headers or retain unresolved lines in a review queue. Enforce the order foreign key on new writes and validate historic data after repair. Deleting lines would remove evidence. |
| [Missing product and purchase links in the movement log](#inventory-reasons) | **120 logs** record **120 units of outward movement** against missing product records. Separately, **262,088 sale movements** lack an enforced purchase link. Investigation time and the number of sales that cannot be traced by other evidence are unknown. | Recover product links where possible. Link future sale movements to the order item in the checkout transaction and enforce product consistency. Time a sample of sale or refund investigations. Leave uncertain historical purchase links unresolved. |
| [Repeated order/product lines](#duplicate-values) | **32 repeated pairs** appear in the report scenario. The data does not establish a duplicate charge or lost spend. | Compare lines with receipts or fulfillment records. Enforce one line per order/product only if split lines are invalid. A blanket merge could erase valid pricing or fulfillment differences. |
| [Missing time-zone information](#overselling-timestamps-and-indexes) | **4 timestamp columns** store local clock values without offsets. The number of orders that would move to another reporting month is unknown. | Declare a report time zone and test boundary orders under plausible source time zones. Store future event times with explicit offsets. Backfill only when the original zone can be established. |

### Rank 4 · Record only

| Finding | **Operational impact** | **Proposed fix or safeguard** |
|---|---|---|
| [Zero-change stock takes and reason codes](#inventory-reasons) | **90 stock takes** change the balance by **0 units**. The reason profile shows no spelling variants or sign exceptions against the observed rules. | Preserve zero movements for stock takes. Enforce the agreed reason vocabulary and sign rules on new writes. |
| [NULL root parents and optional inventory notes](#missing-fields) | **7 root categories** legitimately have no parent. **281,927 NULL notes** have no demonstrated workflow cost without a rule requiring notes for a movement reason. | Keep root parents and notes optional. Require notes only for reasons with an explicit audit requirement. |
| [Checks with zero violations](#missing-check-constraints-and-cross-row-validation) | No measured impact from broken product-to-category or item-to-product references, blank required-text candidates, duplicate sibling names, self-parent links, nonpositive quantities, invalid numeric values, empty orders, missing stock reconciliation inputs, or reversed dates among matched records. | Keep the proposed foreign keys, required-field checks, and quantity or value constraints in the detailed findings. Apply uniqueness, chronology, and order-completeness rules where the workflow calls for them. These checks need no historical cleanup based on this extract. |

**Avoid double-counting.** Count each physical count, order review, or refund once even if several findings flag it. Keep past losses and future exposure separate. Use line counts, status variants, and subgroup totals to explain why each record was flagged.

## What the audit covered

All **822,832 rows** loaded in isolated PostgreSQL 18.7 with primary keys enforced. All six counts matched the source CSVs. The [baseline schema](../provided_resources/schema.sql) has no foreign keys, CHECKs, or standalone UNIQUE constraints. Every column outside the primary keys allows NULL.

| Table | Entity | Primary key | Rows |
|---|---|---|---:|
| `categories` | Product classification and hierarchy | `category_id` | 94 |
| `products` | Catalog items and stock balances | `product_id` | 4,023 |
| `customers` | Customer records | `customer_id` | 40,420 |
| `orders` | Order headers | `order_id` | 159,384 |
| `order_items` | Products and prices on orders | `order_item_id` | 336,559 |
| `inventory_log` | Stock movements | `log_id` | 282,352 |

## Missing foreign keys

**1,023 rows** point to missing records across the six relationships checked.

- **Detection.** Left-joined each non-NULL reference to the target primary key and counted missing matches.
- **Prevention.** Add the foreign keys below. Use `NOT NULL` for mandatory references.

| Source → target | Rows | Missing keys | Example row → missing key |
|---|---:|---:|---|
| `categories.parent_id → categories.category_id` | 1 | 1 | Category **9003 → 8999** |
| `products.category_id → categories.category_id` | 0 | 0 | — |
| `orders.customer_id → customers.customer_id` | 210 | 190 | Order **502184 → 999953** |
| `order_items.order_id → orders.order_id` | 692 | 689 | Item **1235330 → 982056** |
| `order_items.product_id → products.product_id` | 0 | 0 | — |
| `inventory_log.product_id → products.product_id` | 120 | 111 | Log **982278 → 99958** |

## Missing values and uniqueness

### Missing fields

**Detection.** Counted NULLs by table and column, with row IDs in the export. NULL references were counted separately from broken foreign keys.

| Field | Evidence | **Prevention** |
|---|---|---|
| `order_items.unit_price` | **148 NULLs**, including item **903787** | Require `NOT NULL`. |
| `products.category_id` | **37 NULLs**, including product **5437** | Require `NOT NULL` if every product needs a category. |
| `categories.parent_id` | **7 NULLs**, including root **101** | Allow NULL for root categories. |
| `inventory_log.note` | **281,927 NULLs**, including log **700001** | Keep optional. If a reason requires a note, use a reason-dependent `CHECK` requiring a non-NULL, nonblank note. |

**Blank text.** All other columns had **0 NULLs**. Category and product names, customer name and email, order status, and inventory reason had **0 empty or whitespace-only values**.

- **Detection.** Classified NULL, empty, and whitespace-only values separately. Stray whitespace around nonblank values appears in the status profile.
- **Prevention.** For mandatory text, combine `NOT NULL` with `CHECK (column_name !~ '^\s*$')`.

### Duplicate values

| Finding | Evidence | **Detection** | **Prevention** |
|---|---|---|---|
| Exact email | **299 groups / 598 customers**, including **100014** | Grouped nonblank emails by stored value, kept `HAVING count(*) > 1`, then joined back to source rows. | Add `UNIQUE (email)` if shared addresses are disallowed. |
| Normalized email | **533 groups / 1,068 customers**. **236 groups / 474 rows** have distinct raw spellings, including customer **100323**. Exact and spelling-variant results overlap by **4 rows**. | Trimmed and lowercased emails before grouping. Counted distinct stored spellings within groups. | If approved, use a unique expression index with the same normalization, or a stored normalized key with `UNIQUE`. |
| Sibling category names | **0** exact duplicate groups | Grouped by parent and name, treating NULL parents as one root group. | If root names must also be unique, use `UNIQUE NULLS NOT DISTINCT (parent_id, category_name)`. |
| Repeated order products | **616 pairs / 1,233 lines**, including item **900128** for order **500063** and product **5373** | Grouped non-NULL order/product pairs and joined repeated pairs back to the lines. | Use `UNIQUE (order_id, product_id)` if split lines are disallowed. |

## Missing CHECK constraints and cross-row validation

### Stock and inventory movements

| Finding | Evidence | **Detection** | **Prevention** |
|---|---|---|---|
| Negative stock | **14 products**, balances **−4 to −1** | Filtered `stock_quantity < 0`. | Use `CHECK (stock_quantity >= 0)`. Add `NOT NULL` if a known balance is required. |
| Zero change | **90 logs**, all `stock_take`, including **982398**. Zero may be valid for stock takes. | Filtered `change_qty = 0` and checked reasons. | If only stock takes can be zero, use `CHECK (change_qty <> 0 OR reason = 'stock_take')` with both inputs `NOT NULL`. |
| Stock differs from net movements | **144 products**. Product **5068** has stock **12** and net **16** over **64 logs**. Confirm opening balances and history before calling these errors. | Compared stock with summed linked changes for products with complete inputs. | Record opening stock and all changes. Derive stock from the ledger or update both in one transaction. |
| Missing reconciliation inputs | **0 products**. Each has a balance and linked movements with quantities. | Left-joined grouped movements and checked for missing balances, movement groups, or quantities. | Require non-NULL stock and quantities. Record opening balance or movement where required. |

| Negative balance | Product IDs |
|---:|---|
| −4 | 5005, 5057, 5391 |
| −3 | 7390 |
| −2 | 5322, 8107 |
| −1 | 6148, 6295, 6392, 6399, 7049, 8353, 8659, 8862 |

### Quantities, prices, and order totals

| Finding | Evidence | **Detection** | **Prevention** |
|---|---|---|---|
| Nonpositive item quantity | **0 rows** | Filtered `quantity <= 0`. | Use `CHECK (quantity > 0)` and `NOT NULL`. |
| Invalid prices or totals | **0** negative or `NaN` values in product price, item unit price, and order total. NULLs are counted separately. | Tested each numeric column for negatives and `NaN`. | Use `CHECK (amount >= 0 AND amount <> 'NaN'::numeric)` with the actual column name. Existing precision limits exclude infinities. |
| Orders without items | **0 orders**. The **692 orphan lines** instead refer to missing orders. | Left-joined orders to grouped lines and counted missing matches, also by status. | Require a line before entering a status that needs items. Guard deletion of the last line. |
| Stored total differs from line sum | **2,279** fully priced orders. Order **500063** stores **2,975.33** vs. **2,380.26** in lines, a **595.07** difference. Taxes, discounts, or shipping may explain differences. | Summed `quantity × unit_price` per order and compared complete inputs only. | Derive totals with approved adjustments or recompute them whenever inputs change in the same transaction. |
| Incomplete total inputs | **147 orders**, excluded from the mismatch count. Order **501809** has three lines, two calculable. | Compared `count(*)` with `count(quantity * unit_price)` and checked for NULL stored totals. | Require quantity, unit price, and any required stored total with `NOT NULL`. Validate line presence separately. |

### Category hierarchy

| Finding | Evidence | **Detection** | **Prevention** |
|---|---|---|---|
| Self-parent | **0 categories** | Compared each ID with its parent. | Use `CHECK (category_id <> parent_id)`. NULL parents remain valid. |
| Parent cycle | **2 categories**, **9001 → 9002 → 9001**. Both parent IDs exist. | A recursive CTE tracked parent paths and flagged returns to the starting category. | Validate ancestry in a trigger or controlled write procedure. Serialize parent changes to prevent concurrent updates from forming a cycle. |

### Timestamp comparisons

Both checks found **0 violations** among matching records. The foreign-key checks cover unmatched rows.

| Rule | **Detection** | **Prevention**, if required |
|---|---|---|
| Orders follow customer registration | Joined orders to customers and checked for earlier order dates. | Check order writes and registration date edits, or make the registration date immutable. |
| Movements follow product creation | Joined movements to products and checked for earlier movement dates. | Check movement writes and product creation date edits, or make the creation date immutable. |

## Order statuses and inventory reasons

### Order statuses

**Effect on the outreach list.** In the 12-month audit window, **82 customers**
enter the top 100 when the chosen completion rule replaces exact `completed`
matching. Including `shipped` in the chosen rule brings **17 different
customers** into that list. Each comparison counts new members, so moving up
or down within the same list does not count. The Monday report’s live ranking
uses all eligible history before the cutoff. [Decision and comparison query](adr/003-adr-004-monday-report-business-rules.md#ranking-sensitivity)

- **Evidence.** **15 stored values**, **7 after trimming and lowercasing**. Normalization leaves `complete` and `completed` distinct, as it does `canceled` and `cancelled`.
- **Detection.** `ROLLUP` counts stored spellings, normalized groups, and all **159,384 orders** in one query. The four `completed` spellings total **89,066 orders**. Orders without items total **0** at every level.
- **Prevention.** Once the vocabulary is approved, enforce it with `CHECK (status IN (...))` or a lookup foreign key. Add `NOT NULL` if required.

| Normalized status | Stored spelling and count | Example order |
|---|---|---:|
| `canceled` | `canceled` 3,209 | 500084 |
| `cancelled` | `cancelled` 3,238, `Cancelled` 3,131, `CANCELLED` 3,119 | 500011 |
| `complete` | `complete` 22,285 | 500003 |
| `completed` | leading-space ` completed` 22,395, `completed` 22,091, `Completed` 22,188, `COMPLETED` 22,392 | 500012 |
| `pending` | `pending` 7,928, `Pending` 8,081 | 500008 |
| `refunded` | `refunded` 3,217, `Refunded` 3,262 | 500007 |
| `shipped` | `shipped` 6,516, `Shipped` 6,332 | 500048 |

### Inventory reasons

- **Evidence.** Six stored reasons with **0** case or whitespace variants and **0** NULL quantities. Sales and damage are negative, restocks and returns positive, and corrections go both ways.
- **Detection.** Conditional counts bucket quantities by sign. `GROUPING SETS` returns reason rows and the grand total in one query. A `CUBE` would add unused subtotals.
- **Prevention.** Use a reason lookup foreign key and a row-level `CHECK` for approved signs, allowing zero for stock takes and both signs for corrections.

| Reason | Negative | Zero | Positive | Example log |
|---|---:|---:|---:|---:|
| `correction` | 290 | 0 | 195 | 962104 |
| `damage` | 268 | 0 | 0 | 962107 |
| `restock` | 0 | 0 | 18,857 | 963381 |
| `return` | 0 | 0 | 564 | 962105 |
| `sale` | 262,088 | 0 | 0 | 700001 |
| `stock_take` | 0 | 90 | 0 | 982398 |
| **Total** | **262,646** | **90** | **19,616** | — |

**Sale traceability**

- **Evidence.** All **262,088 sale movements** carry product IDs but lack an enforced link to a purchase.
- **Detection.** The schema has no order or order-item key on the log. The reason profile counted the sale rows.
- **Prevention.** If required, add an order-item foreign key and a `CHECK` requiring it for sales. A composite foreign key with a matching unique constraint on the order-item/product pair also enforces product consistency.

## Overselling, timestamps, and indexes

**Overselling**

- **Evidence.** The **14 negative balances** prove invalid stock persisted. The extract cannot identify whether concurrent buyers, adjustments, or another write caused it.
- **Detection.** The stock check exposes negative balances. The baseline lacks `place_order(bigint,bigint,integer)`, which the race harness requires, so concurrency remains untested.
- **Prevention.** Decrement stock with an atomic conditional update using `WHERE stock_quantity >= quantity` and `RETURNING`. Write the order, item, and movement in the same transaction.

**Time-zone information**

- **Evidence.** Four columns omit time-zone information. `products.created_at` (**4,023**), `customers.registered_at` (**40,420**), `orders.order_date` (**159,384**), and `inventory_log.created_at` (**282,352**).
- **Detection.** Inspected column types. Date checks compare stored wall-clock values. Original offsets and consistent writer time zones cannot be established from the extract.
- **Prevention.** Store event instants as `timestamptz` and require explicit offsets at ingestion.

**Indexes**

- **Evidence.** Six primary-key indexes. The customer/date query used a parallel hash join and sequential scans on orders and items. Product lookup used a parallel sequential scan on the log.
- **Detection.** Queried `pg_indexes` and used `EXPLAIN` on representative queries.
- **Prevention.** Evaluate indexes on `orders (customer_id, order_date)`, `order_items (order_id)`, and `inventory_log (product_id)`. Measure on representative workloads before adding them.

## Reproduce the audit

Use a disposable PostgreSQL database because the baseline schema drops existing tables. Skip the load if the extract is already loaded.

```bash
(
  cd provided_resources
  psql -X "$AUDIT_DATABASE_URL" -v ON_ERROR_STOP=1 -f schema.sql -f load.sql
)
psql -X -q --csv "$AUDIT_DATABASE_URL" \
  -v details=/tmp/kk-audit-rows.csv -f scripts/schema_audit.sql
```

The [audit SQL](../scripts/schema_audit.sql) uses session-local views and a read-only, repeatable-read transaction. It prints counts including zeroes, profiles, indexes, and query plans.

The optional `details` export includes `check_id`, `table_name`, `row_id`, `group_key`, and JSON evidence, including source emails and row IDs for all statuses and reasons. Filter by `check_stock_negative` or `fk_order_items_order` for full row lists. Rows can appear in multiple checks.
