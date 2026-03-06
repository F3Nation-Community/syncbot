"""Backup/Restore and Data Migration handlers (modals and submissions)."""

import json
import logging
from datetime import UTC, datetime
from logging import Logger

from slack_sdk.web import WebClient

import constants
import helpers
from db import DbManager, schemas
from helpers import export_import as ei
from slack import actions

_logger = logging.getLogger(__name__)


def _is_admin(client: WebClient, user_id: str, body: dict) -> bool:
    return helpers.is_user_authorized(client, user_id)


# ---------------------------------------------------------------------------
# Backup/Restore
# ---------------------------------------------------------------------------

def handle_backup_restore(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Open Backup/Restore modal (admin only)."""
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return
    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    from slack import orm

    blocks = [
        orm.SectionBlock(label="*Download backup*\nGenerate a full-instance backup (JSON) and receive it in your DM."),
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label=":floppy_disk: Download backup",
                    action=actions.CONFIG_BACKUP_DOWNLOAD,
                ),
            ],
        ),
        orm.DividerBlock(),
        orm.SectionBlock(
            label="*Restore from backup*\nPaste the backup JSON below. You will be asked to confirm if the encryption key or integrity check does not match.",
        ),
        orm.InputBlock(
            label="Backup JSON",
            action=actions.CONFIG_BACKUP_RESTORE_JSON_INPUT,
            element=orm.PlainTextInputElement(
                placeholder='Paste backup JSON here (e.g. {"version": 1, ...})',
                multiline=True,
                max_length=3000,
            ),
        ),
    ]

    view = orm.BlockView(blocks=blocks)
    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": actions.CONFIG_BACKUP_RESTORE_SUBMIT,
            "title": {"type": "plain_text", "text": "Backup / Restore"},
            "submit": {"type": "plain_text", "text": "Restore"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": view.as_form_field(),
        },
    )


def handle_backup_download(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Generate backup and send to user's DM (called from modal button)."""
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return
    try:
        payload = ei.build_full_backup()
        json_str = json.dumps(payload, default=ei._json_serializer, indent=2)
        client.files_upload(
            content=json_str,
            filename=f"syncbot-backup-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json",
            channels=user_id,
            initial_comment="Your SyncBot full-instance backup. Keep this file secure.",
        )
    except Exception as e:
        _logger.exception("backup_download failed: %s", e)
        return
    # Optionally update the modal to say "Backup sent to your DM"
    response_url = helpers.safe_get(body, "response_url")
    if response_url:
        try:
            from slack_sdk.webhook import WebhookClient
            w = WebhookClient(response_url)
            w.send(text=":white_check_mark: Backup sent to your DM.")
        except Exception:
            pass


def handle_backup_restore_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> dict | None:
    """Process restore submission. Returns response dict with errors or None to close."""
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return None

    values = helpers.safe_get(body, "view", "state", "values") or {}
    json_text = ""
    for _block_id, block_data in values.items():
        for action_id, action_data in block_data.items():
            if action_id == actions.CONFIG_BACKUP_RESTORE_JSON_INPUT:
                json_text = (action_data.get("value") or "").strip()

    if not json_text:
        return {"response_action": "errors", "errors": {actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: "Paste backup JSON to restore."}}

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"response_action": "errors", "errors": {actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: f"Invalid JSON: {e}"}}

    if data.get("version") != ei.BACKUP_VERSION:
        return {"response_action": "errors", "errors": {actions.CONFIG_BACKUP_RESTORE_JSON_INPUT: f"Unsupported backup version (expected {ei.BACKUP_VERSION})."}}

    hmac_ok = ei.verify_backup_hmac(data)
    key_ok = ei.verify_backup_encryption_key(data)

    # If warnings needed, store payload in cache and show confirmation modal
    if not hmac_ok or not key_ok:
        from helpers._cache import _cache_set
        cache_key = f"restore_pending:{user_id}"
        _cache_set(cache_key, data, ttl=600)
        return {
            "response_action": "push",
            "view": {
                "type": "modal",
                "callback_id": actions.CONFIG_BACKUP_RESTORE_CONFIRM,
                "title": {"type": "plain_text", "text": "Confirm restore"},
                "submit": {"type": "plain_text", "text": "Proceed anyway"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": user_id,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                ("*Integrity check failed.* The file may have been modified or could be malicious. Only proceed if you intentionally edited the file.\n\n" if not hmac_ok else "")
                                + ("*Encryption key mismatch.* Restored bot tokens will not be usable; workspaces must reinstall the app to re-authorize.\n\n" if not key_ok else "")
                                + "Do you want to proceed with restore anyway?"
                            ),
                        },
                    },
                ],
            },
        }

    _do_restore(data, client, user_id)
    return None


