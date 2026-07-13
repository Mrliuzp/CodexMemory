# V1.2 Migration Strategy

Migration `0011_v12_admin_scopes` is additive and creates `knowledge_scopes` with a unique `(project_id, scope_key)` constraint and a foreign key to `projects`. Each existing project receives a `default` Scope projection. Existing project-level records keep their original fields and are not rewritten.

The migration is idempotent at the operational level: upgrading an already migrated database does not create duplicate default Scopes. Downgrade removes only the new table. PostgreSQL and SQLite are covered; SQLite receives an explicit trigger guard for the foreign key because deployments may not enable SQLite foreign keys globally.
