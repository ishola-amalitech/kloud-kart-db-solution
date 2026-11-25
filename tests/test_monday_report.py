import csv
import unittest
from datetime import date
from decimal import Decimal
from io import StringIO

from kloud_kart_db_solution.monday_report import ReportFormatError, split_report_csv


class MondayReportCsvTests(unittest.TestCase):
    def test_split_uses_column_names_and_keeps_monthly_history(self) -> None:
        source = [
            "top_100_rank,row_type,customer_id,customer_name,month_start,"
            "order_count,recorded_spend,running_spend,extra_column\n",
            "1,customer_total,12,Adwoa,,2,75.00,75.00,ignored\n",
            ",month,12,,2027-07-01,1,25.00,70.00,ignored\n",
            ",unmatched_orders,,,,1,10.00,,ignored\n",
        ]

        report = split_report_csv(source, date(2027, 8, 21))

        main_csv = csv.DictReader(StringIO(report.report_csv.decode("utf-8")))
        self.assertEqual(main_csv.fieldnames, [
            "row_type", "customer_id", "customer_name", "month_start", "order_count", "recorded_spend", "running_spend",
        ])
        rows = list(main_csv)
        self.assertEqual([row["row_type"] for row in rows], ["customer_total", "month", "unmatched_orders"])
        self.assertEqual(rows[0]["order_count"], "2")
        self.assertEqual(rows[0]["recorded_spend"], "75.00")
        self.assertEqual(rows[0]["running_spend"], "75.00")
        ranking = list(csv.DictReader(StringIO(report.top_100_csv.decode("utf-8"))))
        self.assertEqual(ranking, [{
            "top_100_rank": "1", "customer_id": "12", "customer_name": "Adwoa",
            "order_count": "2", "recorded_spend": "75.00",
        }])
        self.assertEqual(report.monthly_history(12)[0].running_spend, Decimal("70.00"))
        self.assertEqual(report.unmatched_spend, Decimal("10.00"))

    def test_missing_column_reports_a_format_error(self) -> None:
        with self.assertRaisesRegex(ReportFormatError, "unexpected columns"):
            split_report_csv(["customer_id,order_count\n"], date(2027, 8, 21))

    def test_row_wider_than_its_header_reports_a_format_error(self) -> None:
        source = [
            "row_type,customer_id,customer_name,month_start,order_count,recorded_spend,running_spend,top_100_rank\n",
            "customer_total,12,Adwoa,,2,75.00,,1,unexpected\n",
        ]
        with self.assertRaisesRegex(ReportFormatError, "wrong number of fields"):
            split_report_csv(source, date(2027, 8, 21))
