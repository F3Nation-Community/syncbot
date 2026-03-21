"""Unit tests for infra/aws/db_setup/handler.py (MySQL vs PostgreSQL branches)."""

from unittest.mock import MagicMock, patch

import pytest


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
    with (
        patch("handler.send") as mock_send,
        patch("handler.get_app_password", return_value="apppw"),
        patch("handler.setup_database_mysql") as mock_mysql,
        patch("handler.setup_database_postgresql") as mock_pg,
    ):
        import handler

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
    with patch("handler.send") as mock_send:
        import handler

        handler._handler_impl(delete_event, MagicMock())
    mock_send.assert_called_once()
    assert mock_send.call_args[0][2] == "SUCCESS"
    assert mock_send.call_args[1]["physical_resource_id"] == "syncbot_test"


def test_handler_calls_postgresql_setup(cfn_create_event):
    cfn_create_event["ResourceProperties"]["DatabaseEngine"] = "postgresql"
    with (
        patch("handler.send") as mock_send,
        patch("handler.get_app_password", return_value="apppw"),
        patch("handler.setup_database_mysql") as mock_mysql,
        patch("handler.setup_database_postgresql") as mock_pg,
    ):
        import handler

        handler._handler_impl(cfn_create_event, MagicMock())
        mock_pg.assert_called_once()
        mock_mysql.assert_not_called()
        assert mock_send.call_args[0][2] == "SUCCESS"
