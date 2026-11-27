"""Kloud Kart Database Solutions stock reconciliation, Monday exports, and low stock."""

import os
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import cast

import psycopg2
import streamlit as st

from kloud_kart_db_solution.inventory_trail import render_inventory_trail, trail_installed
from kloud_kart_db_solution.monday_report import (
    CustomerSummary,
    MondayReport,
    ReportFormatError,
    load_monday_report,
)
from kloud_kart_db_solution.stock_rollup import render_stock_rollup


@dataclass(frozen=True)
class Product:
    product_id: int
    name: str | None
    stock: int | None
    available: bool

    @property
    def label(self) -> str:
        return f"{self.product_id} · {self.name or 'Unnamed product'}"


def load_products(dsn: str) -> list[Product]:
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("""
            SELECT product_id, product_name, stock_quantity, is_available
            FROM public.products
            ORDER BY product_id
        """)
        rows = cast(list[tuple[int, str | None, int | None, bool]], cursor.fetchall())
    return [Product(*row) for row in rows]


def save_batch(
    dsn: str,
    products: list[Product],
    counts: dict[int, int],
    reason: str,
    availability: bool | None,
    *,
    restock: bool = False,
    movement_reason: str | None = None,
) -> list[int]:
    log_ids: list[int] = []
    # Use one transaction for the whole selection. A stale count or invalid
    # resume rolls back the entire batch, including any log rows it inserted.
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        for product in sorted(products, key=lambda item: item.product_id):
            if movement_reason is not None:
                cursor.execute(
                    "SELECT public.record_stock_movement(%s, %s, %s, %s, %s)",
                    (product.product_id, movement_reason, counts[product.product_id], reason, product.stock),
                )
            elif restock:
                cursor.execute(
                    "SELECT public.restock_paused_product(%s, %s, %s)",
                    (product.product_id, counts[product.product_id], reason),
                )
            elif availability is None:
                cursor.execute(
                    "SELECT public.reconcile_negative_stock(%s, %s, %s, %s)",
                    (product.product_id, product.stock, counts[product.product_id], reason),
                )
            if movement_reason is not None or restock or availability is None:
                row = cast(tuple[int], cursor.fetchone())
                log_ids.append(row[0])
            if availability is not None:
                cursor.execute(
                    "SELECT public.set_product_sale_availability(%s, %s)",
                    (product.product_id, availability),
                )
    return log_ids


def main() -> None:
    st.set_page_config(page_title="Kloud Kart Database Solutions", layout="wide")
    st.title("Kloud Kart Database Solutions")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        st.error("Set DATABASE_URL in the environment file before launching the app.")
        return

    has_trail = trail_installed(dsn)
    stock_tab, exports_tab, low_stock_tab, trail_tab = st.tabs(
        ["Stock reconciliation", "Monday exports", "Low stock", "Inventory trail"]
    )
    with stock_tab:
        render_stock_reconciliation(dsn, has_trail=has_trail)
    with exports_tab:
        render_monday_exports(dsn)
    with low_stock_tab:
        render_stock_rollup(dsn)
    with trail_tab:
        render_inventory_trail(dsn, has_trail=has_trail)


