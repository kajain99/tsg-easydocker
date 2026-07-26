import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.secret_service import generate_from_config, generate_secret


class SecretServiceTests(unittest.TestCase):
    def test_hex_secret_matches_openssl_rand_hex_32_shape(self):
        value = generate_secret("hex", bytes_count=32)
        self.assertEqual(len(value), 64)
        int(value, 16)

    def test_password_has_requested_length(self):
        self.assertEqual(len(generate_secret("password", length=24)), 24)

    def test_base64_decodes_to_requested_byte_count(self):
        value = generate_secret("base64", bytes_count=32)
        self.assertEqual(len(base64.b64decode(value)), 32)

    def test_invalid_generator_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_from_config({"type": "shell"})

    def test_unsafe_size_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_secret("hex", bytes_count=1024)


if __name__ == "__main__":
    unittest.main()
