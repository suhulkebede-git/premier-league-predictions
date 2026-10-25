import logging
import unittest

from plpredict.logging_config import configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        self._saved = (root.level, list(root.handlers))

    def tearDown(self):
        root = logging.getLogger()
        root.setLevel(self._saved[0])
        root.handlers[:] = self._saved[1]

    def test_sets_requested_level(self):
        configure_logging("DEBUG")
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_level_is_case_insensitive(self):
        configure_logging("warning")
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_rejects_unknown_level(self):
        with self.assertRaisesRegex(ValueError, "Invalid log level"):
            configure_logging("LOUD")

    def test_rejects_non_string(self):
        with self.assertRaises(ValueError):
            configure_logging(10)


if __name__ == "__main__":
    unittest.main()
