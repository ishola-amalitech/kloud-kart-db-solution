-- Run on the seeded test database after migration 004. These checks use
-- synthetic rows inside a transaction and roll back every fixture.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '5s';

-- Independent store-wide check against the source products, before fixtures.
DO $check$
DECLARE
    expected bigint;
    in_worklist bigint;
    in_root_branches bigint;
    with_bad_category bigint;
BEGIN
    SELECT count(*) INTO expected
    FROM public.products
    WHERE is_available AND stock_quantity BETWEEN 0 AND 5;

    SELECT count(*), count(*) FILTER (WHERE hierarchy_issue IS NOT NULL)
    INTO in_worklist, with_bad_category
    FROM public.low_stock_worklist
    WHERE needs_reorder;

    SELECT COALESCE(sum(branch_low_stock), 0) INTO in_root_branches
    FROM public.low_stock_category_rollup
    WHERE cardinality(ancestor_ids) = 1;

    IF expected <> in_worklist OR expected <> in_root_branches + with_bad_category THEN
        RAISE EXCEPTION 'Reorder counts disagree: source %, worklist %, roots %, bad category %',
            expected, in_worklist, in_root_branches, with_bad_category;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.low_stock_worklist
        GROUP BY product_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Worklist contains duplicate product IDs';
    END IF;

    RAISE NOTICE 'Seeded counts agree: % reorder candidates = % in root branches + % with category issues',
        expected, in_root_branches, with_bad_category;
END;
$check$;

-- A 12-level branch and a sibling under the same root. Reuse a display
-- name to check that keys, rather than category names, govern grouping.
INSERT INTO public.categories (category_id, category_name, parent_id)
SELECT -10000 - n, 'Rollup test', CASE WHEN n > 1 THEN -10000 - (n - 1) END
FROM generate_series(1, 12) AS depth(n);

INSERT INTO public.categories (category_id, category_name, parent_id) VALUES
    (-10013, 'Rollup test', -10001),
    (-10021, 'Cycle A', -10022),
    (-10022, 'Cycle B', -10021),
    (-10023, 'Child of cycle', -10021),
    (-10024, 'Missing parent', -10999),
    (-10025, 'Self-parent', -10025);

INSERT INTO public.products (product_id, product_name, category_id, stock_quantity, is_available) VALUES
    (-10001, 'Zero stock', -10012, 0, true),
    (-10002, 'Threshold', -10012, 5, true),
    (-10003, 'Above threshold', -10012, 6, true),
    (-10004, 'Paused at zero', -10012, 0, false),
    (-10005, 'Unknown stock', -10012, NULL, true),
    (-10006, 'At root', -10001, 2, true),
    (-10007, 'At sibling', -10013, 1, true),
    (-10008, 'In cycle', -10022, 3, true),
    (-10009, 'Below cycle', -10023, 3, true),
    (-10010, 'Missing parent', -10024, 3, true),
    (-10011, 'No assignment', NULL, 3, true),
    (-10012, 'Missing assignment', -10999, 3, true),
    (-10013, 'Self-parent', -10025, 3, true);

DO $check$
BEGIN
    -- Leaf products count once at the leaf and once at every ancestor.
    -- The root also includes its own product and the sibling's product.
    IF (SELECT branch_low_stock FROM public.low_stock_category_rollup WHERE category_id = -10001)
           IS DISTINCT FROM 4
       OR (SELECT direct_low_stock FROM public.low_stock_category_rollup WHERE category_id = -10001)
           IS DISTINCT FROM 1
       OR (SELECT count(*) FROM public.low_stock_category_rollup
           WHERE category_id BETWEEN -10012 AND -10002 AND branch_low_stock = 2) <> 11
       OR (SELECT cardinality(ancestor_ids) FROM public.stock_category_paths WHERE category_id = -10012)
           IS DISTINCT FROM 12
       OR (SELECT direct_low_stock FROM public.low_stock_category_rollup WHERE category_id = -10012)
           IS DISTINCT FROM 2
       OR (SELECT branch_low_stock FROM public.low_stock_category_rollup WHERE category_id = -10013)
           IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'Deep-branch or sibling counts are incorrect';
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.low_stock_category_rollup WHERE category_id BETWEEN -10025 AND -10021
    ) OR EXISTS (
        -- Compare the expected issue even if a broken row disappeared.
        SELECT 1
        FROM (VALUES
            (-10008, 'Category cycle'),
            (-10009, 'Category cycle'),
            (-10010, 'Missing parent category'),
            (-10011, 'Uncategorized'),
            (-10012, 'Missing assigned category'),
            (-10013, 'Category cycle')
        ) AS expected(product_id, issue)
        LEFT JOIN public.low_stock_worklist AS actual USING (product_id)
        WHERE actual.hierarchy_issue IS DISTINCT FROM expected.issue
            OR actual.needs_reorder IS DISTINCT FROM true
    ) THEN
        RAISE EXCEPTION 'A broken path was rolled up or lost from the product worklist';
    END IF;

    IF EXISTS (SELECT 1 FROM public.low_stock_worklist WHERE product_id = -10003)
       OR (SELECT needs_reorder FROM public.low_stock_worklist WHERE product_id = -10004)
           IS DISTINCT FROM false
       OR (SELECT stock_issue FROM public.low_stock_worklist WHERE product_id = -10005)
           IS DISTINCT FROM 'Unknown stock'
       OR (SELECT needs_reorder FROM public.low_stock_worklist WHERE product_id = -10005)
           IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'Stock availability, threshold or unknown count is incorrect';
    END IF;
END;
$check$;

ROLLBACK;
