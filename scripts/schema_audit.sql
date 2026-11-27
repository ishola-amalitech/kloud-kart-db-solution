\set ON_ERROR_STOP on
-- Kloud Kart baseline schema and data audit (PostgreSQL 18).
--
-- Prerequisites: load provided_resources/schema.sql and load.sql into a
-- disposable database. Run this file in a fresh psql session:
--
--   psql -X -q -P pager=off "$AUDIT_DATABASE_URL" -f scripts/schema_audit.sql
--
-- Add -v details=/path/to/rows.csv for a row-level export. That file is
-- written by psql on the client machine and includes actual email values.
--
-- The views are temporary and contain no stored data. Set them up before
-- the read-only transaction, then run all output queries against one
-- repeatable-read snapshot.

-- Confirm that all six tables were loaded before interpreting findings.
CREATE TEMP VIEW audit_counts AS
SELECT 'categories' AS table_name, count(*) AS rows
FROM categories
UNION ALL
SELECT 'products', count(*)
FROM products
UNION ALL
SELECT 'customers', count(*)
FROM customers
UNION ALL
SELECT 'orders', count(*)
FROM orders
UNION ALL
SELECT 'order_items', count(*)
FROM order_items
UNION ALL
SELECT 'inventory_log', count(*)
FROM inventory_log;

-- Expand each row into named fields while keeping its primary key.
-- JSON null represents a SQL NULL in a converted record. This lets the
-- summary and row export use the same definition of a missing value.
CREATE TEMP VIEW audit_fields AS
SELECT table_name, row_id, column_name, value
FROM (
    SELECT 'categories' AS table_name, category_id AS row_id, to_jsonb(t) AS record
    FROM categories t
    UNION ALL
    SELECT 'products', product_id, to_jsonb(t)
    FROM products t
    UNION ALL
    SELECT 'customers', customer_id, to_jsonb(t)
    FROM customers t
    UNION ALL
    SELECT 'orders', order_id, to_jsonb(t)
    FROM orders t
    UNION ALL
    SELECT 'order_items', order_item_id, to_jsonb(t)
    FROM order_items t
    UNION ALL
    SELECT 'inventory_log', log_id, to_jsonb(t)
    FROM inventory_log t
) records
CROSS JOIN LATERAL jsonb_each(record) AS fields(column_name, value);

-- Each branch produces one evidence row per affected source row.
-- group_key identifies a duplicate group; it is NULL for row-level checks.
-- UNION ALL preserves findings that happen to involve the same source row.
CREATE TEMP VIEW audit_rows AS
WITH RECURSIVE category_walk AS (
    -- Start at every category. Keep visited IDs in path to stop on cycles;
    -- a missing parent ends the walk and is reported by the FK check.
    SELECT category_id AS origin, parent_id, ARRAY[category_id] AS path
    FROM categories
    UNION ALL
    SELECT walk.origin, parent.parent_id, walk.path || parent.category_id
    FROM category_walk walk
    JOIN categories parent ON parent.category_id = walk.parent_id
    WHERE NOT walk.parent_id = ANY(walk.path)
), order_lines AS (
    -- COUNT(expression) skips NULL, and SUM would silently ignore such
    -- lines. Keep both counts so a partial sum cannot pass reconciliation.
    SELECT order_id, count(*) AS lines, count(quantity * unit_price) AS priced_lines,
           sum(quantity * unit_price) AS line_total
    FROM order_items
    GROUP BY order_id
), product_movements AS (
    -- Count all linked movements and separately count known quantities.
    -- The extract may omit opening balances or earlier movements.
    SELECT product_id, count(*) AS movements, count(change_qty) AS known_movements,
           sum(change_qty) AS net_change
    FROM inventory_log
    GROUP BY product_id
), emails AS (
    -- Keep raw and normalized values side by side. Missing or blank
    -- addresses belong to the field profiles, not the duplicate checks.
    SELECT customer_id, email,
           lower(regexp_replace(email, '^\s+|\s+$', '', 'g')) AS normalized
    FROM customers
    WHERE email IS NOT NULL AND email !~ '^\s*$'
), exact_emails AS (
    -- WHERE in emails removes missing addresses before any groups form.
    -- GROUP BY produces one row per exact address. HAVING filters the
    -- completed groups, keeping only addresses with multiple customers.
    SELECT email
    FROM emails
    GROUP BY email
    HAVING count(*) > 1
), normalized_emails AS (
    -- Case and outer whitespace can merge several spellings into one key.
    SELECT normalized
    FROM emails
    GROUP BY normalized
    HAVING count(*) > 1
), varied_emails AS (
    -- Count distinct stored spellings, so repeated copies of a single
    -- spelling contribute to the duplicate check but not this variant check.
    SELECT normalized
    FROM emails
    GROUP BY normalized
    HAVING count(DISTINCT email) > 1
), repeated_categories AS (
    -- Group roots together when evaluating the candidate sibling rule.
    SELECT parent_id, category_name
    FROM categories
    WHERE category_name IS NOT NULL AND category_name !~ '^\s*$'
    GROUP BY parent_id, category_name
    HAVING count(*) > 1
), repeated_items AS (
    -- Evaluate repeated order/product pairs only when both keys exist
    -- on the line. The FK checks independently validate their targets.
    SELECT order_id, product_id
    FROM order_items
    WHERE order_id IS NOT NULL AND product_id IS NOT NULL
    GROUP BY order_id, product_id
    HAVING count(*) > 1
)

