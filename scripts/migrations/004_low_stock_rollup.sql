-- Install the low-stock reporting views after migration 001 adds is_available.
-- The transaction exposes all three views together. It changes no product or
-- category data. Keep the lock wait short if other schema work is in progress.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE VIEW public.stock_category_paths AS
WITH RECURSIVE ancestry AS (
    -- Anchor: start a separate walk for each category, including categories
    -- that cannot be reached from a root. Record the starting category once.
    SELECT category.category_id,
        category.parent_id AS next_parent_id,
        ARRAY[category.category_id] AS ancestor_ids,
        ARRAY[COALESCE(NULLIF(btrim(category.category_name), ''), 'Category ' || category.category_id)] AS path_names,
        false AS is_cycle
    FROM public.categories AS category

    UNION ALL

    -- Recursive step: follow the one parent link. IDs are stored from child
    -- to root for membership checks. Names are stored in display order.
    -- Retain the row that detects a repeat, then stop walking that path.
    SELECT walk.category_id,
        parent.parent_id,
        array_append(walk.ancestor_ids, parent.category_id),
        array_prepend(
            COALESCE(NULLIF(btrim(parent.category_name), ''), 'Category ' || parent.category_id),
            walk.path_names
        ),
        parent.category_id = ANY(walk.ancestor_ids)
    FROM ancestry AS walk
    JOIN public.categories AS parent ON parent.category_id = walk.next_parent_id
    WHERE NOT walk.is_cycle
)
-- One terminal row per starting category. A path can end at a root, at
-- a repeat, or at a parent ID that has no matching category row.
SELECT walk.category_id,
    walk.ancestor_ids,
    array_to_string(walk.path_names, ' / ') AS category_path,
    CASE
        WHEN walk.is_cycle THEN 'Category cycle'
        WHEN walk.next_parent_id IS NOT NULL THEN 'Missing parent category'
    END AS hierarchy_issue
FROM ancestry AS walk
LEFT JOIN public.categories AS parent ON parent.category_id = walk.next_parent_id
WHERE walk.is_cycle OR walk.next_parent_id IS NULL OR parent.category_id IS NULL;

CREATE VIEW public.low_stock_worklist AS
WITH classified AS (
    -- Classify on-hand stock independently of category health. An available
    -- product with a broken category link can still require a reorder.
    SELECT product.product_id,
        product.product_name,
        product.category_id,
        category.category_path,
        category.ancestor_ids,
        product.stock_quantity,
        product.is_available,
        product.is_available AND COALESCE(product.stock_quantity BETWEEN 0 AND 5, false) AS needs_reorder,
        CASE
            WHEN product.stock_quantity IS NULL THEN 'Unknown stock'
            WHEN product.stock_quantity < 0 THEN 'Negative stock'
        END AS stock_issue,
        CASE
            WHEN product.category_id IS NULL THEN 'Uncategorized'
            WHEN category.category_id IS NULL THEN 'Missing assigned category'
            ELSE category.hierarchy_issue
        END AS hierarchy_issue
    FROM public.products AS product
    LEFT JOIN public.stock_category_paths AS category ON category.category_id = product.category_id
)
-- A product can have several flags, but the worklist has one row per product.
SELECT *
FROM classified
WHERE needs_reorder OR NOT is_available OR stock_issue IS NOT NULL OR hierarchy_issue IS NOT NULL;

CREATE VIEW public.low_stock_category_rollup AS
WITH direct_counts AS (
    -- Group at the assigned category before distributing counts upward.
    -- Products with bad paths stay visible in the worklist.
    SELECT category_id, count(*) AS product_count
    FROM public.low_stock_worklist
    WHERE needs_reorder AND hierarchy_issue IS NULL
    GROUP BY category_id
),
branch_counts AS (
    -- A valid path contains the assigned category and each ancestor once.
    -- This makes the branch total inclusive of the category's direct count.
    SELECT ancestor.category_id, sum(direct.product_count)::bigint AS product_count
    FROM direct_counts AS direct
    JOIN public.stock_category_paths AS path USING (category_id)
    CROSS JOIN LATERAL unnest(path.ancestor_ids) AS ancestor(category_id)
    GROUP BY ancestor.category_id
)
SELECT path.category_id,
    path.category_path,
    path.ancestor_ids,
    COALESCE(direct.product_count, 0) AS direct_low_stock,
    COALESCE(branch.product_count, 0) AS branch_low_stock
FROM public.stock_category_paths AS path
LEFT JOIN direct_counts AS direct USING (category_id)
LEFT JOIN branch_counts AS branch USING (category_id)
WHERE path.hierarchy_issue IS NULL;

COMMIT;
