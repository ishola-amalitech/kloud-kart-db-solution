"""Measure the report, rollup, and checkout against the rehearsal database."""

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from queue import SimpleQueue
from time import perf_counter
from unittest.mock import patch

from psycopg2.extensions import connection

from kloud_kart_db_solution.monday_report import MondayReport, load_monday_report
from kloud_kart_db_solution.stock_rollup import load_stock_rollup
from scripts import flash_sale_test as flash


def timed_report(dsn: str, cutoff: date) -> tuple[float, float, MondayReport]:
    started = perf_counter()
    report = load_monday_report(dsn, cutoff)
    finished = perf_counter()
    if len(report.customers) != 40_420 or sum(row.rank is not None for row in report.customers) != 100:
        raise RuntimeError("Expected 40,420 customer summaries and 100 ranked customers")
    return started, finished, report


def run_with_report(dsn: str, cutoff: date, baseline: MondayReport) -> None:
    run_checkout = flash.run_checkout
    run_scenario = flash.run_scenario

    def mixed_scenario(admin: connection, connections: SimpleQueue[connection], starting_stock: int) -> None:
        arrival: SimpleQueue[tuple[float, int]] = SimpleQueue()

        def checkout_and_signal(
            pool: SimpleQueue[connection],
            customer_id: int,
            product_id: int,
            scheduled_at: float,
            started_at: float,
        ) -> flash.CheckoutResult:
            # Use the harness's planned arrival at one second into the sale.
            if abs(scheduled_at - started_at - 1) < 0.001:
                arrival.put((started_at, customer_id))
            return run_checkout(pool, customer_id, product_id, scheduled_at, started_at)

        def measure_report() -> tuple[float, float, MondayReport, float, int]:
            sale_start, customer_id = arrival.get(timeout=20)
            start, finish, report = timed_report(dsn, cutoff)
            return start, finish, report, sale_start, customer_id

        with ThreadPoolExecutor(max_workers=1) as reports:
            pending = reports.submit(measure_report)
            with patch.object(flash, "run_checkout", checkout_and_signal):
                run_scenario(admin, connections, starting_stock)
            start, finish, report, sale_start, customer_id = pending.result()

        prior = {row.customer_id: row for row in baseline.customers}
        current = {row.customer_id: row for row in report.customers}
        previous, buyer = prior.pop(customer_id), current.pop(customer_id)
        visible_sales = buyer.order_count - previous.order_count
        # Only this customer's orders are added during the rehearsal. Ignore
        # ranks when checking other customers, since their positions can move.
        if any(
            (row.order_count, row.spend) != (prior[key].order_count, prior[key].spend)
            for key, row in current.items()
        ):
            raise RuntimeError("Another customer's totals changed during checkout")
        if not 0 < visible_sales <= starting_stock or (buyer.spend or 0) - (previous.spend or 0) != visible_sales * 100:
            raise RuntimeError("Report does not reconcile with the visible fixture purchases")
        if (report.unmatched_orders, report.unmatched_spend) != (baseline.unmatched_orders, baseline.unmatched_spend):
            raise RuntimeError("The report's unmatched orders changed during checkout")
        print(
            f"  Concurrent report ({starting_stock} units): {finish - start:.3f}s, "
            f"sale clock {start - sale_start:.3f}–{finish - sale_start:.3f}s, "
            f"{visible_sales} fixture sales in snapshot"
        )
        if start - sale_start >= flash.SECONDS:
            raise RuntimeError("Report did not start during the sale")

    with patch.object(flash, "run_scenario", mixed_scenario):
        flash.main()


def main(results: Path) -> None:
    dsn = os.environ["DATABASE_URL"]
    # Keep the broad seed cutoff and include orders created during this run
    # even when the rehearsal is repeated after the seed's last order date.
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    cutoff = max(date(2027, 8, 21), tomorrow)
    print(f"Monday report cutoff: {cutoff}")
    for run in range(1, 4):
        start, finish, report = timed_report(dsn, cutoff)
        print(f"Monday report and both CSVs, run {run}: {finish - start:.3f}s")
        start = perf_counter()
        load_stock_rollup(dsn)
        print(f"Low-stock views and connection, run {run}: {perf_counter() - start:.3f}s")
        if run == 3:
            (results / report.report_filename).write_bytes(report.report_csv)
            (results / report.top_100_filename).write_bytes(report.top_100_csv)

    for pair in (1, 2):
        start, finish, baseline = timed_report(dsn, cutoff)
        print(f"Pair {pair}, report alone: {finish - start:.3f}s")
        print(f"Pair {pair}, checkout alone")
        flash.main()
        print(f"Pair {pair}, checkout with report")
        run_with_report(dsn, cutoff, baseline)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
