"""
Development RSA Key Helper for Anvaya SSO Testing.
Generates and caches an RSA keypair for local development and test simulations.
NEVER used in production.
"""

import os
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

DEV_KEYS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config" / "dev_keys"
DEV_PRIVATE_KEY_FILE = DEV_KEYS_DIR / "dev_private_key.pem"
DEV_PUBLIC_KEY_FILE = DEV_KEYS_DIR / "dev_public_key.pem"


def ensure_dev_keys():
    """Generates an RSA 2048-bit keypair for development if not already present."""
    if DEV_PRIVATE_KEY_FILE.exists() and DEV_PUBLIC_KEY_FILE.exists():
        return

    DEV_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    
    pub_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    
    DEV_PRIVATE_KEY_FILE.write_bytes(priv_pem)
    DEV_PUBLIC_KEY_FILE.write_bytes(pub_pem)


def get_dev_private_key_pem() -> str:
    ensure_dev_keys()
    return DEV_PRIVATE_KEY_FILE.read_text(encoding="utf-8")


def get_dev_public_key_pem() -> str:
    ensure_dev_keys()
    return DEV_PUBLIC_KEY_FILE.read_text(encoding="utf-8")