-- Foreign keys. A non-NULL reference is orphaned when the target's primary
-- key is NULL after a left join. Profile absent references separately.
SELECT 'fk_categories_parent'::text AS check_id, 'categories'::text AS table_name,
       c.category_id AS row_id, NULL::text AS group_key,
       jsonb_build_object('parent_id', c.parent_id) AS evidence
FROM categories c
LEFT JOIN categories p ON p.category_id = c.parent_id
WHERE c.parent_id IS NOT NULL AND p.category_id IS NULL
UNION ALL
SELECT 'fk_products_category', 'products', p.product_id, NULL,
       jsonb_build_object('category_id', p.category_id)
FROM products p
LEFT JOIN categories c USING (category_id)
WHERE p.category_id IS NOT NULL AND c.category_id IS NULL
UNION ALL
SELECT 'fk_orders_customer', 'orders', o.order_id, NULL,
       jsonb_build_object('customer_id', o.customer_id)
FROM orders o
LEFT JOIN customers c USING (customer_id)
WHERE o.customer_id IS NOT NULL AND c.customer_id IS NULL
UNION ALL
SELECT 'fk_order_items_order', 'order_items', i.order_item_id, NULL,
       jsonb_build_object('order_id', i.order_id)
FROM order_items i
LEFT JOIN orders o USING (order_id)
WHERE i.order_id IS NOT NULL AND o.order_id IS NULL
UNION ALL
SELECT 'fk_order_items_product', 'order_items', i.order_item_id, NULL,
       jsonb_build_object('product_id', i.product_id)
FROM order_items i
LEFT JOIN products p USING (product_id)
WHERE i.product_id IS NOT NULL AND p.product_id IS NULL
UNION ALL
SELECT 'fk_inventory_log_product', 'inventory_log', l.log_id, NULL,
       jsonb_build_object('product_id', l.product_id)
FROM inventory_log l
LEFT JOIN products p USING (product_id)
WHERE l.product_id IS NOT NULL AND p.product_id IS NULL

-- Candidate uniqueness rules. Join duplicate keys back to the source rows
-- so the export identifies every member of each group.
UNION ALL
SELECT 'unique_email_exact', 'customers', customer_id, email,
       jsonb_build_object('email', email)
FROM emails
JOIN exact_emails USING (email)
UNION ALL
SELECT 'unique_email_normalized', 'customers', customer_id, normalized,
       jsonb_build_object('email', email)
FROM emails
JOIN normalized_emails USING (normalized)
UNION ALL
SELECT 'unique_email_variants', 'customers', customer_id, normalized,
       jsonb_build_object('email', email)
FROM emails
JOIN varied_emails USING (normalized)
UNION ALL
SELECT 'unique_category_sibling_name', 'categories', c.category_id,
       jsonb_build_array(c.parent_id, c.category_name)::text,
       jsonb_build_object('parent_id', c.parent_id, 'category_name', c.category_name)
FROM categories c
JOIN repeated_categories r
  ON c.parent_id IS NOT DISTINCT FROM r.parent_id
 AND c.category_name = r.category_name
UNION ALL
SELECT 'unique_order_product', 'order_items', order_item_id,
       jsonb_build_array(order_id, product_id)::text,
       jsonb_build_object('order_id', order_id, 'product_id', product_id)
