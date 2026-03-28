"""
Custom CloudFormation resource: create database and app user for SyncBot.

Supports MySQL (port 3306) and PostgreSQL (port 5432). It can use:
- explicit admin password (existing-host mode), or
- admin password fetched from an admin secret ARN (new-RDS mode).
"""

import base64
import json
import re
import socket
import time

import boto3
import psycopg2
import pymysql
from psycopg2 import sql as psql
from pymysql.cursors import DictCursor

DB_CONNECT_TIMEOUT_SECONDS = 5
DB_CONNECT_ATTEMPTS = 6
DB_CONNECT_RETRY_SECONDS = 2
POSTGRES_DB_CONNECT_ATTEMPTS = 5
POSTGRES_DB_CONNECT_RETRY_SECONDS = 1


# CloudFormation custom resource response helper (no cfnresponse in Lambda by default for Python 3)
def send(event, context, status, data=None, reason=None, physical_resource_id=None):
    import urllib.error
    import urllib.request

    pid = physical_resource_id or event.get("PhysicalResourceId") or event["LogicalResourceId"]
    log_ref = getattr(context, "log_stream_name", None) or "n/a"
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch Log Stream: {log_ref}",
            "PhysicalResourceId": pid,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "Data": data or {},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    # Custom resource responses must reach CloudFormation or the stack hangs (delete/update failures).
    try:
        with urllib.request.urlopen(req, timeout=60) as f:
            f.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CFN response HTTP {e.code}: {e.read()!r}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"CFN response URL error: {e}") from e


def handler(event, context):
    try:
        return _handler_impl(event, context)
    except Exception as e:
        try:
            send(event, context, "FAILED", reason=f"Unhandled error: {e}")
        except Exception as send_err:
            raise RuntimeError(f"Unhandled error in handler: {e}; failed to notify CloudFormation: {send_err}") from e
        raise


def _safe_ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid identifier: {name}")
    return name


def _handler_impl(event, context):
    request_type = event.get("RequestType", "Create")
    props = event.get("ResourceProperties", {})
    host = props.get("Host", "").strip()
    admin_user = (props.get("AdminUser") or "").strip()
    admin_password = props.get("AdminPassword") or ""
    admin_secret_arn = (props.get("AdminSecretArn") or "").strip()
    schema = (props.get("Schema") or "syncbot").strip()
    stage = (props.get("Stage") or "test").strip()
    secret_arn = (props.get("SecretArn") or "").strip()
    database_engine = (props.get("DatabaseEngine") or "mysql").strip().lower()

    if request_type == "Delete":
        # Must return the same PhysicalResourceId as Create; never use a placeholder.
        delete_pid = event.get("PhysicalResourceId") or event["LogicalResourceId"]
        send(event, context, "SUCCESS", {"Username": ""}, physical_resource_id=delete_pid)
        return

    if not all([host, admin_user, schema, stage, secret_arn]):
        send(
            event,
            context,
            "FAILED",
            reason="Missing Host, AdminUser, Schema, Stage, or SecretArn",
        )
        return
    if not admin_password and not admin_secret_arn:
        send(
            event,
            context,
            "FAILED",
            reason="Missing admin credentials: set AdminPassword or AdminSecretArn",
        )
        return

    app_username = f"syncbot_user_{stage}".replace("-", "_")
    try:
        app_password = get_secret_value(secret_arn)
    except Exception as e:
        send(event, context, "FAILED", reason=f"GetSecretValue failed: {e}")
        return
    if not admin_password:
        try:
            # RDS-managed master-user secrets store JSON; extract the password field.
            admin_password = get_secret_value(admin_secret_arn, json_key="password")
        except Exception as e:
            send(event, context, "FAILED", reason=f"Get admin secret failed: {e}")
            return

    try:
        # Fail fast on obvious network connectivity issues before opening DB client sessions.
        _assert_tcp_reachable(host, 3306 if database_engine == "mysql" else 5432)
        if database_engine == "mysql":
            setup_database_mysql(
                host=host,
                admin_user=admin_user,
                admin_password=admin_password,
                schema=schema,
                app_username=app_username,
                app_password=app_password,
            )
        else:
            setup_database_postgresql(
                host=host,
                admin_user=admin_user,
                admin_password=admin_password,
                schema=schema,
                app_username=app_username,
                app_password=app_password,
            )
    except Exception as e:
        send(event, context, "FAILED", reason=f"Database setup failed: {e}")
        return

    send(event, context, "SUCCESS", {"Username": app_username}, reason="OK", physical_resource_id=app_username)
    return {"Username": app_username}


