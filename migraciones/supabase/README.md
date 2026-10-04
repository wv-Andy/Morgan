# Supabase migrations

**English** · [Español](README.es.md)

The cloud schema. **It's here because not having it here turned out to be expensive.**

Until V1.8, these migrations were applied directly on the Supabase project and left no trace in
the repository. Nobody reviewed them, they didn't show up in any *diff*, and the result was a quota
function that had been **broken and out of PostgREST's reach** for a whole version without anything
giving it away: it only failed for users who weren't the owner, which is exactly the case nobody
tests by hand.

## The rule

Every migration applied to Supabase is also written here, with the same name it was given, **in the
same commit**. A file here that isn't applied, or an applied migration that isn't here, are the two
ways for this to stop being useful at all.

To see what is really applied:

```sql
select version, name from supabase_migrations.schema_migrations order by version;
```

## What to check when writing one

The three things that have failed, in order of how expensive they turned out:

1. **Will the backend call it through PostgREST?** Then it has to be in `public`. PostgREST doesn't
   see `morgan_priv`, and a function it can't find answers 404 — which reaches the browser turned
   into a 500 with no explanation. The functions used by RLS policies are the opposite case: those
   do go in `morgan_priv`, because they're used *inside* the policy.
2. **Do the types match the columns'?** Postgres converts a text literal to a date, but **not a
   parameter**. `dia` is `date`, and receiving it as `text` gives `42804` the first time it runs.
3. **Who can execute it?** `CREATE FUNCTION` grants `EXECUTE` to `PUBLIC` by default. If it isn't
   revoked, it's within reach of anyone with the publishable key.

## Earlier history

Migrations v1 to v14 were applied before this rule and lived only in Supabase. Since 4.20 they're in
[`v01_v14_esquema_inicial.sql`](v01_v14_esquema_inicial.sql), recovered from
`supabase_migrations.schema_migrations` and checked one by one against the MD5 Postgres keeps of
each: written down from what was really applied, not from memory.

## Recreating the schema in a new project

It was done on 2026-09-18 for the load-test project (`morgan-carga`), and the procedure works for
any copy:

1. **v1 to v14**: [`v01_v14_esquema_inicial.sql`](v01_v14_esquema_inicial.sql). (Before 4.20 they
   were read from production, **only the migrations' SQL**, without touching data:
   `select version, name, array_to_string(statements, E';\n') as sql from supabase_migrations.schema_migrations order by version;`.)
2. **From v15 on**, the files in this folder, in order.
3. **It's checked** with [`huella_esquema.sql`](huella_esquema.sql) on both projects: seven
   fingerprints (columns, constraints, indexes, policies, RLS, functions and buckets) that have to
   match one by one.

The first time the functions didn't match, and it was the copy's fault: the files had been applied
without their comments, and **in a function the comments are part of the stored code**. Same
behavior, different fingerprint. The two functions were reapplied with `pg_get_functiondef` from
production and all seven matched.
