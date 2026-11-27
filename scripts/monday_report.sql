-- The caller sets kloud_kart.report_cutoff inside a read-only transaction.
-- Keep this query usable both inside COPY TO STDOUT and directly in psql.
WITH report_period AS (
    SELECT
        cutoff,
        (date_trunc('month', cutoff) - INTERVAL '12 months')::date AS first_complete_month
    FROM (
        VALUES (current_setting('kloud_kart.report_cutoff')::timestamp without time zone)
    ) AS input(cutoff)
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
), complete_months AS (
    -- Generate exactly 12 month starts, beginning at the first complete month.
    -- Month arithmetic uses the same calendar as the Stage 2 buckets.
    SELECT (p.first_complete_month + m.offset_months * INTERVAL '1 month')::date AS month_start
    FROM report_period AS p
    CROSS JOIN generate_series(0, 11) AS m(offset_months)
), ranked_customers AS (
    -- Rank the customer totals once, before expanding to one row per month.
    -- A customer needs a recorded spend amount to rank. Zero spend on an
    -- eligible order is a valid amount. Customer ID breaks exact ties.
    SELECT
        customer_id,
        order_count,
        recorded_spend,
        CASE WHEN recorded_spend IS NOT NULL THEN
            ROW_NUMBER() OVER (ORDER BY recorded_spend DESC NULLS LAST, customer_id)
        END AS spend_rank
    FROM spend_groups
    WHERE is_customer_total AND customer_id IS NOT NULL
), monthly_activity AS (
    -- Begin with every customer and month so a missing subtotal becomes a
    -- zero-activity row. Keep a NULL spend when there were orders but all
    -- their amounts were missing, as in the Stage 2 subtotal.
    SELECT
        c.customer_id,
        m.month_start,
        COALESCE(g.order_count, 0) AS order_count,
        CASE WHEN g.order_count IS NULL THEN 0 ELSE g.recorded_spend END AS recorded_spend
    FROM public.customers AS c
    CROSS JOIN complete_months AS m
    LEFT JOIN spend_groups AS g
        ON g.customer_id = c.customer_id
        AND g.bucket_month = m.month_start
        AND NOT g.is_customer_total
), monthly_running AS (
    -- Restart the sum for each customer. The explicit frame adds this month
    -- to all previous visible months, leaving one row per customer and month.
    SELECT
        customer_id,
        month_start,
        order_count,
        recorded_spend,
        SUM(recorded_spend) OVER (
            PARTITION BY customer_id
            ORDER BY month_start
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS spend_in_window_to_date
    FROM monthly_activity
), report_rows AS (
    -- Keep the overall spend and top-100 position on one summary row per
    -- customer. They would be counted repeatedly if copied to monthly rows.
    SELECT
        'customer_total' AS row_type,
        c.customer_id,
        -- Spreadsheet apps can evaluate formula-like names on CSV import.
        -- Prefix those values as text while keeping ordinary names intact.
        CASE
            WHEN c.full_name ~ '^[[:space:]]*[=+@-]'
                OR left(c.full_name, 1) IN (chr(9), chr(10), chr(13))
                THEN chr(39) || c.full_name
            ELSE c.full_name
        END AS customer_name,
        NULL::date AS month_start,
        COALESCE(r.order_count, 0) AS order_count,
        CASE WHEN r.order_count IS NULL THEN 0 ELSE r.recorded_spend END AS recorded_spend,
        -- The summary running balance ends at the report cutoff, including
        -- eligible spend in the partial month after the last displayed month.
        COALESCE(r.recorded_spend, 0) AS running_spend,
        CASE WHEN r.spend_rank <= 100 THEN r.spend_rank END AS top_100_rank
    FROM public.customers AS c
    LEFT JOIN ranked_customers AS r USING (customer_id)

    UNION ALL

    -- Add the earlier-spend bucket to the running sum. The cutoff month's
    -- partial spend contributes to the summary only, outside the month grid.
    SELECT
        'month',
        m.customer_id,
        NULL,
        m.month_start,
        m.order_count,
        m.recorded_spend,
        COALESCE(opening.recorded_spend, 0) + COALESCE(m.spend_in_window_to_date, 0),
        NULL
    FROM monthly_running AS m
    LEFT JOIN spend_groups AS opening
        ON opening.customer_id = m.customer_id
        AND opening.bucket_month IS NULL
        AND NOT opening.is_customer_total

    UNION ALL

    -- Keep the exception available for reconciliation without assigning its
    -- spend to a customer or a ranking position.
    SELECT 'unmatched_orders', NULL, NULL, NULL, order_count, recorded_spend, NULL, NULL
    FROM spend_groups
    WHERE customer_id IS NULL
)
SELECT
    row_type, customer_id, customer_name, month_start,
    order_count, recorded_spend, running_spend, top_100_rank
FROM report_rows
-- A NULL month places the summary first, and the missing customer ID puts
-- the exception row after all customers. The CSV loader validates this
-- customer order so the app can stop scanning once it passes the desired ID.
ORDER BY customer_id NULLS LAST, month_start NULLS FIRST;