def _assert_tcp_reachable(host: str, port: int) -> None:
    last_exc = None
    for _attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(DB_CONNECT_TIMEOUT_SECONDS)
        try:
            sock.connect((host, port))
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(DB_CONNECT_RETRY_SECONDS)
        finally:
            sock.close()
    raise RuntimeError(f"Cannot reach {host}:{port} over TCP after {DB_CONNECT_ATTEMPTS} attempts: {last_exc}")


def get_secret_value(secret_arn: str, json_key: str | None = None) -> str:
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_arn)
    secret_string = resp.get("SecretString")
    if secret_string is None:
        secret_binary = resp.get("SecretBinary")
        if secret_binary is not None:
            secret_string = base64.b64decode(secret_binary).decode("utf-8")
    secret_string = (secret_string or "").strip()
    if not secret_string:
        raise ValueError(f"Secret {secret_arn} is empty")

    if json_key:
        try:
            payload = json.loads(secret_string)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Secret {secret_arn} is not JSON; cannot read key '{json_key}'") from exc
        value = (payload.get(json_key) or "").strip() if isinstance(payload, dict) else ""
        if not value:
            raise ValueError(f"Secret {secret_arn} missing key '{json_key}'")
        return value

    return secret_string


def setup_database_mysql(
    *,
    host: str,
    admin_user: str,
    admin_password: str,
    schema: str,
    app_username: str,
    app_password: str,
) -> None:
    safe_schema = _safe_ident(schema)
    _safe_ident(app_username)
    conn = None
    last_exc = None
    for _attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            conn = pymysql.connect(
                host=host,
                user=admin_user,
                password=admin_password,
                port=3306,
                charset="utf8mb4",
                cursorclass=DictCursor,
                connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
            )
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(DB_CONNECT_RETRY_SECONDS)
    if conn is None:
        raise RuntimeError(f"MySQL connect failed after {DB_CONNECT_ATTEMPTS} attempts: {last_exc}")
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{safe_schema}`")
            cur.execute(
                "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
                (app_username, app_password),
            )
            cur.execute(f"GRANT ALL PRIVILEGES ON `{safe_schema}`.* TO %s@'%%'", (app_username,))
            cur.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        conn.close()


def setup_database_postgresql(
    *,
    host: str,
    admin_user: str,
    admin_password: str,
    schema: str,
    app_username: str,
    app_password: str,
) -> None:
    max_db_connect_attempts = POSTGRES_DB_CONNECT_ATTEMPTS
    db_connect_retry_seconds = POSTGRES_DB_CONNECT_RETRY_SECONDS
    _safe_ident(schema)
    _safe_ident(app_username)
    conn = psycopg2.connect(
        host=host,
        user=admin_user,
        password=admin_password,
        port=5432,
        dbname="postgres",
        connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        sslmode="require",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_username,))
            if cur.fetchone() is None:
                q = psql.SQL("CREATE ROLE {name} WITH LOGIN PASSWORD %s").format(
                    name=psql.Identifier(app_username),
                )
                cur.execute(q, (app_password,))
            else:
                q = psql.SQL("ALTER ROLE {name} WITH LOGIN PASSWORD %s").format(
                    name=psql.Identifier(app_username),
                )
                cur.execute(q, (app_password,))

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (schema,))
            if cur.fetchone() is None:
                cur.execute(
                    psql.SQL("CREATE DATABASE {db} OWNER {owner}").format(
                        db=psql.Identifier(schema),
                        owner=psql.Identifier(app_username),
                    )
                )
    finally:
        conn.close()

    # Ensure runtime role can connect and run migrations in the target DB.
    # After CREATE DATABASE, RDS can take a short time before accepting connections.
    last_exc = None
    for _attempt in range(1, max_db_connect_attempts + 1):
        try:
            db_conn = psycopg2.connect(
                host=host,
                user=admin_user,
                password=admin_password,
                port=5432,
                dbname=schema,
                connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
                sslmode="require",
            )
            db_conn.autocommit = True
            try:
                with db_conn.cursor() as cur:
                    cur.execute(
                        psql.SQL("GRANT CONNECT, TEMP ON DATABASE {db} TO {user}").format(
                            db=psql.Identifier(schema),
                            user=psql.Identifier(app_username),
                        )
                    )
                    cur.execute(
                        psql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {user}").format(
                            user=psql.Identifier(app_username),
                        )
                    )
            finally:
                db_conn.close()
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(db_connect_retry_seconds)
    raise RuntimeError(
        f"Failed connecting to newly created database '{schema}' after {max_db_connect_attempts} attempts: {last_exc}"
    )
