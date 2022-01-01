import base64
from typing import Optional, Union

from construct import Container
from pymp4.parser import Box

from vinetrimmer.utils.Widevine.key import Key
from vinetrimmer.utils.Widevine.protos import widevine_pb2 as widevine


class Session:
    def __init__(self, session_id: bytes, pssh: Container, raw: bool, offline: bool):
        if not session_id:
            raise ValueError("A session_id must be provided...")
        if not pssh:
            raise ValueError("A PSSH Box must be provided...")
        self.session_id = session_id
        self.pssh = pssh
        self.cenc_header = pssh if raw else self.parse_pssh_box(pssh)
        self.offline = offline
        self.raw = raw
        self.session_key: Optional[bytes] = None
        self.derived_keys: dict[str, Optional[bytes]] = {
            "enc": None,
            "auth_1": None,
            "auth_2": None
        }
        self.license_request: Union[widevine.SignedLicenseRequest, widevine.SignedLicenseRequestRaw]
        self.signed_license: Optional[widevine.SignedLicense] = None
        self.signed_device_certificate: Optional[widevine.SignedDeviceCertificate] = None
        self.privacy_mode = False
        self.keys: list[Key] = []

    def __repr__(self) -> str:
        return "{name}({items})".format(
            name=self.__class__.__name__,
            items=", ".join([f"{k}={repr(v)}" for k, v in self.__dict__.items()])
        )

    @staticmethod
    def parse_pssh_box(pssh: Union[str, bytes, Container]) -> widevine.WidevineCencHeader:
        """
        Parse a PSSH box's init_data into a WidevineCencHeader.

        Parameters:
            pssh: A pssh box as str (base64), bytes, or a PSSH Box Container.

        Returns:
            The init_data parsed as a WidevineCencHeader.
        """
        if isinstance(pssh, str):
            pssh = base64.b64decode(pssh)
        if not isinstance(pssh, Container):
            pssh = Box.parse(pssh)
        cenc_header = widevine.WidevineCencHeader()
        cenc_header.ParseFromString(pssh.init_data)
        return cenc_header
