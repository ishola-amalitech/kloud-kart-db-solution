\set ON_ERROR_STOP on

-- Pass the required cutoff with psql -v report_cutoff='YYYY-MM-DD HH:MM:SS'.
-- The script only reads order headers. A read-only transaction also prevents
-- accidental writes if someone edits the query while investigating the results.
BEGIN TRANSACTION READ ONLY;

WITH eligible_orders AS (
    SELECT o.order_id, o.customer_id, o.order_date, o.total_amount
    FROM public.orders AS o
    -- Quote the psql variable as a SQL string and cast it to the column's
    -- timestamp type. Both values use the same wall clock. The strict < keeps
    -- orders placed exactly at the cutoff out of this run.
    WHERE o.order_date < :'report_cutoff'::timestamp without time zone
      -- Trim surrounding spaces and ignore case. Treat both spellings as
      -- completed under the lab's proposed reporting rule.
      AND lower(btrim(o.status)) IN ('complete', 'completed')
    -- The order header already gives us one row per order and its recorded
    -- amount. Skipping joins here retains orders with missing customer records
    -- for a later exception total and avoids duplicating orders with many items.
)
SELECT order_id, customer_id, order_date, total_amount
FROM eligible_orders
-- Sort this standalone extract for review. Later report stages can use the
-- CTE without sorting the individual orders first.
ORDER BY order_date, order_id;

COMMIT;
