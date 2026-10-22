"""Unit tests for renderdoc_mcp.qrenderdoc_runner — argument validation only."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from renderdoc_mcp.qrenderdoc_runner import run_qrenderdoc_script


class QRenderDocRunnerTests(unittest.TestCase):
    def test_requires_code_or_script_path(self):
        result = run_qrenderdoc_script()
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_rejects_both_inputs(self):
        result = run_qrenderdoc_script(code="print(1)", script_path=__file__)
        self.assertEqual(result["code"], "INVALID_ARGUMENT")

    def test_missing_script(self):
        result = run_qrenderdoc_script(script_path=os.path.join(os.path.dirname(__file__), "nope.py"))
        self.assertEqual(result["code"], "SCRIPT_NOT_FOUND")

    def test_missing_executable(self):
        result = run_qrenderdoc_script(code="print(1)", qrenderdoc_path=r"C:\definitely\missing\qrenderdoc.exe")
        self.assertEqual(result["code"], "QRENDERDOC_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
