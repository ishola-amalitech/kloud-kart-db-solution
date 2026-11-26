# ADR-007: Route stock changes through database functions and restrict direct writes

**Status:** Accepted · **Date:** 2026-11-26 · **Deciders:** Lab implementation team

[Stage 005 · Inventory trail rollout](../005_inventory_trail.md)

## Context

Checkout already saved the stock deduction, order, and movement together. A
direct edit to the product balance could still bypass the movement log. The
audit found **144 products** whose recorded balances differed from their net
logged movements. Unknown opening balances and gaps in the old history make
it hard to explain those differences. [Data integrity diagnosis](../001_data_integrity_diagnosis.md)

## Decision

Keep checkout on `place_order` and route deliveries, sellable returns, damage,
and verified counts through `record_stock_movement`. The functions update
stock and insert a movement in one transaction. New entries capture the
quantity before and after, the reason, and the database login. A sale links
to its order item. An operator movement carries a source reference.
Availability-only changes leave stock untouched and create no movement.

Grant `kk_checkout` to the checkout login, `kk_stock_operator` to warehouse
operator logins, and `kk_reporting` to reporting and auditor logins. Give
checkout and operators permission to call their approved functions without
granting direct edits to stock or the movement log. Check each production
login for other grants or role memberships before opening writes. [Inventory trail rollout](../005_inventory_trail.md#production-cutover)

## Alternatives considered

| Alternative | Trade-off |
| --- | --- |
| Write the log from the app | A direct SQL edit or another app could skip it. Each writer would need to implement and maintain the same transaction rules. |
| Use a trigger to capture every stock update | A trigger sees the before and after quantities. It would need extra context from each caller to identify the order, delivery, or reason. Existing function logging would need to move to the trigger to avoid duplicate entries. |
| Keep one shared writer login | Easy to deploy, but gives checkout and operators access to actions beyond their jobs and makes it harder to identify who made a change. |

## Consequences

A failed movement insert rolls back the stock change. The same rule applies
to writes from Streamlit and other callers of the approved functions.
Production jobs and integrations must move to these functions or remain
paused until they have an approved path. Owners and administrators can
still bypass the functions, so their credentials stay out of applications.
A shared login identifies the account used. Its source reference must lead
to the person who performed the operation.

## Revisit when

Revisit if the permission check finds an operational login that can write
directly, an approved workload cannot use the functions, or a verified
product balance diverges from its movement history after cutover.
