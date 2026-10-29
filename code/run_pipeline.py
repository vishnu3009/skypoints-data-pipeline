"""Run the SkyPoints data pipeline on Windows."""

import argparse
import logging
import os
import shutil
import smtplib
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from dotenv import load_dotenv


PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
CODE_DIRECTORY = PROJECT_DIRECTORY / "code"
if str(CODE_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(CODE_DIRECTORY))

import load_members
import load_redemptions
import run_sql


LOGGER = logging.getLogger("skypoints.pipeline")
MEMBER_FILE_PATTERN = "member_*.txt"
REDEMPTION_FILE_PATTERN = "redemption_*.json"


def _configured_directory(variable: str, default: Path) -> Path:
    return Path(os.environ.get(variable, str(default))).expanduser().resolve()


def _wait_for_files(
    pattern: str,
    data_directory: Path,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> List[Path]:
    deadline = time.monotonic() + timeout_seconds
    LOGGER.info(
        "Waiting for files matching %s in %s (timeout: %s seconds).",
        pattern,
        data_directory,
        timeout_seconds,
    )
    while True:
        if not data_directory.is_dir():
            raise FileNotFoundError(
                "Input directory does not exist: {}".format(data_directory)
            )
        files = sorted(
            path.resolve()
            for path in data_directory.glob(pattern)
            if path.is_file()
        )
        if files:
            LOGGER.info("Found %s file(s) matching %s.", len(files), pattern)
            return files

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                "Timed out waiting for files matching {} in {}.".format(
                    pattern, data_directory
                )
            )
        time.sleep(min(poll_interval_seconds, remaining))


def _archive_files(
    input_files: List[Path], archive_directory: Path, run_id: str
) -> List[Path]:
    destination_directory = archive_directory / run_id
    destination_directory.mkdir(parents=True, exist_ok=True)
    moves: List[Tuple[Path, Path]] = []

    for source in input_files:
        destination = destination_directory / source.name
        if not source.is_file():
            if destination.is_file():
                continue
            raise FileNotFoundError(
                "Input and archived file are both missing: {}".format(source)
            )
        if destination.exists():
            raise FileExistsError(
                "Archive destination already exists: {}".format(destination)
            )
        moves.append((source, destination))

    completed: List[Tuple[Path, Path]] = []
    try:
        for source, destination in moves:
            shutil.move(str(source), str(destination))
            completed.append((source, destination))
    except Exception as error:
        restore_errors = []
        for source, destination in reversed(completed):
            try:
                shutil.move(str(destination), str(source))
            except OSError as restore_error:
                restore_errors.append(str(restore_error))
        if restore_errors:
            raise RuntimeError(
                "Archival failed and some files could not be restored: {}".format(
                    "; ".join(restore_errors)
                )
            ) from error
        raise

    archived_files = [
        destination_directory / source.name for source in input_files
    ]
    LOGGER.info(
        "Archived %s file(s) to %s.",
        len(archived_files),
        destination_directory,
    )
    return archived_files


def _send_success_email(run_id: str, archived_files: List[Path]) -> None:
    _validate_email_configuration()
    recipient = os.environ.get("SKYPOINTS_SUCCESS_EMAIL", "").strip()
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    sender = os.environ.get("SMTP_FROM", "").strip()
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))

    message = EmailMessage()
    message["Subject"] = "SkyPoints data pipeline succeeded"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "The SkyPoints data pipeline completed successfully.\n\n"
        "Run: {}\n"
        "Archived files:\n{}".format(
            run_id, "\n".join(str(path) for path in archived_files)
        )
    )

    username = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if username:
            if not password:
                raise ValueError(
                    "SMTP_PASSWORD is required when SMTP_USER is configured."
                )
            server.login(username, password)
        server.send_message(message)
    LOGGER.info("Sent pipeline success email to %s.", recipient)


def _validate_email_configuration() -> None:
    required_settings = ("SKYPOINTS_SUCCESS_EMAIL", "SMTP_HOST", "SMTP_FROM")
    missing = [
        name for name in required_settings if not os.environ.get(name, "").strip()
    ]
    if missing:
        raise ValueError(
            "Configure the email setting(s): {}".format(", ".join(missing))
        )
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError as error:
        raise ValueError("SMTP_PORT must be an integer.") from error
    if not 1 <= port <= 65535:
        raise ValueError("SMTP_PORT must be between 1 and 65535.")
    if (
        os.environ.get("SMTP_USER", "").strip()
        and not os.environ.get("SMTP_PASSWORD")
    ):
        raise ValueError("SMTP_PASSWORD is required when SMTP_USER is configured.")


