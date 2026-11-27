"""Read the stock investigation views and pause products selected for recount."""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from typing import cast

import psycopg2
import streamlit as st


@dataclass(frozen=True)
class Discrepancy:
    product_id: int
    name: str | None
    stock: int | None
    available: bool
    verified_count_id: int | None
    logged_net: int | None
    comparison: int | None
    possible_oversell: bool
    low_stock_differs: bool


@dataclass(frozen=True)
class Movement:
    log_id: int
    product_id: int | None
    name: str | None
    product_missing: bool
    timestamp: datetime | None
    reason: str | None
    change: int | None
    before: int | None
    after: int | None
    actor: str | None
    note: str | None
    order_item_id: int | None
    order_id: int | None
    count_id: int | None
    replayed_balance: int | None


def trail_installed(dsn: str) -> bool:
    try:
        with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.inventory_discrepancy_worklist') IS NOT NULL")
            return cast(tuple[bool], cursor.fetchone())[0]
    except psycopg2.Error:
        return False


def load_discrepancies(dsn: str) -> list[Discrepancy]:
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("""
            SELECT product_id, product_name, stock_quantity, is_available, verified_count_id,
                   logged_net, comparison_stock, possible_oversell, low_stock_differs
            FROM public.inventory_discrepancy_worklist
            ORDER BY possible_oversell DESC, low_stock_differs DESC, product_id
        """)
        return [Discrepancy(*row) for row in cursor.fetchall()]


