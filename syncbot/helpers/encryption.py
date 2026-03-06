"""Bot-token encryption / decryption using Fernet (AES-128-CBC + HMAC-SHA256).

The PASSWORD_ENCRYPT_KEY env var is stretched to a 32-byte key using
PBKDF2-HMAC-SHA256 with 600,000 iterations.  The derived Fernet instance
is cached so the expensive KDF runs at most once per key per process.
"""

import base64
import functools
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

import constants

_logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 600_000
_PBKDF2_SALT_PREFIX = b"syncbot-fernet-v1"


@functools.lru_cache(maxsize=2)
def _get_fernet(key: str) -> Fernet:
    """Derive a Fernet cipher from an arbitrary passphrase via PBKDF2."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    salt = _PBKDF2_SALT_PREFIX + key.encode()[:16]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    derived = kdf.derive(key.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def _encryption_enabled() -> bool:
    """Return *True* if bot-token encryption is active."""
    key = os.environ.get(constants.PASSWORD_ENCRYPT_KEY, "")
    return bool(key) and key != "123"


def encrypt_bot_token(token: str) -> str:
    """Encrypt a bot token before storing it in the database."""
    if not _encryption_enabled():
        return token
    key = os.environ[constants.PASSWORD_ENCRYPT_KEY]
    return _get_fernet(key).encrypt(token.encode()).decode()


def decrypt_bot_token(encrypted: str) -> str:
    """Decrypt a bot token read from the database.

    Raises on failure when encryption is enabled.
    """
    if not _encryption_enabled():
        return encrypted
    key = os.environ[constants.PASSWORD_ENCRYPT_KEY]
    try:
        return _get_fernet(key).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        _logger.error(
            "Bot token decryption failed — refusing to use the token. "
            "If you recently enabled encryption, run "
            "db/migrate_002_encrypt_tokens.py to encrypt existing tokens."
        )
        raise ValueError(
            "Bot token decryption failed. The token may be plaintext (not yet migrated) or tampered with."
        ) from None