def render_monday_exports(dsn: str) -> None:
    st.header("Monday exports")
    st.write("Review customer spending and monthly history, or download the report and top 100 separately.")
    today = datetime.now(UTC).date()
    with st.form("monday-export"):
        selected_date = st.date_input(
            "Report cutoff date",
            value=today - timedelta(days=today.weekday()),
            help="Orders before the start of this date count. Choose a Monday for a weekly run.",
        )
        # Streamlit's type assumes a default date stays set, but an empty
        # browser value can deserialize to None.
        cutoff = cast(date | None, selected_date)
        submitted = st.form_submit_button("Generate report", type="primary")

    if submitted:
        st.session_state.pop("monday_export", None)
        if cutoff is None:
            st.error("Choose a report cutoff date.")
            return
        try:
            with st.spinner("Generating the Monday report…"):
                st.session_state["monday_export"] = load_monday_report(dsn, cutoff)
                reset_monday_preview_page()
        except ReportFormatError as error:
            st.error(str(error))
        except (OSError, psycopg2.Error):
            st.error("Could not generate the report. Check the database connection and report read permissions.")

    report = cast(MondayReport | None, st.session_state.get("monday_export"))
    if report is None:
        return

    st.subheader(f"Customer spending before {report.cutoff.isoformat()}")
    spend_col, orders_col, unmatched_col = st.columns(3)
    recorded_spend = sum((row.spend for row in report.customers if row.spend is not None), Decimal(0))
    spend_col.metric("Recorded customer spend", f"{recorded_spend:,.2f}")
    orders_col.metric("Orders matched to customers", f"{sum(row.order_count for row in report.customers):,}")
    unmatched_col.metric("Orders missing a customer", f"{report.unmatched_orders:,}")
    if report.unmatched_orders and report.unmatched_spend is not None:
        st.caption(f"Unmatched orders carry {report.unmatched_spend:,.2f} in recorded spend.")

    render_monday_analytics(report, recorded_spend)
    st.subheader("Customer preview")
    render_monday_preview(report)

    st.subheader("Downloads")
    main_file, top_file = st.columns(2)
    main_file.download_button(
        "Download customer report CSV",
        data=report.report_csv,
        file_name=report.report_filename,
        mime="text/csv",
        on_click="ignore",
    )
    top_file.download_button(
        "Download top 100 CSV",
        data=report.top_100_csv,
        file_name=report.top_100_filename,
        mime="text/csv",
        on_click="ignore",
    )


def render_monday_analytics(report: MondayReport, recorded_spend: Decimal) -> None:
    st.subheader("Monthly customer activity")
    st.caption("Last 12 complete months. Includes completed orders matched to a customer.")
    if report.monthly_activity:
        measure = st.radio("Monthly measure", ["Recorded spend", "Completed orders"], horizontal=True)
        st.line_chart(  # pyright: ignore[reportUnknownMemberType]
            [
                {
                    "Month": month.month[:7],
                    "Recorded spend": float(month.spend) if month.spend.is_finite() else None,
                    "Completed orders": month.order_count,
                }
                for month in report.monthly_activity
            ],
            x="Month",
            y=measure,
        )
    else:
        st.info("There are no customer-month rows to chart.")

    coverage, concentration = st.columns(2)
    coverage.subheader("Customer order coverage")
    with_orders = sum(row.order_count > 0 for row in report.customers)
    coverage.caption("Customers grouped by whether they had a completed order before the cutoff.")
    coverage.bar_chart(  # pyright: ignore[reportUnknownMemberType]
        [
            {"Group": "With completed orders", "Customers": with_orders},
            {"Group": "Without completed orders", "Customers": len(report.customers) - with_orders},
        ],
        x="Group",
        y="Customers",
        horizontal=True,
    )

    concentration.subheader("Top 100 spend concentration")
    ranked_spend = (
        row.spend for row in report.customers
        if row.rank is not None and row.spend is not None
    )
    top_spend = sum(ranked_spend, Decimal(0))
    if recorded_spend.is_finite() and recorded_spend > 0 and top_spend.is_finite():
        concentration.metric("Share of matched customer spend", f"{top_spend / recorded_spend:.1%}")
        concentration.bar_chart(  # pyright: ignore[reportUnknownMemberType]
            [
                {"Group": "Top 100", "Recorded spend": float(top_spend)},
                {"Group": "Other customers", "Recorded spend": float(recorded_spend - top_spend)},
            ],
            x="Group",
            y="Recorded spend",
            horizontal=True,
        )
    else:
        concentration.info("Spend concentration requires a positive recorded customer total.")


def reset_monday_preview_page() -> None:
    st.session_state.pop("monday-preview-page", None)


