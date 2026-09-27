from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey


def verify_signature(public_key_hex: str, signature: str, timestamp: str, body: bytes) -> bool:
    if not signature or not timestamp:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(timestamp.encode() + body, bytes.fromhex(signature))
        return True
    except (BadSignatureError, ValueError):
        return False
