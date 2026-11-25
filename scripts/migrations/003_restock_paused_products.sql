-- Phase 3 adds an audited way to receive stock while a product is paused.
-- Migration 001 supplies the availability flag and the inventory-log ID default.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout = '5s';

CREATE FUNCTION public.restock_paused_product(
    p_product_id integer,
    p_quantity integer,
    p_reason text
) RETURNS integer
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
DECLARE
    v_log_id integer;
BEGIN
    -- A restock records units received, so the delta must be positive.
    -- Unknown or negative balances belong in the stocktake workflow.
    IF p_quantity IS NULL OR p_quantity <= 0
       OR p_reason IS NULL OR btrim(p_reason) = '' THEN
        RAISE EXCEPTION 'Supply a positive restock quantity and a reason';
    END IF;

    -- Add to the current balance under the row lock. Use bigint for the
    -- range check so an oversized sum fails cleanly before assignment.
    UPDATE public.products AS p
    SET stock_quantity = p.stock_quantity + p_quantity
    WHERE p.product_id = p_product_id
      AND NOT p.is_available
      AND p.stock_quantity >= 0
      AND p.stock_quantity::bigint + p_quantity <= 2147483647;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Product % must be paused with a known balance and room for the added quantity',
            p_product_id;
    END IF;

    -- A failed log insert rolls back the increment. Availability only
    -- changes if the caller explicitly resumes sales in its transaction.
    INSERT INTO public.inventory_log (product_id, change_qty, reason, created_at, note)
    VALUES (
        p_product_id, p_quantity, 'restock', statement_timestamp() AT TIME ZONE 'UTC',
        format('Restocked by %s. %s', current_user, btrim(p_reason))
    )
    RETURNING log_id INTO v_log_id;

    RETURN v_log_id;
END;
$function$;

COMMIT;