def render_monday_preview(report: MondayReport) -> None:
    view_col, search_col, sort_col = st.columns(3)
    view = view_col.radio(
        "Show", ["All customers", "With completed orders", "Top 100"],
        horizontal=True, on_change=reset_monday_preview_page,
    )
    search = search_col.text_input("Customer name or ID", on_change=reset_monday_preview_page).strip().casefold()
    sort = sort_col.selectbox(
        "Sort customers by",
        ["Highest spend", "Lowest spend", "Most orders", "Customer ID"],
        on_change=reset_monday_preview_page,
    )
    customers = [
        row for row in report.customers
        if (
            view == "All customers"
            or (view == "With completed orders" and row.order_count > 0)
            or (view == "Top 100" and row.rank is not None)
        )
        and (search in row.name.casefold() or search == str(row.customer_id))
    ]
    if not customers:
        st.info("No customers match this view.")
        return

    # Sort before slicing so the sort applies to the filtered result across
    # every page. Missing amounts stay at the end in either spend sort.
    if sort in {"Highest spend", "Lowest spend"}:
        def spend_key(row: CustomerSummary) -> tuple[bool, Decimal, int]:
            amount = row.spend if row.spend is not None else Decimal(0)
            if amount.is_nan():
                amount = Decimal("Infinity")  # PostgreSQL orders NaN above finite amounts.
            return row.spend is None, -amount if sort == "Highest spend" else amount, row.customer_id

        customers.sort(key=spend_key)
    elif sort == "Most orders":
        customers.sort(key=lambda row: (-row.order_count, row.customer_id))
    else:
        customers.sort(key=lambda row: row.customer_id)

    # Render the table above the page input while using the input's current value.
    preview = st.container()
    page_size = 25
    page_count = ceil(len(customers) / page_size)
    page = 1
    if page_count > 1:
        page = st.number_input("Page", min_value=1, max_value=page_count, step=1, key="monday-preview-page")
    first = (page - 1) * page_size
    visible = customers[first:first + page_size]
    preview.caption(f"Showing {first + 1:,}–{first + len(visible):,} of {len(customers):,} customers")
    preview.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [
            {
                "Customer ID": row.customer_id,
                "Customer": row.name,
                "Orders": row.order_count,
                "Recorded spend": float(row.spend) if row.spend is not None else None,
                "Rank": row.rank,
            }
            for row in visible
        ],
        hide_index=True,
        column_config={"Recorded spend": st.column_config.NumberColumn(format="localized", step=0.01)},
    )

    st.subheader(
        "Inspect Monthly History A User",
        help=(
            f"The picker lists the {len(visible):,} customers on the current preview page (up to 25). "
            "Choose All customers to include customers without completed orders. "
            "Use the search above to find a customer on another page."
        ),
    )
    on_page = {row.customer_id: row for row in visible}
    selected = st.selectbox(
        "Inspect monthly history",
        list(on_page),
        index=None,
        placeholder="Choose a customer on this page",
        format_func=lambda customer_id: f"{on_page[customer_id].name or 'Unnamed customer'} · {customer_id}",
        label_visibility="collapsed",
    )
    if selected is None:
        return
    history = report.monthly_history(selected)
    st.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [
            {
                "Month": month.month,
                "Orders": month.order_count,
                "Recorded spend": float(month.spend) if month.spend is not None else None,
                "Running spend": float(month.running_spend),
            }
            for month in history
        ],
        hide_index=True,
        column_config={
            "Recorded spend": st.column_config.NumberColumn(format="localized", step=0.01),
            "Running spend": st.column_config.NumberColumn(format="localized", step=0.01),
        },
    )


