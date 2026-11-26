-- Install after 001–004. Run as a trusted, non-login deployment owner where
-- possible. The definer functions retain that owner's privileges at runtime.
-- Rehearse grants and lock times before the production maintenance window.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';

-- Credentials belong to separate LOGIN roles. These reusable group roles own
-- no objects. Inspect existing memberships before reuse on a shared cluster.
DO $roles$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'kk_checkout') THEN
        CREATE ROLE kk_checkout NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'kk_stock_operator') THEN
        CREATE ROLE kk_stock_operator NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'kk_reporting') THEN
        CREATE ROLE kk_reporting NOLOGIN;
    END IF;
END;
$roles$;

-- Imported movements lack reliable before/after counts and actors. Keep them
-- as-is. A NOT VALID foreign key enforces new product links while preserving
-- existing orphan movements. The index also serves per-product history.
ALTER TABLE public.inventory_log
    ADD COLUMN stock_before integer,
    ADD COLUMN stock_after integer,
    ADD COLUMN recorded_by name,
    ADD CONSTRAINT inventory_log_product_fk FOREIGN KEY (product_id)
        REFERENCES public.products (product_id) NOT VALID,
    ADD CONSTRAINT inventory_log_recorded_balance CHECK (
        recorded_by IS NULL OR (
            product_id IS NOT NULL AND created_at IS NOT NULL
            AND reason IS NOT NULL AND stock_after IS NOT NULL AND stock_after >= 0
            AND CASE WHEN stock_before IS NULL THEN reason = 'stock_take' AND change_qty IS NULL
                     ELSE change_qty IS NOT NULL AND stock_after::bigint - stock_before = change_qty
                END
        )
    ) NOT VALID;

CREATE INDEX inventory_log_product_log_idx ON public.inventory_log (product_id, log_id);
CREATE INDEX inventory_log_verified_count_idx ON public.inventory_log (product_id, log_id DESC)
    WHERE reason = 'stock_take' AND stock_after IS NOT NULL;

-- Use one operation for deliveries, sellable returns, damage, and verified
-- counts. A count replaces the balance; the other operations apply a delta.
-- Lock the product before reading its balance, and retain that lock until the
-- movement commits. A count uses the observed database balance to detect a
-- stale worklist. The operator must also hold warehouse movements during counting.
CREATE FUNCTION public.record_stock_movement(
    p_product_id integer,
    p_reason text,
    p_units integer,
    p_reference text,
    p_expected_stock integer DEFAULT NULL
) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    v_before integer;
    v_available boolean;
    v_after bigint;
    v_log_id integer;
BEGIN
    IF p_reason IS NULL OR p_reason NOT IN ('restock', 'return', 'damage', 'stock_take')
       OR p_units IS NULL OR p_units < 0
       OR (p_reason <> 'stock_take' AND p_units = 0)
       OR p_reference IS NULL OR btrim(p_reference) = '' THEN
        RAISE EXCEPTION 'Supply a valid movement, quantity, and reference';
    END IF;

    SELECT p.stock_quantity, p.is_available INTO v_before, v_available
    FROM public.products AS p
    WHERE p.product_id = p_product_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Product % does not exist', p_product_id;
    END IF;

    IF p_reason = 'stock_take' THEN
        IF v_available THEN
            RAISE EXCEPTION 'Pause product % before recording a verified count', p_product_id;
        END IF;
        IF v_before IS DISTINCT FROM p_expected_stock THEN
            RAISE EXCEPTION 'Product % balance changed. Refresh and verify the count', p_product_id;
        END IF;
        v_after := p_units;
    ELSE
        IF v_before IS NULL OR v_before < 0 THEN
            RAISE EXCEPTION 'Product % needs a verified count first', p_product_id;
        END IF;
        v_after := v_before::bigint + CASE WHEN p_reason = 'damage' THEN -p_units ELSE p_units END;
    END IF;

    -- The existing quantity and delta columns are integers. Calculate in
    -- bigint first so out-of-range inputs fail before either write.
    IF v_after NOT BETWEEN 0 AND 2147483647
       OR v_after - v_before NOT BETWEEN -2147483648 AND 2147483647 THEN
        RAISE EXCEPTION 'Product % movement exceeds the supported stock range', p_product_id;
    END IF;

    UPDATE public.products SET stock_quantity = v_after::integer WHERE product_id = p_product_id;

    -- For an unknown starting balance the difference remains NULL. For a
    -- verified count that matches a known balance, it is legitimately zero.
    -- SESSION_USER records the login, unlike CURRENT_USER in a definer function.
    INSERT INTO public.inventory_log (
        product_id, change_qty, reason, created_at, note, stock_before, stock_after, recorded_by
    ) VALUES (
        p_product_id, (v_after - v_before)::integer, p_reason,
        clock_timestamp() AT TIME ZONE 'UTC', btrim(p_reference),
        v_before, v_after::integer, session_user
    ) RETURNING log_id INTO v_log_id;

    RETURN v_log_id;
