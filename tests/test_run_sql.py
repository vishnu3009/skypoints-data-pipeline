"""Unit tests for the generic Snowflake SQL runner."""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIRECTORY / "code" / "run_sql.py"


def load_script_module():
    snowflake_module = types.ModuleType("snowflake")
    connector_module = types.ModuleType("snowflake.connector")
    connector_module.Error = Exception
    connector_module.connect = mock.Mock()
    snowflake_module.connector = connector_module
    dotenv_module = types.ModuleType("dotenv")
    dotenv_module.load_dotenv = mock.Mock()

    with mock.patch.dict(
        sys.modules,
        {
            "snowflake": snowflake_module,
            "snowflake.connector": connector_module,
            "dotenv": dotenv_module,
        },
    ):
        spec = importlib.util.spec_from_file_location(
            "run_sql_under_test", SCRIPT_PATH
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


runner = load_script_module()


def valid_environment():
    return {
        "SNOWFLAKE_ACCOUNT": "test-account",
        "SNOWFLAKE_USER": "test-user",
        "SNOWFLAKE_PASSWORD": "test-password",
        "SNOWFLAKE_WAREHOUSE": "test-warehouse",
        "SNOWFLAKE_DATABASE": "test-database",
        "SNOWFLAKE_SCHEMA": "test-schema",
        "SNOWFLAKE_ROLE": "LOADER_ROLE",
    }


class ConnectionParametersTests(unittest.TestCase):
    def test_reads_required_settings_and_optional_role(self):
        with mock.patch.dict(os.environ, valid_environment(), clear=True):
            with mock.patch.object(runner, "load_dotenv") as load_dotenv:
                parameters = runner.connection_parameters()

        load_dotenv.assert_called_once_with(
            dotenv_path=runner.ENV_FILE, override=False
        )
        self.assertEqual(parameters["account"], "test-account")
        self.assertEqual(parameters["database"], "test-database")
        self.assertEqual(parameters["role"], "LOADER_ROLE")
        self.assertFalse(parameters["autocommit"])

    def test_reports_missing_settings(self):
        environment = valid_environment()
        del environment["SNOWFLAKE_PASSWORD"]
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "SNOWFLAKE_PASSWORD"):
                runner.connection_parameters()


class RunSqlFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)
        self.sql_file = self.directory / "example.sql"
        self.sql_file.write_text("SELECT 1;", encoding="utf-8")
        self.connection = mock.MagicMock()
        self.cursor = mock.MagicMock()
        self.connection.cursor.return_value.__enter__.return_value = self.cursor

    def test_executes_all_statements_and_commits(self):
        self.connection.execute_stream.return_value = iter(
            [mock.MagicMock(description=None), mock.MagicMock(description=None)]
        )
        with mock.patch.object(
            runner, "connection_parameters", return_value={"account": "test"}
        ), mock.patch.object(
            runner.snowflake.connector,
            "connect",
            return_value=self.connection,
        ):
            statement_count = runner.run_sql_file(self.sql_file)

        self.assertEqual(statement_count, 2)
        self.connection.execute_stream.assert_called_once()
        self.connection.commit.assert_called_once_with()
        self.connection.rollback.assert_not_called()
        self.connection.close.assert_called_once_with()

    def test_fail_on_error_stops_when_quality_errors_exist(self):
        self.connection.execute_stream.return_value = iter(
            [mock.MagicMock(description=None)]
        )
        self.cursor.fetchone.return_value = (2,)
        with mock.patch.object(
            runner, "connection_parameters", return_value={"account": "test"}
        ), mock.patch.object(
            runner.snowflake.connector,
            "connect",
            return_value=self.connection,
        ):
            with self.assertRaisesRegex(RuntimeError, "2 ERROR issue"):
                runner.run_sql_file(self.sql_file, fail_on_error=True)

        self.cursor.execute.assert_called_once_with(
            "SELECT COUNT(*) FROM DATA_QUALITY_ISSUES "
            "WHERE SEVERITY = 'ERROR'"
        )
        self.connection.rollback.assert_called_once_with()
        self.connection.close.assert_called_once_with()

    def test_rejects_non_sql_files(self):
        non_sql_file = self.directory / "data.txt"
        non_sql_file.write_text("not sql", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Expected a .sql file"):
            runner.run_sql_file(non_sql_file)
