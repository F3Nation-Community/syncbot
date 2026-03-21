"""
Custom CloudFormation resource: create database and app user on an existing RDS host.

Supports MySQL (port 3306) and PostgreSQL / Aurora DSQL (port 5432). Uses bootstrap
credentials to create the database and a dedicated app user; the app password is read
from the generated Secrets Manager secret.
"""

import json
import re

import boto3
import psycopg2
import pymysql
from psycopg2 import sql as psql
from pymysql.cursors import DictCursor


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
            raise RuntimeError(
                f"Unhandled error in handler: {e}; failed to notify CloudFormation: {send_err}"
            ) from e
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
    schema = (props.get("Schema") or "syncbot").strip()
    stage = (props.get("Stage") or "test").strip()
    secret_arn = (props.get("SecretArn") or "").strip()
    database_engine = (props.get("DatabaseEngine") or "postgresql").strip().lower()

    if request_type == "Delete":
        # Must return the same PhysicalResourceId as Create; never use a placeholder.
        delete_pid = event.get("PhysicalResourceId") or event["LogicalResourceId"]
        send(event, context, "SUCCESS", {"Username": ""}, physical_resource_id=delete_pid)
        return

    if not all([host, admin_user, admin_password, schema, stage, secret_arn]):
        send(
            event,
            context,
            "FAILED",
            reason="Missing Host, AdminUser, AdminPassword, Schema, Stage, or SecretArn",
        )
        return

    app_username = f"syncbot_{stage}".replace("-", "_")
    try:
        app_password = get_app_password(secret_arn)
    except Exception as e:
        send(event, context, "FAILED", reason=f"GetSecretValue failed: {e}")
        return

    try:
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


def get_app_password(secret_arn: str) -> str:
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=secret_arn)
    return (resp.get("SecretString") or "").strip()


def setup_database_mysql(
    *,
    host: str,
    admin_user: str,
    admin_password: str,
    schema: str,
    app_username: str,
    app_password: str,
) -> None:
    conn = pymysql.connect(
        host=host,
        user=admin_user,
        password=admin_password,
        port=3306,
        charset="utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=15,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}`")
            cur.execute(
                "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
                (app_username, app_password),
            )
            cur.execute(f"GRANT ALL PRIVILEGES ON `{schema}`.* TO %s@'%%'", (app_username,))
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
    _safe_ident(schema)
    _safe_ident(app_username)
    conn = psycopg2.connect(
        host=host,
        user=admin_user,
        password=admin_password,
        port=5432,
        dbname="postgres",
        connect_timeout=15,
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