def handle_backup_restore_confirm_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> dict | None:
    """Second-step restore when user confirmed warnings."""
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return None
    private_metadata = (helpers.safe_get(body, "view", "private_metadata") or "").strip()
    if not private_metadata:
        return {"response_action": "errors", "errors": {"": "Missing state."}}
    from helpers._cache import _cache_get
    data = _cache_get(f"restore_pending:{private_metadata}")
    if not data:
        return {"response_action": "errors", "errors": {"": "Restore data expired. Please paste the backup JSON again and submit."}}
    _do_restore(data, client, user_id)
    return None


def _do_restore(data: dict, client: WebClient, user_id: str) -> None:
    """Run restore and invalidate caches."""
    try:
        team_ids = ei.restore_full_backup(data, skip_hmac_check=True, skip_encryption_key_check=True)
        ei.invalidate_home_tab_caches_for_all_teams(team_ids)
    except Exception as e:
        _logger.exception("restore failed: %s", e)
        raise
    # Refresh home for user
    team_id = helpers.safe_get(client, "team_id")  # not on client
    # We don't have team_id here easily; the next time user opens Home they'll get fresh data due to cache clear.


# ---------------------------------------------------------------------------
# Data Migration
# ---------------------------------------------------------------------------

def handle_data_migration(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Open Data Migration modal (admin only, federation enabled)."""
    if not constants.FEDERATION_ENABLED:
        return
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return
    trigger_id = helpers.safe_get(body, "trigger_id")
    if not trigger_id:
        return

    from slack import orm

    blocks = [
        orm.SectionBlock(
            label="*Export*\nDownload your workspace data for migration to another instance. You will receive a JSON file in your DM.",
        ),
        orm.ActionsBlock(
            elements=[
                orm.ButtonElement(
                    label=":outbox_tray: Export my workspace data",
                    action=actions.CONFIG_DATA_MIGRATION_EXPORT,
                ),
            ],
        ),
        orm.DividerBlock(),
        orm.SectionBlock(
            label="*Import*\nPaste a migration file JSON below. Existing sync channels in the federated group will be replaced.",
        ),
        orm.InputBlock(
            label="Migration JSON",
            action=actions.CONFIG_DATA_MIGRATION_JSON_INPUT,
            element=orm.PlainTextInputElement(
                placeholder='Paste migration JSON here (e.g. {"version": 1, "workspace": {...}, ...})',
                multiline=True,
                max_length=3000,
            ),
        ),
    ]

    view = orm.BlockView(blocks=blocks)
    client.views_open(
        trigger_id=trigger_id,
        view={
            "type": "modal",
            "callback_id": actions.CONFIG_DATA_MIGRATION_SUBMIT,
            "title": {"type": "plain_text", "text": "Data Migration"},
            "submit": {"type": "plain_text", "text": "Import"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": view.as_form_field(),
        },
    )


def handle_data_migration_export(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> None:
    """Export workspace migration JSON and send to user's DM."""
    if not constants.FEDERATION_ENABLED:
        return
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    team_id = helpers.safe_get(body, "team", "id") or helpers.safe_get(body, "team_id")
    if not _is_admin(client, user_id, body):
        return
    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record:
        return
    try:
        payload = ei.build_migration_export(workspace_record.id, include_source_instance=True)
        json_str = json.dumps(payload, default=ei._json_serializer, indent=2)
        client.files_upload(
            content=json_str,
            filename=f"syncbot-migration-{workspace_record.team_id}-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json",
            channels=user_id,
            initial_comment="Your SyncBot workspace migration file. Use it on the new instance after connecting via federation.",
        )
    except Exception as e:
        _logger.exception("data_migration_export failed: %s", e)


def handle_data_migration_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> dict | None:
    """Process migration import submission."""
    if not constants.FEDERATION_ENABLED:
        return None
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    team_id = helpers.safe_get(body, "view", "team_id") or helpers.safe_get(body, "team_id")
    if not _is_admin(client, user_id, body):
        return None

    values = helpers.safe_get(body, "view", "state", "values") or {}
    json_text = ""
    for _block_id, block_data in values.items():
        for action_id, action_data in block_data.items():
            if action_id == actions.CONFIG_DATA_MIGRATION_JSON_INPUT:
                json_text = (action_data.get("value") or "").strip()

    if not json_text:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: "Paste migration JSON to import."}}

    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: f"Invalid JSON: {e}"}}

    if data.get("version") != ei.MIGRATION_VERSION:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: f"Unsupported migration version (expected {ei.MIGRATION_VERSION})."}}

    workspace_payload = data.get("workspace", {})
    export_team_id = workspace_payload.get("team_id")
    if not export_team_id:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: "Migration file missing workspace.team_id."}}

    workspace_record = helpers.get_workspace_record(team_id, body, context, client)
    if not workspace_record or workspace_record.team_id != export_team_id:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: "This migration file is for a different workspace. Open the app from the workspace that matches the migration file."}}

    # Build team_id -> workspace_id on B
    team_id_to_workspace_id = {workspace_record.team_id: workspace_record.id}
    workspaces_b = DbManager.find_records(schemas.Workspace, [schemas.Workspace.deleted_at.is_(None)])
    for w in workspaces_b:
        if w.team_id:
            team_id_to_workspace_id[w.team_id] = w.id

    # Optional: establish connection if source_instance present
    source = data.get("source_instance")
    if source and source.get("connection_code"):
        import secrets
        from federation import core as federation
        result = federation.initiate_federation_connect(
            source["webhook_url"],
            source["connection_code"],
            team_id=workspace_record.team_id,
            workspace_name=workspace_record.workspace_name or None,
        )
        if result and result.get("ok"):
            fed_ws = federation.get_or_create_federated_workspace(
                instance_id=source["instance_id"],
                webhook_url=source["webhook_url"],
                public_key=source["public_key"],
                name=f"Connection {source['instance_id'][:8]}",
            )
            my_groups = helpers.get_groups_for_workspace(workspace_record.id)
            my_group_ids = {g.id for g, _ in my_groups}
            fed_members = DbManager.find_records(
                schemas.WorkspaceGroupMember,
                [
                    schemas.WorkspaceGroupMember.federated_workspace_id == fed_ws.id,
                    schemas.WorkspaceGroupMember.deleted_at.is_(None),
                    schemas.WorkspaceGroupMember.status == "active",
                ],
            )
            found = False
            for fm in fed_members:
                if fm.group_id in my_group_ids:
                    found = True
                    break
            if not found:
                now = datetime.now(UTC)
                new_group = schemas.WorkspaceGroup(
                    name=f"Federation — {fed_ws.name}",
                    invite_code=f"FED-{secrets.token_hex(4).upper()}",
                    status="active",
                    created_at=now,
                    created_by_workspace_id=workspace_record.id,
                )
                DbManager.create_record(new_group)
                DbManager.create_record(schemas.WorkspaceGroupMember(
                    group_id=new_group.id,
                    workspace_id=workspace_record.id,
                    status="active",
                    role="creator",
                    joined_at=now,
                ))
                DbManager.create_record(schemas.WorkspaceGroupMember(
                    group_id=new_group.id,
                    federated_workspace_id=fed_ws.id,
                    status="active",
                    role="member",
                    joined_at=now,
                ))

    # Resolve federated group (W + connection to source instance)
    my_groups = helpers.get_groups_for_workspace(workspace_record.id)
    my_group_ids = {g.id for g, _ in my_groups}
    fed_members = DbManager.find_records(
        schemas.WorkspaceGroupMember,
        [
            schemas.WorkspaceGroupMember.federated_workspace_id.isnot(None),
            schemas.WorkspaceGroupMember.deleted_at.is_(None),
            schemas.WorkspaceGroupMember.status == "active",
        ],
    )
    candidate_groups = [fm.group_id for fm in fed_members if fm.group_id in my_group_ids]
    group_id = candidate_groups[0] if candidate_groups else None
    if not group_id:
        return {"response_action": "errors", "errors": {actions.CONFIG_DATA_MIGRATION_JSON_INPUT: "No federation connection found. Connect to the other instance first (Enter Connection Code), then import."}}

    sig_ok = ei.verify_migration_signature(data)
    if not sig_ok and source:
        # Store in cache and show confirmation modal (private_metadata size limit)
        from helpers._cache import _cache_set
        cache_key = f"migration_import_pending:{user_id}"
        _cache_set(cache_key, {
            "data": data,
            "group_id": group_id,
            "workspace_id": workspace_record.id,
            "team_id_to_workspace_id": team_id_to_workspace_id,
        }, ttl=600)
        return {
            "response_action": "push",
            "view": {
                "type": "modal",
                "callback_id": actions.CONFIG_DATA_MIGRATION_CONFIRM,
                "title": {"type": "plain_text", "text": "Confirm import"},
                "submit": {"type": "plain_text", "text": "Proceed anyway"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "private_metadata": user_id,
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Integrity check failed.* The file may have been modified or could be malicious. Only proceed if you intentionally edited the file.\n\nProceed with import anyway?",
                        },
                    },
                ],
            },
        }

    ei.import_migration_data(
        data,
        workspace_record.id,
        group_id,
        team_id_to_workspace_id=team_id_to_workspace_id,
    )
    ei.invalidate_home_tab_caches_for_team(workspace_record.team_id)
    return None


