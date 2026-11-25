# ADR-004: Measure completed order spend at an explicit cutoff and show twelve complete months

**Status:** Accepted · **Date:** 2026-11-25 · **Deciders:** Lab implementation team

## Context

Adwoa needs a weekly customer-spending report with totals, monthly history,
running spend, and a top 100. The brief leaves the definition of spend and the
date boundaries open. The audit measured a difference of **6,888 orders** and
**21,243,400.85** in recorded totals for known customers in its 12-month
scenario when it compared the proposed completion rule with exact
`completed` matching. It also found **114 fully priced orders** in that
window whose recorded totals differ from their line sums. The extract does
not identify which differences reflect valid adjustments. [Data integrity diagnosis](../data_integrity_diagnosis.md)

## Decision

Count orders dated before an explicit cutoff whose status becomes `complete`
or `completed` after trimming spaces and lowercasing. Sum the recorded order
header amount, and count each eligible header once. Keep unmatched orders
in a separate total. Missing amounts are excluded from sums and included in
order counts.

Use midnight at the start of the supplied date on the clock used to store
order timestamps. Kloud Kart Database Solutions defaults the date to the latest Monday on the
UTC calendar. Customer totals and the ranking cover all eligible history
through the cutoff. Display the 12 complete calendar months before the
cutoff month, and carry earlier eligible spend into the first displayed
month’s running total. Break spend ties by ascending customer ID.

## Alternatives considered

| Alternative | Trade-off |
| --- | --- |
| Match the raw status `completed` exactly | Easy to apply, but spelling, case, and spaces exclude orders that appear to mean the same thing. |
| Include shipped orders or report net payments after refunds | Could be better for a revenue or cash report. The extract lacks the payment and refund amounts and timing needed to apply a net-payment rule reliably. |
| Rebuild spend from item prices and quantities | Can help reconcile receipts. Missing prices and undisclosed taxes, discounts, or shipping can make the result differ from the amount recorded at checkout. |
| Use the server’s current date, a rolling 365-day window, or the current partial month | Current-date cutoffs change when a run is retried. Rolling days or a partial month are useful for recent activity, but month-to-month comparisons would include periods of different lengths at the edges. |
| Restart the running total at zero in the first displayed month | Shows cumulative spend within the visible window. It omits the earlier spend Adwoa needs to see a customer’s balance through each month-end. |

## Consequences

Adwoa can compare complete months, rerun a cutoff on the same data, and
explain how the top 100 was calculated. The customer total can include
current-month spend that has yet to appear in the monthly history. Later
status or amount edits can change a rerun because the extract carries
current order values rather than their historical versions. The report
measures recorded completed order spend. Finance would need payment and
refund records to verify net revenue. Confirm the clock used by historical
order writers before scheduling the live report.

## Revisit when

Revisit if verified receipts or finance totals consistently disagree with
the approved completion rule, if payment and refund history becomes
available, or if Adwoa needs current-month comparisons or historical
snapshots that remain identical after orders are edited.