FROM order_items
JOIN repeated_items USING (order_id, product_id)

-- Hierarchy checks. A direct self-reference is a separate check. Count a
-- longer loop only when the path returns to its starting category.
UNION ALL
SELECT 'check_category_self_parent', 'categories', category_id, NULL,
       jsonb_build_object('parent_id', parent_id)
FROM categories
WHERE category_id = parent_id
UNION ALL
SELECT 'rule_category_long_cycle', 'categories', origin, NULL,
       jsonb_build_object('path', path || origin)
FROM category_walk
WHERE parent_id = origin AND cardinality(path) > 1

-- Value checks. Comparisons skip NULL; the field profile covers missing
-- values. Numeric NaN is explicitly flagged even though it sorts above
-- ordinary numbers in PostgreSQL.
UNION ALL
SELECT 'check_stock_negative', 'products', product_id, NULL,
       jsonb_build_object('stock_quantity', stock_quantity)
FROM products
WHERE stock_quantity < 0
UNION ALL
SELECT 'check_order_quantity_nonpositive', 'order_items', order_item_id, NULL,
       jsonb_build_object('quantity', quantity)
FROM order_items
WHERE quantity <= 0
UNION ALL
SELECT 'check_product_price', 'products', product_id, NULL,
       jsonb_build_object('price', price)
FROM products
WHERE price < 0 OR price = 'NaN'::numeric
UNION ALL
SELECT 'check_item_unit_price', 'order_items', order_item_id, NULL,
       jsonb_build_object('unit_price', unit_price)
FROM order_items
WHERE unit_price < 0 OR unit_price = 'NaN'::numeric
UNION ALL
SELECT 'check_order_total', 'orders', order_id, NULL,
       jsonb_build_object('total_amount', total_amount)
FROM orders
WHERE total_amount < 0 OR total_amount = 'NaN'::numeric
UNION ALL
SELECT 'check_inventory_zero_movement', 'inventory_log', log_id, NULL,
       jsonb_build_object('reason', reason, 'change_qty', change_qty)
FROM inventory_log
WHERE change_qty = 0

-- Chronology checks compare matched records using stored local timestamps.
-- Orphans are reported above and cannot yield a date comparison.
UNION ALL
SELECT 'rule_order_before_registration', 'orders', o.order_id, NULL,
       jsonb_build_object(
           'customer_id', o.customer_id,
           'order_date', order_date,
           'registered_at', registered_at,
           'difference', registered_at - order_date
       )
FROM orders o
JOIN customers USING (customer_id)
WHERE order_date < registered_at
UNION ALL
SELECT 'rule_movement_before_product', 'inventory_log', l.log_id, NULL,
       jsonb_build_object(
           'product_id', l.product_id,
           'movement_at', l.created_at,
           'product_at', p.created_at,
           'difference', p.created_at - l.created_at
       )
FROM inventory_log l
JOIN products p USING (product_id)
WHERE l.created_at < p.created_at

-- Order completeness and reconciliation. Distinguish no lines, missing
-- amounts, and a mismatch with all operands present.
UNION ALL
SELECT 'rule_order_without_items', 'orders', o.order_id, NULL,
       jsonb_build_object('status', status)
FROM orders o
LEFT JOIN order_lines l USING (order_id)
WHERE l.order_id IS NULL
UNION ALL
SELECT 'rule_order_total_mismatch', 'orders', o.order_id, NULL,
       jsonb_build_object(
           'total_amount', total_amount,
           'line_total', line_total,
           'difference', total_amount - line_total
       )
FROM orders o
JOIN order_lines l USING (order_id)
WHERE priced_lines = lines AND total_amount <> line_total
UNION ALL
SELECT 'rule_order_total_incomplete', 'orders', o.order_id, NULL,
       jsonb_build_object(
           'total_amount', total_amount,
           'lines', lines,
           'priced_lines', priced_lines
       )
FROM orders o
JOIN order_lines l USING (order_id)
WHERE priced_lines <> lines OR total_amount IS NULL

-- Stock reconciliation compares the snapshot balance to net recorded
-- movements. Treat mismatches as leads until the opening stock and
-- history coverage are known.
UNION ALL
SELECT 'rule_stock_movement_mismatch', 'products', p.product_id, NULL,
       jsonb_build_object(
           'stock_quantity', stock_quantity,
           'net_change', net_change,
           'difference', stock_quantity - net_change,
           'movements', movements
       )
