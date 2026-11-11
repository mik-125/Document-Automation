"""The test suite must never open the maintainer's real data directory."""
import unittest
from pathlib import Path

from backend import server


class DataIsolationTests(unittest.TestCase):
    def test_tests_use_a_temporary_data_directory(self):
        real = (Path(__file__).resolve().parent.parent / "data").resolve()
        self.assertNotEqual(server.DATA_DIR, real)
        self.assertNotIn(real, server.DATA_DIR.parents)


if __name__ == "__main__":
    unittest.main()
