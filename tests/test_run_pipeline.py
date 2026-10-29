"""Unit tests for the Windows Task Scheduler pipeline runner."""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_DIRECTORY / "code" / "run_pipeline.py"


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
            "run_pipeline_under_test", SCRIPT_PATH
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


pipeline = load_script_module()


class WaitForFilesTests(unittest.TestCase):
    def test_returns_matching_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory)
            expected = data_directory / "member_20261027.txt"
            expected.write_text("test data", encoding="utf-8")

            files = pipeline._wait_for_files(
                "member_*.txt", data_directory, timeout_seconds=1,
                poll_interval_seconds=1,
            )

        self.assertEqual(files, [expected.resolve()])

    def test_times_out_if_no_matching_files_exist(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with mock.patch.object(
                pipeline.time, "monotonic", side_effect=[0, 1]
            ):
                with self.assertRaisesRegex(TimeoutError, "Timed out waiting"):
                    pipeline._wait_for_files(
                        "member_*.txt",
                        Path(temporary_directory),
                        timeout_seconds=1,
                        poll_interval_seconds=1,
                    )


class ArchiveFilesTests(unittest.TestCase):
    def test_archives_files_under_run_directory(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "data" / "member_20261027.txt"
            source.parent.mkdir()
            source.write_text("test data", encoding="utf-8")

            archived = pipeline._archive_files(
                [source], root / "archive", "run-123"
            )

            self.assertEqual(
                archived, [root / "archive" / "run-123" / source.name]
            )
            self.assertFalse(source.exists())
            self.assertTrue(archived[0].is_file())

    def test_archive_retry_accepts_already_moved_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "data" / "member_20261027.txt"
            destination = root / "archive" / "run-123" / source.name
            destination.parent.mkdir(parents=True)
            destination.write_text("test data", encoding="utf-8")
            source.parent.mkdir()

            archived = pipeline._archive_files(
                [source], root / "archive", "run-123"
            )

            self.assertEqual(archived, [destination])
            self.assertTrue(destination.is_file())


if __name__ == "__main__":
    unittest.main()
