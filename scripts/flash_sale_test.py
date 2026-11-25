#!/usr/bin/env python3
"""Measure checkout performance against a seeded test database.

The shipped race harness releases 400 newly connected buyers at once. This
script instead meters 400 arrivals over ten seconds using reusable connections.
It runs two isolated fixtures so successful writes and sold-out responses both
get measured. Each fixture is cleaned up after its scenario.
"""

import os
import statistics
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import ExitStack, closing
from queue import SimpleQueue
from typing import Literal, NamedTuple, cast

import psycopg2
from psycopg2.extensions import connection

ATTEMPTS = 400
SECONDS = 10
CONNECTIONS = 32


type Outcome = Literal["sold", "refused", "error"]

class CheckoutResult(NamedTuple):
    outcome: Outcome
    latency: float
    finished_after_start: float


def run_checkout(
    connections: SimpleQueue[connection],
    customer_id: int,
    product_id: int,
    scheduled_at: float,
    started_at: float,
) -> CheckoutResult:
    # Start timing at the planned arrival so latency includes time waiting for
    # an available worker or connection, as it would at the checkout service.
    # Each connection is lent to exactly one worker at a time. Autocommit
    # means place_order and its database transaction finish in this one call.
    conn = connections.get()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT success, order_id, message FROM place_order(%s, %s, 1)",
                (customer_id, product_id),
            )
            result = cast(tuple[bool, int | None, str] | None, cursor.fetchone())
            if result is None:
                raise RuntimeError("place_order returned no result")
            success, order_id, message = result
        # A sold-out response is an expected business outcome. Count malformed
        # responses separately so they cannot inflate the refusal total.
        if success and order_id is not None:
            outcome = "sold"
        elif success is False and order_id is None and "out of stock" in message.lower():
            outcome = "refused"
        else:
            outcome = "error"
            print(f"Unexpected checkout result: {success!r}, {order_id!r}, {message!r}")
    except (psycopg2.Error, RuntimeError) as error:
        outcome = "error"
        print(f"Checkout failed: {error}")
    finally:
        finished_at = time.perf_counter()
        connections.put(conn)
    return CheckoutResult(outcome, finished_at - scheduled_at, finished_at - started_at)


