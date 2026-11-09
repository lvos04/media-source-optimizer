"""Unit test for the log check of smoke.py (c_error_logs): restart refusals of PostgreSQL are only tolerated
inside a deliberate restart window. Runs without a stack:

    python tests/stack/test_smoke_logcheck.py
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import smoke

T0 = datetime(2026, 11, 9, 11, 19, 30, tzinfo=UTC)
WINDOW = [(T0, T0 + timedelta(seconds=20))]


def line(at: datetime, text: str) -> str:
    return f"{at.strftime('%Y-%m-%dT%H:%M:%S')}.{at.microsecond:06d}123Z {text}"


SHUTDOWN = "2026-11-09 12:19:40.952 CET [152] FATAL:  the database system is shutting down"
STARTUP = "2026-11-09 12:19:45.001 CET [160] FATAL:  the database system is starting up"


class LogTimeTest(unittest.TestCase):
    def test_docker_timestamp_with_nanoseconds(self) -> None:
        ts = smoke.log_time("2026-11-09T11:19:40.952345678Z hello")
        self.assertEqual(ts, datetime(2026, 11, 9, 11, 19, 40, 952345, tzinfo=UTC))

    def test_offset_and_missing_fraction(self) -> None:
        ts = smoke.log_time("2026-11-09T12:19:40+01:00 hello")
        self.assertEqual(ts, datetime(2026, 11, 9, 11, 19, 40, tzinfo=UTC))

    def test_line_without_timestamp(self) -> None:
        self.assertIsNone(smoke.log_time(SHUTDOWN))


class ClassifyTest(unittest.TestCase):
    def test_postgres_restart_refusals_inside_window_are_tolerated(self) -> None:
        text = "\n".join([line(T0 + timedelta(seconds=10), SHUTDOWN), line(T0 + timedelta(seconds=15), STARTUP)])
        bad, ok = smoke.classify_error_lines("postgres", text, WINDOW)
        self.assertEqual(bad, [])
        self.assertEqual(len(ok), 2)

    def test_same_lines_outside_window_are_faults(self) -> None:
        text = "\n".join([line(T0 - timedelta(seconds=30), SHUTDOWN), line(T0 + timedelta(seconds=60), STARTUP)])
        bad, ok = smoke.classify_error_lines("postgres", text, WINDOW)
        self.assertEqual(len(bad), 2)
        self.assertEqual(ok, [])

    def test_without_any_restart_window_they_are_faults(self) -> None:
        bad, ok = smoke.classify_error_lines("postgres", line(T0, SHUTDOWN), [])
        self.assertEqual((len(bad), ok), (1, []))

    def test_margin_is_small(self) -> None:
        end = WINDOW[0][1]
        inside = line(end + timedelta(seconds=1), SHUTDOWN)
        outside = line(end + timedelta(seconds=3), SHUTDOWN)
        bad, ok = smoke.classify_error_lines("postgres", f"{inside}\n{outside}", WINDOW)
        self.assertEqual((len(bad), len(ok)), (1, 1))

    def test_only_the_postgres_service(self) -> None:
        bad, ok = smoke.classify_error_lines("api", line(T0 + timedelta(seconds=5), STARTUP), WINDOW)
        self.assertEqual((len(bad), ok), (1, []))

    def test_other_errors_inside_window_stay_faults(self) -> None:
        text = "\n".join(
            [
                line(T0 + timedelta(seconds=5), "12:19:35.000 CET [99] FATAL:  password authentication failed"),
                line(T0 + timedelta(seconds=6), "Traceback (most recent call last):"),
                line(T0 + timedelta(seconds=7), "12:19:37.000 CET [99] PANIC:  could not write to file"),
            ]
        )
        bad, ok = smoke.classify_error_lines("postgres", text, WINDOW)
        self.assertEqual((len(bad), ok), (3, []))

    def test_restart_line_without_timestamp_is_a_fault(self) -> None:
        bad, ok = smoke.classify_error_lines("postgres", SHUTDOWN, WINDOW)
        self.assertEqual((len(bad), ok), (1, []))

    def test_administrator_command_stays_allowed_everywhere(self) -> None:
        text = line(T0 - timedelta(hours=1), "FATAL:  terminating connection due to administrator command")
        self.assertEqual(smoke.classify_error_lines("postgres", text, []), ([], []))

    def test_normal_lines_are_ignored(self) -> None:
        text = line(T0, "LOG:  database system is ready to accept connections")
        self.assertEqual(smoke.classify_error_lines("postgres", text, WINDOW), ([], []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
