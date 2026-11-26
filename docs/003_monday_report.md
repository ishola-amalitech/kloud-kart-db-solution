# Monday customer spending report

Adwoa can use Kloud Kart Database Solutions to see which customers account for the most spending,
inspect a customer’s monthly history, and spot orders that need a customer
match. The report replaces manually maintained running totals and gives the
top 100 a file of its own.

## Contents

- [What the report calculates](#what-the-report-calculates)
- [Read the dashboard charts](#read-the-dashboard-charts)
- [How the query works](#how-the-query-works)
- [What was verified](#what-was-verified)
- [Run the app and export](#run-the-app-and-export)

## What the report calculates

An eligible order has a date before the selected cutoff and a status of
`complete` or `completed` after trimming surrounding spaces and lowercasing
it. Spend uses the recorded `orders.total_amount`. The cutoff is exclusive
at midnight on the selected date, using the stored order dates’ clock.
The monthly window covers the 12 complete calendar months before the cutoff
month. For example, a cutoff of **2027-08-21** shows **August 2026 through
July 2027**. See [ADR-004](adr/003-adr-004-monday-report-business-rules.md)
for the alternatives and why we chose these rules.

| Figure | Calculation |
| --- | --- |
| Customer total and order count | Sum known recorded amounts and count eligible order headers for each customer through the cutoff, including any eligible orders in the current partial month. Each header counts once. Customers with no eligible orders show zero spend and zero orders. |
| Monthly spend and order count | Apply the same sum and count for each customer in each of the 12 complete months. Months without orders show zero. |
| Running spend | On monthly rows, add all eligible spend before the first displayed month and accumulate visible monthly spend in date order. On the customer summary, show known spend through the report cutoff, including the partial month. |
| Top 100 | Rank customer totals by recorded spend descending, breaking ties by ascending customer ID. Customers with an eligible order and a recorded spend amount can qualify. |
| Unmatched orders | Count eligible orders with no matching customer and sum their known amounts separately. Use this row to reconcile report totals and find spend that cannot be assigned to a customer. |

A summary or monthly bucket with orders but no amounts has a blank spend
field. Order counts still include those orders. Running spend adds the known
amounts available. The CSV prefixes formula-like customer names so spreadsheet
apps treat them as text.

`order_count` is the number of orders. `recorded_spend` is the amount for the
row’s period. `running_spend` is the cumulative amount through its end.

| Download | Contents |
| --- | --- |
| **Customer report CSV** | One `customer_total` row and twelve `month` rows per customer, plus an `unmatched_orders` row when there are unmatched orders. Join monthly rows to the customer’s summary by `customer_id`. Filter to `customer_total` before summing customer spend. |
| **Top 100 CSV** | Up to 100 rows in rank order, with rank, customer ID, name, total eligible orders, and recorded spend. |

## Read the dashboard charts

| Chart | What Adwoa can use it for |
| --- | --- |
| **Monthly customer activity** | Switch between recorded spend and completed order count for the last 12 complete months. Compare the two views to see whether a spend change coincides with a change in order volume. The cutoff month is excluded from this chart. |
| **Customer order coverage** | See how many customer records have at least one completed order before the cutoff. Customers without completed orders can include people with orders still pending or otherwise outside the reporting rule. |
| **Top 100 spend concentration** | Compare the top 100 with everyone else and see their percentage of matched recorded spend through the cutoff. A high share shows how much of the reported spend comes from a small group. |

The charts use the same snapshot as the CSVs. They cover the full report and
stay fixed while Adwoa searches or filters the customer preview. Orders with
no customer match are excluded from the charts and shown separately above them.

## How the query works

1. `eligible_orders` applies the cutoff and status rule to the order headers.
   `bucketed_orders` matches customer IDs and uses `CASE` to combine older
   orders into an opening-balance bucket.
2. `spend_groups` uses `GROUPING SETS` to get per-customer totals and
   per-customer time buckets in one aggregation. It keeps a combined total for
   unmatched orders.
3. `generate_series` supplies 12 months for every customer. A windowed
   `SUM` adds the monthly amounts in order, and the query adds the opening
   balance. `ROW_NUMBER` ranks the customer summaries.
4. Kloud Kart Database Solutions and the terminal exporter use the same [SQL query](../scripts/monday_report.sql)
   in a read-only transaction. One result supplies the preview and both CSVs,
   so browsing, sorting, and downloading use the same data snapshot.

## What was verified

The broad seeded test produced **40,420 customer summaries**, **485,040
monthly rows**, one exception row for **144 unmatched orders**, and
**100 ranked customers**. The spend and order counts reconciled to the
[diagnostic subtotals](../scripts/monday_report_customer_spend.sql).
A fixture verified the tie at rank 100. In the initial PostgreSQL 18 rehearsal,
the query scanned `orders` once and executed in about **1.85 seconds**.
Creating both CSV files from the broad seed cutoff took about **3.7 seconds**
in a later terminal run. Both Kloud Kart Database Solutions downloads matched those files.
Confirm the clock used by historical live order writers and measure runtime
on the live workload before making this a routine production report.

## Run the app and export

From the repository root, start the seeded test database and Kloud Kart Database Solutions.

```bash
uv run kk-test-db
uv run kk-app
```

Open `http://127.0.0.1:8501` and select **Monday exports**. The date defaults
to the latest Monday on the UTC calendar. To reproduce the broad seed test,
choose **2027-08-21**, then select **Generate report**. Choose **All customers**,
**With completed orders**, or **Top 100**. **With completed orders** shows
customers with at least one qualifying order before the cutoff, including
customers whose recorded spend is zero. Combine it with **Lowest spend** to
find the lowest-spending customers who placed completed orders.

Search by name or exact ID, or choose a sort to apply across all pages. Select
a customer on the current page to inspect their 12 monthly amounts and running
totals. Download either CSV below the preview. Preview filters and sorts leave
the download contents intact.

To create both files from the terminal, use the same configured
`DATABASE_URL`. See [connection settings](002_stock_rollout.md#connection-settings).

```bash
output_dir=$(mktemp -d)
uv run python -m kloud_kart_db_solution.monday_report \
    --cutoff 2027-08-21 --output-dir "$output_dir"
```

The command prints the paths for `monday-report-2027-08-21.csv` and
`monday-top-100-2027-08-21.csv`.