def render_stock_reconciliation(dsn: str, *, has_trail: bool) -> None:
    # Keep the displayed balances until refresh or a successful write. A
    # Streamlit rerun on submit must use the counts the operator reviewed.
    if st.button("Refresh products"):
        st.session_state.pop("inventory_snapshot", None)
    if not isinstance(st.session_state.get("inventory_snapshot"), list):
        try:
            st.session_state["inventory_snapshot"] = load_products(dsn)
            st.session_state["worklist_selections"] = {}
            st.session_state["worklist_selection_revisions"] = {}
            st.session_state["snapshot_version"] = st.session_state.get("snapshot_version", 0) + 1
        except psycopg2.Error:
            st.error("Could not load products. Check the connection and apply migration 001.")
            return

    products = cast(list[Product], st.session_state["inventory_snapshot"])
    version = cast(int, st.session_state["snapshot_version"])
    feedback = cast(str | None, st.session_state.pop("inventory_feedback", None))
    if feedback:
        st.success(feedback)

    st.subheader("Product worklist")
    views = {
        "Negative stock": [
            product for product in products if product.stock is not None and product.stock < 0
        ],
        "Paused": [product for product in products if not product.available],
        "All products": products,
    }
    default_view = next((index for index, rows in enumerate(views.values()) if rows), 0)
    scope = st.radio(
        "Show products", list(views), index=default_view, horizontal=True, key="product-view"
    )
    products = views[scope]

    if not products:
        st.info("No products in this view. The Paused view includes products awaiting release.")
        return

    st.caption("Select rows in the table, then update them below. Selections carry across pages in this view.")
    selected = select_worklist_products(products, version, scope)
    if selected:
        with st.container(border=True):
            edit_products(dsn, version, scope, selected, has_trail=has_trail)


def select_worklist_products(products: list[Product], version: int, scope: str) -> list[Product]:
    worklist = st.container()
    page_size = 10
    page_count = ceil(len(products) / page_size)
    page = 1
    if page_count > 1:
        page = st.number_input(
            "Page", min_value=1, max_value=page_count, step=1, key=f"page-{version}-{scope}"
        )
    first = (page - 1) * page_size
    page_products = products[first:first + page_size]
    worklist.caption(f"Showing {first + 1:,}–{first + len(page_products):,} of {len(products):,} products")

    # Store IDs so selections are independent of the row positions returned
    # by each page. Clear selections when refreshing the stock snapshot.
    selections = cast(dict[str, set[int]], st.session_state["worklist_selections"])
    selected_ids = selections.setdefault(scope, set())
    revisions = cast(dict[str, int], st.session_state.get("worklist_selection_revisions", {}))
    revision = revisions.get(scope, 0)
    select_all_column, clear_column = worklist.columns(2)
    select_all = select_all_column.button(
        f"Select all {len(products):,}",
        help="Select every product in this view, including other pages.",
        disabled=len(selected_ids) == len(products),
    )
    clear = clear_column.button("Clear selection", disabled=not selected_ids)
    if select_all or clear:
        selected_ids.clear()
        if select_all:
            selected_ids.update(product.product_id for product in products)
        # Use fresh table widgets so the bulk selection replaces their
        # previous page-specific checkbox state.
        revision += 1
        revisions[scope] = revision
        st.session_state["worklist_selection_revisions"] = revisions
    default_rows = [
        index for index, product in enumerate(page_products) if product.product_id in selected_ids
    ]

    # Streamlit includes optional dataframe backends in this overload.
    table = worklist.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [
            {
                "product_id": product.product_id,
                "product_name": product.name,
                "stock_quantity": product.stock,
                "is_available": product.available,
            }
            for product in page_products
        ],
        hide_index=True,
        width="stretch",
        key=f"worklist-{version}-{scope}-{revision}-{page}",
        on_select="rerun",
        selection_mode="multi-row",
        selection_default={"selection": {"rows": default_rows}},
    )
    selected_ids.difference_update(product.product_id for product in page_products)
    selected_ids.update(page_products[index].product_id for index in table.selection.rows)
    return [product for product in products if product.product_id in selected_ids]


