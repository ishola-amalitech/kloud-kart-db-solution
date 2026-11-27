# ADR-009: Group inventory movements by product before comparing stock balances

**Status:** Accepted · **Date:** 2026-11-27 · **Deciders:** Lab implementation team

[Stage 006 · Performance measurements](../006_performance.md)

## Context

Nana uses the discrepancy worklist to decide which products to count first.
The worklist needs current balances and includes older records that may lack
a verified physical count. Processing every movement across the joins
made the worklist slower as history grew.

## Decision

Group eligible movements by product before joining the totals to the current
stock record. Preserve the comparison rules and output columns. Compare the
old and new results in both directions before rolling out migration 006.

## Alternatives considered

- **Keep the original query.** This avoids deployment work and preserves a
  simpler expression of the original joins, at the cost of processing more
  intermediate rows on each read.
- **Store a refreshed discrepancy list.** Repeated reads could be faster.
  Between refreshes, the list could miss an adjustment or a completed sale
  that changes Nana's counting priorities.

## Consequences

Median execution fell from 201.191 ms to 54.375 ms on the seeded test
database. The two-way comparison found no changed rows. The change still
reads movement history when the worklist is opened. Migration 006 acquires
a lock to replace the view, so deploy during an approved window and check
the worklist immediately afterwards.
[Before and after plans](../query_plans/006_inventory_discrepancy_plans.txt)

## Revisit when

Remeasure when the movement ledger grows materially or operators experience
delays opening the worklist. Use current production volumes and measure the
freshness and maintenance costs before choosing a stored list.
