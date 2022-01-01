from __future__ import annotations

from typing import Optional


class Key:
    def __init__(self, kid: bytes, key_type: str, key: bytes, permissions: Optional[list[str]] = None):
        self.kid: bytes = kid
        self.type: str = key_type
        self.key: bytes = key
        self.permissions = permissions or []

    def __repr__(self) -> str:
        return "{name}({items})".format(
            name=self.__class__.__name__,
            items=", ".join([f"{k}={repr(v)}" for k, v in self.__dict__.items()])
        )
