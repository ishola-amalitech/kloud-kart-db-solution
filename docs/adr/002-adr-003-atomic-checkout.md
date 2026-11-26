# ADR-003: Commit the stock deduction, order, order item, and sale movement in one transaction

**Status:** Accepted · **Date:** 2026-11-25 · **Deciders:** Lab implementation team

[Stage 002 · Stock reconciliation and safe checkout](../002_stock_rollout.md)

## Context

Nana needs an inventory trail that shows which purchase caused each stock deduction. The baseline extract contains 262,088 sale movements without an enforced link to a purchase. The supplied race harness also expects successful checkouts to leave sale movements in the log.

## Decision

Use `place_order(customer_id, product_id, quantity)` as the checkout entry point. Within the caller's transaction, claim stock, insert the order and its item, and insert the corresponding negative inventory movement linked to the item. Commit those writes together. A failed write rolls them all back. A checkout that cannot claim stock leaves no order or sale movement.

## Alternatives considered

- **Commit the stock update, order, and log separately.** A failure between commits could leave stock deducted without an order, or an order without a movement.
- **Generate the sale log with a trigger on stock changes.** A generic stock trigger sees a quantity change but needs additional context to distinguish a sale from a restock or correction and link it to the exact order item. The checkout already has that context.

## Consequences

An operator can trace each new sale movement to its purchase, and a failed log write cannot leave a partial checkout. A test that forced the movement insert to fail confirmed that the stock deduction, order, and order item rolled back. Every sale path must use the transaction, and the product lock lasts until all writes commit.

## Revisit when

Revisit if a new sale has a missing or mismatched movement, or if checkout starts depending on a service or database outside the PostgreSQL transaction.
