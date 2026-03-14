# Backup, Restore, and Data Migration

## Full-Instance Backup and Restore

Use **Backup/Restore** (Home tab, next to Refresh) to:

- **Download backup** — Generates a JSON file containing all tables (workspaces, groups, syncs, channels, post meta, user directory, user mappings, federation, instance keys). The file is sent to your DM. Backup includes an HMAC for integrity and a hash of the encryption key. **Use the same `PASSWORD_ENCRYPT_KEY` on the target instance** so restored bot tokens decrypt; otherwise workspaces must reinstall the app to re-authorize.
- **Restore from backup** — Paste the backup JSON in the modal and submit. Restore is intended for an **empty or fresh database** (e.g. after an AWS rebuild). If the encryption key hash or HMAC does not match, you will see a warning and can still proceed (e.g. if you edited the file on purpose).

After restore, Home tab caches are cleared so the next Refresh shows current data.

## Workspace Data Migration (Federation)

When **External Connections** is enabled, **Data Migration** (in that section) lets you:

- **Export** — Download a workspace-scoped JSON file (syncs, sync channels, post meta, user directory, user mappings) plus an optional one-time connection code so the new instance can connect to the source in one step. The file is signed (Ed25519) for tampering detection.
- **Import** — Paste a migration file, then submit. If the file includes a connection payload and you are not yet connected, the app establishes the federation connection and creates the group, then imports. Existing sync channels for that workspace in the federated group are **replaced** (replace mode). User mappings are imported where both workspaces exist on the new instance. If the signature check fails, a warning is shown but you can still proceed.

After import, Home tab and sync-list caches for that workspace are cleared.

### Instance A Behavior

When a workspace that used to be on Instance A connects to A from a new instance (B) via federation and sends its `team_id`, A soft-deletes the matching local workspace row so only the federated connection represents that workspace. See [ARCHITECTURE.md](ARCHITECTURE.md) for details.
