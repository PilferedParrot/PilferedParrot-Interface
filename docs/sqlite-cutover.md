# Explicit SQLite cutover and JSON rollback (POSIX preview)

SQLite authority is opt-in. The normal launcher still writes JSON. This procedure
must run from the same verified source tree that will run the app. It has been
rehearsed with synthetic and copied histories; it has not switched Chris's live
history. Windows export is unavailable, so this cutover is disabled there.

## Prepare while the app is stopped

1. Close the PPI window and stop every PPI server or other writer using this
   `chats.json`. Wait for active provider jobs to end. Check the relevant port and
   process tree. Do not run the JSON and SQLite versions together. The command
   checks source stability, but cannot prove that another process will not write
   later. Keep the original JSON file unchanged for every SQLite restart.
2. Make a new owner-only directory for the database on a suitable local disk.
   Take and record the source hash after stopping the writers. Use absolute paths:

   ```bash
   PPI_SOURCE=/absolute/path/to/chats.json
   PPI_STATE_DIR=/absolute/path/to/private-state
   PPI_DATABASE="$PPI_STATE_DIR/chats.sqlite3"
   mkdir -m 700 -p "$PPI_STATE_DIR"
   PPI_HASH=$(sha256sum "$PPI_SOURCE" | cut -d ' ' -f 1)
   python3 -m pilferedparrot.sqlite_cutover prepare \
     --source "$PPI_SOURCE" --database "$PPI_DATABASE" \
     --expect-source-sha256 "$PPI_HASH"
   ```

   Record the printed revision, source SHA-256 and tree SHA-256. Preparation
   retains the exact raw JSON bytes in SQLite revision zero. Repeating it with
   a changed source fails closed. The database directory must already exist,
   belong to the invoking user, and have no group/other access.

3. Start the verified app with the same config and an explicit SQLite flag:

   ```bash
   bin/pilferedparrot --config /absolute/path/to/config.json \
     --cwd /absolute/project gui --sqlite-state "$PPI_DATABASE"
   ```

   A compatible or stale app on the configured port causes this start to fail;
   it will not attach to or terminate that app. Open a project and check known
   sessions, drafts, and Chat history. The app keeps the JSON source untouched.
   After stopping it, rerun `verify` and compare the source hash:

   ```bash
   python3 -m pilferedparrot.sqlite_cutover verify \
     --source "$PPI_SOURCE" --database "$PPI_DATABASE" \
     --expect-source-sha256 "$PPI_HASH"
   ```

## Roll back to JSON

Stop the SQLite app and its provider jobs first. Export the committed SQLite
document to a **new** owner-only JSON file; the command refuses an existing
destination and does not edit the original source:

```bash
PPI_ROLLBACK="$PPI_STATE_DIR/rollback.json"
python3 -m pilferedparrot.sqlite_cutover export \
  --source "$PPI_SOURCE" --database "$PPI_DATABASE" \
  --expect-source-sha256 "$PPI_HASH" --destination "$PPI_ROLLBACK"
sha256sum "$PPI_ROLLBACK"
```

The printed `export_sha256` must match `sha256sum`. Point a separate local PPI
config's `web.chat_store` at that new file and start normally, **without**
`--sqlite-state`. Keep the original source and SQLite database until the JSON
restart has been inspected. If SQLite had no writes, the unchanged original
source can instead be used to return to the exact pre-cutover state. Do not
restart the old JSON writer during SQLite operation: any write to the original
source invalidates future SQLite starts. A JSON rollback that receives new
writes is a new authority and must not be mixed with the old SQLite database.

The export preserves the full committed document, including opaque fields.
The app's compatibility loader may still transform some fields on a later
JSON startup; consult the value-free SQLite load report before claiming exact
runtime equivalence. Keep a private copy of the source hash, report, and
rollback export with the cutover record. Never put chat data in a bug report or
public release artifact.
