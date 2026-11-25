"""Operator worklist for stock reconciliation and sale availability."""

import os
from math import ceil
from contextlib import closing
from dataclasses import dataclass
from typing import cast

import psycopg2
import streamlit as st


@dataclass(frozen=True)
class Product:
    product_id: int
    name: str | None
    stock: int | None
    available: bool

    @property
    def label(self) -> str:
        return f"{self.product_id} · {self.name or 'Unnamed product'}"


def load_products(dsn: str) -> tuple[str, list[Product]]:
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("SELECT current_database(), current_user")
        target = cast(tuple[str, str], cursor.fetchone())
        cursor.execute("""
            SELECT product_id, product_name, stock_quantity, is_available
            FROM public.products
            ORDER BY product_id
        """)
        rows = cast(list[tuple[int, str | None, int | None, bool]], cursor.fetchall())
    return f"{target[0]} · {target[1]}", [Product(*row) for row in rows]


def save_batch(
    dsn: str,
    products: list[Product],
    counts: dict[int, int],
    reason: str,
    availability: bool | None,
    *,
    restock: bool = False,
) -> list[int]:
    log_ids: list[int] = []
    # Use one transaction for the whole selection. A stale count or invalid
    # resume rolls back the entire batch, including any log rows it inserted.
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        for product in sorted(products, key=lambda item: item.product_id):
            if restock:
                cursor.execute(
                    "SELECT public.restock_paused_product(%s, %s, %s)",
                    (product.product_id, counts[product.product_id], reason),
                )
            elif availability is None:
                cursor.execute(
                    "SELECT public.reconcile_negative_stock(%s, %s, %s, %s)",
                    (product.product_id, product.stock, counts[product.product_id], reason),
                )
            if restock or availability is None:
                row = cast(tuple[int], cursor.fetchone())
                log_ids.append(row[0])
            if availability is not None:
                cursor.execute(
                    "SELECT public.set_product_sale_availability(%s, %s)",
                    (product.product_id, availability),
                )
    return log_ids


def main() -> None:
    st.set_page_config(page_title="Stock reconciliation", layout="wide")
    st.title("Stock reconciliation")

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        st.error("Set DATABASE_URL in the environment file before launching the app.")
        return

    # Keep the displayed balances until refresh or a successful write. A
    # Streamlit rerun on submit must use the counts the operator reviewed.
    if st.button("Refresh products"):
        st.session_state.pop("inventory_snapshot", None)
    if "inventory_snapshot" not in st.session_state:
        try:
            st.session_state["inventory_snapshot"] = load_products(dsn)
            st.session_state["worklist_selections"] = {}
            st.session_state["worklist_selection_revisions"] = {}
            st.session_state["snapshot_version"] = st.session_state.get("snapshot_version", 0) + 1
        except psycopg2.Error:
            st.error("Could not load products. Check the connection and apply migration 001.")
            return

    target, products = cast(tuple[str, list[Product]], st.session_state["inventory_snapshot"])
    version = cast(int, st.session_state["snapshot_version"])
    st.caption(f"Connected as {target}")
    feedback = cast(str | None, st.session_state.pop("inventory_feedback", None))
    if feedback:
        st.success(feedback)

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

    st.subheader("Product worklist")
    st.caption("Select rows in the table, then update them below. Selections carry across pages in this view.")
    selected = select_worklist_products(products, version, scope)
    if selected:
        with st.container(border=True):
            edit_products(dsn, version, scope, selected)


def select_worklist_products(products: list[Product], version: int, scope: str) -> list[Product]:
    page_size = 10
    page_count = ceil(len(products) / page_size)
    page = 1
    if page_count > 1:
        page = st.number_input(
            "Page", min_value=1, max_value=page_count, step=1, key=f"page-{version}-{scope}"
        )
    first = (page - 1) * page_size
    page_products = products[first:first + page_size]
    st.caption(f"Showing {first + 1:,}–{first + len(page_products):,} of {len(products):,} products")

    # Store IDs so selections are independent of the row positions returned
    # by each page. Clear selections when refreshing the stock snapshot.
    selections = cast(dict[str, set[int]], st.session_state["worklist_selections"])
    selected_ids = selections.setdefault(scope, set())
    revisions = cast(dict[str, int], st.session_state.get("worklist_selection_revisions", {}))
    revision = revisions.get(scope, 0)
    select_all_column, clear_column = st.columns(2)
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
    table = st.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [
            {
                "ID": product.product_id,
                "Product": product.name,
                "Recorded stock": product.stock,
                "Availability": "On sale" if product.available else "Paused",
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


def edit_products(dsn: str, version: int, scope: str, selected: list[Product]) -> None:
    st.subheader(f"Update {len(selected):,} selected product(s)")
    with st.expander("Review selected products"):
        for product in selected:
            st.text(f"{product.label} · stock {product.stock}")

    # A batch can only use an action that applies to every selected row.
    if all(not product.available and product.stock is not None and product.stock < 0 for product in selected):
        actions = ["Record physical counts"]
    elif all(not product.available and product.stock is not None and product.stock >= 0 for product in selected):
        actions = ["Resume sales", "Add stock and resume", "Add stock and keep paused"]
    elif all(product.available for product in selected):
        actions = ["Pause sales"]
    else:
        st.info("Select products that can share an action. Paused products with unknown stock need a verified balance.")
        return

    action = st.radio("Action", actions, horizontal=True, key=f"action-{version}-{scope}-{actions[0]}")
    restock = action.startswith("Add stock")
    record_count = action == "Record physical counts"
    needs_quantity = record_count or restock

    counts: dict[int, int] = {}
    reason = ""
    with st.form(f"edit-{version}-{scope}-{action}"):
        if needs_quantity:
            if restock:
                st.caption("Enter the units received for each product. These units are added to current stock.")
            else:
                st.caption("Enter a verified count for each product. Products stay paused after saving.")
            for product in selected:
                count = st.number_input(
                    f"{'Units to add' if restock else 'Physical count'} · {product.label}",
                    min_value=1 if restock else 0,
                    max_value=2_147_483_647,
                    step=1,
                    value=None,
                    key=f"count-{version}-{scope}-{action}-{product.product_id}",
                )
                if count is not None:
                    counts[product.product_id] = int(count)
            reason = st.text_input("Reason or delivery reference" if restock else "Reason or stocktake reference")
        else:
            st.write(f"{action} for {len(selected)} selected product(s).")
        submitted = st.form_submit_button(action, type="primary")

    if not submitted:
        return
    keep_availability = record_count or action == "Add stock and keep paused"
    availability = None if keep_availability else action != "Pause sales"
    if needs_quantity and (len(counts) != len(selected) or not reason.strip()):
        st.error("Enter a quantity for every selected product and provide a reason.")
        return

    try:
        log_ids = save_batch(dsn, selected, counts, reason, availability, restock=restock)
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
    st.rerun()


if __name__ == "__main__":
    main()
