\set ON_ERROR_STOP on

-- Pass report_cutoff as a local wall-clock timestamp, as in Stage 1. This
-- read-only transaction keeps the rehearsal and the report safe to rerun.
-- Inspect the subtotals separately when reconciling the full Monday report.
BEGIN TRANSACTION READ ONLY;

-- Set -v explain_plan=1 to execute the query and show its plan and timings.
\if :{?explain_plan}
EXPLAIN (ANALYZE, BUFFERS)
\endif
WITH report_period AS (
    SELECT
        cutoff,
        date_trunc('month', cutoff)::date AS current_month,
        (date_trunc('month', cutoff) - INTERVAL '12 months')::date AS first_complete_month
    FROM (VALUES (:'report_cutoff'::timestamp without time zone)) AS input(cutoff)
), eligible_orders AS (
    -- Keep the Stage 1 eligibility rule. Read order headers so each order
    -- contributes one recorded amount and one order to the count.
    SELECT o.customer_id, o.order_date, o.total_amount
    FROM public.orders AS o
    CROSS JOIN report_period AS p
    WHERE o.order_date < p.cutoff
      AND lower(btrim(o.status)) IN ('complete', 'completed')
), bucketed_orders AS (
    SELECT
        c.customer_id,
        CASE
            -- Preserve the month for the 12 complete months and the current
            -- partial month. Older orders share one opening-balance bucket.
            WHEN e.order_date >= p.first_complete_month
                THEN date_trunc('month', e.order_date)::date
            ELSE NULL
        END AS bucket_month,
        e.total_amount
    FROM eligible_orders AS e
    CROSS JOIN report_period AS p
    -- Use the ID from customers so missing and invalid references share one
    -- NULL customer group. The primary key keeps each matched order unique.
    LEFT JOIN public.customers AS c ON c.customer_id = e.customer_id
), spend_groups AS (
    SELECT
        customer_id,
        bucket_month,
        -- A total row and an earlier-spend row both show a NULL month.
        -- GROUPING returns 1 when the month was omitted from the grouping
        -- level and 0 when a row belongs to an actual time bucket.
        GROUPING(bucket_month) = 1 AS is_customer_total,
        COUNT(*) AS order_count,
        SUM(total_amount) AS recorded_spend
    FROM bucketed_orders
    GROUP BY GROUPING SETS ((customer_id), (customer_id, bucket_month))
    -- Keep just one exception total for unmatched orders. Their individual
    -- time buckets have no customer to report them against.
    HAVING customer_id IS NOT NULL OR GROUPING(bucket_month) = 1
)
SELECT
    CASE
        WHEN g.customer_id IS NULL THEN 'unmatched_orders'
        WHEN g.is_customer_total THEN 'customer_total'
        WHEN g.bucket_month IS NULL THEN 'earlier_spend'
        WHEN g.bucket_month = p.current_month THEN 'partial_month'
        ELSE 'month'
    END AS row_type,
    g.customer_id,
    g.bucket_month,
    g.order_count,
    g.recorded_spend
FROM spend_groups AS g
CROSS JOIN report_period AS p
-- Put each customer total before its time buckets and the exception row last.
ORDER BY g.customer_id NULLS LAST, g.is_customer_total DESC, g.bucket_month NULLS FIRST;

COMMIT;
