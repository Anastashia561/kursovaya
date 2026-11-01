import time
from uuid import uuid4
import jwt
from fastapi import HTTPException

def issue_token(name, role, private_key):
    now = int(time.time())
    return jwt.encode({"sub": name, "role": role, "iat": now,
                       "exp": now + 900, "jti": str(uuid4()),
                       "iss": "ubi193-auth", "aud": "ubi193-admin"},
                      private_key, algorithm="RS256")

def verify_token(authorization, public_key, roles=("admin", "auditor")):
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
        claims = jwt.decode(token, public_key, algorithms=["RS256"],
                            issuer="ubi193-auth", audience="ubi193-admin",
                            options={"require": ["sub", "role", "iat", "exp", "jti"]})
    except (jwt.InvalidTokenError, ValueError, AttributeError):
        raise HTTPException(401, "Invalid or expired token")
    if claims["role"] not in roles:
        raise HTTPException(403, "Role is not permitted")
    return claims