FROM products p
JOIN product_movements m USING (product_id)
WHERE movements = known_movements AND stock_quantity <> net_change
UNION ALL
SELECT 'rule_stock_movement_incomplete', 'products', p.product_id, NULL,
       jsonb_build_object(
           'stock_quantity', stock_quantity,
           'movements', movements,
           'known_movements', known_movements
       )
FROM products p
LEFT JOIN product_movements m USING (product_id)
WHERE movements IS NULL OR movements <> known_movements OR stock_quantity IS NULL;

-- Inspect the text fields that identify business entities or serve as
-- classifications. Keep NULL, empty strings, and whitespace-only strings
-- distinct so the report can point to the correct validation rule.
CREATE TEMP VIEW audit_profiles AS
WITH text_fields(table_name, column_name, row_id, value) AS (
    SELECT 'categories', 'category_name', category_id, category_name
    FROM categories
    UNION ALL
    SELECT 'products', 'product_name', product_id, product_name
    FROM products
    UNION ALL
    SELECT 'customers', 'full_name', customer_id, full_name
    FROM customers
    UNION ALL
    SELECT 'customers', 'email', customer_id, email
    FROM customers
    UNION ALL
    SELECT 'orders', 'status', order_id, status
    FROM orders
    UNION ALL
    SELECT 'inventory_log', 'reason', log_id, reason
    FROM inventory_log
)
SELECT table_name,
       column_name,
       row_id,
       value,
       CASE
           WHEN value IS NULL THEN 'null'
           WHEN value = '' THEN 'empty'
           WHEN value ~ '^\s+$' THEN 'whitespace only'
           ELSE 'nonblank'
       END AS kind
FROM text_fields;

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT version() AS postgres_version;

SELECT table_name, rows
FROM audit_counts
ORDER BY table_name;

SELECT table_name, column_name, count(*) FILTER (WHERE value = 'null'::jsonb) AS null_rows
FROM audit_fields
GROUP BY table_name, column_name
ORDER BY table_name, column_name;

-- Keep a catalog of check IDs so zero-hit checks still appear. When
-- adding an audit_rows branch, register its check_id here as well.
-- groups counts distinct non-NULL keys, and example_id picks a stable
-- example by primary key. Full evidence is available in the CSV export.
WITH checks(check_id) AS (VALUES
    ('fk_categories_parent'),
    ('fk_products_category'),
    ('fk_orders_customer'),
    ('fk_order_items_order'),
    ('fk_order_items_product'),
    ('fk_inventory_log_product'),
    ('unique_email_exact'),
    ('unique_email_normalized'),
    ('unique_email_variants'),
    ('unique_category_sibling_name'),
    ('unique_order_product'),
    ('check_category_self_parent'),
    ('rule_category_long_cycle'),
    ('check_stock_negative'),
    ('check_order_quantity_nonpositive'),
    ('check_product_price'),
    ('check_item_unit_price'),
    ('check_order_total'),
    ('check_inventory_zero_movement'),
    ('rule_order_before_registration'),
    ('rule_movement_before_product'),
    ('rule_order_without_items'),
    ('rule_order_total_mismatch'),
    ('rule_order_total_incomplete'),
    ('rule_stock_movement_mismatch'),
    ('rule_stock_movement_incomplete')
), counts AS (
    -- One GROUP BY gives all three metrics per check. COUNT(*) includes
    -- every evidence row, while COUNT(DISTINCT group_key) skips NULL keys.
    SELECT check_id,
           count(*) AS affected_rows,
           count(DISTINCT group_key) AS groups,
           min(row_id) AS example_id
    FROM audit_rows
    GROUP BY check_id
)
SELECT check_id,
       coalesce(affected_rows, 0) AS affected_rows,
       coalesce(groups, 0) AS groups,
       example_id
FROM checks
-- Start from the catalog so a check with no evidence still appears.
LEFT JOIN counts USING (check_id)
ORDER BY check_id;

-- Aggregate FILTER applies to one count only. A query-wide WHERE on
-- kind would remove fields whose values are all nonblank.
SELECT table_name, column_name, count(*) FILTER (WHERE kind = 'empty') AS empty_rows,
       count(*) FILTER (WHERE kind = 'whitespace only') AS whitespace_rows
