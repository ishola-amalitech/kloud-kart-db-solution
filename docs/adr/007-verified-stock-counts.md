# ADR-007: Compare recorded stock with movements since a verified physical count

**Status:** Accepted · **Date:** 2026-11-26 · **Deciders:** Lab implementation team

## Context

The seeded test database showed **144 products** with discrepancies.
**21** were still on sale and had a positive recorded balance higher than
the movement-based comparison. The old log can help decide what to count
first, but it may be missing an opening balance or movements. [Inventory trail rollout](../inventory_trail.md#rehearse-on-test)

## Decision

For a physical count, pause sales and hold warehouse movements while the
operator counts the units. Save the actual quantity and stocktake reference
as a `stock_take` entry. Record it even when the quantity stays the same.
The app checks that the database balance still matches the one reviewed
before saving. The entry's `log_id` becomes the `verified_count_id`.

Compare `products.stock_quantity` with `comparison_stock` in the discrepancy
worklist.

- Before a verified count, `comparison_stock` uses `logged_net`, the sum of
  known quantity changes. Treat the result as a clue for prioritizing recounts.
- After a verified count, `comparison_stock` uses the counted quantity plus
  later logged changes. Use the difference to spot a new mismatch.

Movement history shows a replayed balance only from a verified count
onward. For example, after counting eight units and selling two, the
expected balance is six.

## Alternatives considered

| Alternative | Trade-off |
| --- | --- |
| Always compare stock with the sum of every old movement | Simple, but a missing opening balance can keep a corrected product on the worklist. |
| Show nothing until a product has been physically counted | Avoids rough estimates, but removes a way to prioritize the first recounts. |
| Use the saved database balance as the starting quantity | Avoids a physical count, but carries any existing error into the replay. |

## Consequences

A product leaves the worklist once its saved and comparison balances agree.
If saved stock later changes without a corresponding movement, it returns
to the list. A mismatch calls for investigation. The comparison depends on
the accuracy of the physical count and the movements recorded after it.

## Revisit when

Revisit if repeat physical counts frequently disagree with balances
replayed from verified counts, or if dated and verified opening balances
become available for the historical products.
