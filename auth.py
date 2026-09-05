import hashlib
import hmac
import json
import base64
import time
from typing import Optional, Dict, Any

SECRET_KEY = "roomsplit-jwt-secret-key-passbook-2026"
DEFAULT_PASSWORD = "room@123"

def hash_password(password: str) -> str:
    """Hash password using SHA-256 with project secret salt."""
    return hashlib.sha256(f"{password}:{SECRET_KEY}".encode("utf-8")).hexdigest()

def verify_password(stored_hash: str, password: str) -> bool:
    """Verify input password against stored hash."""
    return hmac.compare_digest(stored_hash, hash_password(password))

def create_jwt_token(username: str, expires_days: int = 30) -> str:
    """Create a standard HS256 JWT token with 30-day expiration."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "sub": username.lower(),
        "iat": now,
        "exp": now + (expires_days * 24 * 3600)
    }
    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode("utf-8")).rstrip(b"=").decode("utf-8")
    p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).rstrip(b"=").decode("utf-8")
    msg = f"{h_b64}.{p_b64}"
    sig = hmac.new(SECRET_KEY.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    s_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode("utf-8")
    return f"{msg}.{s_b64}"

def decode_jwt_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify and decode HS256 JWT token. Returns payload or None."""
    if not token or not isinstance(token, str):
        return None
    try:
        parts = token.strip().split(".")
        if len(parts) != 3:
            return None
        msg = f"{parts[0]}.{parts[1]}"
        expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
        expected_b64 = base64.urlsafe_b64encode(expected_sig).rstrip(b"=").decode("utf-8")
        if not hmac.compare_digest(parts[2], expected_b64):
            return None
        
        rem = len(parts[1]) % 4
        p_b64 = parts[1] + ("=" * (4 - rem) if rem else "")
        payload = json.loads(base64.urlsafe_b64decode(p_b64.encode("utf-8")).decode("utf-8"))
        if payload.get("exp") and payload["exp"] < time.time():
            return None
        return payload
    except Exception:
        return None
