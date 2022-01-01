from __future__ import annotations

from typing import Union
from uuid import UUID

from construct import Container
from Cryptodome.Random import get_random_bytes, random

from vinetrimmer.utils.Widevine.device import LocalDevice, RemoteDevice
from vinetrimmer.utils.Widevine.key import Key
from vinetrimmer.utils.Widevine.session import Session


class Cdm:
    system_id = b"\xed\xef\x8b\xa9\x79\xd6\x4a\xce\xa3\xc8\x27\xdc\xd5\x1d\x21\xed"
    uuid = UUID(bytes=system_id)
    urn = f"urn:uuid:{uuid}"
    service_certificate_challenge = b"\x08\x04"
    common_privacy_cert = ("CAUSxwUKwQIIAxIQFwW5F8wSBIaLBjM6L3cqjBiCtIKSBSKOAjCCAQoCggEBAJntWzsyfateJO/DtiqVtZhSCtW8y"
                           "zdQPgZFuBTYdrjfQFEEQa2M462xG7iMTnJaXkqeB5UpHVhYQCOn4a8OOKkSeTkwCGELbxWMh4x+Ib/7/up34QGeHl"
                           "eB6KRfRiY9FOYOgFioYHrc4E+shFexN6jWfM3rM3BdmDoh+07svUoQykdJDKR+ql1DghjduvHK3jOS8T1v+2RC/TH"
                           "hv0CwxgTRxLpMlSCkv5fuvWCSmvzu9Vu69WTi0Ods18Vcc6CCuZYSC4NZ7c4kcHCCaA1vZ8bYLErF8xNEkKdO7Dev"
                           "Sy8BDFnoKEPiWC8La59dsPxebt9k+9MItHEbzxJQAZyfWgkCAwEAAToUbGljZW5zZS53aWRldmluZS5jb20SgAOuN"
                           "HMUtag1KX8nE4j7e7jLUnfSSYI83dHaMLkzOVEes8y96gS5RLknwSE0bv296snUE5F+bsF2oQQ4RgpQO8GVK5uk5M"
                           "4PxL/CCpgIqq9L/NGcHc/N9XTMrCjRtBBBbPneiAQwHL2zNMr80NQJeEI6ZC5UYT3wr8+WykqSSdhV5Cs6cD7xdn9"
                           "qm9Nta/gr52u/DLpP3lnSq8x2/rZCR7hcQx+8pSJmthn8NpeVQ/ypy727+voOGlXnVaPHvOZV+WRvWCq5z3CqCLl5"
                           "+Gf2Ogsrf9s2LFvE7NVV2FvKqcWTw4PIV9Sdqrd+QLeFHd/SSZiAjjWyWOddeOrAyhb3BHMEwg2T7eTo/xxvF+YkP"
                           "j89qPwXCYcOxF+6gjomPwzvofcJOxkJkoMmMzcFBDopvab5tDQsyN9UPLGhGC98X/8z8QSQ+spbJTYLdgFenFoGq4"
                           "7gLwDS6NWYYQSqzE3Udf2W7pzk4ybyG4PHBYV3s4cyzdq8amvtE/sNSdOKReuHpfQ=")

    def __init__(self, device: Union[LocalDevice, RemoteDevice]):
        """Create a Widevine Content Decryption Module using a specific devices data."""
        self.sessions: dict[bytes, Session] = {}
        self.device = device

    def open(self, pssh: Union[Container, str], raw: bool = False, offline: bool = False) -> bytes:
        """
        Open a CDM session with the specified pssh box.
        Multiple sessions can be active at the same time.

        Parameters:
            pssh: PSSH Data, either a full WidevineCencHeader or a full mp4 pssh box.
            raw: If the PSSH Data is incomplete, e.g. NF Key Exchange, set this to True.
            offline: 'OFFLINE' License Type field value.

        Returns:
            New Session ID.
        """
        session_id = self.create_session_id(self.device)
        self.sessions[session_id] = Session(session_id, pssh, raw, offline)
        return session_id

    def close(self, session_id: bytes) -> bool:
        """
        Close a CDM session.
        :param session_id: Session to close.
        :returns: True if Successful.
        """
        if self.is_session_open(session_id):
            self.sessions.pop(session_id)
            return True
        return False

    def is_session_open(self, session_id: bytes) -> bool:
        return session_id in self.sessions

    def set_service_certificate(self, session_id: bytes, certificate: Union[bytes, str]) -> bool:
        if not self.is_session_open(session_id):
            raise ValueError(f"There's no session with the id [{session_id!r}]...")
        return self.device.set_service_certificate(self.sessions[session_id], certificate)

    def get_license_challenge(self, session_id: bytes) -> bytes:
        if not self.is_session_open(session_id):
            raise ValueError(f"There's no session with the id [{session_id!r}]...")
        return self.device.get_license_challenge(self.sessions[session_id])

    def parse_license(self, session_id: bytes, license_res: Union[bytes, str]) -> bool:
        if not self.is_session_open(session_id):
            raise ValueError(f"There's no session with the id [{session_id!r}]...")
        return self.device.parse_license(self.sessions[session_id], license_res)

    def get_keys(self, session_id: bytes, content_only: bool = False) -> list[Key]:
        if not self.is_session_open(session_id):
            raise ValueError(f"There's no session with the id [{session_id!r}]...")
        keys = self.sessions[session_id].keys
        if content_only:
            return [x for x in keys if x.type == "CONTENT"]
        return keys

    @staticmethod
    def create_session_id(device: Union[LocalDevice, RemoteDevice]) -> bytes:
        if device.type == LocalDevice.Types.ANDROID:
            session_id = "{hex:16X}{counter}".format(
                hex=random.getrandbits(64),
                counter="01"  # counter, this resets regularly so it's fine to use 01
            )
            session_id.ljust(32, "0")  # pad to 16 bytes (32 chars)
            return session_id.encode("ascii")
        if device.type == LocalDevice.Types.CHROME:
            return get_random_bytes(16)
        raise ValueError(f"Device Type {device.type.name} is not implemented")
