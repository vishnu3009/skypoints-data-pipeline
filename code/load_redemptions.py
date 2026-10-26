"""Validate JSON redemption feeds and insert one Snowflake row per transaction."""

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Tuple

import snowflake.connector
from dotenv import load_dotenv


PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
DATA_DIRECTORY = PROJECT_DIRECTORY / "data"
ENV_FILE = PROJECT_DIRECTORY / ".env"
REDEMPTION_FILE_PATTERN = "redemption_*.json"
INSERT_COLUMNS = (
    "MEMBER_ID",
    "FEED_DATE",
    "TXN_ID",
    "TXN_DATE",
    "PARTNER",
    "MILES_REDEEMED",
    "STATUS",
)

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
    table: str

    @classmethod
    def from_environment(cls) -> "SnowflakeConfig":
        load_dotenv(dotenv_path=ENV_FILE, override=False)
        required_variables = (
            "SNOWFLAKE_ACCOUNT",
            "SNOWFLAKE_USER",
            "SNOWFLAKE_PASSWORD",
            "SNOWFLAKE_WAREHOUSE",
            "SNOWFLAKE_DATABASE",
            "SNOWFLAKE_SCHEMA",
            "SNOWFLAKE_REDEMPTIONS_TABLE",
        )
        missing = [
            name for name in required_variables if not os.environ.get(name, "").strip()
        ]
        if missing:
            raise ValueError(
                "Missing or empty setting(s) in {}: {}".format(
                    ENV_FILE, ", ".join(missing)
                )
            )

        table = os.environ["SNOWFLAKE_REDEMPTIONS_TABLE"]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", table):
            raise ValueError(
                "SNOWFLAKE_REDEMPTIONS_TABLE must be a simple Snowflake identifier."
            )

        return cls(
            account=os.environ["SNOWFLAKE_ACCOUNT"],
            user=os.environ["SNOWFLAKE_USER"],
            password=os.environ["SNOWFLAKE_PASSWORD"],
            warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
            role=os.environ.get("SNOWFLAKE_ROLE", ""),
            database=os.environ["SNOWFLAKE_DATABASE"],
            schema=os.environ["SNOWFLAKE_SCHEMA"],
            table=table,
        )

    def connection_parameters(self) -> dict:
        parameters = {
            "account": self.account,
            "user": self.user,
            "password": self.password,
            "warehouse": self.warehouse,
            "database": self.database,
            "schema": self.schema,
            "autocommit": False,
        }
        if self.role:
            parameters["role"] = self.role
        return parameters


def _parse_feed_date(value: object, field_name: str, file_path: Path) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError(
            "{} in {} must be a date string in YYYYMMDD format.".format(
                field_name, file_path.name
            )
        )
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError as error:
        raise ValueError(
            "{} in {} is not a valid calendar date.".format(field_name, file_path.name)
        ) from error


