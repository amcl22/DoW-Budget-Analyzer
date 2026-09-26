#!/usr/bin/env bash
# Fill a hosted Postgres (e.g. Neon) with the budget data. Run it from your own machine.
#
#   deploy/load-database.sh restore deploy/data/dow-budget.dump   # load the snapshot (fast)
#   deploy/load-database.sh ingest                    # download and parse every release (slow;
#                                                     # needs Docker and ~1 GB of disk)
#
# DATABASE_URL must be set to the database's *direct* connection string, e.g.
#   export DATABASE_URL='postgresql://USER:PASSWORD@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require'
set -euo pipefail

: "${DATABASE_URL:?set DATABASE_URL to the connection string of the hosted database}"
here="$(cd "$(dirname "$0")/.." && pwd)"
# the pg tools and Docker want the plain URL; the app accepts either form
plain_url="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

case "${1:-}" in
  restore)
    dump="${2:?usage: $0 restore FILE.dump}"
    # --clean replaces whatever an earlier load left; the team watchlist is part of the dump
    pg_restore --no-owner --no-acl --clean --if-exists -d "$plain_url" "$dump"
    ;;
  ingest)
    docker build -t dow-budget "$here"
    mkdir -p "$here/data"
    # data/ keeps the downloaded files, so a re-run only fetches what changed; put manually
    # downloaded Army / Air Force books in data/manual/ first (see the main README)
    docker run --rm -e DATABASE_URL="$plain_url" -v "$here/data:/app/data" dow-budget \
      sh -c "alembic upgrade head && budget ingest-all --publish"
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 2
    ;;
esac
echo "Done. Check it: psql \"$plain_url\" -c 'SELECT budget_cycle, exhibit_type, count(*) FROM line_item_flat GROUP BY 1, 2 ORDER BY 1, 2'"