@contextmanager
def _single_instance_lock(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("a+b")
    acquired = False
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as error:
                raise RuntimeError(
                    "Another SkyPoints pipeline run is already active."
                ) from error
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as error:
                raise RuntimeError(
                    "Another SkyPoints pipeline run is already active."
                ) from error
        yield
    finally:
        try:
            if acquired:
                lock_file.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except OSError:
            LOGGER.exception("Could not release pipeline lock cleanly.")
        lock_file.close()


def run_pipeline(
    wait_timeout_seconds: int = 86400,
    poll_interval_seconds: int = 60,
    data_directory: Optional[Path] = None,
    archive_directory: Optional[Path] = None,
) -> List[Path]:
    if wait_timeout_seconds < 1:
        raise ValueError("wait_timeout_seconds must be at least one.")
    if poll_interval_seconds < 1:
        raise ValueError("poll_interval_seconds must be at least one.")

    load_dotenv(dotenv_path=PROJECT_DIRECTORY / ".env", override=False)
    _validate_email_configuration()
    input_directory = (
        Path(data_directory).expanduser().resolve()
        if data_directory is not None
        else _configured_directory(
            "SKYPOINTS_DATA_DIR", PROJECT_DIRECTORY / "data"
        )
    )
    output_directory = (
        Path(archive_directory).expanduser().resolve()
        if archive_directory is not None
        else _configured_directory(
            "SKYPOINTS_ARCHIVE_DIR", PROJECT_DIRECTORY / "archive"
        )
    )

    run_id = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%f%z")
    with _single_instance_lock(PROJECT_DIRECTORY / ".pipeline.lock"):
        member_paths = _wait_for_files(
            MEMBER_FILE_PATTERN,
            input_directory,
            wait_timeout_seconds,
            poll_interval_seconds,
        )
        member_config = load_members.SnowflakeConfig.from_environment()
        member_counts = [
            load_members.validate_member_file(path, member_config)
            for path in member_paths
        ]
        LOGGER.info(
            "Validated %s member file(s), %s record(s).",
            len(member_paths),
            sum(member_counts),
        )
        load_members.load_members(member_config, file_paths=member_paths)

        run_sql.run_sql_file(PROJECT_DIRECTORY / "sql" / "load_member_staging.sql")
        run_sql.run_sql_file(PROJECT_DIRECTORY / "sql" / "split_member_countries.sql")

        redemption_paths = _wait_for_files(
            REDEMPTION_FILE_PATTERN,
            input_directory,
            wait_timeout_seconds,
            poll_interval_seconds,
        )
        redemption_counts = [
            len(load_redemptions.parse_redemption_feed(path))
            for path in redemption_paths
        ]
        LOGGER.info(
            "Validated %s redemption file(s), %s transaction(s).",
            len(redemption_paths),
            sum(redemption_counts),
        )
        redemption_config = load_redemptions.SnowflakeConfig.from_environment()
        load_redemptions.load_redemptions(
            redemption_config, file_paths=redemption_paths
        )

        run_sql.run_sql_file(
            PROJECT_DIRECTORY / "sql" / "validate_member_redemption_data.sql",
            fail_on_error=True,
        )
        run_sql.run_sql_file(PROJECT_DIRECTORY / "sql" / "flatten_redemptions.sql")

        archived_files = _archive_files(
            member_paths + redemption_paths, output_directory, run_id
        )
        _send_success_email(run_id, archived_files)
        return archived_files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wait-timeout-seconds",
        type=int,
        default=86400,
        help="Maximum time to wait for each feed type (default: 24 hours).",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=int,
        default=60,
        help="How often to check for feeds (default: 60 seconds).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Override SKYPOINTS_DATA_DIR for this run.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        help="Override SKYPOINTS_ARCHIVE_DIR for this run.",
    )
    arguments = parser.parse_args()

    log_directory = PROJECT_DIRECTORY / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_directory / "pipeline.log", encoding="utf-8"),
        ],
    )
    try:
        archived = run_pipeline(
            wait_timeout_seconds=arguments.wait_timeout_seconds,
            poll_interval_seconds=arguments.poll_interval_seconds,
            data_directory=arguments.data_dir,
            archive_directory=arguments.archive_dir,
        )
    except Exception:
        LOGGER.exception("SkyPoints pipeline failed.")
        return 1

    LOGGER.info("Pipeline completed successfully; archived %s file(s).", len(archived))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
