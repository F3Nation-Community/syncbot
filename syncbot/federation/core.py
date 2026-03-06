"""Cross-instance federation for SyncBot.

Provides:

* **Ed25519 signing and verification** of inter-instance HTTP requests.
* **Auto-generated keypair** created on first boot and stored in the DB.
* **HTTP client** for pushing events (messages, edits, deletes, reactions,
  user-directory exchanges) to federated workspaces.
* **Connection code** generation and parsing (encodes webhook URL + code +
  instance ID + public key).
* **Payload builders** for standardised federation message formats.
"""

import base64
import ipaddress
import json
import logging
import os
import secrets
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

import constants
from db import DbManager, schemas

_logger = logging.getLogger(__name__)

FEDERATION_USER_AGENT = "SyncBot-Federation/1.0"

# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------

_INSTANCE_ID: str | None = None


def get_instance_id() -> str:
    """Return a persistent UUID identifying this SyncBot instance.

    Reads from ``SYNCBOT_INSTANCE_ID`` env var.  If not set, generates one
    and stores it in-memory for the lifetime of the process.
    """
    global _INSTANCE_ID
    if _INSTANCE_ID:
        return _INSTANCE_ID
    _INSTANCE_ID = os.environ.get("SYNCBOT_INSTANCE_ID") or str(uuid.uuid4())
    return _INSTANCE_ID


def get_public_url() -> str:
    """Return the public base URL of this instance (no trailing slash)."""
    url = os.environ.get("SYNCBOT_PUBLIC_URL", "").rstrip("/")
    if not url:
        _logger.warning("SYNCBOT_PUBLIC_URL is not set — federation will not work")
    return url


# ---------------------------------------------------------------------------
# Ed25519 keypair management
# ---------------------------------------------------------------------------

_cached_private_key = None
_cached_public_pem: str | None = None


def get_or_create_instance_keypair():
    """Return this instance's Ed25519 (private_key, public_key_pem).

    Auto-generates and persists the keypair on first call.  The private key
    is Fernet-encrypted at rest in the ``instance_keys`` table.
    """
    global _cached_private_key, _cached_public_pem
    if _cached_private_key and _cached_public_pem:
        return _cached_private_key, _cached_public_pem

    from helpers import decrypt_bot_token, encrypt_bot_token

    existing = DbManager.find_records(schemas.InstanceKey, [])
    if existing:
        private_pem = decrypt_bot_token(existing[0].private_key_encrypted)
        private_key = load_pem_private_key(private_pem.encode(), password=None)
        _cached_private_key = private_key
        _cached_public_pem = existing[0].public_key
        return private_key, existing[0].public_key

    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    ).decode()
    private_pem = private_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    ).decode()

    record = schemas.InstanceKey(
        public_key=public_pem,
        private_key_encrypted=encrypt_bot_token(private_pem),
        created_at=datetime.now(UTC),
    )
    DbManager.create_record(record)

    _cached_private_key = private_key
    _cached_public_pem = public_pem
    _logger.info("instance_keypair_generated")
    return private_key, public_pem


# ---------------------------------------------------------------------------
# Ed25519 signing / verification
# ---------------------------------------------------------------------------

_TIMESTAMP_MAX_AGE = 300  # 5 minutes


def federation_sign(body: str) -> tuple[str, str]:
    """Sign *body* with this instance's Ed25519 private key.

    Returns ``(signature_b64, timestamp_str)``.
    """
    private_key, _ = get_or_create_instance_keypair()
    ts = str(int(time.time()))
    signing_str = f"{ts}:{body}".encode()
    sig = private_key.sign(signing_str)
    return base64.b64encode(sig).decode(), ts


def federation_verify(body: str, signature_b64: str, timestamp: str, public_key_pem: str) -> bool:
    """Verify an incoming federation request using the sender's public key.

    Returns *True* if the signature is valid and the timestamp is fresh.
    """
    try:
        ts_int = int(timestamp)
    except (TypeError, ValueError):
        return False

    if abs(time.time() - ts_int) > _TIMESTAMP_MAX_AGE:
        _logger.warning("federation_verify: timestamp too old/future", extra={"ts": timestamp})
        return False

    try:
        public_key = load_pem_public_key(public_key_pem.encode())
        signing_str = f"{timestamp}:{body}".encode()
        public_key.verify(base64.b64decode(signature_b64), signing_str)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sign_body(body: str) -> str:
    """Sign *body* only (no timestamp). Used for migration export integrity."""
    private_key, _ = get_or_create_instance_keypair()
    sig = private_key.sign(body.encode())
    return base64.b64encode(sig).decode()


