"""Load and display the low-stock report from one read-only snapshot."""

import csv
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from io import StringIO
from math import ceil
from typing import NamedTuple, cast

import psycopg2
import streamlit as st


class CategoryStock(NamedTuple):
    category_id: int
    path: str
    ancestor_ids: list[int]
    direct_count: int
    branch_count: int


class StockProduct(NamedTuple):
    product_id: int
    name: str | None
    category_id: int | None
    category_path: str | None
    ancestor_ids: list[int] | None
    stock: int | None
    available: bool
    needs_reorder: bool
    stock_issue: str | None
    hierarchy_issue: str | None

    @property
    def needs_review(self) -> bool:
        return not self.available or self.stock_issue is not None or self.hierarchy_issue is not None


@dataclass(frozen=True)
class StockRollup:
    captured_at: datetime
    categories: list[CategoryStock]
    products: list[StockProduct]


def load_stock_rollup(dsn: str) -> StockRollup:
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn:
        # Use the same snapshot for counts and the worklist, even if checkout
        # changes stock between the two reads. Close it before user interaction.
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with conn, conn.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            cursor.execute("SELECT statement_timestamp()")
            captured_at, = cast(tuple[datetime], cursor.fetchone())
            cursor.execute("""
                SELECT category_id, category_path, ancestor_ids, direct_low_stock, branch_low_stock
                FROM public.low_stock_category_rollup
                ORDER BY category_path, category_id
            """)
            category_rows = cast(list[tuple[int, str, list[int], int, int]], cursor.fetchall())
            cursor.execute("""
                SELECT product_id, product_name, category_id, category_path, ancestor_ids,
                    stock_quantity, is_available, needs_reorder, stock_issue, hierarchy_issue
                FROM public.low_stock_worklist
                ORDER BY stock_quantity NULLS LAST, product_id
            """)
            product_rows = cast(
                list[tuple[int, str | None, int | None, str | None, list[int] | None,
                           int | None, bool, bool, str | None, str | None]],
                cursor.fetchall(),
            )
    return StockRollup(captured_at, [CategoryStock(*row) for row in category_rows],
                       [StockProduct(*row) for row in product_rows])