def edit_products(
    dsn: str, version: int, scope: str, selected: list[Product], *, has_trail: bool
) -> None:
    st.subheader(f"Update {len(selected):,} selected product(s)")
    with st.expander("Review selected products"):
        for product in selected:
            st.text(f"{product.label} · stock {product.stock}")

    # A batch can only use an action that applies to every selected row.
    if has_trail:
        actions: list[str] = []
        if all(not product.available for product in selected):
            actions.append("Record physical counts")
        if all(product.available for product in selected):
            actions.append("Pause sales")
        if all(product.stock is not None and product.stock >= 0 for product in selected):
            actions.extend(["Receive delivery", "Return to sellable stock", "Record damage"])
            if all(not product.available for product in selected):
                actions.extend(["Resume sales", "Add stock and resume"])
    elif all(not product.available and product.stock is not None and product.stock < 0 for product in selected):
        actions = ["Record physical counts"]
    elif all(not product.available and product.stock is not None and product.stock >= 0 for product in selected):
        actions = ["Resume sales", "Add stock and resume", "Add stock and keep paused"]
    elif all(product.available for product in selected):
        actions = ["Pause sales"]
    else:
        st.info("Select products that can share an action. Paused products with unknown stock need a verified balance.")
        return

    if not actions:
        st.info("Select products that can share an action.")
        return
    action_help = {
        "Record physical counts": "Save the actual counted quantity and its stocktake reference. Sales stay paused.",
        "Pause sales": "Stop checkout for the selected products. The stock quantity stays the same.",
        "Receive delivery": "Add the units received and record the delivery reference.",
        "Return to sellable stock": "Add returned units that are ready to sell and record the return reference.",
        "Record damage": "Subtract damaged units and record why they were removed.",
        "Resume sales": "Allow checkout again using the current balance.",
        "Add stock and resume": "Record incoming stock and resume sales in the same transaction.",
        "Add stock and keep paused": "Record incoming stock while keeping sales paused.",
    }
    action = st.radio(
        "Action", actions, horizontal=True, key=f"action-{version}-{scope}-{actions[0]}",
        help="\n\n".join(f"**{name}** — {action_help[name]}" for name in actions),
    )
    restock = action.startswith("Add stock")
    movement_reason = {
        "Record physical counts": "stock_take",
        "Receive delivery": "restock",
        "Add stock and resume": "restock",
        "Return to sellable stock": "return",
        "Record damage": "damage",
    }.get(action) if has_trail else None
    record_count = action == "Record physical counts"
    needs_quantity = record_count or restock or movement_reason is not None

    counts: dict[int, int] = {}
    reason = ""
    with st.form(f"edit-{version}-{scope}-{action}"):
        if needs_quantity:
            if restock or movement_reason == "restock":
                st.caption("Enter the units received for each product. These units are added to current stock.")
            elif record_count:
                st.caption("Enter a verified count for each product. Products stay paused after saving.")
            field = {
                "damage": "Damaged units",
                "return": "Sellable units returned",
            }.get(movement_reason or "", "Physical count" if record_count else "Units to add")
            for product in selected:
                count = st.number_input(
                    f"{field} · {product.label}",
                    min_value=0 if record_count else 1,
                    max_value=2_147_483_647,
                    step=1,
                    value=None,
                    key=f"count-{version}-{scope}-{action}-{product.product_id}",
                )
                if count is not None:
                    counts[product.product_id] = int(count)
            reason = st.text_input("Stocktake reference" if record_count else "Reason or source reference")
        else:
            st.write(f"{action} for {len(selected)} selected product(s).")
        submitted = st.form_submit_button(action, type="primary")

    if not submitted:
        return
    keep_availability = (needs_quantity and action != "Add stock and resume")
    availability = None if keep_availability else action != "Pause sales"
    if needs_quantity and (len(counts) != len(selected) or not reason.strip()):
        st.error("Enter a quantity for every selected product and provide a reason.")
        return

    try:
        log_ids = save_batch(
            dsn, selected, counts, reason, availability,
            restock=restock, movement_reason=movement_reason,
        )
    except psycopg2.Error as error:
        if error.pgcode == "42883" and restock:
            st.error("Apply migration 003 to this database before adding stock.")
            return
        message = error.diag.message_primary if error.pgcode == "P0001" else None
        st.error(f"Batch was not saved. {message or 'Check your connection, permissions, or input.'}")
        return

    feedback = f"Updated {len(selected)} product(s)."
    if log_ids:
        feedback += " Inventory log IDs: " + ", ".join(map(str, log_ids))
    st.session_state["inventory_feedback"] = feedback
    st.session_state.pop("inventory_snapshot", None)
    st.session_state.pop("discrepancy_snapshot", None)
    st.rerun()


if __name__ == "__main__":
    main()
