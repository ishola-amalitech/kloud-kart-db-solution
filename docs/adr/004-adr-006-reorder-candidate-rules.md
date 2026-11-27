# ADR-006: Flag available products with zero to five recorded units for reorder review

**Status:** Accepted · **Date:** 2026-11-26 · **Deciders:** Lab implementation team

[Stage 004 · Low-stock category rollup](../004_low_stock_rollup.md)

## Context

Purchasing needs a list of products to review for reordering. Paused products
may have provisional zero balances, and broken category links can hide products
that are still on sale. The diagnosis used five units as its working threshold.
[Data integrity diagnosis](../001_data_integrity_diagnosis.md)

## Decision

Flag products that are available for sale and have **0–5 recorded units**,
inclusive. Zero units on an available product also qualifies as out of stock.
Show paused products and products with unknown or negative balances in
Exceptions so operators can investigate.

Evaluate reorder eligibility independently of the category path. Otherwise
eligible products with missing or invalid category links still count toward
the store-wide reorder total and appear in Exceptions. Assign them to a branch
once their category path is valid.

Read current stock when the operator loads or refreshes the report. Refresh
immediately before placing or approving a reorder. If the refresh fails,
clear the old list and verify stock with the warehouse before buying.

## Alternatives considered

- **Flag only zero stock.** Purchasing would only see a product after its
  recorded balance had run out.
- **Include paused products in the reorder count.** A provisional zero could
  create unnecessary purchasing work before a physical count is verified.
- **Set thresholds per product.** This needs agreed inputs such as demand,
  supplier lead times, and incoming stock.

## Consequences

The seeded rehearsal flagged 1,671 candidates, including 15 with category
issues that would be missed by adding up root branch totals alone. The list
tells purchasing what to review. The purchase quantity still depends on
demand, supplier lead times, and stock already on order.
[Seeded test results](../004_low_stock_rollup.md#seeded-test-results)

## Revisit when

Revisit if products regularly run out before a reorder arrives or purchasing
repeatedly dismisses candidates because they have sufficient cover. Use demand
and lead-time data to test product-specific thresholds.
