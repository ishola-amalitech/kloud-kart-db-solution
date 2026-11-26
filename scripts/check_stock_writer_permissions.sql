-- Read-only cutover check. Run against the intended database for each actual
-- checkout, operator, and reporting login. All bypass columns should be false.
-- Example: psql -X -v login=shop_checkout -f scripts/check_stock_writer_permissions.sql
\set ON_ERROR_STOP on
SELECT rolname,
       rolsuper OR rolcreaterole AS privileged_role,
       has_schema_privilege(oid, 'public', 'CREATE') AS can_create_in_public,
       has_any_column_privilege(oid, 'public.products', 'INSERT, UPDATE')
           OR has_table_privilege(oid, 'public.products', 'DELETE, TRUNCATE, TRIGGER') AS direct_product_write,
       has_any_column_privilege(oid, 'public.inventory_log', 'INSERT, UPDATE')
           OR has_table_privilege(oid, 'public.inventory_log', 'DELETE, TRUNCATE, TRIGGER') AS direct_log_write,
       has_any_column_privilege(oid, 'public.orders', 'INSERT, UPDATE')
           OR has_any_column_privilege(oid, 'public.order_items', 'INSERT, UPDATE')
           OR has_table_privilege(oid, 'public.orders', 'DELETE, TRUNCATE, TRIGGER')
           OR has_table_privilege(oid, 'public.order_items', 'DELETE, TRUNCATE, TRIGGER') AS direct_order_write,
       has_function_privilege(oid, 'public.place_order(bigint,bigint,integer)', 'EXECUTE') AS checkout,
       has_function_privilege(oid, 'public.record_stock_movement(integer,text,integer,text,integer)', 'EXECUTE')
           AS stock_movement,
       has_function_privilege(oid, 'public.set_product_sale_availability(integer,boolean)', 'EXECUTE') AS availability,
       has_function_privilege(oid, 'public.reconcile_negative_stock(integer,integer,integer,text)', 'EXECUTE')
           OR has_function_privilege(oid, 'public.restock_paused_product(integer,integer,text)', 'EXECUTE')
           AS legacy_stock_operation,
       has_table_privilege(oid, 'public.inventory_movement_history', 'SELECT') AS movement_history
FROM pg_catalog.pg_roles
WHERE rolname = :'login';

-- Review roles the login can switch into, including roles that do not
-- automatically inherit privileges. Check alternate writers and definer
-- functions separately before retiring privileged application credentials.
SELECT parent.rolname AS switchable_role
FROM pg_catalog.pg_roles AS login_role
CROSS JOIN pg_catalog.pg_roles AS parent
WHERE login_role.rolname = :'login' AND parent.oid <> login_role.oid
  AND pg_has_role(login_role.oid, parent.oid, 'SET')
ORDER BY parent.rolname;
