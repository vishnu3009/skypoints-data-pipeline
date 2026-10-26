"""Unit tests for the JSON redemption-feed loader."""

import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIRECTORY / "code" / "load_redemptions.py"


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
            "load_redemptions_under_test", SCRIPT_PATH
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


loader = load_script_module()


def valid_environment():
    return {
        "SNOWFLAKE_ACCOUNT": "test-account",
        "SNOWFLAKE_USER": "test-user",
        "SNOWFLAKE_PASSWORD": "test-password",
        "SNOWFLAKE_WAREHOUSE": "test-warehouse",
        "SNOWFLAKE_ROLE": "",
        "SNOWFLAKE_DATABASE": "VISHNU_DB",
        "SNOWFLAKE_SCHEMA": "INSURANCE_EH_SCHEMA",
        "SNOWFLAKE_REDEMPTIONS_TABLE": "REDEMPTIONS_RAW",
    }


def sample_config():
    return loader.SnowflakeConfig(
        account="test-account",
        user="test-user",
        password="test-password",
        warehouse="test-warehouse",
        role="",
        database="VISHNU_DB",
        schema="INSURANCE_EH_SCHEMA",
        table="REDEMPTIONS_RAW",
    )


def sample_feed():
    return {
        "member_id": "223457",
        "feed_date": "20240115",
        "redemptions": [
            {
                "txn_id": "RX10091",
                "txn_date": "20240110",
                "partner": "AeroLink",
                "miles_redeemed": 12000,
                "status": "COMPLETED",
            },
            {
                "txn_id": "RX10092",
                "txn_date": "20240113",
                "partner": "SkyPoints",
                "miles_redeemed": 5000,
                "status": "PENDING",
            },
        ],
    }


class SnowflakeConfigTests(unittest.TestCase):
    def test_reads_settings_and_preserves_shell_environment(self):
        with mock.patch.dict(os.environ, valid_environment(), clear=True):
            with mock.patch.object(loader, "load_dotenv") as load_dotenv:
                config = loader.SnowflakeConfig.from_environment()

        load_dotenv.assert_called_once_with(
            dotenv_path=loader.ENV_FILE,
            override=False,
        )
        self.assertEqual(config.table, "REDEMPTIONS_RAW")

    def test_password_connection_parameters(self):
        self.assertEqual(
            sample_config().connection_parameters(),
            {
                "account": "test-account",
                "user": "test-user",
                "password": "test-password",
                "warehouse": "test-warehouse",
                "database": "VISHNU_DB",
                "schema": "INSURANCE_EH_SCHEMA",
                "autocommit": False,
            },
        )

    def test_optional_role_is_included_when_set(self):
        environment = valid_environment()
        environment["SNOWFLAKE_ROLE"] = "LOADER_ROLE"
        with mock.patch.dict(os.environ, environment, clear=True):
            config = loader.SnowflakeConfig.from_environment()

        self.assertEqual(config.connection_parameters()["role"], "LOADER_ROLE")

    def test_missing_required_setting_is_reported(self):
        environment = valid_environment()
        del environment["SNOWFLAKE_REDEMPTIONS_TABLE"]
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                ValueError, "SNOWFLAKE_REDEMPTIONS_TABLE"
            ):
                loader.SnowflakeConfig.from_environment()

    def test_invalid_table_identifier_is_rejected(self):
        environment = valid_environment()
        environment["SNOWFLAKE_REDEMPTIONS_TABLE"] = "RAW; DROP TABLE USERS"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                ValueError, "simple Snowflake identifier"
            ):
                loader.SnowflakeConfig.from_environment()


class ParseRedemptionFeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)

    def write_feed(self, value, name="redemption_20240115.json"):
        path = self.directory / name
        if isinstance(value, bytes):
            path.write_bytes(value)
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_flattens_sample_feed_to_one_row_per_redemption(self):
        rows = loader.parse_redemption_feed(
            self.write_feed(sample_feed())
        )

        self.assertEqual(
            rows,
            [
                (
                    "223457",
                    date(2024, 1, 15),
                    "RX10091",
                    date(2024, 1, 10),
                    "AeroLink",
                    12000,
                    "COMPLETED",
                ),
                (
                    "223457",
                    date(2024, 1, 15),
                    "RX10092",
                    date(2024, 1, 13),
                    "SkyPoints",
                    5000,
                    "PENDING",
                ),
            ],
        )

    def test_accepts_integer_member_id_and_preserves_as_string(self):
        feed = sample_feed()
        feed["member_id"] = 223457

        rows = loader.parse_redemption_feed(self.write_feed(feed))

        self.assertEqual(rows[0][0], "223457")

    def test_empty_redemptions_array_returns_no_rows(self):
        feed = sample_feed()
        feed["redemptions"] = []

        self.assertEqual(loader.parse_redemption_feed(self.write_feed(feed)), [])

    def test_empty_file_is_rejected(self):
        path = self.directory / "empty.json"
        path.write_bytes(b"")

        with self.assertRaisesRegex(ValueError, "is empty"):
            loader.parse_redemption_feed(path)

    def test_invalid_json_is_rejected(self):
        path = self.write_feed(b'{"member_id":')

        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            loader.parse_redemption_feed(path)

    def test_non_object_json_is_rejected(self):
        path = self.write_feed([sample_feed()])

        with self.assertRaisesRegex(ValueError, "JSON object"):
            loader.parse_redemption_feed(path)

    def test_member_id_is_required(self):
        feed = sample_feed()
        del feed["member_id"]

        with self.assertRaisesRegex(ValueError, "member_id"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_empty_member_id_is_rejected(self):
        feed = sample_feed()
        feed["member_id"] = " "

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_boolean_member_id_is_rejected(self):
        feed = sample_feed()
        feed["member_id"] = True

        with self.assertRaisesRegex(ValueError, "member_id"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_non_numeric_member_id_is_rejected(self):
        feed = sample_feed()
        feed["member_id"] = "member-223457"

        with self.assertRaisesRegex(ValueError, "must contain only digits"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_feed_date_must_have_yyyymmdd_format(self):
        feed = sample_feed()
        feed["feed_date"] = "2024-01-15"

        with self.assertRaisesRegex(ValueError, "YYYYMMDD"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_nonexistent_feed_calendar_date_is_rejected(self):
        feed = sample_feed()
        feed["feed_date"] = "20240230"

        with self.assertRaisesRegex(ValueError, "valid calendar date"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_redemptions_must_be_an_array(self):
        feed = sample_feed()
        feed["redemptions"] = {}

        with self.assertRaisesRegex(ValueError, "must be a JSON array"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_redemption_entry_must_be_an_object(self):
        feed = sample_feed()
        feed["redemptions"] = ["not an object"]

        with self.assertRaisesRegex(ValueError, "must be a JSON object"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_missing_required_transaction_field_is_rejected(self):
        feed = sample_feed()
        del feed["redemptions"][0]["txn_id"]

        with self.assertRaisesRegex(ValueError, "txn_id"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_blank_transaction_field_is_rejected(self):
        feed = sample_feed()
        feed["redemptions"][0]["partner"] = " "

        with self.assertRaisesRegex(ValueError, "partner"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_transaction_date_must_be_valid(self):
        feed = sample_feed()
        feed["redemptions"][0]["txn_date"] = "20240230"

        with self.assertRaisesRegex(ValueError, "valid calendar date"):
            loader.parse_redemption_feed(self.write_feed(feed))

    def test_miles_redeemed_must_be_non_negative_integer(self):
        for invalid_value in (-1, 1.5, "12000", True, None):
            with self.subTest(value=invalid_value):
                feed = sample_feed()
                feed["redemptions"][0]["miles_redeemed"] = invalid_value
                with self.assertRaisesRegex(ValueError, "non-negative integer"):
                    loader.parse_redemption_feed(self.write_feed(feed))

    def test_invalid_utf8_is_rejected(self):
        path = self.write_feed(b"\xff")

        with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
            loader.parse_redemption_feed(path)


class FindRedemptionFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory_patch = mock.patch.object(
            loader, "DATA_DIRECTORY", self.directory
        )
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)

    def test_discovers_only_matching_json_files(self):
        expected = self.directory / "redemption_20240115.json"
        expected.write_text("{}", encoding="utf-8")
        (self.directory / "other.json").write_text("{}", encoding="utf-8")
        (self.directory / "redemption_20240115.txt").write_text(
            "{}", encoding="utf-8"
        )

        self.assertEqual(loader.find_redemption_files(), [expected.resolve()])

    def test_reports_missing_data_directory(self):
        missing = self.directory / "missing"
        with mock.patch.object(loader, "DATA_DIRECTORY", missing):
            with self.assertRaisesRegex(FileNotFoundError, "Data directory"):
                loader.find_redemption_files()

    def test_reports_when_no_matching_files_exist(self):
        with self.assertRaisesRegex(FileNotFoundError, "No files matching"):
            loader.find_redemption_files()


class LoadRedemptionsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory_patch = mock.patch.object(
            loader, "DATA_DIRECTORY", self.directory
        )
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        self.config = sample_config()

    def write_feed(self, feed, name="redemption_20240115.json"):
        path = self.directory / name
        path.write_text(json.dumps(feed), encoding="utf-8")
        return path

    def make_connection(self):
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection, cursor

    def test_inserts_all_flattened_rows_and_commits(self):
        self.write_feed(sample_feed())
        connection, cursor = self.make_connection()

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ) as connect:
            result = loader.load_redemptions(self.config)

        self.assertEqual(result, 2)
        connect.assert_called_once_with(
            **self.config.connection_parameters()
        )
        sql, rows = cursor.executemany.call_args.args
        self.assertIn("INSERT INTO REDEMPTIONS_RAW", sql)
        self.assertIn("MEMBER_ID, FEED_DATE, TXN_ID", sql)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "223457")
        self.assertEqual(rows[1][2], "RX10092")
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        connection.close.assert_called_once_with()

    def test_multiple_files_are_flattened_before_database_insert(self):
        self.write_feed(sample_feed(), "redemption_20240115.json")
        second_feed = sample_feed()
        second_feed["member_id"] = "999"
        second_feed["redemptions"] = second_feed["redemptions"][:1]
        self.write_feed(second_feed, "redemption_20240116.json")
        connection, cursor = self.make_connection()

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            result = loader.load_redemptions(self.config)

        self.assertEqual(result, 3)
        self.assertEqual(len(cursor.executemany.call_args.args[1]), 3)

    def test_invalid_feed_is_rejected_before_connection(self):
        feed = sample_feed()
        feed["feed_date"] = "bad-date"
        self.write_feed(feed)

        with mock.patch.object(loader.snowflake.connector, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "YYYYMMDD"):
                loader.load_redemptions(self.config)

        connect.assert_not_called()

    def test_empty_redemptions_commit_no_insert(self):
        feed = sample_feed()
        feed["redemptions"] = []
        self.write_feed(feed)
        connection, cursor = self.make_connection()

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            self.assertEqual(loader.load_redemptions(self.config), 0)

        cursor.executemany.assert_not_called()
        connection.commit.assert_not_called()
        connection.close.assert_called_once_with()

    def test_database_failure_rolls_back_and_closes(self):
        self.write_feed(sample_feed())
        connection, cursor = self.make_connection()
        cursor.executemany.side_effect = RuntimeError("insert failed")

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                loader.load_redemptions(self.config)

        connection.rollback.assert_called_once_with()
        connection.commit.assert_not_called()
        connection.close.assert_called_once_with()

    def test_main_prints_success_note(self):
        with mock.patch.object(
            loader.SnowflakeConfig, "from_environment", return_value=self.config
        ), mock.patch.object(
            loader, "load_redemptions", return_value=2
        ), mock.patch("builtins.print") as print_note:
            result = loader.main()

        self.assertEqual(result, 0)
        print_note.assert_called_once_with(
            "Redemption load completed successfully. "
            "2 row(s) inserted into REDEMPTIONS_RAW."
        )

    def test_main_prints_failure_note(self):
        with mock.patch.object(
            loader.SnowflakeConfig,
            "from_environment",
            side_effect=ValueError("Invalid configuration"),
        ), mock.patch("builtins.print") as print_note:
            result = loader.main()

        self.assertEqual(result, 1)
        print_note.assert_called_once_with(
            "Redemption load failed: Invalid configuration"
        )


if __name__ == "__main__":
    unittest.main()
