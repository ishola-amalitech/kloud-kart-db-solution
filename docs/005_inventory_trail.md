# Leave the trail

Rollout and operating guide for migration 005 and the Inventory trail tab.

- [Review and recount stock](#review-and-recount-stock)
- [Follow a movement](#follow-a-movement)
- [Rehearse on test](#rehearse-on-test)
- [Production cutover](#production-cutover)
- [If cutover fails](#if-cutover-fails)
- [Historical evidence](#historical-evidence)
- [Related decisions](#related-decisions)

## Review and recount stock

Open **Inventory trail → Products to recount → Load or refresh discrepancies** in the app. The worklist shows `stock_quantity` and `comparison_stock`. If `verified_count_id` is blank, `comparison_stock` equals `logged_net`, the sum of known quantity changes. An unknown opening balance or missing history can explain a difference. Once a verified count exists, `verified_count_id` is that entry’s `log_id`, and `comparison_stock` uses the counted quantity plus movements recorded after it.

- **Possible oversell** means the product is on sale, its saved balance is positive, and the comparison suggests fewer units. Review these rows first. A discrepancy by itself changes neither the balance nor availability.
- **Low-stock status differs** means one quantity is within the 0–5 unit range and the other is outside it. This worklist compares quantities without considering availability. The operational Low stock tab still applies its own availability rule.
- Select products on the current page and click **Pause selected on-sale products** when you need to hold sales for a count. Go to **Stock reconciliation → Paused → Record physical counts**. Stop physical and database movements for those products while counting, then enter the verified quantities and stocktake reference. Resume sales separately when ready. The app rejects a count if the saved database balance changed since review. Pausing removes the product from **Possible oversell** while leaving it in **All** until you resolve the discrepancy. After saving a verified count, refresh the list. If both balances agree, the product leaves the worklist.

Use **Receive delivery**, **Return to sellable stock**, and **Record damage** under **Stock reconciliation** to log other movements. Use a delivery, return, or damage reference that someone can look up. A verified count that agrees with the saved balance records a zero-change `stock_take`. If the prior balance was unknown, the count establishes it and the change is left blank.

Each stock operation locks the product, updates the balance, and inserts the movement in one transaction. The app returns the movement IDs and rolls back the entire selection if any operation fails. Checkout keeps its conditional decrement and its exact link to the order line. Pausing or resuming without a quantity change updates availability only.

## Follow a movement

In **Inventory trail → Movement history**, enter a product ID to browse its history or a movement ID to verify one entry. New entries show the balance before and after, the database login, and the order or source reference where applicable. The replayed balance is calculated from the most recent verified count at or before each entry. Older entries without a verified count have a blank replayed balance.

For an auditor working in SQL, the same read-only views are available.

```sql
SELECT product_id, stock_quantity, comparison_stock, verified_count_id,
       possible_oversell, low_stock_differs
FROM public.inventory_discrepancy_worklist
ORDER BY possible_oversell DESC, low_stock_differs DESC, product_id;

SELECT log_id, created_at, reason, change_qty, stock_before, stock_after,
       balance_from_count, recorded_by, order_id, order_item_id, note, product_missing
FROM public.inventory_movement_history
WHERE product_id = 5068
ORDER BY log_id DESC
LIMIT 25;
```

## Rehearse on test

Run from the repository root against the seeded **test** database. For a fresh replay, follow the [stock rollout](002_stock_rollout.md#rehearse-from-an-empty-test-database) through migration 003, then apply [migration 004](004_low_stock_rollup.md) before migration 005.

```bash
uv run kk-test-db
test_psql() {
    uv run kk-test-db exec -T postgres sh -ec \
        'exec psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}

# Apply once to a database already on 004.
test_psql < scripts/migrations/005_inventory_trail.sql

# Run in the test database. Fixtures and test roles roll back.
test_psql < tests/verify_inventory_trail.sql

uv run kk-app
```

The role test exercises checkout, stocktake, delivery, sellable return, damage, a failed batch, and direct-write denial. Rerun [both checkout suites](002_stock_rollout.md#run-the-checkout-tests) with the test administrator connection. The suites create and remove fixtures. The supplied 400-connection case needs temporary connection headroom on test.

The seeded test database returned **144** products for review, including **21** flagged for possible oversell and **38** where the low-stock status differs. The original audit's **30** low-stock changes came from the pre-reconciliation data. The current worklist compares the balances and movements after the earlier test corrections. The review criteria identify possible problems for a recount.

In the rehearsal, all four supplied race tests passed. The 400-buyer burst sold 50 units, refused 350, and returned zero errors. The audit case recorded five movements for five successful purchases. Both paced scenarios completed all 400 attempts within ten seconds and passed the persisted balance and movement checks. The test connection limit was restored to 100. Streamlit verification covered the discrepancy filter, an orphan lookup, and saving a count and a delivery.

## Production cutover

The checked-in operational writers are checkout and the Streamlit reconciliation actions. The supplied import creates the initial snapshot, and the test suites write disposable fixtures with administrator access. Treat production jobs and integrations as separate writers to verify before switching credentials.

### 1. Prepare the cutover

- Confirm migrations 001–004 are applied, the latest backup can be restored, and the test rehearsal has passed. Use the production connection approved for the deployment.
- List the services, import jobs, and scripts that write stock or orders, along with the database login each uses. Map checkout to `place_order` and operator actions to the approved stock functions. Pause checkout and warehouse writes for the change. Keep any writer paused if it still needs a direct table edit.

### 2. Apply migration 005 and deploy the app

Apply [migration 005](../scripts/migrations/005_inventory_trail.sql) once. Run it as the trusted deployment role that can alter the existing functions and tables and provision the group roles. Keep the functions' ownership with a trusted role that has the privileges they need. The migration runs in one transaction, takes table locks, and builds two history indexes. It aborts if it waits more than five seconds to acquire a lock. Use the test rehearsal to estimate the maintenance window.

From the repository root, with the production deployment connection already configured for `psql`, confirm the target and then apply the file.

```bash
psql -X -c 'SELECT current_database(), current_user, inet_server_addr(), inet_server_port();'
psql -X -v ON_ERROR_STOP=1 -f scripts/migrations/005_inventory_trail.sql
```

### 3. Assign and check the real logins

The migration creates permission groups. Assign them to the existing service or staff logins. Use individual operator logins where possible. With a shared login, use the movement reference to identify the operator in the source record.

| Login | Grant | Expected access |
| --- | --- | --- |
| Checkout service | `kk_checkout` | Call `place_order`. |
| Warehouse operator | `kk_stock_operator` | Record stock movements, change availability, and read product and movement history. |
| Analyst or auditor | `kk_reporting` | Read reporting and inventory history. |
| Streamlit operator who also runs Monday exports | `kk_stock_operator` and `kk_reporting` | Perform operator actions and use the reporting tabs. |

For example, if the checkout login is named `shop_checkout`, run `GRANT kk_checkout TO shop_checkout;` as the deployment administrator. Use the actual login names for the other grants. Set the app's `DATABASE_URL` through the approved secret store and point checkout at its separate login.

Run the read-only permission check for each login.

```bash
psql -X -v login=shop_checkout -f scripts/check_stock_writer_permissions.sql
```

Expect one row for the login. Direct product, log, and order writes, public schema creation, privileged roles, and legacy stock-function access should all be false. Match the permitted functions and read access to the table above. Review any roles the login can switch into and remove old grants that would let it bypass the approved functions. Keep administrator and table-owner credentials out of checkout and Streamlit.

### 4. Verify and reopen

- Before reopening writes, verify that the deployed checkout calls `place_order` and the Streamlit connection can open the inventory worklist and history under its operator login. Confirm that the auditor can read history under the reporting login.
- Reopen the approved paths. For the first real sale and stock movement, verify that the new history entries match the quantities and references. The sale should link to its order item. Run the fixture-writing tests on test only.
- Load the production discrepancy worklist and assign the products flagged for possible overselling to be reviewed first. Pause products individually or in selected batches. Use verified physical quantities for corrections.

## If cutover fails

If the migration fails inside its transaction, keep writers paused, diagnose the error, and retry after resolving it. If it commits but a login or workflow check fails, keep the affected writer paused and correct the credentials, grants, or routing. An app deployment rollback must preserve movement history and keep the stock-writing path compatible with migration 005. The older reconciliation app calls functions that the restricted roles can no longer use.

## Historical evidence

The migration preserves the **262,088** imported sale movements without verified purchase links, the **120** movements referencing **111** missing products, and the **90** zero-change `stock_take` entries. It leaves missing references visible in history. For example, movement **982278** still shows missing product **99958**. The product foreign key applies to new references while allowing historical orphans to remain under review. Recover old links only from evidence such as receipts, fulfillment records, and backups. Keep the original entries and their timestamps. New movements record UTC in the existing timestamp format.

## Related decisions

- [ADR-006 · Route stock changes through database functions and restrict direct writes](adr/005-adr-006-controlled-stock-writes.md)
- [ADR-007 · Compare recorded stock with movements since a verified physical count](adr/005-adr-007-verified-stock-counts.md)
