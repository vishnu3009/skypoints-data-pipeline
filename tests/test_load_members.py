"""Unit tests for the marked pipe-delimited member-feed loader."""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIRECTORY / "code" / "load_members.py"
MEMBER_COLUMNS = (
    "Member_Name",
    "Member_Id",
    "Enrollment_Date",
    "Last_Flight_Date",
    "Tier_Code",
    "Agent_Name",
    "State",
    "Country",
    "DOB",
    "Is_Active",
)


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
            "load_members_under_test", SCRIPT_PATH
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
        "SNOWFLAKE_STAGE": "@MEMBER_STAGE",
        "SNOWFLAKE_TABLE": "MEMBER_RAW",
        "SNOWFLAKE_FILE_FORMAT": "PIPE_DELIMITED_FORMAT",
        "MEMBER_FILE_PATTERN": "member_*.txt",
        "MEMBER_DELIMITER": "|",
        "MEMBER_COLUMNS": ",".join(MEMBER_COLUMNS),
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
        stage="@MEMBER_STAGE",
        table="MEMBER_RAW",
        file_format="PIPE_DELIMITED_FORMAT",
        member_file_pattern="member_*.txt",
        delimiter="|",
        member_columns=MEMBER_COLUMNS,
    )


def detail_record(
    name="Elena",
    member_id="223457",
    enrollment_date="20101012",
    last_flight_date="20121013",
    tier="GLD",
    agent="Sam",
    state="CA",
    country="USA",
    dob="03051985",
    active="A",
):
    fields = (
        name,
        member_id,
        enrollment_date,
        last_flight_date,
        tier,
        agent,
        state,
        country,
        dob,
        active,
    )
    return "|D|" + "|".join(fields)


def marked_file(*records):
    return "|H|" + "|".join(MEMBER_COLUMNS) + "\n" + "\n".join(records) + "\n"


class SnowflakeConfigTests(unittest.TestCase):
    def test_loads_environment_and_preserves_shell_values(self):
        with mock.patch.dict(os.environ, valid_environment(), clear=True):
            with mock.patch.object(loader, "load_dotenv") as load_dotenv:
                config = loader.SnowflakeConfig.from_environment()

        load_dotenv.assert_called_once_with(
            dotenv_path=loader.ENV_FILE, override=False
        )
        self.assertEqual(config.member_columns, MEMBER_COLUMNS)

    def test_password_connection_parameters_are_configured(self):
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

    def test_optional_role_is_included_when_present(self):
        environment = valid_environment()
        environment["SNOWFLAKE_ROLE"] = "LOADER_ROLE"
        with mock.patch.dict(os.environ, environment, clear=True):
            config = loader.SnowflakeConfig.from_environment()

        self.assertEqual(config.connection_parameters()["role"], "LOADER_ROLE")

    def test_missing_setting_is_reported(self):
        environment = valid_environment()
        del environment["SNOWFLAKE_PASSWORD"]
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "SNOWFLAKE_PASSWORD"):
                loader.SnowflakeConfig.from_environment()

    def test_non_pipe_delimiter_is_rejected(self):
        environment = valid_environment()
        environment["MEMBER_DELIMITER"] = ","
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "MEMBER_DELIMITER"):
                loader.SnowflakeConfig.from_environment()

    def test_wrong_pattern_is_rejected(self):
        environment = valid_environment()
        environment["MEMBER_FILE_PATTERN"] = "*.csv"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "MEMBER_FILE_PATTERN"):
                loader.SnowflakeConfig.from_environment()

    def test_invalid_snowflake_identifier_is_rejected(self):
        environment = valid_environment()
        environment["SNOWFLAKE_TABLE"] = "MEMBER_RAW; DROP TABLE USERS"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "SNOWFLAKE_TABLE"):
                loader.SnowflakeConfig.from_environment()

    def test_invalid_member_column_is_rejected(self):
        environment = valid_environment()
        environment["MEMBER_COLUMNS"] = "Member_Name,Member_Id; DROP TABLE"
        with mock.patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "MEMBER_COLUMNS"):
                loader.SnowflakeConfig.from_environment()


class FindMemberFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_directory.name)
        self.addCleanup(self.temp_directory.cleanup)
        self.config = sample_config()
        self.directory_patch = mock.patch.object(
            loader, "DATA_DIRECTORY", self.directory
        )
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)

    def test_discovers_member_txt_files_only(self):
        expected = self.directory / "member_20240115.txt"
        expected.write_text(marked_file(detail_record()), encoding="utf-8")
        (self.directory / "other.txt").write_text("ignore", encoding="utf-8")
        (self.directory / "member_20240115.json").write_text("{}", encoding="utf-8")

        self.assertEqual(loader.find_member_files(self.config), [expected.resolve()])

    def test_reports_missing_data_directory(self):
        with mock.patch.object(loader, "DATA_DIRECTORY", self.directory / "missing"):
            with self.assertRaisesRegex(FileNotFoundError, "Data directory"):
                loader.find_member_files(self.config)

    def test_reports_no_matching_files(self):
        with self.assertRaisesRegex(FileNotFoundError, "No files matching"):
            loader.find_member_files(self.config)

    def test_rejects_filename_without_date(self):
        (self.directory / "member_batch1.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "member_YYYYMMDD.txt"):
            loader.find_member_files(self.config)

    def test_rejects_invalid_calendar_date_in_filename(self):
        (self.directory / "member_20230229.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "calendar date"):
            loader.find_member_files(self.config)


class ParseMemberFileTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_directory.name)
        self.addCleanup(self.temp_directory.cleanup)
        self.config = sample_config()

    def make_file(self, content, filename="member_20240115.txt"):
        path = self.directory / filename
        path.write_text(content, encoding="utf-8")
        return path

    def test_parses_all_member_fields_and_converts_dates_for_snowflake(self):
        path = self.make_file(marked_file(detail_record()))

        rows = loader.parse_member_file(path, self.config)

        self.assertEqual(
            rows,
            [
                (
                    "Elena",
                    "223457",
                    "2010-10-12",
                    "2012-10-13",
                    "GLD",
                    "Sam",
                    "CA",
                    "USA",
                    "1985-03-05",
                    "A",
                )
            ],
        )

    def test_sample_result_contains_multiple_members(self):
        content = marked_file(
            detail_record(),
            detail_record(
                name="Ravi",
                member_id="223458",
                tier="SLV",
                state="TN",
                country="IND",
            ),
            detail_record(
                name="Mateo",
                member_id="223459",
                state="NCR",
                country="PHIL",
            ),
            detail_record(
                name="Nora",
                member_id="22345",
                tier="PLT",
                state="ONT",
                country="CAN",
            ),
            detail_record(
                name="Jacob",
                member_id="2256",
                tier="SLV",
                state="VIC",
                country="AU",
            ),
        )

        rows = loader.parse_member_file(self.make_file(content), self.config)

        self.assertEqual(len(rows), 5)
        self.assertEqual([row[0] for row in rows], ["Elena", "Ravi", "Mateo", "Nora", "Jacob"])
        self.assertEqual([row[1] for row in rows], ["223457", "223458", "223459", "22345", "2256"])

    def test_record_markers_are_not_in_output(self):
        rows = loader.parse_member_file(
            self.make_file(marked_file(detail_record())), self.config
        )
        self.assertEqual(len(rows[0]), 10)
        self.assertNotIn("D", rows[0])

    def test_quoted_pipe_in_member_name_is_supported(self):
        record = detail_record(name="Elena|Ann")
        record = record.replace("Elena|Ann", '"Elena|Ann"', 1)
        rows = loader.parse_member_file(
            self.make_file(marked_file(record)), self.config
        )
        self.assertEqual(rows[0][0], "Elena|Ann")

    def test_rejects_empty_file(self):
        path = self.make_file("")
        with self.assertRaisesRegex(ValueError, "empty"):
            loader.parse_member_file(path, self.config)

    def test_rejects_missing_header(self):
        path = self.make_file(detail_record() + "\n")
        with self.assertRaisesRegex(ValueError, "before header"):
            loader.parse_member_file(path, self.config)

    def test_rejects_header_mismatch(self):
        path = self.make_file("|H|Name|Member_Id\n" + detail_record() + "\n")
        with self.assertRaisesRegex(ValueError, "Invalid header"):
            loader.parse_member_file(path, self.config)

    def test_rejects_duplicate_header(self):
        header = "|H|" + "|".join(MEMBER_COLUMNS)
        path = self.make_file(header + "\n" + header + "\n" + detail_record() + "\n")
        with self.assertRaisesRegex(ValueError, "exactly once"):
            loader.parse_member_file(path, self.config)

    def test_rejects_unknown_marker(self):
        path = self.make_file("|X|name\n")
        with self.assertRaisesRegex(ValueError, "Unknown record marker"):
            loader.parse_member_file(path, self.config)

    def test_rejects_wrong_data_field_count(self):
        path = self.make_file(
            "|H|" + "|".join(MEMBER_COLUMNS) + "\n|D|Elena|223457\n"
        )
        with self.assertRaisesRegex(ValueError, "expected 10 member fields"):
            loader.parse_member_file(path, self.config)

    def test_rejects_empty_required_field(self):
        path = self.make_file(marked_file(detail_record(name="")))
        with self.assertRaisesRegex(ValueError, "Member_Name.*empty"):
            loader.parse_member_file(path, self.config)

    def test_member_id_must_contain_digits(self):
        path = self.make_file(marked_file(detail_record(member_id="22A")))
        with self.assertRaisesRegex(ValueError, "MEMBER_ID must be an integer"):
            loader.parse_member_file(path, self.config)

    def test_rejects_invalid_enrollment_date(self):
        path = self.make_file(marked_file(detail_record(enrollment_date="20190229")))
        with self.assertRaisesRegex(ValueError, "valid date"):
            loader.parse_member_file(path, self.config)

    def test_rejects_wrong_date_format(self):
        path = self.make_file(marked_file(detail_record(dob="19850305")))
        with self.assertRaisesRegex(ValueError, "DOB must be a valid date in %m%d%Y"):
            loader.parse_member_file(path, self.config)

    def test_country_is_required_but_not_allowlist_validated(self):
        rows = loader.parse_member_file(
            self.make_file(marked_file(detail_record(country="unknown"))),
            self.config,
        )
        self.assertEqual(rows[0][7], "unknown")

    def test_non_utf8_data_is_rejected(self):
        path = self.directory / "member_20240115.txt"
        path.write_bytes(b"\xff|D|Elena\n")
        with self.assertRaisesRegex(ValueError, "not valid UTF-8"):
            loader.parse_member_file(path, self.config)

    def test_malformed_quoted_value_is_rejected(self):
        path = self.make_file(
            "|H|" + "|".join(MEMBER_COLUMNS) + "\n"
            + detail_record(name='"Elena') + "\n"
        )
        with self.assertRaisesRegex(ValueError, "Malformed pipe-delimited"):
            loader.parse_member_file(path, self.config)

    def test_validation_returns_number_of_data_records(self):
        path = self.make_file(marked_file(detail_record(), detail_record(name="Ravi")))
        self.assertEqual(loader.validate_member_file(path, self.config), 2)

    def test_header_with_no_data_records_is_rejected(self):
        path = self.make_file("|H|" + "|".join(MEMBER_COLUMNS) + "\n")
        with self.assertRaisesRegex(ValueError, "no data records"):
            loader.parse_member_file(path, self.config)


class LoadMembersTests(unittest.TestCase):
    def setUp(self):
        self.temp_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp_directory.name)
        self.addCleanup(self.temp_directory.cleanup)
        self.directory_patch = mock.patch.object(
            loader, "DATA_DIRECTORY", self.directory
        )
        self.directory_patch.start()
        self.addCleanup(self.directory_patch.stop)
        self.config = sample_config()

    def make_connection(self):
        connection = mock.MagicMock()
        cursor = mock.MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        cursor.fetchall.return_value = [("success",)]
        cursor.fetchone.return_value = (5,)
        return connection, cursor

    def write_file(self, name, content):
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_uploads_normalized_data_and_copies_to_configured_columns(self):
        self.write_file(
            "member_20240115.txt",
            marked_file(detail_record()),
        )
        connection, cursor = self.make_connection()

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ) as connect:
            count = loader.load_members(self.config)

        self.assertEqual(count, 5)
        connect.assert_called_once_with(**self.config.connection_parameters())
        sql_statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(len(sql_statements), 7)
        self.assertEqual(sql_statements[0], "USE SCHEMA VISHNU_DB.INSURANCE_EH_SCHEMA")
        self.assertIn("CREATE TEMPORARY TABLE TEMP_MEMBER_LOAD", sql_statements[1])
        self.assertIn("PUT ", sql_statements[2])
        self.assertIn("COPY INTO TEMP_MEMBER_LOAD", sql_statements[3])
        self.assertIn("Member_Name, Member_Id", sql_statements[3])
        self.assertIn("FIELD_DELIMITER = '|'", sql_statements[3])
        self.assertIn("DATE_FORMAT = 'YYYY-MM-DD'", sql_statements[3])
        self.assertIn("SKIP_HEADER = 0", sql_statements[3])
        self.assertIn("DELETE FROM MEMBER_RAW", sql_statements[4])
        self.assertIn("INSERT INTO MEMBER_RAW", sql_statements[5])
        self.assertIn("SELECT COUNT(*) FROM MEMBER_RAW", sql_statements[6])
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_multiple_member_files_are_loaded_into_one_idempotent_batch(self):
        self.write_file(
            "member_20240115.txt",
            marked_file(detail_record(member_id="223457")),
        )
        self.write_file(
            "member_20240116.txt",
            marked_file(detail_record(name="Elena Updated", member_id="223457")),
        )
        connection, cursor = self.make_connection()

        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            loader.load_members(self.config)

        sql_statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(
            sum("COPY INTO TEMP_MEMBER_LOAD" in sql for sql in sql_statements),
            2,
        )
        self.assertEqual(
            sum("DELETE FROM MEMBER_RAW" in sql for sql in sql_statements),
            1,
        )
        self.assertEqual(
            sum("INSERT INTO MEMBER_RAW" in sql for sql in sql_statements),
            1,
        )
        connection.commit.assert_called_once_with()

    def test_invalid_input_does_not_connect(self):
        self.write_file("member_20240115.txt", "|D|Elena\n")
        with mock.patch.object(loader.snowflake.connector, "connect") as connect:
            with self.assertRaisesRegex(ValueError, "before header"):
                loader.load_members(self.config)
        connect.assert_not_called()

    def test_connection_closes_if_snowflake_operation_fails(self):
        self.write_file("member_20240115.txt", marked_file(detail_record()))
        connection, cursor = self.make_connection()
        cursor.execute.side_effect = RuntimeError("Snowflake failure")
        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            with self.assertRaisesRegex(RuntimeError, "Snowflake failure"):
                loader.load_members(self.config)
        connection.rollback.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_missing_validation_result_raises_and_closes(self):
        self.write_file("member_20240115.txt", marked_file(detail_record()))
        connection, cursor = self.make_connection()
        cursor.fetchone.return_value = None
        with mock.patch.object(
            loader.snowflake.connector, "connect", return_value=connection
        ):
            with self.assertRaisesRegex(RuntimeError, "no result"):
                loader.load_members(self.config)
        connection.close.assert_called_once_with()


class MainTests(unittest.TestCase):
    def test_prints_success_note(self):
        config = sample_config()
        with mock.patch.object(
            loader.SnowflakeConfig, "from_environment", return_value=config
        ), mock.patch.object(loader, "load_members", return_value=5), mock.patch(
            "builtins.print"
        ) as print_note:
            result = loader.main()

        self.assertEqual(result, 0)
        print_note.assert_called_once_with(
            "Member load completed successfully. "
            "MEMBER_RAW currently contains 5 record(s)."
        )

    def test_prints_failure_note(self):
        with mock.patch.object(
            loader.SnowflakeConfig,
            "from_environment",
            side_effect=ValueError("Invalid configuration"),
        ), mock.patch("builtins.print") as print_note:
            result = loader.main()

        self.assertEqual(result, 1)
        print_note.assert_called_once_with(
            "Member load failed: Invalid configuration"
        )


if __name__ == "__main__":
    unittest.main()