def render_stock_rollup(dsn: str) -> None:
    st.header("Low stock")
    st.write("Find products to reorder and stock or category records that need review.")
    report = cast(StockRollup | None, st.session_state.get("stock-rollup"))
    if st.button(
        "Refresh low-stock report" if report else "Load low-stock report",
        type="primary",
        key="refresh-stock-rollup",
    ):
        # A failed refresh must clear the previous snapshot and its download.
        st.session_state.pop("stock-rollup", None)
        try:
            with st.spinner("Reading current stock…"):
                report = load_stock_rollup(dsn)
        except psycopg2.errors.UndefinedTable:
            st.error("The low-stock views are missing. Apply migration 004 to the app database.")
            return
        except psycopg2.errors.QueryCanceled:
            st.error("The low-stock query timed out or was canceled. Retry the refresh in a moment.")
            return
        except psycopg2.errors.InsufficientPrivilege:
            st.error("The app login needs permission to read the low-stock views.")
            return
        except psycopg2.Error:
            st.error("Could not refresh the low-stock report. Check the database connection and try again.")
            return
        else:
            st.session_state["stock-rollup"] = report
            reset_stock_rollup_page()
    if report is None:
        return

    st.caption(
        f"Snapshot taken {report.captured_at.astimezone(UTC):%d %b %Y at %H:%M:%S} UTC. "
        "Refresh before placing or approving a reorder."
    )
    reorder = [row for row in report.products if row.needs_reorder]
    exceptions = [row for row in report.products if row.needs_review]
    low, empty, review = st.columns(3)
    low.metric("Reorder candidates", len(reorder))
    empty.metric("Out of stock on sale", sum(row.stock == 0 for row in reorder))
    review.metric("Products to review", len(exceptions),
                  help="Includes paused products, unknown or negative stock, and category issues. "
                       "A product with a category issue can also need reordering.")

    st.subheader("Category rollup")
    categories = {row.category_id: row for row in report.categories}
    branch = st.selectbox(
        "Category branch",
        [None, *categories],
        format_func=lambda key: f"{categories[key].path} · {key}" if key is not None else "All categories",
        on_change=reset_stock_rollup_page,
        help="Select a category to see its counts and the reorder candidates in its branch.",
    )
    if branch is None:
        visible_categories = [row for row in report.categories if len(row.ancestor_ids) == 1]
    else:
        visible_categories = [
            row for row in report.categories
            if row.category_id == branch or (len(row.ancestor_ids) > 1 and row.ancestor_ids[1] == branch)
        ]
    if visible_categories:
        st.dataframe(  # pyright: ignore[reportUnknownMemberType]
            [
                {"Category": row.path, "Category ID": row.category_id, "Direct": row.direct_count,
                 "Whole branch": row.branch_count}
                for row in sorted(visible_categories, key=lambda row: -row.branch_count)
            ],
            hide_index=True,
        )
        st.caption("Direct counts products assigned here. Whole branch also includes all subcategories.")
    else:
        st.info("There are no valid category branches to display.")

    st.subheader("Product worklist")
    scope = st.radio("Worklist", ["Reorder candidates", "Exceptions"], horizontal=True,
                     on_change=reset_stock_rollup_page)
    search = st.text_input("Product name or ID", on_change=reset_stock_rollup_page).strip().casefold()
    if scope == "Exceptions":
        st.caption("Store-wide. Use Stock reconciliation to review paused balances. "
                   "Use the category issue to route classification problems to the catalog team.")
        exception_type = st.selectbox(
            "Exception type", ["All exceptions", "Paused products", "Stock issues", "Category issues"],
            on_change=reset_stock_rollup_page,
        )
        rows = [
            row for row in exceptions
            if exception_type == "All exceptions"
            or (exception_type == "Paused products" and not row.available)
            or (exception_type == "Stock issues" and row.stock_issue is not None)
            or (exception_type == "Category issues" and row.hierarchy_issue is not None)
        ]
    elif branch is None:
        st.caption("Store-wide. Includes reorder candidates with category issues.")
        rows = reorder
    else:
        rows = [
            row for row in reorder
            if row.hierarchy_issue is None and row.ancestor_ids is not None and branch in row.ancestor_ids
        ]
    if scope == "Reorder candidates" and st.checkbox("Out of stock only", on_change=reset_stock_rollup_page):
        rows = [row for row in rows if row.stock == 0]
    filtered = [
        row for row in rows
        if search == str(row.product_id) or search in (row.name or "").casefold()
    ]
    if not filtered:
        st.info("No products match this worklist.")
        return

    table = st.container()
    page_count = ceil(len(filtered) / 25)
    page = st.number_input("Page", 1, page_count, step=1, key="stock-rollup-page") if page_count > 1 else 1
    first = (page - 1) * 25
    visible = filtered[first:first + 25]
    table.caption(f"Showing {first + 1:,}–{first + len(visible):,} of {len(filtered):,} products")
    table.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [dict(zip(PRODUCT_COLUMNS, product_cells(row), strict=True)) for row in visible],
        hide_index=True,
    )
    st.download_button(
        "Download filtered worklist CSV",
        data=lambda: worklist_csv(filtered),
        file_name="low-stock-exceptions.csv" if scope == "Exceptions" else "low-stock-reorder.csv",
        mime="text/csv",
        on_click="ignore",
    )


def reset_stock_rollup_page() -> None:
    st.session_state.pop("stock-rollup-page", None)


PRODUCT_COLUMNS = (
    "Product ID",
    "Product",
    "Category ID",
    "Category",
    "Units",
    "Availability",
    "Stock issue",
    "Category issue",
    "Reorder candidate",
)


def product_cells(row: StockProduct) -> tuple[
    int, str | None, int | None, str | None, int | None, str, str | None, str | None, str
]:
    return (
        row.product_id,
        row.name,
        row.category_id,
        row.category_path,
        row.stock,
        "On sale" if row.available else "Paused",
        row.stock_issue,
        row.hierarchy_issue,
        "Yes" if row.needs_reorder else "No",
    )


def worklist_csv(rows: list[StockProduct]) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(PRODUCT_COLUMNS)
    for row in rows:
        # Product and category names can come from imports. Escape strings
        # a spreadsheet could interpret as formulas when opening the CSV.
        writer.writerow(
            "'" + cell if isinstance(cell, str) and (
                cell.startswith(("\t", "\r", "\n")) or cell.lstrip().startswith(("=", "+", "-", "@"))
            ) else cell
            for cell in product_cells(row)
        )
    return output.getvalue()