def verify_body(body: str, signature_b64: str, public_key_pem: str) -> bool:
    """Verify a signature over *body* (no timestamp). Used for migration import."""
    try:
        public_key = load_pem_public_key(public_key_pem.encode())
        public_key.verify(base64.b64decode(signature_b64), body.encode())
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# URL validation (SSRF protection)
# ---------------------------------------------------------------------------

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def validate_webhook_url(url: str) -> bool:
    """Return *True* if *url* is safe to use as a federation webhook target.

    Rejects private/loopback IPs (SSRF protection) and requires HTTPS in
    production.  HTTP is allowed only when ``LOCAL_DEVELOPMENT`` is true.
    """
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if constants.LOCAL_DEVELOPMENT:
        if parsed.scheme not in ("http", "https"):
            return False
    else:
        if parsed.scheme != "https":
            return False

    hostname = parsed.hostname
    if not hostname:
        return False

    import socket
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
        for info in addr_infos:
            addr = ipaddress.ip_address(info[4][0])
            for net in _PRIVATE_NETWORKS:
                if addr in net:
                    _logger.warning(
                        "federation_ssrf_blocked",
                        extra={"url": url, "resolved_ip": str(addr)},
                    )
                    return False
    except (socket.gaierror, ValueError):
        return False

    return True


# ---------------------------------------------------------------------------
# Connection code generation / parsing
# ---------------------------------------------------------------------------


def generate_federation_code(workspace_id: int, label: str | None = None) -> tuple[str, str]:
    """Generate a federation connection code and create a pending group record.

    Returns ``(encoded_payload, raw_code)`` where *encoded_payload* is the
    base64-encoded JSON string the admin shares with the remote instance.
    The payload includes this instance's public key for signature verification.
    """
    raw_code = "FED-" + secrets.token_hex(4).upper()
    public_url = get_public_url()
    instance_id = get_instance_id()
    _, public_key_pem = get_or_create_instance_keypair()

    payload = {
        "code": raw_code,
        "webhook_url": public_url,
        "instance_id": instance_id,
        "public_key": public_key_pem,
    }
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

    now = datetime.now(UTC)
    group = schemas.WorkspaceGroup(
        name=label or "External connection",
        invite_code=raw_code,
        status="active",
        created_at=now,
        created_by_workspace_id=workspace_id,
    )
    DbManager.create_record(group)

    member = schemas.WorkspaceGroupMember(
        group_id=group.id,
        workspace_id=workspace_id,
        status="active",
        role="creator",
        joined_at=now,
    )
    DbManager.create_record(member)

    return encoded, raw_code


def parse_federation_code(encoded: str) -> dict | None:
    """Decode a federation connection payload.

    Returns ``{"code": ..., "webhook_url": ..., "instance_id": ...,
    "public_key": ...}`` or *None* if the payload is invalid.
    """
    try:
        decoded = base64.urlsafe_b64decode(encoded.encode()).decode()
        payload = json.loads(decoded)
        if all(k in payload for k in ("code", "webhook_url", "instance_id")):
            return payload
    except Exception as exc:
        _logger.debug(f"decode_federation_code: invalid payload: {exc}")
    return None


# ---------------------------------------------------------------------------
# Federated workspace management
# ---------------------------------------------------------------------------


