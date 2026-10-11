import json

from cryptography.fernet import Fernet

from .settings import settings

_fernet = Fernet(settings.webui_master_key.encode("ascii"))


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet.encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    return _fernet.decrypt(token)


def encrypt_json(data: dict) -> bytes:
    return encrypt_bytes(json.dumps(data).encode("utf-8"))


def decrypt_json(token: bytes) -> dict:
    return json.loads(decrypt_bytes(token).decode("utf-8"))
