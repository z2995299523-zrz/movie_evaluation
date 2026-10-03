"""Run mandatory suites and reject missing/skipped tests before publication."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


def run_checks(mode: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    patterns = (["test_app.py"] if mode == "desktop" else
                ["test_web.py", "test_storage_migration.py", "test_git_deploy.py", "test_security_*.py"])
    with tempfile.TemporaryDirectory(prefix="movie-review-qa-") as temporary:
        os.environ["MOVIE_REVIEW_DATA_DIR"] = temporary
        suite = unittest.TestSuite()
        for pattern in patterns:
            tests = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=pattern)
            if not tests.countTestCases():
                raise RuntimeError(f"No tests discovered for {pattern}")
            suite.addTests(tests)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or result.skipped:
            raise RuntimeError("Required tests failed or were skipped")
        return {"mode": mode, "tests": result.testsRun, "skipped": 0, "passed": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["desktop", "web"])
    print(json.dumps(run_checks(parser.parse_args().mode)))
