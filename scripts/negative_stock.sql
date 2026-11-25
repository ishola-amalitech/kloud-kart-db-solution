\set ON_ERROR_STOP on

-- Read-only worklist. Use the recorded balance as the expected value when
-- recording a verified physical count. A paused product stays paused after
-- correction, so compare this list before and after reconciliation.
SELECT product_id, product_name, stock_quantity AS recorded_stock, is_available
FROM public.products
WHERE stock_quantity < 0
ORDER BY stock_quantity, product_id;