FROM audit_profiles
GROUP BY table_name, column_name
ORDER BY table_name, column_name;

-- Profile the order population once for both status frequency and empty
-- orders. Reduce the item table to one row per referenced order before the
-- join so an order with multiple lines contributes only once to each count.
WITH orders_with_items AS (
    SELECT DISTINCT order_id
    FROM order_items
    WHERE order_id IS NOT NULL
), order_status AS (
    SELECT o.status,
           lower(regexp_replace(o.status, '^\s+|\s+$', '', 'g')) AS normalized,
           items.order_id IS NULL AS has_no_items
    FROM orders AS o
    LEFT JOIN orders_with_items AS items ON items.order_id = o.order_id
)
-- ROLLUP follows the status hierarchy: exact spelling, normalized status,
-- then all orders. GROUPING() marks columns omitted at subtotal levels,
-- so a genuine NULL status can still appear as a detail row.
SELECT CASE
           WHEN GROUPING(normalized) = 1 THEN 'total'
           WHEN GROUPING(status) = 1 THEN 'normalized subtotal'
           ELSE 'stored spelling'
       END AS aggregation_level,
       normalized,
       status,
       count(*) AS orders,
       count(*) FILTER (WHERE has_no_items) AS orders_without_items
FROM order_status
GROUP BY ROLLUP (normalized, status)
ORDER BY GROUPING(normalized), normalized, GROUPING(status), status;

-- Reason spelling and movement sign can be profiled from the same rows.
-- Retain normalized text as a comparison aid for spotting spelling variants.
WITH movement_reasons AS (
    SELECT reason,
           lower(regexp_replace(reason, '^\s+|\s+$', '', 'g')) AS normalized,
           change_qty
    FROM inventory_log
)
-- GROUPING SETS requests just the stored reason breakdown and the overall
-- total. The empty set () groups all movements together. The four FILTER
-- clauses count quantities in different buckets within each group.
SELECT CASE WHEN GROUPING(reason) = 1 THEN 'total'
            ELSE 'stored reason' END AS aggregation_level,
       reason,
       normalized,
       count(*) FILTER (WHERE change_qty < 0) AS negative,
       count(*) FILTER (WHERE change_qty = 0) AS zero,
       count(*) FILTER (WHERE change_qty > 0) AS positive,
       count(*) FILTER (WHERE change_qty IS NULL) AS null_qty
FROM movement_reasons
GROUP BY GROUPING SETS ((reason, normalized), ())
ORDER BY GROUPING(reason), normalized, reason;

-- Report physical indexes and planner choices separately from row defects.
-- Plain EXPLAIN shows the plan without executing or timing the sample query.
SELECT tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = current_schema() AND tablename IN (SELECT table_name FROM audit_counts)
ORDER BY tablename, indexname;

EXPLAIN (COSTS OFF)
SELECT o.order_id, i.product_id
FROM orders o
JOIN order_items i USING (order_id)
WHERE o.customer_id = (SELECT min(customer_id) FROM customers)
  AND o.order_date >= (SELECT max(order_date) - interval '30 days' FROM orders);

EXPLAIN (COSTS OFF)
SELECT log_id
FROM inventory_log
WHERE product_id = (SELECT min(product_id) FROM products);

-- Check the race harness prerequisite. A populated result means only
-- that the expected function exists; concurrency requires a separate test.
SELECT to_regprocedure('place_order(bigint,bigint,integer)') AS race_test_function;

-- Optional evidence uses the same rules as the summary. Status and
-- reason profiles include all rows, so each spelling can be traced by ID.
-- For profile rows, group_key carries the column name.
\if :{?details}
\pset format csv
\o :details
SELECT check_id, table_name, row_id, group_key, evidence
FROM audit_rows
UNION ALL
SELECT 'profile_' || p.kind, table_name, row_id, column_name,
       jsonb_build_object('value', value)
FROM audit_profiles p
WHERE kind IN ('empty', 'whitespace only') OR column_name IN ('status', 'reason')
UNION ALL
SELECT 'profile_null', table_name, row_id, column_name, '{}'::jsonb
FROM audit_fields
WHERE value = 'null'::jsonb
ORDER BY check_id, table_name, row_id, group_key;
\o
\endif

ROLLBACK;
