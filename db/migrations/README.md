# Database Migrations

SyncBot now applies SQL migrations automatically during app startup.

## How it works

- `syncbot/db/__init__.py` ensures the DB exists.
- It applies `db/init.sql` once for new databases.
- It records applied versions in `schema_migrations`.
- It applies pending `*.sql` files in this folder in filename sort order.

## Naming convention

Use lexicographically sortable prefixes:

- `001_add_new_table.sql`
- `002_add_index_for_lookup.sql`

Keep migrations:

- idempotent when practical
- forward-only (never rewrite old files)
- focused (one change objective per file)
