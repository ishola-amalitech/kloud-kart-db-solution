# Kloud Kart schema findings

| Finding | Observed in the extract |
|---|---|
| [Broken references](#missing-foreign-keys) | **1,023 rows**, including **692 order items** pointing to missing orders |
| [Missing fields](#missing-fields) | **148 items** without a unit price and **37 products** without a category |
| [Customer email duplicates](#duplicate-values) | **299 exact groups / 598 rows**. After trimming and lowercasing, **533 groups / 1,068 rows** |
| [Repeated order products](#duplicate-values) | **616 order/product pairs / 1,233 lines**. Confirm whether split lines are allowed. |
| [Stock and movements](#stock-and-inventory-movements) | **14 negative balances**, **144 ledger differences**, and **90 zero-change stock takes** |
| [Order totals](#quantities-prices-and-order-totals) | **2,279 differences** on fully priced orders and **147 orders** with incomplete inputs |
| [Category cycle](#category-hierarchy) | **2 categories** in one cycle |
| [Status and sale traceability](#order-statuses-and-inventory-reasons) | **15 stored status spellings**. **262,088 sale movements** lack an enforced link to a purchase. |

## Table of contents

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
