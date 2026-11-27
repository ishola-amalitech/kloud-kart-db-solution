"""Check the live report SQL using rolled-back fixtures in the rehearsal database."""

import os
from contextlib import closing
from datetime import date
from decimal import Decimal
from typing import cast

import psycopg2

from kloud_kart_db_solution.monday_report import REPORT_SQL

type ReportRow = tuple[
    str, int | None, str | None, date | None, int, Decimal | None, Decimal | None, int | None
]


def main() -> None:
    report_sql = REPORT_SQL.read_text(encoding="utf-8").rstrip().removesuffix(";")
    # Use explicit fixture IDs and dates before the supplied extract. The
    # connection closes without a commit, including when a check fails.
    with closing(psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=5)) as conn:
        with conn.cursor() as cursor:
            cursor.execute("""
                INSERT INTO public.customers (customer_id, full_name)
                VALUES (-3, '=Fixture'), (-2, 'Fixture tie'), (-1, 'Fixture no orders');

                INSERT INTO public.orders (order_id, customer_id, order_date, status, total_amount)
                VALUES
                    (-8, -3, '2000-06-30 23:59:59', 'complete', 30),
                    (-7, -3, '2000-07-01 00:00:00', ' Completed ', 20),
                    (-6, -3, '2000-08-01', 'completed', NULL),
                    (-5, -3, '2001-07-14 23:59:59', 'completed', 10),
                    (-4, -3, '2001-07-15 00:00:00', 'completed', 500),
                    (-3, -3, '2000-07-02', 'shipped', 200),
                    (-2, -2, '2000-07-02', 'completed', 60),
                    (-1, -4, '2000-07-03', 'completed', 15);

                SET LOCAL kloud_kart.report_cutoff = '2001-07-15';
            """)
            cursor.execute(f"""
                SELECT *
                FROM ({report_sql}) AS report
                WHERE customer_id IN (-3, -2, -1) OR row_type = 'unmatched_orders'
            """)
            rows = cast(list[ReportRow], cursor.fetchall())

    summaries = [row for row in rows if row[0] != "month"]
    expected_summaries: list[ReportRow] = [
        ("customer_total", -3, "'=Fixture", None, 4, Decimal(60), Decimal(60), 1),
        ("customer_total", -2, "Fixture tie", None, 1, Decimal(60), Decimal(60), 2),
        ("customer_total", -1, "Fixture no orders", None, 0, Decimal(0), Decimal(0), None),
        ("unmatched_orders", None, None, None, 1, Decimal(15), None, None),
    ]
    if sorted(summaries, key=lambda row: row[1] or 0) != expected_summaries:
        raise RuntimeError("Report summaries fail the eligibility, cutoff, rank or unmatched-order checks")

    months = [date(2000, month, 1) for month in range(7, 13)]
    months.extend(date(2001, month, 1) for month in range(1, 7))
    for customer_id, running in ((-3, 50), (-2, 60), (-1, 0)):
        history = {row[3]: row[4:7] for row in rows if row[0] == "month" and row[1] == customer_id}
        expected_history: dict[date | None, tuple[int, Decimal | None, Decimal | None]] = {
            month: (0, Decimal(0), Decimal(running)) for month in months
        }
        # July includes opening spend in the running total. August has an
        # order with no amount. All other months carry the balance forward.
        if customer_id == -3:
            expected_history[months[0]] = (1, Decimal(20), Decimal(50))
            expected_history[months[1]] = (1, None, Decimal(50))
        elif customer_id == -2:
            expected_history[months[0]] = (1, Decimal(60), Decimal(60))
        if history != expected_history:
            raise RuntimeError("Monthly rows, spend or the running balance do not match the fixtures")

    if len(rows) != len(expected_summaries) + 3 * len(months):
        raise RuntimeError("Report returned duplicate fixture rows")
    print("Monday report SQL checks passed.")


if __name__ == "__main__":
    main()
