import logging
import unittest

from plpredict.logging_config import configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def tearDown(self):
        logging.getLogger().setLevel(logging.WARNING)

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
