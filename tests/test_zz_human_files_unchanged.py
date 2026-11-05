"""Runs last (alphabetical): the human-owned files are byte-identical to
what they were when the suite loaded (reviews/21_zoning, Z-17)."""

from __future__ import annotations

import hashlib
import unittest

from zoning_helpers import HUMAN_OWNED_AT_LOAD, REPO_ROOT


class HumanOwnedFilesUnchangedTests(unittest.TestCase):
    def test_no_test_modified_a_human_owned_file(self):
        for name, sha in HUMAN_OWNED_AT_LOAD.items():
            now = hashlib.sha256((REPO_ROOT / name).read_bytes()).hexdigest()
            self.assertEqual(now, sha, f"{name} changed during the test suite")


if __name__ == "__main__":
    unittest.main()
