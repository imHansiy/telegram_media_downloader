"""Regression tests for managed PostgreSQL connection recovery."""

import unittest
from unittest import mock

from module.db import DB


class ResultCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, *_):
        return None

    def fetchone(self):
        return ({"profiles": []},)


class HealthyConnection:
    closed = 0

    def cursor(self):
        return ResultCursor()


class ClosedConnection:
    closed = 1


class DatabaseReconnectTestCase(unittest.TestCase):
    def test_load_setting_reconnects_before_using_a_closed_connection(self):
        database = DB.__new__(DB)
        database.conn = ClosedConnection()
        database.dsn = "postgresql://configured"
        healthy_connection = HealthyConnection()

        def reconnect():
            database.conn = healthy_connection

        database._reconnect = mock.Mock(side_effect=reconnect)

        value = database.load_setting("profiles")

        self.assertEqual({"profiles": []}, value)
        database._reconnect.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
