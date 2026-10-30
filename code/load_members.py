"""Validate matching member files in data and load them into Snowflake."""

import csv
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import snowflake.connector
from dotenv import load_dotenv


PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
ENV_FILE = PROJECT_DIRECTORY / ".env"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str
    user: str
    password: str
    warehouse: str
    role: str
    database: str
    schema: str
    stage: str
    table: str
    file_format: str
    member_file_pattern: str
    delimiter: str
    member_columns: Tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "SnowflakeConfig":
        load_dotenv(dotenv_path=ENV_FILE, override=False)
        required_variables = {
            "SNOWFLAKE_ACCOUNT": None,
            "SNOWFLAKE_USER": None,
            "SNOWFLAKE_WAREHOUSE": None,
            "SNOWFLAKE_ROLE": "",
            "SNOWFLAKE_DATABASE": None,
            "SNOWFLAKE_SCHEMA": None,
            "SNOWFLAKE_STAGE": None,
            "SNOWFLAKE_TABLE": None,
            "SNOWFLAKE_FILE_FORMAT": None,
            "MEMBER_FILE_PATTERN": None,
            "MEMBER_DELIMITER": None,
            "MEMBER_COLUMNS": None,
            "SNOWFLAKE_PASSWORD": None,
        }
        settings = {
            name: os.environ.get(name, default)
            for name, default in required_variables.items()
        }
        missing = [
            name
            for name, value in settings.items()
            if value is None or (
                name
                in {
                    "SNOWFLAKE_ACCOUNT",
                    "SNOWFLAKE_USER",
                    "SNOWFLAKE_WAREHOUSE",
                    "SNOWFLAKE_DATABASE",
                    "SNOWFLAKE_SCHEMA",
                    "SNOWFLAKE_STAGE",
                    "SNOWFLAKE_TABLE",
                    "SNOWFLAKE_FILE_FORMAT",
                    "MEMBER_FILE_PATTERN",
                    "MEMBER_DELIMITER",
                    "MEMBER_COLUMNS",
                    "SNOWFLAKE_PASSWORD",
                }
                and not value.strip()
            )
        ]
        if missing:
            raise ValueError(
                "Missing or empty setting(s) in {}: {}".format(
                    ENV_FILE, ", ".join(missing)
                )
            )

        delimiter = settings["MEMBER_DELIMITER"]
        if delimiter != "|":
            raise ValueError("MEMBER_DELIMITER must be '|'.")
        if settings["MEMBER_FILE_PATTERN"] != "member_*.txt":
            raise ValueError(
                "MEMBER_FILE_PATTERN must be 'member_*.txt'."
            )
        member_columns = tuple(
            column.strip()
            for column in settings["MEMBER_COLUMNS"].split(",")
            if column.strip()
        )
        if not member_columns:
            raise ValueError("MEMBER_COLUMNS must contain at least one column.")
        for name, value in (
            ("SNOWFLAKE_DATABASE", settings["SNOWFLAKE_DATABASE"]),
            ("SNOWFLAKE_SCHEMA", settings["SNOWFLAKE_SCHEMA"]),
            ("SNOWFLAKE_TABLE", settings["SNOWFLAKE_TABLE"]),
            ("SNOWFLAKE_FILE_FORMAT", settings["SNOWFLAKE_FILE_FORMAT"]),
        ):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value):
                raise ValueError(
                    "{} must be a simple Snowflake identifier.".format(name)
                )
        if not re.fullmatch(
            r"@[A-Za-z_][A-Za-z0-9_$]*", settings["SNOWFLAKE_STAGE"]
        ):
            raise ValueError(
                "SNOWFLAKE_STAGE must be a named stage such as @MEMBER_STAGE."
            )
        for column in member_columns:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", column):
                raise ValueError(
                    "MEMBER_COLUMNS entries must be simple Snowflake identifiers."
                )

        return cls(
            account=settings["SNOWFLAKE_ACCOUNT"],
            user=settings["SNOWFLAKE_USER"],
            password=settings["SNOWFLAKE_PASSWORD"],
            warehouse=settings["SNOWFLAKE_WAREHOUSE"],
            role=settings["SNOWFLAKE_ROLE"],
            database=settings["SNOWFLAKE_DATABASE"],
            schema=settings["SNOWFLAKE_SCHEMA"],
            stage=settings["SNOWFLAKE_STAGE"],
            table=settings["SNOWFLAKE_TABLE"],
            file_format=settings["SNOWFLAKE_FILE_FORMAT"],
            member_file_pattern=settings["MEMBER_FILE_PATTERN"],
            delimiter=delimiter,
            member_columns=member_columns,
        )

    def connection_parameters(self) -> dict:
        parameters = {
            "account": self.account,
            "user": self.user,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
            "autocommit": False,
        }
        if self.role:
            parameters["role"] = self.role
        parameters["password"] = self.password
        return parameters


