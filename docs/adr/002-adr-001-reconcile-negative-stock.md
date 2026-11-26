# ADR-001: Pause and reconcile products with negative stock before enforcing a nonnegative balance

**Status:** Accepted · **Date:** 2026-11-25 · **Deciders:** Lab implementation team

[Stage 002 · Stock reconciliation and safe checkout](../002_stock_rollout.md)

## Context

The audit found 14 products with recorded stock from −4 to −1, together 27 units below zero. These values identified which products needed a stocktake. The extract could establish neither the physical counts nor the cause of the discrepancies. The rollout must work against Kloud Kart's existing live database.

## Decision

Pause sales for affected products while preserving their recorded balances. Coordinate stock writers during the count, then use the reconciliation function to set each balance to its verified physical count. Record the difference and a stocktake reference in the inventory log in the same transaction. Leave the product paused until an operator explicitly resumes sales.

After the negative-stock worklist is empty, add and validate `CHECK (stock_quantity >= 0)`. Checkout independently excludes unknown balances. In the test rehearsal, use explicitly labeled simulated counts of zero. Production corrections require verified counts.

## Alternatives considered

- **Set every negative balance to zero.** This would clear the worklist immediately but could overwrite real stock without establishing a reliable count.
- **Validate the stock constraint before reconciliation.** Validation would fail against the 14 existing negative balances.
- **Keep affected products available while investigating.** Sales or other stock movements during the count could make the correction stale.

## Consequences

The worklist and movement log make each correction traceable. The constraint protects against new negative balances, and affected products are temporarily unavailable. Operators must coordinate stocktakes, supply the verified counts, and explicitly release reconciled products.

## Revisit when

Revisit the rollout if one or more products remain unresolved at the end of the approved maintenance window, or if a stocktake finds a discrepancy between the verified count and a reconciled balance.
