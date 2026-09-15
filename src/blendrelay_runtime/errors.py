"""Shared errors and deterministic JSON helpers for the BlendRelay local runtime."""
import hashlib
import json
import uuid


class BlendRelayError(ValueError):
    def __init__(self, code, message, entity_ids=(), **details):
        super().__init__(message)
        self.code = code
        self.entity_ids = list(entity_ids)
        self.details = details

    def as_dict(self):
        return {
            "code": self.code,
            "message": str(self),
            "entity_ids": self.entity_ids,
            "retryable": False,
            "details": self.details,
        }


DomainError = BlendRelayError


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()