def run_scenario(
    admin: connection, connections: SimpleQueue[connection], starting_stock: int
) -> None:
    # Commit the fixture before starting the independent worker connections.
    with admin, admin.cursor() as cursor:
        cursor.execute("SELECT min(customer_id) FROM customers")
        customer = cast(tuple[int | None] | None, cursor.fetchone())
        if customer is None or customer[0] is None:
            raise RuntimeError("Load the sample customers first")
        customer_id = customer[0]
        cursor.execute("""
            INSERT INTO products (product_id, product_name, price, stock_quantity, created_at)
            SELECT coalesce(max(product_id), 0) + 1,
                   'FLASH TIMING FIXTURE', 100.00, %s, now() AT TIME ZONE 'UTC'
            FROM products
            RETURNING product_id
        """, (starting_stock,))
        product = cast(tuple[int] | None, cursor.fetchone())
        if product is None:
            raise RuntimeError("Fixture insert returned no product ID")
        product_id = product[0]

    try:
        with ThreadPoolExecutor(max_workers=CONNECTIONS) as workers:
            started_at = time.perf_counter()
            pending: list[Future[CheckoutResult]] = []
            # Calculate every arrival from the same start, so a delayed
            # request does not shift the rest of the schedule by its delay.
            for attempt in range(ATTEMPTS):
                scheduled_at = started_at + attempt * SECONDS / ATTEMPTS
                time.sleep(max(0, scheduled_at - time.perf_counter()))
                pending.append(workers.submit(
                    run_checkout, connections, customer_id, product_id, scheduled_at, started_at
                ))
            results = [future.result() for future in pending]

        # Scheduled-arrival latency includes queueing. Completion time is
        # measured from the first planned arrival to compare with ten seconds.
        outcomes = Counter(result.outcome for result in results)
        latencies = [result.latency for result in results]
        finish_times = [result.finished_after_start for result in results]
        stats = statistics.quantiles(latencies, n=100, method="inclusive")
        finished_in_window = sum(value <= SECONDS for value in finish_times)
        print(
            f"{starting_stock} units: {outcomes['sold']} sold, "
            f"{outcomes['refused']} refused, {outcomes['error']} errors, "
            f"{finished_in_window}/{ATTEMPTS} finished in {SECONDS}s, "
            f"last response at {max(finish_times):.3f}s, "
            f"p50 {statistics.median(latencies) * 1000:.1f}ms, p95 {stats[94] * 1000:.1f}ms"
        )

        # Verify persisted results independently of the function responses.
        # Join the sale log by item and product to check the traceable deduction.
        with admin, admin.cursor() as cursor:
            cursor.execute("SELECT stock_quantity FROM products WHERE product_id = %s", (product_id,))
            stock = cast(tuple[int] | None, cursor.fetchone())
            if stock is None:
                raise RuntimeError("Fixture product is missing")
            final_stock = stock[0]
            cursor.execute("""
                SELECT count(DISTINCT o.order_id), count(DISTINCT i.order_item_id),
                       count(l.log_id), coalesce(sum(l.change_qty), 0)
                FROM order_items AS i
                JOIN orders AS o ON o.order_id = i.order_id
                LEFT JOIN inventory_log AS l ON l.order_item_id = i.order_item_id
                    AND l.product_id = i.product_id AND l.reason = 'sale'
                WHERE i.product_id = %s
            """, (product_id,))
            trail = cast(tuple[int, int, int, int] | None, cursor.fetchone())
            if trail is None:
                raise RuntimeError("Movement summary returned no result")
            orders, items, movements, stock_change = trail

        expected_sales = min(starting_stock, ATTEMPTS)
        if (
            outcomes["sold"] != expected_sales
            or outcomes["refused"] != ATTEMPTS - expected_sales
            or outcomes["error"] != 0
        ):
            raise RuntimeError("Checkout responses did not match the scenario")
        if final_stock != starting_stock - expected_sales:
            raise RuntimeError("Final stock did not match successful orders")
        if (orders, items, movements, stock_change) != (
            expected_sales, expected_sales, expected_sales, -expected_sales
        ):
            raise RuntimeError("Persisted orders or movement trail did not match successful checkouts")
    finally:
        # Clean up only this run's fixture. The movement link is deferred so
        # fixture rows can all be removed in this one transaction.
        with admin, admin.cursor() as cursor:
            cursor.execute("DELETE FROM inventory_log WHERE product_id = %s", (product_id,))
            cursor.execute("""
                DELETE FROM order_items WHERE product_id = %s RETURNING order_id
            """, (product_id,))
            order_ids = [row[0] for row in cast(list[tuple[int]], cursor.fetchall())]
            cursor.execute("DELETE FROM orders WHERE order_id = ANY(%s)", (order_ids,))
            cursor.execute("DELETE FROM products WHERE product_id = %s", (product_id,))


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Set DATABASE_URL to the seeded test database")

    # Build the pool before timing so connection setup stays outside the
    # measurement. ExitStack closes all sockets if setup or a scenario fails.
    connections: SimpleQueue[connection] = SimpleQueue()
    with ExitStack() as stack:
        admin = stack.enter_context(closing(psycopg2.connect(dsn)))
        for _ in range(CONNECTIONS):
            conn = stack.enter_context(closing(psycopg2.connect(dsn)))
            conn.set_session(isolation_level="READ COMMITTED", autocommit=True)
            connections.put(conn)
        # Full stock exercises every write. Limited stock also measures the
        # customer-facing refusal path after the hot product sells out.
        for starting_stock in (ATTEMPTS, 50):
            run_scenario(admin, connections, starting_stock)


if __name__ == "__main__":
    main()
