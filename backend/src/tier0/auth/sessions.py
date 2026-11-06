from __future__ import annotations

from itsdangerous import BadData, URLSafeTimedSerializer

COOKIE_NAME = "tier0_session"
_SALT = "tier0.session.v1"


class SessionCodec:
    """Signed, expiring session tokens. They carry only the user id; everything else is looked
    up on each request, so removing a user ends their access immediately."""

    def __init__(self, secret: str, max_age_seconds: int) -> None:
        self._serializer = URLSafeTimedSerializer(secret, salt=_SALT)
        self.max_age_seconds = max_age_seconds

    def issue(self, user_id: str) -> str:
        return str(self._serializer.dumps({"u": user_id}))

    def read(self, token: str) -> str | None:
        try:
            payload = self._serializer.loads(token, max_age=self.max_age_seconds)
        except BadData:  # bad signature, expired, or malformed
            return None
        user_id = payload.get("u") if isinstance(payload, dict) else None
        return user_id if isinstance(user_id, str) and user_id else None