def load_history(dsn: str, field: str, identifier: int, before: int | None) -> list[Movement]:
    # Two fixed predicates keep a product lookup and a log-ID lookup indexable.
    predicate = "product_id = %s" if field == "Product ID" else "log_id = %s"
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT log_id, product_id, product_name, product_missing, created_at, reason, change_qty,
                   stock_before, stock_after, recorded_by, note, order_item_id, order_id,
                   verified_count_id, balance_from_count
            FROM public.inventory_movement_history
            WHERE {predicate} AND (%s IS NULL OR log_id < %s)
            ORDER BY log_id DESC
            LIMIT 26
        """, (identifier, before, before))
        return [Movement(*row) for row in cursor.fetchall()]


def pause_products(dsn: str, product_ids: list[int]) -> None:
    # Use the same lock order as reconciliation batches. A failed pause rolls
    # back the entire selection. The connection context commits only after
    # every call succeeds, then closing releases the connection.
    with closing(psycopg2.connect(dsn, connect_timeout=5)) as conn, conn, conn.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        for product_id in sorted(product_ids):
            cursor.execute("SELECT public.set_product_sale_availability(%s, false)", (product_id,))


def render_inventory_trail(dsn: str, *, has_trail: bool) -> None:
    st.header("Inventory trail")
    if not has_trail:
        st.info("Apply inventory trail migration 005 to enable this tab.")
        return
    render_discrepancies(dsn)
    render_history(dsn)


def render_discrepancies(dsn: str) -> None:
    st.subheader("Products to recount")
    st.caption("Before a verified count, the sum of logged changes is only a clue. Review before pausing sales.")
    if st.button("Load or refresh discrepancies"):
        try:
            st.session_state["discrepancy_snapshot"] = load_discrepancies(dsn)
            st.session_state["discrepancy_revision"] = st.session_state.get("discrepancy_revision", 0) + 1
        except psycopg2.Error:
            st.error("Could not load discrepancies. Check your connection and reporting permissions.")
    snapshot = cast(list[Discrepancy] | None, st.session_state.get("discrepancy_snapshot"))
    feedback = cast(str | None, st.session_state.pop("discrepancy_feedback", None))
    if feedback:
        st.success(feedback)
    if snapshot is None:
        return
    if not snapshot:
        st.info("No discrepancies found.")
        return

    st.write(f"{len(snapshot):,} to review · {sum(row.possible_oversell for row in snapshot):,} possible oversells")
    filter_name = st.radio("Show discrepancies", ["All", "Possible oversell", "Low-stock status differs"], horizontal=True)
    rows = snapshot
    if filter_name == "Possible oversell":
        rows = [row for row in snapshot if row.possible_oversell]
    elif filter_name == "Low-stock status differs":
        rows = [row for row in snapshot if row.low_stock_differs]
    if not rows:
        st.info("No products match this filter.")
        return

    revision = cast(int, st.session_state["discrepancy_revision"])
    table = st.container()
    page = st.number_input(
        "Discrepancy page", min_value=1, max_value=ceil(len(rows) / 25), value=1,
        key=f"discrepancy-page-{revision}-{filter_name}",
    )
    current = rows[(page - 1) * 25:page * 25]
    selected = table.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [{
            "product_id": row.product_id, "product_name": row.name, "stock_quantity": row.stock,
            "is_available": row.available, "logged_net": row.logged_net, "comparison_stock": row.comparison,
            "verified_count_id": row.verified_count_id, "possible_oversell": row.possible_oversell,
            "low_stock_differs": row.low_stock_differs,
        } for row in current],
        column_config={
            "logged_net": st.column_config.NumberColumn(help="Sum of all logged quantity changes for this product."),
            "comparison_stock": st.column_config.NumberColumn(
                help="Uses logged_net until there is a verified count. Then it uses that count plus later movements."
            ),
            "verified_count_id": st.column_config.NumberColumn(
                help="Movement log_id of the latest verified physical count. Blank if a count has yet to be recorded."
            ),
        },
        hide_index=True, on_select="rerun", selection_mode="multi-row",
        key=f"discrepancies-{revision}-{filter_name}-{page}",
    )
    selected_products = [current[index] for index in selected.selection.rows]
    pause_ids = [row.product_id for row in selected_products if row.available]
    if st.button(f"Pause {len(pause_ids)} selected on-sale products", disabled=not pause_ids):
        try:
            pause_products(dsn, pause_ids)
        except psycopg2.Error:
            st.error("No products were paused. Check operator permissions and try again.")
            return
        st.session_state.pop("inventory_snapshot", None)
        st.session_state.pop("discrepancy_snapshot", None)
        st.session_state["discrepancy_feedback"] = (
            f"Paused {len(pause_ids)} product(s). Record their counts in Stock reconciliation."
        )
        st.rerun()


def render_history(dsn: str) -> None:
    st.subheader("Movement history")
    st.caption("Older entries may lack a product record or purchase link. A replayed balance starts at a verified count.")
    with st.form("movement-search"):
        field = st.radio("Find by", ["Product ID", "Movement ID"], horizontal=True)
        identifier = st.number_input("ID", min_value=1, max_value=2_147_483_647, value=None, step=1)
        if st.form_submit_button("Find movements"):
            if identifier is None:
                st.warning("Enter an ID.")
                return
            st.session_state["movement_search"] = (field, identifier)
            st.session_state["movement_pages"] = [None]
    search = cast(tuple[str, int] | None, st.session_state.get("movement_search"))
    if search is None:
        return

    pages = cast(list[int | None], st.session_state["movement_pages"])
    st.caption(f"{search[0]} {search[1]} · page {len(pages)}")
    try:
        rows = load_history(dsn, *search, pages[-1])
    except psycopg2.Error:
        st.error("Could not load movement history. Check your connection and reporting permissions.")
        return
    if not rows:
        st.info("No matching movements.")
        return
    visible = rows[:25]
    st.dataframe(  # pyright: ignore[reportUnknownMemberType]
        [{
            "log_id": row.log_id, "product_id": row.product_id, "product_name": row.name,
            "product_missing": row.product_missing, "created_at": row.timestamp, "reason": row.reason,
            "change_qty": row.change, "stock_before": row.before, "stock_after": row.after,
            "verified_count_id": row.count_id, "balance_from_count": row.replayed_balance,
            "order_id": row.order_id, "order_item_id": row.order_item_id,
            "recorded_by": row.actor, "note": row.note,
        } for row in visible],
        column_config={
            "product_missing": st.column_config.CheckboxColumn(help="The movement has no matching product record."),
            "verified_count_id": st.column_config.NumberColumn(
                help="Movement log_id of the verified physical count used for this row."
            ),
            "balance_from_count": st.column_config.NumberColumn(
                help="Verified count plus the movements through this row. Blank before the first verified count."
            ),
        },
        hide_index=True,
    )
    newer, older = st.columns(2)
    if newer.button("Newer movements", disabled=len(pages) == 1):
        pages.pop()
        st.rerun()
    if older.button("Older movements", disabled=len(rows) <= 25):
        pages.append(visible[-1].log_id)
        st.rerun()
