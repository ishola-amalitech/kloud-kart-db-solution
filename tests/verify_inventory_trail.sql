-- Run as the test administrator. Fixtures and login roles are rolled back.
-- Identity sequences may advance. Never run fixture-writing tests on production.
\set ON_ERROR_STOP on
BEGIN;

CREATE ROLE kk_trail_fixture_operator LOGIN;
CREATE ROLE kk_trail_fixture_checkout LOGIN;
CREATE ROLE kk_trail_fixture_auditor LOGIN;
GRANT kk_stock_operator TO kk_trail_fixture_operator;
GRANT kk_checkout TO kk_trail_fixture_checkout;
GRANT kk_reporting TO kk_trail_fixture_auditor;

INSERT INTO public.customers (customer_id, full_name)
VALUES (2147483001, 'Trail test');
INSERT INTO public.products (product_id, product_name, price, stock_quantity, is_available)
VALUES (2147483001, 'Trail test', 10, 4, true),
       (2147483002, 'Trail test unknown count', 10, NULL, false);

SET SESSION AUTHORIZATION kk_trail_fixture_operator;
DO $test$
BEGIN
    BEGIN
        UPDATE public.products SET stock_quantity = 100 WHERE product_id = 2147483001;
        RAISE EXCEPTION 'Direct stock edit was allowed';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    BEGIN
        PERFORM public.place_order(2147483001, 2147483001, 1);
        RAISE EXCEPTION 'Operator could place orders';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;

    PERFORM public.set_product_sale_availability(2147483001, false);
    PERFORM public.record_stock_movement(2147483001, 'stock_take', 4, 'count #1', 4);
    PERFORM public.record_stock_movement(2147483001, 'restock', 3, 'delivery #1');
    PERFORM public.record_stock_movement(2147483001, 'return', 2, 'returned #1');
    PERFORM public.record_stock_movement(2147483001, 'damage', 2, 'damage #1');
    PERFORM public.record_stock_movement(2147483002, 'stock_take', 3, 'initial count');

    -- Force a failure after the first write in a batch. A caught SQL
    -- subtransaction error rolls back both the earlier balance and log.
    BEGIN
        PERFORM public.record_stock_movement(2147483001, 'restock', 1, 'batch to roll back');
        PERFORM public.record_stock_movement(2147483001, 'damage', 9, 'invalid batch');
        RAISE EXCEPTION 'Invalid batch succeeded' USING ERRCODE = 'XX000';
    EXCEPTION WHEN raise_exception THEN NULL;
    END;
    IF (SELECT stock_quantity FROM public.products WHERE product_id = 2147483001) <> 7
       OR EXISTS (SELECT FROM public.inventory_movement_history WHERE note = 'batch to roll back') THEN
        RAISE EXCEPTION 'Failed batch left a stock or movement write behind';
    END IF;

    BEGIN
        PERFORM public.record_stock_movement(2147483001, 'stock_take', 8, 'stale count', 4);
        RAISE EXCEPTION 'Stale count succeeded' USING ERRCODE = 'XX000';
    EXCEPTION WHEN raise_exception THEN NULL;
    END;
    PERFORM public.set_product_sale_availability(2147483001, true);

    BEGIN
        DELETE FROM public.inventory_log WHERE product_id = 2147483001;
        RAISE EXCEPTION 'Operator could delete a movement';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$test$;

RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION kk_trail_fixture_checkout;
DO $test$
BEGIN
    IF (SELECT success FROM public.place_order(2147483001, 2147483001, 2)) IS DISTINCT FROM true
       OR (SELECT success FROM public.place_order(2147483001, 2147483001, 6)) IS DISTINCT FROM false THEN
        RAISE EXCEPTION 'Unexpected checkout result';
    END IF;
    BEGIN
        INSERT INTO public.inventory_log (log_id) VALUES (2147483001);
        RAISE EXCEPTION 'Checkout could insert log rows directly';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
    BEGIN
        PERFORM public.record_stock_movement(2147483001, 'restock', 1, 'unauthorized');
        RAISE EXCEPTION 'Checkout could restock';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$test$;

RESET SESSION AUTHORIZATION;
SET SESSION AUTHORIZATION kk_trail_fixture_auditor;
DO $test$
BEGIN
    IF (SELECT count(*) FROM public.inventory_movement_history WHERE product_id = 2147483001) <> 5
       OR NOT EXISTS (
           SELECT FROM public.inventory_movement_history
           WHERE product_id = 2147483001 AND reason = 'stock_take' AND change_qty = 0
             AND stock_after = 4 AND balance_from_count = 4 AND recorded_by = 'kk_trail_fixture_operator'
       ) OR NOT EXISTS (
           SELECT FROM public.inventory_movement_history
           WHERE product_id = 2147483001 AND reason = 'sale' AND change_qty = -2
             AND stock_before = 7 AND stock_after = 5 AND balance_from_count = 5
             AND order_id IS NOT NULL AND order_item_id IS NOT NULL
             AND recorded_by = 'kk_trail_fixture_checkout'
       ) OR NOT EXISTS (
           SELECT FROM public.inventory_movement_history
           WHERE product_id = 2147483002 AND reason = 'stock_take' AND change_qty IS NULL
             AND stock_before IS NULL AND stock_after = 3 AND balance_from_count = 3
       ) OR EXISTS (
           SELECT FROM public.inventory_discrepancy_worklist WHERE product_id IN (2147483001, 2147483002)
       ) THEN
        RAISE EXCEPTION 'Movement trail or replay does not match the fixture';
    END IF;
    BEGIN
        PERFORM public.set_product_sale_availability(2147483001, false);
        RAISE EXCEPTION 'Auditor could pause sales';
    EXCEPTION WHEN insufficient_privilege THEN NULL;
    END;
END;
$test$;

RESET SESSION AUTHORIZATION;
ROLLBACK;
\echo Inventory trail and role checks passed.
