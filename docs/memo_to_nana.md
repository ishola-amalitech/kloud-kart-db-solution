# Memo to Nana Adjei

**27 November 2026 · Kloud Kart operations**

Before the flash sale, count the products most at risk of overselling.
Set aside a maintenance window to switch sales channels to the new checkout
and test a purchase through each one before reopening.

## Protect the last unit

Two checkouts can both see one unit left and both record zero after selling
it. An automatic rule that only flags negative stock would miss that. A rule
that reserves the unit before confirming the sale could work. We put that
check in checkout itself and save the stock deduction, order, and movement
together. In a rehearsal, 400 buyers tried for 50 units at once. Exactly 50
bought one and 350 were told it was sold out, with zero errors. Buyers for
the same item may briefly wait for an earlier sale to clear, so watch
checkout delays at launch.

The supplied data showed 14 products a combined 27 units below zero.
Pause sales of those items until staff verify the quantities. Budget 70–140
minutes of staff time for the first counts, assuming five to ten minutes
per product. The two oversells last month required refunds. The
financial loss could not be established. [Cost estimates](001_data_integrity_diagnosis.md#business-cost-estimates)

## Give Adwoa a usable Monday report

A bigger server could do the same work faster at added cost. We first
changed the report so one read of completed orders supplies both the counts
and spending totals. It took about 1.1 seconds to calculate for one date
on the supplied data. The old live process reportedly took about 40 minutes,
and the cause of that live delay still needs checking. Time the new report
on the live system before buying capacity. Reconsider the purchase if the
report still misses Adwoa’s deadline and the server is running at capacity.

A saved copy took about 1.4 seconds to rebuild and open once.
Corrections would only appear after a rebuild. Revisit this option
if Adwoa opens the same report repeatedly and can use
figures as of the last rebuild.

The report shows 12 complete months, running totals, and the top 100
customers. Adwoa can review orders with no matching customer separately.

## Find what needs counting or reordering

The reorder list flags products on sale with five units or fewer. It
surfaced 15 candidates that category totals alone would have missed.
Check demand and incoming deliveries before ordering. Revisit the threshold
if products routinely sell out before replacements arrive.

Staff can record counts, deliveries, returns, or damage with a reference
an auditor can follow. Count products first where the records suggest
checkout is offering more stock than may be on hand.

## What still needs checking

We rehearsed 400 checkout attempts over ten seconds. A real flash sale
remains unverified. Have Adwoa check the first live report’s totals against
a sample of orders. About 262,000 older stock deductions lack a verified
purchase link. Use physical counts to establish today’s balances and
receipts to confirm old links where possible.
