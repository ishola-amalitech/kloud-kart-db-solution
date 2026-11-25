"""Load a Monday report once and produce the two CSVs used by Kloud Kart Database Solutions."""

import argparse
import csv
import os
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import BytesIO, StringIO, TextIOWrapper
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from kloud_kart_db_solution import PROJECT_ROOT


REPORT_SQL = PROJECT_ROOT / "scripts" / "monday_report.sql"
CUSTOMER_REPORT_COLUMNS = (
    "row_type", "customer_id", "customer_name", "month_start", "order_count", "recorded_spend", "running_spend",
)
RANK_COLUMN = "top_100_rank"


class ReportFormatError(ValueError):
    """The SQL result does not match the report's CSV schema."""


@dataclass(frozen=True)
class CustomerSummary:
    customer_id: int
    name: str
    order_count: int
    spend: Decimal | None
    rank: int | None


@dataclass(frozen=True)
class MonthSummary:
    month: str
    order_count: int
    spend: Decimal | None
    running_spend: Decimal


@dataclass(frozen=True)
class MonthlyActivity:
    month: str
    order_count: int
    spend: Decimal


@dataclass(frozen=True)
class MondayReport:
    cutoff: date
    report_csv: bytes
    top_100_csv: bytes
    customers: list[CustomerSummary]
    unmatched_orders: int
    unmatched_spend: Decimal | None
    monthly_activity: list[MonthlyActivity]

    @property
    def report_filename(self) -> str:
        return f"monday-report-{self.cutoff.isoformat()}.csv"

    @property
    def top_100_filename(self) -> str:
        return f"monday-top-100-{self.cutoff.isoformat()}.csv"

    def monthly_history(self, customer_id: int) -> list[MonthSummary]:
        # The export is ordered by customer ID. Read one customer's 12 rows
        # on demand, without retaining a second copy of every monthly row.
        months: list[MonthSummary] = []
        with TextIOWrapper(BytesIO(self.report_csv), encoding="utf-8", newline="") as source:
            rows = csv.reader(source)
            next(rows)
            for kind, raw_id, _, month, count, spend, running in rows:
                if not raw_id:
                    break
                row_id = int(raw_id)
                if row_id > customer_id:
                    break
                if kind == "month" and row_id == customer_id:
                    months.append(MonthSummary(
                        month, int(count), Decimal(spend) if spend else None, Decimal(running),
                    ))
        return months


def load_monday_report(dsn: str, cutoff: date) -> MondayReport:
    query = REPORT_SQL.read_text(encoding="utf-8").rstrip().removesuffix(";")
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, BytesIO() as output:
        conn.set_session(readonly=True)
        with conn, conn.cursor() as cursor:
            cursor.execute("SET LOCAL DateStyle = 'ISO, YMD'")
            cursor.execute(
                "SELECT set_config('kloud_kart.report_cutoff', %s, true)",
                (cutoff.isoformat(),),
            )
            # PostgreSQL runs the report once. Both files and the preview
            # come from this same read-only snapshot, even during live sales.
            cursor.copy_expert(
                f"COPY ({query}) TO STDOUT WITH (FORMAT CSV, HEADER, ENCODING 'UTF8')",
                output,
            )

        output.seek(0)
        with TextIOWrapper(output, encoding="utf-8", newline="") as source:
            return split_report_csv(source, cutoff)


def split_report_csv(source: Iterable[str], cutoff: date) -> MondayReport:
    customers: list[CustomerSummary] = []
    activity_by_month: dict[str, tuple[int, Decimal]] = {}
    unmatched_orders = 0
    unmatched_spend: Decimal | None = None
    rows = csv.reader(source)
    header = next(rows, None)
    if header is None:
        raise ReportFormatError("The report query returned no CSV header.")
    positions = {column: index for index, column in enumerate(header)}
    required_columns = (*CUSTOMER_REPORT_COLUMNS, RANK_COLUMN)
    if len(positions) != len(header) or not all(column in positions for column in required_columns):
        raise ReportFormatError("The report query returned unexpected columns. Check that the app and report SQL match.")

    # Read the SQL result by column name. The customer file always uses its
    # own stable layout, even if the query reorders or adds output columns.
    export_positions = [positions[column] for column in CUSTOMER_REPORT_COLUMNS]
    with BytesIO() as main_bytes, TextIOWrapper(main_bytes, encoding="utf-8", newline="") as main_csv:
        main_writer = csv.writer(main_csv)
        main_writer.writerow(CUSTOMER_REPORT_COLUMNS)
        for row in rows:
            if len(row) != len(header):
                raise ReportFormatError("The report query returned a row with the wrong number of fields.")
            main_writer.writerow([row[index] for index in export_positions])
            kind = row[positions["row_type"]]
            if kind == "customer_total":
                spend = row[positions["recorded_spend"]]
                rank = row[positions[RANK_COLUMN]]
                customers.append(CustomerSummary(
                    int(row[positions["customer_id"]]),
                    row[positions["customer_name"]],
                    int(row[positions["order_count"]]),
                    Decimal(spend) if spend else None,
                    int(rank) if rank else None,
                ))
            elif kind == "unmatched_orders":
                spend = row[positions["recorded_spend"]]
                unmatched_orders = int(row[positions["order_count"]])
                unmatched_spend = Decimal(spend) if spend else None
            elif kind == "month":
                # Aggregate the existing customer-month rows while writing the
                # CSV. This keeps the dashboard on the same report snapshot.
                month = row[positions["month_start"]]
                spend = row[positions["recorded_spend"]]
                count = int(row[positions["order_count"]])
                orders, total = activity_by_month.get(month, (0, Decimal(0)))
                activity_by_month[month] = (
                    orders + count,
                    total + Decimal(spend) if count and spend else total,
                )
        main_csv.flush()
        report_csv = main_bytes.getvalue()

    with StringIO(newline="") as top_csv:
        top_writer = csv.writer(top_csv)
        top_writer.writerow(("top_100_rank", "customer_id", "customer_name", "order_count", "recorded_spend"))
        # Iterate the same ranked summaries used by the app. Rank order in
        # this file is independent of the sort or search in the preview.
        ranked_customers = (row for row in customers if row.rank is not None)
        for customer in sorted(ranked_customers, key=lambda row: row.rank or 0):
            top_writer.writerow((
                customer.rank, customer.customer_id, customer.name, customer.order_count, customer.spend,
            ))
        top_100_csv = top_csv.getvalue().encode("utf-8")

    monthly_activity = [
        MonthlyActivity(month, orders, spend)
        for month, (orders, spend) in sorted(activity_by_month.items())
    ]
    return MondayReport(cutoff, report_csv, top_100_csv, customers, unmatched_orders, unmatched_spend, monthly_activity)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the Monday customer report and top 100 as separate CSVs.")
    parser.add_argument("--cutoff", required=True, type=date.fromisoformat, help="Exclusive cutoff date (YYYY-MM-DD).")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="Existing directory for the two CSVs.")
    args = parser.parse_args()
    cutoff: date = args.cutoff
    output_dir: Path = args.output_dir
    if not output_dir.is_dir():
        parser.error("--output-dir must be an existing directory.")
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("Set DATABASE_URL before exporting.")

    try:
        report = load_monday_report(dsn, cutoff)
    except ReportFormatError as error:
        parser.exit(1, f"{error}\n")
    for filename, data in ((report.report_filename, report.report_csv), (report.top_100_filename, report.top_100_csv)):
        path = output_dir / filename
        path.write_bytes(data)
        print(path)


if __name__ == "__main__":
    main()