def parse_redemption_feed(file_path: Path) -> List[Tuple[object, ...]]:
    if file_path.stat().st_size == 0:
        raise ValueError("Redemption feed is empty: {}".format(file_path.name))

    try:
        with file_path.open("r", encoding="utf-8-sig") as source:
            feed = json.load(source)
    except UnicodeDecodeError as error:
        raise ValueError(
            "Redemption feed is not valid UTF-8 text: {}".format(file_path.name)
        ) from error
    except json.JSONDecodeError as error:
        raise ValueError(
            "Invalid JSON in {} at line {}, column {}.".format(
                file_path.name, error.lineno, error.colno
            )
        ) from error

    if not isinstance(feed, dict):
        raise ValueError("Redemption feed must contain a JSON object.")

    member_id = feed.get("member_id")
    if isinstance(member_id, bool) or not isinstance(member_id, (str, int)):
        raise ValueError(
            "member_id in {} must be a non-negative integer identifier.".format(
                file_path.name
            )
        )
    member_id = str(member_id).strip()
    if not member_id:
        raise ValueError("member_id in {} cannot be empty.".format(file_path.name))
    if not re.fullmatch(r"\d+", member_id):
        raise ValueError(
            "member_id in {} must contain only digits.".format(file_path.name)
        )

    feed_date = _parse_feed_date(feed.get("feed_date"), "feed_date", file_path)
    redemptions = feed.get("redemptions")
    if not isinstance(redemptions, list):
        raise ValueError("redemptions in {} must be a JSON array.".format(file_path.name))

    rows = []
    for index, redemption in enumerate(redemptions, start=1):
        if not isinstance(redemption, dict):
            raise ValueError(
                "Redemption {} in {} must be a JSON object.".format(
                    index, file_path.name
                )
            )

        txn_id = redemption.get("txn_id")
        partner = redemption.get("partner")
        status = redemption.get("status")
        for field_name, value in (
            ("txn_id", txn_id),
            ("partner", partner),
            ("status", status),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    "{} in redemption {} of {} must be a non-empty string.".format(
                        field_name, index, file_path.name
                    )
                )

        txn_date = _parse_feed_date(
            redemption.get("txn_date"),
            "txn_date in redemption {}".format(index),
            file_path,
        )
        miles_redeemed = redemption.get("miles_redeemed")
        if (
            isinstance(miles_redeemed, bool)
            or not isinstance(miles_redeemed, int)
            or miles_redeemed < 0
        ):
            raise ValueError(
                "miles_redeemed in redemption {} of {} must be a "
                "non-negative integer.".format(index, file_path.name)
            )

        rows.append(
            (
                member_id,
                feed_date,
                txn_id.strip(),
                txn_date,
                partner.strip(),
                miles_redeemed,
                status.strip(),
            )
        )

    return rows


def find_redemption_files() -> List[Path]:
    if not DATA_DIRECTORY.is_dir():
        raise FileNotFoundError(
            "Data directory does not exist: {}".format(DATA_DIRECTORY)
        )
    files = sorted(
        path.resolve()
        for path in DATA_DIRECTORY.glob(REDEMPTION_FILE_PATTERN)
        if path.is_file()
    )
    if not files:
        raise FileNotFoundError(
            "No files matching {} were found in {}.".format(
                REDEMPTION_FILE_PATTERN, DATA_DIRECTORY
            )
        )
    LOGGER.info("Found %s redemption JSON file(s).", len(files))
    return files


def load_redemptions(config: SnowflakeConfig) -> int:
    files = find_redemption_files()
    feed_rows = []
    for file_path in files:
        rows = parse_redemption_feed(file_path)
        feed_rows.extend(rows)
        LOGGER.info(
            "Validated %s redemption row(s) in %s.", len(rows), file_path.name
        )

    connection = snowflake.connector.connect(**config.connection_parameters())
    try:
        if feed_rows:
            columns = ", ".join(INSERT_COLUMNS)
            placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
            insert_sql = (
                "INSERT INTO {} ({}) VALUES ({})".format(
                    config.table, columns, placeholders
                )
            )
            try:
                with connection.cursor() as cursor:
                    cursor.executemany(insert_sql, feed_rows)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        else:
            LOGGER.info("No redemption transactions found in the validated feeds.")

        LOGGER.info(
            "Load completed: %s file(s), %s redemption row(s) inserted into %s.",
            len(files),
            len(feed_rows),
            config.table,
        )
        return len(feed_rows)
    finally:
        connection.close()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = SnowflakeConfig.from_environment()
        inserted_rows = load_redemptions(config)
    except (
        FileNotFoundError,
        ValueError,
        snowflake.connector.Error,
        RuntimeError,
    ) as error:
        LOGGER.error("%s", error)
        print("Redemption load failed: {}".format(error))
        return 1

    print(
        "Redemption load completed successfully. "
        "{} row(s) inserted into {}.".format(inserted_rows, config.table)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