def get_or_create_federated_workspace(
    instance_id: str,
    webhook_url: str,
    public_key: str,
    name: str | None = None,
    *,
    primary_team_id: str | None = None,
    primary_workspace_name: str | None = None,
) -> schemas.FederatedWorkspace:
    """Find or create a federated workspace record."""
    matches = DbManager.find_records(
        schemas.FederatedWorkspace,
        [schemas.FederatedWorkspace.instance_id == instance_id],
    )
    existing = matches[0] if matches else None
    if existing:
        update_fields = {
            schemas.FederatedWorkspace.webhook_url: webhook_url,
            schemas.FederatedWorkspace.public_key: public_key,
            schemas.FederatedWorkspace.status: "active",
            schemas.FederatedWorkspace.updated_at: datetime.now(UTC),
        }
        if primary_team_id is not None:
            update_fields[schemas.FederatedWorkspace.primary_team_id] = primary_team_id
        if primary_workspace_name is not None:
            update_fields[schemas.FederatedWorkspace.primary_workspace_name] = primary_workspace_name
        DbManager.update_records(
            schemas.FederatedWorkspace,
            [schemas.FederatedWorkspace.id == existing.id],
            update_fields,
        )
        return DbManager.get_record(schemas.FederatedWorkspace, existing.id)

    fed_ws = schemas.FederatedWorkspace(
        instance_id=instance_id,
        webhook_url=webhook_url,
        public_key=public_key,
        status="active",
        name=name,
        primary_team_id=primary_team_id,
        primary_workspace_name=primary_workspace_name,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    DbManager.create_record(fed_ws)
    return DbManager.get_record(schemas.FederatedWorkspace, fed_ws.id)


# ---------------------------------------------------------------------------
# HTTP client — push events to a federated workspace
# ---------------------------------------------------------------------------

_REQUEST_TIMEOUT = 15  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = [1, 2, 4]  # seconds between retries


def _federation_request(
    fed_ws: schemas.FederatedWorkspace,
    path: str,
    payload: dict,
    method: str = "POST",
) -> dict | None:
    """Send an authenticated request to a federated workspace.

    Signs the request with this instance's Ed25519 private key.
    Retries up to :data:`_MAX_RETRIES` times on transient failures.
    """
    url = fed_ws.webhook_url.rstrip("/") + path
    body = json.dumps(payload)

    start_time = time.time()

    for attempt in range(_MAX_RETRIES):
        try:
            sig, ts = federation_sign(body)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": FEDERATION_USER_AGENT,
                "X-Federation-Signature": sig,
                "X-Federation-Timestamp": ts,
                "X-Federation-Instance": get_instance_id(),
            }
            resp = requests.request(method, url, data=body, headers=headers, timeout=_REQUEST_TIMEOUT)
            elapsed = round((time.time() - start_time) * 1000, 1)

            if resp.status_code == 200:
                _logger.debug(
                    "federation_request_ok",
                    extra={"url": url, "elapsed_ms": elapsed, "attempts": attempt + 1},
                )
                try:
                    return resp.json()
                except Exception as exc:
                    _logger.debug(f"federation_request: non-JSON success response: {exc}")
                    return {"ok": True}
            elif resp.status_code >= 500:
                _logger.warning(
                    "federation_request_retry",
                    extra={
                        "url": url,
                        "status": resp.status_code,
                        "attempt": attempt + 1,
                        "remote": fed_ws.instance_id,
                    },
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                continue
            elif resp.status_code == 401:
                _logger.error(
                    "federation_auth_rejected",
                    extra={
                        "url": url,
                        "remote": fed_ws.instance_id,
                        "message": "Keypair may have changed — reconnection required",
                    },
                )
                return None
            else:
                _logger.error(
                    "federation_request_failed",
                    extra={
                        "url": url,
                        "status": resp.status_code,
                        "body": resp.text[:500],
                        "remote": fed_ws.instance_id,
                    },
                )
                return None
        except requests.exceptions.Timeout:
            _logger.warning(
                "federation_request_timeout",
                extra={"url": url, "attempt": attempt + 1, "remote": fed_ws.instance_id},
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        except requests.exceptions.ConnectionError as e:
            _logger.warning(
                "federation_connection_error",
                extra={"url": url, "attempt": attempt + 1, "error": str(e), "remote": fed_ws.instance_id},
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        except Exception as e:
            _logger.error(
                "federation_request_error",
                extra={"url": url, "error": str(e), "remote": fed_ws.instance_id},
            )
            return None

    elapsed = round((time.time() - start_time) * 1000, 1)
    _logger.error(
        "federation_request_exhausted",
        extra={"url": url, "elapsed_ms": elapsed, "attempts": _MAX_RETRIES, "remote": fed_ws.instance_id},
    )
    return None


def push_message(fed_ws: schemas.FederatedWorkspace, payload: dict) -> dict | None:
    """Forward a message (new post, thread reply) to a federated workspace."""
    return _federation_request(fed_ws, "/api/federation/message", payload)


def push_edit(fed_ws: schemas.FederatedWorkspace, payload: dict) -> dict | None:
    """Forward a message edit to a federated workspace."""
    return _federation_request(fed_ws, "/api/federation/message/edit", payload)


def push_delete(fed_ws: schemas.FederatedWorkspace, payload: dict) -> dict | None:
    """Forward a message deletion to a federated workspace."""
    return _federation_request(fed_ws, "/api/federation/message/delete", payload)


def push_reaction(fed_ws: schemas.FederatedWorkspace, payload: dict) -> dict | None:
    """Forward a reaction add/remove to a federated workspace."""
    return _federation_request(fed_ws, "/api/federation/message/react", payload)


def push_users(fed_ws: schemas.FederatedWorkspace, payload: dict) -> dict | None:
    """Exchange user directory with a federated workspace."""
    return _federation_request(fed_ws, "/api/federation/users", payload)


def initiate_federation_connect(
    remote_url: str,
    code: str,
    *,
    team_id: str | None = None,
    workspace_name: str | None = None,
) -> dict | None:
    """Call the remote instance's /api/federation/pair endpoint.

    Signs the request with this instance's Ed25519 private key so the
    receiver can verify we control the keypair advertised in the connection code.
    Optionally sends team_id and workspace_name so the remote (Instance A) can
    tag the connection and soft-delete the matching local workspace.
    """
    _, public_key_pem = get_or_create_instance_keypair()

    url = remote_url.rstrip("/") + "/api/federation/pair"
    payload = {
        "code": code,
        "webhook_url": get_public_url(),
        "instance_id": get_instance_id(),
        "public_key": public_key_pem,
    }
    if team_id:
        payload["team_id"] = team_id
    if workspace_name:
        payload["workspace_name"] = workspace_name
    body = json.dumps(payload)
    sig, ts = federation_sign(body)

    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": FEDERATION_USER_AGENT,
                    "X-Federation-Signature": sig,
                    "X-Federation-Timestamp": ts,
                    "X-Federation-Instance": get_instance_id(),
                },
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code == 200:
                _logger.info("federation_pair_success", extra={"url": url})
                return resp.json()
            elif resp.status_code >= 500:
                _logger.warning(
                    "federation_pair_retry",
                    extra={"url": url, "status": resp.status_code, "attempt": attempt + 1},
                )
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF[attempt])
                continue
            else:
                _logger.error(
                    "federation_pair_failed",
                    extra={"url": url, "status": resp.status_code, "body": resp.text[:500]},
                )
                return None
        except requests.exceptions.ConnectionError as e:
            _logger.warning(
                "federation_pair_connection_error",
                extra={"url": url, "attempt": attempt + 1, "error": str(e)},
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        except requests.exceptions.Timeout:
            _logger.warning(
                "federation_pair_timeout",
                extra={"url": url, "attempt": attempt + 1},
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[attempt])
        except Exception as e:
            _logger.error("federation_pair_error", extra={"url": url, "error": str(e)})
            return None

    _logger.error("federation_pair_exhausted", extra={"url": url, "attempts": _MAX_RETRIES})
    return None


def ping_federated_workspace(fed_ws: schemas.FederatedWorkspace) -> bool:
    """Check if a federated workspace is reachable."""
    url = fed_ws.webhook_url.rstrip("/") + "/api/federation/ping"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": FEDERATION_USER_AGENT},
            timeout=5,
        )
        return resp.status_code == 200
    except Exception as exc:
        _logger.debug(f"ping_federated_workspace: failed to reach {fed_ws.instance_id}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def build_message_payload(
    *,
    msg_type: str = "message",
    sync_id: int,
    post_id: str,
    channel_id: str,
    user_name: str,
    user_avatar_url: str | None,
    workspace_name: str,
    text: str,
    thread_post_id: str | None = None,
    images: list[dict] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Build a standardised federation message payload."""
    return {
        "type": msg_type,
        "sync_id": sync_id,
        "post_id": post_id,
        "channel_id": channel_id,
        "user": {
            "display_name": user_name,
            "avatar_url": user_avatar_url,
            "workspace_name": workspace_name,
        },
        "text": text,
        "thread_post_id": thread_post_id,
        "images": images or [],
        "timestamp": timestamp,
    }


def build_edit_payload(
    *,
    post_id: str,
    channel_id: str,
    text: str,
    timestamp: str,
) -> dict:
    """Build a federation edit payload."""
    return {
        "type": "edit",
        "post_id": post_id,
        "channel_id": channel_id,
        "text": text,
        "timestamp": timestamp,
    }


def build_delete_payload(
    *,
    post_id: str,
    channel_id: str,
    timestamp: str,
) -> dict:
    """Build a federation delete payload."""
    return {
        "type": "delete",
        "post_id": post_id,
        "channel_id": channel_id,
        "timestamp": timestamp,
    }


def build_reaction_payload(
    *,
    post_id: str,
    channel_id: str,
    reaction: str,
    action: str,
    user_name: str,
    timestamp: str,
) -> dict:
    """Build a federation reaction payload."""
    return {
        "type": "react",
        "post_id": post_id,
        "channel_id": channel_id,
        "reaction": reaction,
        "action": action,
        "user_name": user_name,
        "timestamp": timestamp,
    }
