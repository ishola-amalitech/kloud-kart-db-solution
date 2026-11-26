# ADR-002: Use READ COMMITTED and a conditional stock update for checkout

**Status:** Accepted · **Date:** 2026-11-25 · **Deciders:** Lab implementation team

[Stage 002 · Stock reconciliation and safe checkout](../002_stock_rollout.md)

## Context

The lab requires exactly one of two competing buyers to get the last unit and a clear out-of-stock response for the other. Kloud Kart expects up to 400 attempts in ten seconds for one product, and the supplied race harness must run unchanged.

## Decision

Run checkout at PostgreSQL's `READ COMMITTED` isolation level. Use one conditional `UPDATE … RETURNING` to deduct stock only when the product is available and has enough units. PostgreSQL locks the row for the update and rechecks the condition after a competing writer commits. If the sale cannot claim stock, return a clean out-of-stock or unavailable result. Keep the caller's transaction short and commit promptly.

## Alternatives considered

- **Read the balance with an ordinary SELECT, then update it.** Two buyers could both act on the same balance before either deducted stock.
- **Use READ COMMITTED with SELECT … FOR UPDATE before the deduction.** This can also prevent overselling. The conditional update combines the check and deduction in one statement for the current single-product checkout.
- **Use REPEATABLE READ or SERIALIZABLE.** These can require retrying the transaction when concurrent buyers update the same product. The current checkout needs a clean refusal once stock runs out and can achieve that with the conditional update.

## Consequences

Sales of one product still queue briefly behind its row lock. Different products can be updated independently. The supplied test recorded 25 successful last-unit races. In the test environment, the simultaneous 400-buyer burst produced 50 sales, 350 refusals, and zero errors in 1.67 seconds. The caller must preserve the isolation level and avoid holding the transaction open.

## Revisit when

Revisit if a race produces more than one winner for the last unit, if lock waits prevent the paced test from completing 400 attempts within ten seconds, or if checkout begins enforcing rules across multiple products.
