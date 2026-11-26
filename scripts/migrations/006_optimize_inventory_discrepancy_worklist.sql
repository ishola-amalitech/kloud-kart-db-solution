-- Apply after migration 005. This changes only how the discrepancy view reads
-- and groups data. It does not rewrite movements or change any stock balance.
BEGIN;

-- Replacing a view takes a lock on it. Abort promptly if a concurrent reader
-- prevents the change, so the deployment can retry during a quieter period.
SET LOCAL lock_timeout = '5s';

-- Replacing the definition in place preserves the view's column order, types,
-- ownership, and grants. Existing reporting and operator queries can reuse it.
CREATE OR REPLACE VIEW public.inventory_discrepancy_worklist AS
WITH latest_count AS (
    -- A verified count sets the starting balance for this product. Use the
    -- latest movement ID, which follows movement order within each product.
    SELECT DISTINCT ON (product_id) product_id, log_id, stock_after
    FROM public.inventory_log
    WHERE reason = 'stock_take' AND stock_after IS NOT NULL
    ORDER BY product_id, log_id DESC
), movement_totals AS (
    -- Collapse the movement log to one row per product before joining to
    -- the product table. Compute both sums in the same pass through the log.
    SELECT log.product_id,
           sum(log.change_qty) AS logged_net,
           sum(log.change_qty) FILTER (WHERE log.log_id > count_row.log_id) AS net_after_count
    FROM public.inventory_log AS log
    LEFT JOIN latest_count AS count_row USING (product_id)
    GROUP BY log.product_id
), totals AS (
    -- Keep products with no movement history. Without a verified count, the
    -- replayed balance stays NULL and comparison_stock falls back to net log
    -- movement. A verified count with no later movement is the balance itself.
    SELECT product.product_id, product.product_name, product.stock_quantity, product.is_available,
           count_row.log_id AS verified_count_id,
           movement_totals.logged_net,
           count_row.stock_after + coalesce(movement_totals.net_after_count, 0) AS balance_from_count
    FROM public.products AS product
    LEFT JOIN latest_count AS count_row USING (product_id)
    LEFT JOIN movement_totals USING (product_id)
), compared AS (
    SELECT totals.*, coalesce(balance_from_count, logged_net) AS comparison_stock
    FROM totals
)
-- Keep the original review and prioritization rules, including NULL balances.
SELECT compared.*,
       coalesce(is_available AND stock_quantity > 0 AND comparison_stock < stock_quantity, false)
           AS possible_oversell,
       stock_quantity IS NOT NULL AND comparison_stock IS NOT NULL
           AND ((stock_quantity BETWEEN 0 AND 5) <> (comparison_stock BETWEEN 0 AND 5))
           AS low_stock_differs
FROM compared
WHERE stock_quantity IS NULL OR comparison_stock IS NULL OR stock_quantity <> comparison_stock;

COMMIT;
