"""Execute a Snowflake SQL file, optionally failing on data-quality errors."""

import argparse
import io
import logging
import os
from pathlib import Path
from typing import Dict

import snowflake.connector
from dotenv import load_dotenv


PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_DIRECTORY / ".env"
LOGGER = logging.getLogger(__name__)


def connection_parameters() -> Dict[str, object]:
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    required_variables = (
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_USER",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_WAREHOUSE",
        "SNOWFLAKE_DATABASE",
        "SNOWFLAKE_SCHEMA",
    )
    missing = [
        name for name in required_variables if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise ValueError(
            "Missing or empty Snowflake setting(s): {}".format(", ".join(missing))
        )

    parameters = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "password": os.environ["SNOWFLAKE_PASSWORD"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
        "autocommit": False,
    }
    role = os.environ.get("SNOWFLAKE_ROLE", "").strip()
    if role:
        parameters["role"] = role
    return parameters


def run_sql_file(sql_file: Path, fail_on_error: bool = False) -> int:
    sql_file = Path(sql_file).resolve()
    if not sql_file.is_file():
        raise FileNotFoundError("SQL file does not exist: {}".format(sql_file))
    if sql_file.suffix.lower() != ".sql":
        raise ValueError("Expected a .sql file: {}".format(sql_file))

    sql_text = sql_file.read_text(encoding="utf-8-sig")
    if not sql_text.strip():
        raise ValueError("SQL file is empty: {}".format(sql_file))

    connection = snowflake.connector.connect(**connection_parameters())
    try:
        statement_count = 0
        with connection.cursor() as cursor:
            for result_cursor in connection.execute_stream(io.StringIO(sql_text)):
                statement_count += 1
                if result_cursor.description is not None:
                    row_count = 0
                    rows = result_cursor.fetchmany(1000)
                    while rows:
                        row_count += len(rows)
                        rows = result_cursor.fetchmany(1000)
                    LOGGER.info(
                        "SQL statement %s returned %s row(s).",
                        statement_count,
                        row_count,
                    )

            if fail_on_error:
                cursor.execute(
                    "SELECT COUNT(*) FROM DATA_QUALITY_ISSUES "
                    "WHERE SEVERITY = 'ERROR'"
                )
                result = cursor.fetchone()
                if result is None:
                    raise RuntimeError(
                        "Snowflake returned no data-quality validation result."
                    )
                error_count = result[0]
                if error_count:
                    raise RuntimeError(
                        "Data-quality validation found {} ERROR issue(s).".format(
                            error_count
                        )
                    )
                LOGGER.info("Data-quality validation passed with no ERROR issues.")
        connection.commit()
        LOGGER.info("Executed %s statement(s) from %s.", statement_count, sql_file)
        return statement_count
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sql_file", type=Path, help="Path to a .sql file")
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Fail if DATA_QUALITY_ISSUES contains any ERROR findings.",
    )
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        run_sql_file(arguments.sql_file, fail_on_error=arguments.fail_on_error)
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        snowflake.connector.Error,
    ) as error:
        LOGGER.error("%s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