def handle_data_migration_confirm_submit(
    body: dict,
    client: WebClient,
    logger: Logger,
    context: dict,
) -> dict | None:
    """Second-step import when user confirmed tampering warning."""
    if not constants.FEDERATION_ENABLED:
        return None
    user_id = helpers.safe_get(body, "user", "id") or helpers.get_user_id_from_body(body)
    if not _is_admin(client, user_id, body):
        return None
    private_metadata = (helpers.safe_get(body, "view", "private_metadata") or "").strip()
    if not private_metadata:
        return {"response_action": "errors", "errors": {"": "Missing state."}}
    from helpers._cache import _cache_get
    meta = _cache_get(f"migration_import_pending:{private_metadata}")
    if not meta:
        return {"response_action": "errors", "errors": {"": "Import data expired. Please paste the migration JSON again and submit."}}
    data = meta.get("data")
    group_id = meta.get("group_id")
    workspace_id = meta.get("workspace_id")
    team_id_to_workspace_id = meta.get("team_id_to_workspace_id", {})
    if not data or not group_id or not workspace_id:
        return {"response_action": "errors", "errors": {"": "Missing import data."}}

    workspace_record = DbManager.get_record(schemas.Workspace, workspace_id)
    if not workspace_record:
        return {"response_action": "errors", "errors": {"": "Workspace not found."}}

    ei.import_migration_data(
        data,
        workspace_record.id,
        group_id,
        team_id_to_workspace_id=team_id_to_workspace_id,
    )
    ei.invalidate_home_tab_caches_for_team(workspace_record.team_id)
    return None