def find_member_files(
    config: SnowflakeConfig, data_directory: Optional[Path] = None
) -> List[Path]:
    if data_directory is None:
        data_directory = DATA_DIRECTORY
    if not data_directory.is_dir():
        raise FileNotFoundError(
            "Data directory does not exist: {}".format(data_directory)
        )
    matches = []
    for path in sorted(data_directory.glob(config.member_file_pattern)):
        if not path.is_file():
            continue
        validate_member_filename(path)
        matches.append(path.resolve())
    if not matches:
        raise FileNotFoundError(
            "No files matching {} were found in {}.".format(
                config.member_file_pattern, data_directory
            )
        )
    LOGGER.info(
        "Found %s member file(s) matching %s in %s.",
        len(matches),
        config.member_file_pattern,
        data_directory,
    )
    return matches


def validate_member_filename(file_path: Path) -> None:
    match = re.fullmatch(r"member_(\d{8})\.txt", file_path.name)
    if not match:
        raise ValueError(
            "Invalid member filename {}: expected member_YYYYMMDD.txt.".format(
                file_path.name
            )
        )
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError as error:
        raise ValueError(
            "Invalid calendar date in member filename {}.".format(file_path.name)
        ) from error


def validate_member_file(file_path: Path, config: SnowflakeConfig) -> int:
    return len(parse_member_file(file_path, config))


def _parse_member_date(
    value: str, column: str, input_format: str, file_path: Path, line_number: int
) -> str:
    expected_digits = 8
    if len(value) != expected_digits or not value.isdigit():
        raise ValueError(
            "{} must use {} format in {} at line {}.".format(
                column, input_format, file_path.name, line_number
            )
        )
    try:
        return datetime.strptime(value, input_format).strftime("%Y-%m-%d")
    except ValueError as error:
        raise ValueError(
            "{} must be a valid date in {} format in {} at line {}.".format(
                column, input_format, file_path.name, line_number
            )
        ) from error


