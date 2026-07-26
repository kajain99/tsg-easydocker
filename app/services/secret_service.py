import base64
import secrets
import string
import uuid

MIN_SECRET_SIZE = 8
MAX_SECRET_SIZE = 128
GENERATOR_TYPES = {"hex", "password", "base64", "alphanumeric", "uuid"}
PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
ALPHANUMERIC_ALPHABET = string.ascii_letters + string.digits


def _validated_size(value, default):
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Secret size must be an integer.")
    if value < MIN_SECRET_SIZE or value > MAX_SECRET_SIZE:
        raise ValueError(f"Secret size must be between {MIN_SECRET_SIZE} and {MAX_SECRET_SIZE}.")
    return value


def generate_secret(generator_type, *, bytes_count=None, length=None):
    if generator_type not in GENERATOR_TYPES:
        raise ValueError("Unsupported secret generator type.")
    if generator_type == "hex":
        return secrets.token_hex(_validated_size(bytes_count, 32))
    if generator_type == "base64":
        value = secrets.token_bytes(_validated_size(bytes_count, 32))
        return base64.b64encode(value).decode("ascii")
    if generator_type == "password":
        size = _validated_size(length, 32)
        return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(size))
    if generator_type == "alphanumeric":
        size = _validated_size(length, 32)
        return "".join(secrets.choice(ALPHANUMERIC_ALPHABET) for _ in range(size))
    return str(uuid.uuid4())


def generate_from_config(config):
    if not isinstance(config, dict):
        raise ValueError("Generator configuration must be an object.")
    return generate_secret(
        config.get("type"),
        bytes_count=config.get("bytes"),
        length=config.get("length"),
    )
