"""Brightline regression tests."""
import atexit
import os
import tempfile

# Importing backend.server opens the database in BRIGHTLINE_DATA_DIR, and opening it
# can run schema upgrades. Point every test at a throwaway directory before any test
# module is imported, so no run can touch the maintainer's real data.
_TEST_DATA = tempfile.TemporaryDirectory()
atexit.register(_TEST_DATA.cleanup)
os.environ["BRIGHTLINE_DATA_DIR"] = _TEST_DATA.name