END;
$function$;

-- Keep the existing flag validation and let the operator invoke it without
-- granting UPDATE on products. Availability by itself is not a stock movement.
ALTER FUNCTION public.set_product_sale_availability(integer, boolean) SECURITY DEFINER;
ALTER FUNCTION public.set_product_sale_availability(integer, boolean) SET search_path = pg_catalog, pg_temp;

-- Preserve the existing checkout contract and READ COMMITTED conditional
-- UPDATE. Add structured balance and login details to the same atomic sale.
CREATE OR REPLACE FUNCTION public.place_order(
    p_customer_id bigint,
    p_product_id bigint,
    p_quantity integer
) RETURNS TABLE (success boolean, order_id bigint, message text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $function$
DECLARE
    v_price numeric;
    v_stock_after integer;
    v_total numeric;
    v_order_id integer;
    v_order_item_id integer;
    v_ordered_at timestamp := statement_timestamp() AT TIME ZONE 'UTC';
BEGIN
    -- A zero or negative quantity cannot represent a purchase. Check before
    -- touching rows, since subtracting a negative quantity would add stock.
    IF p_quantity IS NULL OR p_quantity <= 0 THEN
        RETURN QUERY SELECT false, NULL::bigint, 'Quantity must be positive'::text;
        RETURN;
    END IF;

    -- Protect the customer row from deletion during checkout. A missing
    -- customer fails before the stock row is updated.
    PERFORM 1 FROM public.customers AS c
    WHERE c.customer_id = p_customer_id
    FOR KEY SHARE;

    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::bigint, 'Customer not found'::text;
        RETURN;
    END IF;

    -- Use READ COMMITTED. If another checkout or pause holds this row, the
    -- UPDATE waits and rechecks these predicates against the committed row.
    -- A separate unlocked stock read could go stale before a later write.
    UPDATE public.products AS p
    SET stock_quantity = p.stock_quantity - p_quantity
    WHERE p.product_id = p_product_id
      AND p.is_available
      AND p.stock_quantity >= p_quantity
      AND p.price >= 0
      AND p.price <> 'NaN'::numeric
    RETURNING p.price, p.stock_quantity INTO v_price, v_stock_after;

    -- A skipped update has made no writes. Preserve the harness's expected
    -- out-of-stock wording for an ordinary checkout refusal.
    IF NOT FOUND THEN
        RETURN QUERY SELECT false, NULL::bigint, 'Out of stock or unavailable'::text;
        RETURN;
    END IF;

    -- Use the price from the row claimed by the stock update. The same price
    -- is stored on the line so later product price edits keep history intact.
    v_total := v_price * p_quantity;
    IF v_total >= 10000000000 THEN
        -- Raising an error here undoes the deduction as well. Preserve the
        -- legacy numeric(12,2) limit until prices and totals are migrated.
        RAISE EXCEPTION 'Order total exceeds the supported range';
    END IF;

    -- The caller's transaction covers all four writes. Any failure rolls back
    -- the stock decrement, the order, the line, and the movement together.
    INSERT INTO public.orders AS o (customer_id, order_date, status, total_amount)
    VALUES (p_customer_id, v_ordered_at, 'completed', v_total)
    RETURNING o.order_id INTO v_order_id;

    INSERT INTO public.order_items (order_id, product_id, quantity, unit_price)
    VALUES (v_order_id, p_product_id, p_quantity, v_price)
    RETURNING order_item_id INTO v_order_item_id;

    -- Sales are outward movements. Link the movement to the exact order item
    -- so an operator can trace the deduction back to a purchase.
    INSERT INTO public.inventory_log (
        product_id, change_qty, reason, created_at, order_item_id, stock_before, stock_after, recorded_by
    ) VALUES (
        p_product_id, -p_quantity, 'sale', clock_timestamp() AT TIME ZONE 'UTC', v_order_item_id,
        v_stock_after + p_quantity, v_stock_after, session_user
    );

    RETURN QUERY SELECT true, v_order_id::bigint, 'Order placed'::text;
END;
$function$;

-- Keep imported orphans visible. A missing product is different from a
-- product that exists but whose name was never filled in.
CREATE VIEW public.inventory_movement_history AS
SELECT log.log_id, log.product_id, product.product_name,
       product.product_id IS NULL AS product_missing,
       log.created_at, log.reason, log.change_qty, log.stock_before, log.stock_after,
       log.recorded_by, log.note, log.order_item_id, item.order_id,
       anchor.log_id AS verified_count_id,
       anchor.stock_after + (
           SELECT coalesce(sum(later.change_qty), 0)
           FROM public.inventory_log AS later
           WHERE later.product_id = log.product_id
             AND later.log_id > anchor.log_id AND later.log_id <= log.log_id
       ) AS balance_from_count
FROM public.inventory_log AS log
LEFT JOIN public.products AS product ON product.product_id = log.product_id
LEFT JOIN public.order_items AS item ON item.order_item_id = log.order_item_id
LEFT JOIN LATERAL (
    SELECT count_row.log_id, count_row.stock_after
    FROM public.inventory_log AS count_row
    WHERE count_row.product_id = log.product_id AND count_row.log_id <= log.log_id
      AND count_row.reason = 'stock_take' AND count_row.stock_after IS NOT NULL
    ORDER BY count_row.log_id DESC
    LIMIT 1
) AS anchor ON true;

-- Before a verified count, net activity is only an estimate for triage.
-- After a verified count, use the counted quantity plus later movements.
-- New writers assign log IDs while holding the product lock, so ID order
-- reflects movement order for that product even when checkouts overlap.
CREATE VIEW public.inventory_discrepancy_worklist AS
WITH latest_count AS (
    SELECT DISTINCT ON (product_id) product_id, log_id, stock_after
    FROM public.inventory_log
    WHERE reason = 'stock_take' AND stock_after IS NOT NULL
    ORDER BY product_id, log_id DESC
), totals AS (
    SELECT product.product_id, product.product_name, product.stock_quantity, product.is_available,
           count_row.log_id AS verified_count_id,
           sum(log.change_qty) AS logged_net,
           count_row.stock_after + coalesce(
               sum(log.change_qty) FILTER (WHERE log.log_id > count_row.log_id), 0
           ) AS balance_from_count
    FROM public.products AS product
    LEFT JOIN latest_count AS count_row ON count_row.product_id = product.product_id
    LEFT JOIN public.inventory_log AS log ON log.product_id = product.product_id
    GROUP BY product.product_id, count_row.log_id, count_row.stock_after
), compared AS (
    SELECT totals.*, coalesce(balance_from_count, logged_net) AS comparison_stock
    FROM totals
)
SELECT compared.*,
       coalesce(is_available AND stock_quantity > 0 AND comparison_stock < stock_quantity, false)
           AS possible_oversell,
       stock_quantity IS NOT NULL AND comparison_stock IS NOT NULL
           AND ((stock_quantity BETWEEN 0 AND 5) <> (comparison_stock BETWEEN 0 AND 5))
           AS low_stock_differs
FROM compared
WHERE stock_quantity IS NULL OR comparison_stock IS NULL OR stock_quantity <> comparison_stock;

-- Remove PUBLIC and group-role write paths. A login's other memberships,
-- direct grants, ownership, or superuser status must be checked at cutover.
-- Older invoker functions remain for migration history but ordinary roles
-- cannot call them after switching to record_stock_movement.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, TRIGGER
    ON public.products, public.inventory_log, public.orders, public.order_items
    FROM PUBLIC, kk_checkout, kk_stock_operator, kk_reporting;
REVOKE ALL ON FUNCTION public.place_order(bigint, bigint, integer),
    public.set_product_sale_availability(integer, boolean),
    public.reconcile_negative_stock(integer, integer, integer, text),
    public.restock_paused_product(integer, integer, text),
    public.record_stock_movement(integer, text, integer, text, integer)
    FROM PUBLIC, kk_checkout, kk_stock_operator, kk_reporting;

GRANT USAGE ON SCHEMA public TO kk_checkout, kk_stock_operator, kk_reporting;
GRANT EXECUTE ON FUNCTION public.place_order(bigint, bigint, integer) TO kk_checkout;
GRANT EXECUTE ON FUNCTION public.record_stock_movement(integer, text, integer, text, integer),
    public.set_product_sale_availability(integer, boolean) TO kk_stock_operator;
GRANT SELECT ON public.products, public.inventory_discrepancy_worklist,
    public.inventory_movement_history TO kk_stock_operator;
GRANT SELECT ON public.inventory_discrepancy_worklist, public.inventory_movement_history,
    public.products, public.customers, public.orders, public.low_stock_worklist,
    public.low_stock_category_rollup TO kk_reporting;

COMMIT;
