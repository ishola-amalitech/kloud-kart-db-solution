# ADR-005: Walk category ancestry recursively and count each product once per branch

**Status:** Accepted · **Date:** 2026-11-26 · **Deciders:** Lab implementation team

[Stage 004 · Low-stock category rollup](../004_low_stock_rollup.md)

## Context

Purchasing needs counts for both the products assigned directly to a category
and the products across its entire branch. The category tree can have any
depth, and the seed data includes a cycle and a missing parent link. A report
must finish even when a category path is broken.

## Decision

Start a recursive CTE at every category and follow its parent IDs. Keep the IDs
visited along each path. Stop at a root, a missing parent, or a repeated ID.
Flag the last two cases for review and use only valid paths for branch totals.

Count reorder candidates in their assigned category first, then add that
count once to each ancestor on the valid path, including the assigned category.
Show direct and whole-branch counts separately. For store-wide totals, count
products from the worklist, where each product appears once.

## Alternatives considered

- **Join a fixed number of levels.** This would omit descendants deeper than
  the chosen limit.
- **Walk down from the roots.** A root-first walk alone leaves disconnected
  cycles and broken parent paths unclassified.
- **Pass individual product rows up the tree.** Grouping first lets one
  category path carry the count for all candidates assigned there.

## Consequences

The report can cover deeper branches and surface broken paths. A product
contributes once to each branch it belongs to, so adding a parent branch total
to its child branch totals would double-count products. The verification SQL
covers a 12-level branch, siblings, cycles, and missing links.
[Verification SQL](../../tests/verify_low_stock_rollup.sql)

## Revisit when

Revisit if category paths can have multiple parents or if measured report
times exceed the team's target as the tree grows.
