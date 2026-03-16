"""
Custom CloudFormation resource: create schema and app user on an existing MySQL (RDS) host.

Run during stack create/update when ExistingDatabaseHost is set. Uses bootstrap credentials
to create the schema and a dedicated app user; the app password is read from the generated
Secrets Manager secret (created by the template) so the app Lambda can use it.
"""

import json
import os
import boto3
import pymysql
from pymysql.cursors import DictCursor

# CloudFormation custom resource response helper (no cfnresponse in Lambda by default for Python 3)
def send(event, context, status, data=None, reason=None, physical_resource_id=None):
    import urllib.request
    pid = physical_resource_id or event.get("PhysicalResourceId") or event["LogicalResourceId"]
    body = json.dumps({
        "Status": status,
        "Reason": reason or f"See CloudWatch Log Stream: {context.log_stream_name}",
        "PhysicalResourceId": pid,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode("utf-8")
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"Content-Type": ""},
    )
    with urllib.request.urlopen(req) as f:
        f.read()


def handler(event, context):
    request_type = event.get("RequestType", "Create")
    props = event.get("ResourceProperties", {})
    host = props.get("Host", "").strip()
    admin_user = (props.get("AdminUser") or "").strip()
    admin_password = props.get("AdminPassword") or ""
    schema = (props.get("Schema") or "syncbot").strip()
    stage = (props.get("Stage") or "test").strip()
    secret_arn = (props.get("SecretArn") or "").strip()

    if request_type == "Delete":
        # Leave schema and user for manual cleanup if desired
        send(event, context, "SUCCESS", {"Username": ""}, physical_resource_id=event.get("PhysicalResourceId", "n/a"))
        return

    if not all([host, admin_user, admin_password, schema, stage, secret_arn]):
        send(
            event, context, "FAILED",
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
        setup_database(
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


def setup_database(
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
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}`")
            # MySQL 5.7: CREATE USER ... IDENTIFIED BY; 8.0 supports IF NOT EXISTS
            cur.execute(
                "CREATE USER IF NOT EXISTS %s@'%%' IDENTIFIED BY %s",
                (app_username, app_password),
            )
            cur.execute(f"GRANT ALL PRIVILEGES ON `{schema}`.* TO %s@'%%'", (app_username,))
            cur.execute("FLUSH PRIVILEGES")
        conn.commit()
    finally:
        conn.close()
