---
name: database_query
description: Database schema inspection, SQL query optimization, indexing strategies, migrations, and ORM performance tuning.
triggers: [sql, database, sqlite, postgres, query, index, migration, db]
---
# Database Query for Harness

Expert guidance for database interactions, schema design, and query optimization.

## Guidelines
1. **Schema & Indexing**:
   - Always index foreign keys and columns frequently used in `WHERE`, `JOIN`, and `ORDER BY` clauses.
   - Use `EXPLAIN QUERY PLAN` or `EXPLAIN ANALYZE` before concluding a query is performant.
2. **Safe Migrations**:
   - Ensure migrations are reversible (up/down).
   - Never run destructive `DROP TABLE` or `DROP COLUMN` without verification.
   - For SQLite, use transactions and connection pooling wisely.
