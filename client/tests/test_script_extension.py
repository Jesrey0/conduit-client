import unittest

from client.conduit.sdk import Conduit


class ScriptExtensionNormalizationTests(unittest.TestCase):
    def payload(self, extension):
        return Conduit._script_payload(
            "print('ok')", "python3", None, None, extension, True, ".", 30_000, None
        )

    def test_accepts_canonical_extension(self):
        self.assertEqual(self.payload("py")["extension"], "py")

    def test_strips_one_or_more_leading_dots(self):
        self.assertEqual(self.payload(".py")["extension"], "py")
        self.assertEqual(self.payload("...py")["extension"], "py")

    def test_rejects_empty_or_dots_only_extension(self):
        with self.assertRaisesRegex(ValueError, "suffix"):
            self.payload("")
        with self.assertRaisesRegex(ValueError, "suffix"):
            self.payload("...")

    def test_rejects_noncanonical_characters_and_length_locally(self):
        with self.assertRaisesRegex(ValueError, "letters"):
            self.payload("bad.py")
        with self.assertRaisesRegex(ValueError, "letters"):
            self.payload("a" * 17)
