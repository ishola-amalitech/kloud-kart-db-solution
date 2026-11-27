# Improvement beyond the brief

Kloud Kart Database Solutions is a Streamlit app that lets Nana’s team act on
stock issues and lets Adwoa investigate and export the Monday report from a
browser. The app uses the project’s existing reports and stock operations, so
staff can do this work without running SQL or maintaining separate spreadsheet
formulas. [App source](../scripts/app.py)

## What Nana and Adwoa can do

| Tab | What it’s worth to the team |
| --- | --- |
| Stock reconciliation | Nana’s team can pause products while they count them, record verified counts, and resume sales when ready. Staff can update one product or a batch. A quantity change saves the new balance and its movement record together, so staff have a reference to follow later. [Stock reconciliation guide](002_stock_rollout.md#use-the-reconciliation-app) |
| Monday exports | Adwoa can compare monthly order counts and spend, search and sort customers, and inspect an individual customer’s history before exporting. She can share the top 100 as a separate CSV. In the broad test report, the app separately surfaced **144 orders with no matching customer**, giving her a set of orders to investigate before using the rankings. [Monday report verification](003_monday_report.md#what-was-verified) |
| Low stock | Nana can start with the categories that have the most reorder candidates, narrow the list to out-of-stock products, and send purchasing a filtered CSV. In the seeded test, **15 reorder candidates** had category issues. The exceptions list kept them visible for review. [Low-stock test results](004_low_stock_rollup.md#seeded-test-results) |
| Inventory trail | Nana can prioritize products that might oversell and pause them before a recount. An auditor can look up an individual movement and see the before-and-after quantities and any recorded reference. The test rehearsal covered filtering discrepancies, finding a movement for a missing product, and saving a count and a delivery. [Inventory trail rehearsal](005_inventory_trail.md#rehearse-on-test) |

## Try it on the test database

Follow the [local setup](../README.md#run-locally) to seed and migrate the test
database. From the repository root, run `uv run kk-app` and open
`http://127.0.0.1:8501`.

The rehearsals verified the workflows and report outputs on the supplied data.
Staff time saved in live operation has yet to be measured. The app runs locally
by default. For shared use, follow the operating guide for authenticated
internal access and database permissions.
[Production connection guidance](002_stock_rollout.md#connect-the-app-to-production)
