"""Unit tests for infra/aws/db_setup/handler.py (MySQL vs PostgreSQL branches)."""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

# handler.py does ``import psycopg2`` at the top level. The package
# (psycopg2-binary) may not ship wheels for every Python version
# (e.g. 3.14). Stub the module so the import succeeds regardless.
if "psycopg2" not in sys.modules:
    _pg_stub = MagicMock()
    _pg_stub.sql = MagicMock()
    sys.modules["psycopg2"] = _pg_stub
    sys.modules["psycopg2.sql"] = _pg_stub.sql


def _fresh_handler():
    """(Re-)import handler so patches take effect."""
    if "handler" in sys.modules:
        return importlib.reload(sys.modules["handler"])
    import handler

    return handler


@pytest.fixture
def cfn_create_event():
    return {
        "RequestType": "Create",
        "ResponseURL": "https://example.invalid/",
        "StackId": "arn:aws:cloudformation:us-east-1:123:stack/x",
        "RequestId": "req",
        "LogicalResourceId": "AppDbSetup",
        "ResourceProperties": {
            "Host": "db.example.com",
            "AdminUser": "admin",
            "AdminPassword": "adminpw",
            "Schema": "syncbot_test",
            "Stage": "test",
            "SecretArn": "arn:aws:secretsmanager:us-east-1:123:secret:x",
            "DatabaseEngine": "mysql",
        },
    }


def test_handler_calls_mysql_setup(cfn_create_event):
    handler = _fresh_handler()
    with (
        patch.object(handler, "send") as mock_send,
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql") as mock_pg,
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
        mock_mysql.assert_called_once()
        mock_pg.assert_not_called()
        assert mock_send.call_args[0][2] == "SUCCESS"


def test_handler_delete_uses_physical_resource_id():
    """Delete must echo PhysicalResourceId from Create; never a placeholder."""
    delete_event = {
        "RequestType": "Delete",
        "ResponseURL": "https://example.invalid/",
        "StackId": "arn:aws:cloudformation:us-east-1:123:stack/x",
        "RequestId": "req",
        "LogicalResourceId": "AppDbSetup",
        "PhysicalResourceId": "syncbot_test",
    }
    handler = _fresh_handler()
    with patch.object(handler, "send") as mock_send:
        handler._handler_impl(delete_event, MagicMock())
    mock_send.assert_called_once()
    assert mock_send.call_args[0][2] == "SUCCESS"
    assert mock_send.call_args[1]["physical_resource_id"] == "syncbot_test"


def test_handler_calls_postgresql_setup(cfn_create_event):
    cfn_create_event["ResourceProperties"]["DatabaseEngine"] = "postgresql"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send") as mock_send,
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql") as mock_pg,
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
        mock_pg.assert_called_once()
        mock_mysql.assert_not_called()
        assert mock_send.call_args[0][2] == "SUCCESS"


def test_safe_username_accepts_dotted_prefix():
    handler = _fresh_handler()
    handler._safe_username("42bvZAUSurKwhxc.syncbot_user_test")


def test_safe_ident_rejects_dots():
    handler = _fresh_handler()
    with pytest.raises(ValueError, match="Invalid identifier"):
        handler._safe_ident("bad.schema")


def test_handler_username_prefix_with_dot(cfn_create_event):
    cfn_create_event["ResourceProperties"]["UsernamePrefix"] = "pre."
    handler = _fresh_handler()
    with (
        patch.object(handler, "send"),
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql"),
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    assert mock_mysql.call_args[1]["app_username"] == "pre.syncbot_user_test"
    assert mock_mysql.call_args[1]["admin_user"] == "pre.admin"


def test_handler_username_prefix_without_dot(cfn_create_event):
    cfn_create_event["ResourceProperties"]["UsernamePrefix"] = "pre"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send"),
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql"),
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    assert mock_mysql.call_args[1]["app_username"] == "pre.syncbot_user_test"
    assert mock_mysql.call_args[1]["admin_user"] == "pre.admin"


def test_handler_username_prefix_applied_to_bare_root_admin(cfn_create_event):
    cfn_create_event["ResourceProperties"]["AdminUser"] = "root"
    cfn_create_event["ResourceProperties"]["UsernamePrefix"] = "cluster"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send"),
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql"),
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    assert mock_mysql.call_args[1]["admin_user"] == "cluster.root"
    assert mock_mysql.call_args[1]["app_username"] == "cluster.syncbot_user_test"


def test_handler_custom_port_passed_to_tcp_and_mysql(cfn_create_event):
    cfn_create_event["ResourceProperties"]["Port"] = "4000"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send"),
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable") as mock_tcp,
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql"),
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    mock_tcp.assert_called_once_with("db.example.com", 4000)
    assert mock_mysql.call_args[1]["port"] == 4000


def test_handler_mysql_create_schema_false(cfn_create_event):
    cfn_create_event["ResourceProperties"]["CreateSchema"] = "false"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send"),
        patch.object(handler, "get_secret_value", return_value="apppw"),
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql"),
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    assert mock_mysql.call_args[1]["create_schema"] is False


def test_handler_put_secret_when_no_app_user(cfn_create_event):
    cfn_create_event["ResourceProperties"]["CreateAppUser"] = "false"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send") as mock_send,
        patch.object(handler, "get_secret_value") as mock_get,
        patch.object(handler, "_assert_tcp_reachable"),
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql") as mock_pg,
        patch.object(handler, "put_secret_string") as mock_put,
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    mock_get.assert_not_called()
    mock_mysql.assert_called_once()
    assert mock_mysql.call_args[1]["create_app_user"] is False
    mock_pg.assert_not_called()
    mock_put.assert_called_once_with(
        cfn_create_event["ResourceProperties"]["SecretArn"],
        "adminpw",
    )
    assert mock_send.call_args[0][2] == "SUCCESS"
    assert mock_send.call_args[0][3] == {"Username": "admin"}


def test_handler_skip_both_no_db_client(cfn_create_event):
    cfn_create_event["ResourceProperties"]["CreateAppUser"] = "false"
    cfn_create_event["ResourceProperties"]["CreateSchema"] = "false"
    handler = _fresh_handler()
    with (
        patch.object(handler, "send") as mock_send,
        patch.object(handler, "get_secret_value") as mock_get,
        patch.object(handler, "_assert_tcp_reachable") as mock_tcp,
        patch.object(handler, "setup_database_mysql") as mock_mysql,
        patch.object(handler, "setup_database_postgresql") as mock_pg,
        patch.object(handler, "put_secret_string") as mock_put,
    ):
        handler._handler_impl(cfn_create_event, MagicMock())
    mock_get.assert_not_called()
    mock_mysql.assert_not_called()
    mock_pg.assert_not_called()
    mock_tcp.assert_called_once_with("db.example.com", 3306)
    mock_put.assert_called_once_with(
        cfn_create_event["ResourceProperties"]["SecretArn"],
        "adminpw",
    )
    assert mock_send.call_args[0][2] == "SUCCESS"
    assert mock_send.call_args[0][3] == {"Username": "admin"}
