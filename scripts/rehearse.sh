#!/usr/bin/env bash
# Rebuild the lab in its own disposable PostgreSQL instance and retain evidence.
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

# The scripts are tracked and the client CSVs are supplied separately.
# Check both before creating evidence files or calling Docker.
bundle_files=(
    schema.sql
    load.sql
    race_test.py
    data/categories.csv
    data/customers.csv
    data/inventory_log.csv
    data/order_items.csv
    data/orders.csv
    data/products.csv
)
missing_files=()
for file in "${bundle_files[@]}"; do
    path="provided_resources/$file"
    if [[ ! -f $path || ! -r $path || ! -s $path ]]; then
        missing_files+=("$path")
    fi
done
if (( ${#missing_files[@]} )); then
    printf 'Required files missing, unreadable, or empty:\n' >&2
    printf '  %s\n' "${missing_files[@]}" >&2
    printf 'Restore any missing scripts from Git and supply the CSVs in provided_resources/data/.\n' >&2
    printf 'See the "Supply the client data" section of README.md.\n' >&2
    exit 1
fi

results=$(mktemp -d "${TMPDIR:-/tmp}/kk-rehearsal.XXXXXX")
printf 'Rehearsal evidence: %s\n' "$results"
name="kk-rehearsal-${results##*.}"
password=$(python3 -c 'import secrets; print(secrets.token_hex(24))')
container_id=$(POSTGRES_PASSWORD="$password" docker run --detach --rm --name "$name" \
    -e POSTGRES_DB=kloudkart -e POSTGRES_USER=kloudkart -e POSTGRES_PASSWORD \
    -p 127.0.0.1::5432 postgres:18 -c max_connections=450)
trap 'docker stop "$container_id" >/dev/null' EXIT
for attempt in {1..30}; do
    if docker exec "$container_id" pg_isready -h 127.0.0.1 -U kloudkart -d kloudkart -q; then
        break
    fi
    if [[ $attempt == 30 ]]; then
        docker logs "$container_id" > "$results/postgres.log" 2>&1
        printf 'PostgreSQL did not become ready. See %s/postgres.log\n' "$results" >&2
        exit 1
    fi
    sleep 1
done
address=$(docker port "$container_id" 5432/tcp)
export DATABASE_URL="postgresql://kloudkart:$password@$address/kloudkart"

psql_test() {
    docker exec -i "$container_id" psql -X -v ON_ERROR_STOP=1 -U kloudkart -d kloudkart "$@"
}

# The supplied schema drops tables. Check the new database before invoking it.
[[ $(psql_test -Atc "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'") == 0 ]]
docker exec "$container_id" postgres --version > "$results/postgres-version.txt"
tar -C provided_resources -cf - schema.sql load.sql data |
    docker exec -i "$container_id" sh -ec '
        dir=$(mktemp -d)
        cd "$dir"
        tar -xf -
        psql -X -1 -v ON_ERROR_STOP=1 -U kloudkart -d kloudkart -f schema.sql -f load.sql
    ' > "$results/load.log" 2>&1

# Audit the original data before applying any changes.
psql_test -q --csv < scripts/schema_audit.sql > "$results/audit.csv" 2> "$results/audit.log"
printf 'Seeded and audited the original data.\n'
for migration in scripts/migrations/00*.sql; do
    psql_test < "$migration" > "$results/${migration##*/}.log" 2>&1
    if [[ $migration == */001_* ]]; then
        # Simulated counts are only for this disposable database.
        psql_test < scripts/negative_stock.sql > "$results/negative-stock-before.log" 2>&1
        psql_test > "$results/simulated-counts.log" 2>&1 <<'SQL'
SELECT public.reconcile_negative_stock(
    product_id, stock_quantity, 0, 'Rehearsal only; simulated physical count'
)
FROM public.products
WHERE stock_quantity < 0
ORDER BY product_id;
SQL
        psql_test < scripts/negative_stock.sql > "$results/negative-stock-after.log" 2>&1
    fi
    printf 'Applied %s\n' "${migration##*/}"
done

for check in tests/verify_low_stock_rollup.sql tests/verify_inventory_trail.sql; do
    psql_test < "$check" > "$results/${check##*/}.log" 2>&1
done
uv run --locked python -m unittest discover -s tests > "$results/unit-tests.log" 2>&1
for run in 1 2 3; do
    uv run --locked python provided_resources/race_test.py | tee "$results/race-$run.log"
done
uv run --locked python -u -m scripts.rehearsal_workloads "$results" | tee "$results/workloads.log"
printf 'Rehearsal passed. Evidence: %s\n' "$results"
