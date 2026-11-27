# Memo to Nana Adjei

**27 November 2026 · Kloud Kart operations**

Before the flash sale, count the products most at risk of overselling.
Set aside a maintenance window to switch checkout and test a purchase
through each sales channel before reopening it.

## Protect the last unit

Two buyers can both see the last unit and both pay, leaving a recorded
balance of zero. An automatic rule that checks for negative stock would
miss this. Reserving the unit before confirming a sale prevents that race.
The new checkout reserves stock and saves the order and stock movement
together. In rehearsal, 400 buyers tried for 50 units at once. Exactly 50
bought one and 350 got a sold-out response, with zero errors. Buyers for
the same item may briefly wait while an earlier sale clears.

The supplied data showed 14 products a combined 27 units below zero.
Pause those items until counted. Budget 70–140 staff-minutes at five to
ten minutes per product. Last month’s two oversells required refunds.
The financial loss is unknown. [Cost estimates](001_data_integrity_diagnosis.md#business-cost-estimates)

## Give Adwoa a usable Monday report

A bigger server could do the same work faster at added cost. The new
report reads completed orders once for counts and spending totals. It
took about 1.1 seconds to calculate for one date on the supplied data.
The old live process reportedly took about 40 minutes. The cause of that
delay still needs checking. Reconsider buying capacity if the new live
report misses Adwoa’s deadline and the server is already at capacity.

A saved copy took about 1.4 seconds to rebuild and open once. Corrections
would appear only after a rebuild. Revisit if Adwoa opens the same report
repeatedly and can work from figures as of the last rebuild.

## Find what needs counting or reordering

The reorder list flags items on sale with five units or fewer. It
surfaced 15 candidates missing from the category totals. Refresh the list
and check incoming deliveries before ordering. Revisit the threshold if
items routinely sell out before replacements arrive. Staff can record
counts, deliveries, returns, and damage in the app with a reference an
auditor can follow.

## What still needs checking

All 400 buyers got a response within ten seconds in rehearsal. With the
report running, 95% heard back within roughly 19 milliseconds, compared
with 6 milliseconds running checkout alone. A real flash sale remains
unverified. Schedule the first export outside the sale and have Adwoa
check its totals against a sample of orders. About 262,000 older stock
deductions lack a verified purchase link. Use physical counts for today’s
balances and receipts to confirm old links where possible.