def parse_member_file(
    file_path: Path, config: SnowflakeConfig
) -> List[Tuple[str, ...]]:
    if file_path.stat().st_size == 0:
        raise ValueError("Members file is empty: {}".format(file_path))

    member_rows = []
    header_found = False
    try:
        with file_path.open("r", encoding="utf-8-sig", newline="") as source:
            rows = csv.reader(source, delimiter=config.delimiter, strict=True)
            for line_number, row in enumerate(rows, start=1):
                if not row or all(not value.strip() for value in row):
                    continue

                if len(row) < 2 or row[0].strip():
                    raise ValueError(
                        "Invalid record prefix in {} at line {}: "
                        "expected a leading delimiter and H or D marker.".format(
                            file_path.name, line_number
                        )
                    )
                record_type = row[1].strip().upper()
                fields = [value.strip() for value in row[2:]]

                if record_type == "H":
                    if header_found or member_rows:
                        raise ValueError(
                            "Header record must appear exactly once before data "
                            "in {} (line {}).".format(file_path.name, line_number)
                        )
                    normalized_header = tuple(value.upper() for value in fields)
                    expected_header = tuple(
                        column.upper() for column in config.member_columns
                    )
                    if normalized_header != expected_header:
                        raise ValueError(
                            "Invalid header in {}: expected {}.".format(
                                file_path.name,
                                config.delimiter.join(config.member_columns),
                            )
                        )
                    header_found = True
                    continue

                if record_type != "D":
                    raise ValueError(
                        "Unknown record marker {!r} in {} at line {}.".format(
                            record_type, file_path.name, line_number
                        )
                    )
                if not header_found:
                    raise ValueError(
                        "Data record found before header in {} at line {}.".format(
                            file_path.name, line_number
                        )
                    )
                if len(fields) != len(config.member_columns):
                    raise ValueError(
                        "Invalid data record in {} at line {}: expected {} "
                        "member fields, found {}.".format(
                            file_path.name,
                            line_number,
                            len(config.member_columns),
                            len(fields),
                        )
                    )

                values = {
                    column.upper(): value
                    for column, value in zip(config.member_columns, fields)
                }
                missing_fields = [
                    column
                    for column in config.member_columns
                    if not values[column.upper()]
                ]
                if missing_fields:
                    raise ValueError(
                        "Required field(s) {} are empty in {} at line {}.".format(
                            ", ".join(missing_fields), file_path, line_number
                        )
                    )

                for column in ("MEMBER_ID",):
                    if column in values and not re.fullmatch(r"[0-9]+", values[column]):
                        raise ValueError(
                            "{} must be an integer in {} at line {}.".format(
                                column, file_path.name, line_number
                            )
                        )
                normalized_values = list(fields)
                for date_column, date_format in (
                    ("ENROLLMENT_DATE", "%Y%m%d"),
                    ("LAST_FLIGHT_DATE", "%Y%m%d"),
                    ("DOB", "%m%d%Y"),
                ):
                    column_index = next(
                        (
                            index
                            for index, column in enumerate(config.member_columns)
                            if column.upper() == date_column
                        ),
                        None,
                    )
                    if column_index is not None:
                        normalized_values[column_index] = _parse_member_date(
                            fields[column_index],
                            config.member_columns[column_index],
                            date_format,
                            file_path,
                            line_number,
                        )
                member_rows.append(tuple(normalized_values))
    except UnicodeDecodeError as error:
        raise ValueError(
            "Members file is not valid UTF-8 text: {}".format(file_path)
        ) from error
    except csv.Error as error:
        raise ValueError(
            "Malformed pipe-delimited data in {}: {}".format(file_path, error)
        ) from error

    if not header_found:
        raise ValueError("Members file has no header record: {}".format(file_path))
    if not member_rows:
        raise ValueError("Members file contains no data records: {}".format(file_path))
    return member_rows


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_members(
    config: SnowflakeConfig, file_paths: Optional[List[Path]] = None
) -> int:
    if file_paths is None:
        file_paths = find_member_files(config)
    else:
        file_paths = sorted(Path(path).resolve() for path in file_paths)
        if not file_paths:
            raise FileNotFoundError("No member files were provided for loading.")
        for file_path in file_paths:
            if not file_path.is_file():
                raise FileNotFoundError(
                    "Member file does not exist: {}".format(file_path)
                )
            validate_member_filename(file_path)
    total_input_rows = 0
    parsed_files = []

    # Validate every input before connecting, so invalid batches cannot be partially loaded.
    for file_path in file_paths:
        member_rows = parse_member_file(file_path, config)
        row_count = len(member_rows)
        total_input_rows += row_count
        parsed_files.append((file_path, member_rows))
        LOGGER.info("Validated %s data row(s) in %s.", row_count, file_path.name)

    LOGGER.info(
        "Connecting to Snowflake database %s, schema %s.",
        config.database,
        config.schema,
    )
    connection = snowflake.connector.connect(
        **config.connection_parameters()
    )
    try:
        with tempfile.TemporaryDirectory(prefix="member_load_") as temp_directory:
            with connection.cursor() as cursor:
                cursor.execute(
                    "USE SCHEMA {}.{}".format(config.database, config.schema)
                )
                member_id_column = next(
                    (
                        column
                        for column in config.member_columns
                        if column.upper() == "MEMBER_ID"
                    ),
                    None,
                )
                if member_id_column is None:
                    raise ValueError(
                        "MEMBER_COLUMNS must contain MEMBER_ID for retry-safe loads."
                    )
                stage_table = "TEMP_MEMBER_LOAD"
                cursor.execute(
                    "CREATE TEMPORARY TABLE {} LIKE {}".format(
                        stage_table, config.table
                    )
                )
                for file_path, member_rows in parsed_files:
                    normalized_file = Path(temp_directory) / file_path.name
                    with normalized_file.open(
                        "w", encoding="utf-8", newline=""
                    ) as output:
                        writer = csv.writer(
                            output,
                            delimiter=config.delimiter,
                            quotechar='"',
                            quoting=csv.QUOTE_MINIMAL,
                        )
                        writer.writerows(member_rows)

                    local_file_url = "file://" + str(normalized_file).replace(
                        "\\", "/"
                    )
                    put_sql = "PUT {} {} AUTO_COMPRESS=TRUE OVERWRITE=TRUE".format(
                        _sql_string_literal(local_file_url), config.stage
                    )
                    LOGGER.info(
                        "Uploading normalized data for %s to stage %s.",
                        file_path.name,
                        config.stage,
                    )
                    cursor.execute(put_sql)
                    for result in cursor.fetchall():
                        LOGGER.info("PUT result: %s", result)
                    LOGGER.info("Upload completed for %s.", normalized_file.name)

                    staged_filename = normalized_file.name + ".gz"
                    columns = ", ".join(config.member_columns)
                    copy_sql = f"""
                        COPY INTO {stage_table}
                            ({columns})
                        FROM {config.stage}
                        FILES = ({_sql_string_literal(staged_filename)})
                        FILE_FORMAT = (
                            FORMAT_NAME = '{config.file_format}'
                            FIELD_DELIMITER = '{config.delimiter}'
                            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
                            DATE_FORMAT = 'YYYY-MM-DD'
                            SKIP_HEADER = 0
                        )
                        ON_ERROR = 'ABORT_STATEMENT'
                    """
                    LOGGER.info(
                        "Loading %s into temporary table %s.",
                        staged_filename,
                        stage_table,
                    )
                    cursor.execute(copy_sql)
                    for result in cursor.fetchall():
                        LOGGER.info("COPY result: %s", result)

                cursor.execute(
                    """
                    DELETE FROM {table} AS target
                    USING {stage_table} AS source
                    WHERE TO_VARCHAR(target.{member_id}) =
                          TO_VARCHAR(source.{member_id})
                    """.format(
                        table=config.table,
                        stage_table=stage_table,
                        member_id=member_id_column,
                    )
                )
                cursor.execute(
                    "INSERT INTO {table} ({columns}) "
                    "SELECT {columns} FROM {stage_table}".format(
                        table=config.table,
                        columns=columns,
                        stage_table=stage_table,
                    )
                )
                connection.commit()

                cursor.execute(f"SELECT COUNT(*) FROM {config.table}")
                row = cursor.fetchone()
                if row is None:
                    raise RuntimeError(
                        "Snowflake returned no result for the validation query."
                    )

                record_count = row[0]
                LOGGER.info(
                    "Load completed: %s file(s), %s validated input row(s); "
                    "%s currently contains %s row(s).",
                    len(file_paths),
                    total_input_rows,
                    config.table,
                    record_count,
                )
                return record_count
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = SnowflakeConfig.from_environment()
        record_count = load_members(config)
    except (FileNotFoundError, ValueError, snowflake.connector.Error) as error:
        LOGGER.error("%s", error)
        print("Member load failed: {}".format(error))
        return 1

    print(
        "Member load completed successfully. "
        "{} currently contains {} record(s).".format(config.table, record_count)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
