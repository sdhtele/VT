from __future__ import annotations

import base64
import gzip
import json
import random
import time
import zlib
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence

import jsonpickle
import requests
from Cryptodome.Cipher import AES, PKCS1_OAEP
from Cryptodome.Hash import HMAC, SHA256
from Cryptodome.PublicKey import RSA
from Cryptodome.Random import get_random_bytes
from Cryptodome.Util import Padding

from vinetrimmer.utils import Logger
from vinetrimmer.utils.MSL.MSLKeys import MSLKeys
from vinetrimmer.utils.MSL.schemes import EntityAuthenticationSchemes  # noqa: F401
from vinetrimmer.utils.MSL.schemes import KeyExchangeSchemes
from vinetrimmer.utils.MSL.schemes.EntityAuthentication import EntityAuthentication
from vinetrimmer.utils.MSL.schemes.KeyExchangeRequest import KeyExchangeRequest
from vinetrimmer.utils.MSL.schemes.UserAuthentication import UserAuthentication
from vinetrimmer.utils.Widevine.cdm import Cdm
from vinetrimmer.utils.Widevine.device import RemoteDevice
from vinetrimmer.utils.Widevine.key import Key


class MSL:

    def __init__(self, session: requests.Session, endpoint: str, sender: str, keys: MSLKeys, message_id: int,
                 user_auth: Optional[UserAuthentication] = None):
        self.session = session
        self.endpoint = endpoint
        self.sender = sender
        self.keys = keys
        self.user_auth = user_auth
        self.message_id = message_id

    @classmethod
    def handshake(cls, scheme: KeyExchangeSchemes, session: requests.Session, endpoint: str, sender: str,
                  cdm: Optional[Cdm] = None, msl_keys_path: Optional[Path] = None) -> MSL:
        log = Logger.getLogger("MSL")

        message_id = random.randint(0, pow(2, 52))
        msl_keys = MSL.load_cache_data(msl_keys_path)
        if msl_keys is not None:
            log.info("Using cached MSL negotiation handshake data")
        else:
            msl_keys = MSLKeys()
            if scheme != KeyExchangeSchemes.Widevine:
                msl_keys.rsa = RSA.generate(2048)

            if not cdm:
                raise ValueError("No cached data and no CDM specified")

            if not msl_keys_path:
                raise ValueError("No cached data and no MSL key path specified")

            if scheme == KeyExchangeSchemes.Widevine:
                msl_keys.cdm_session = cdm.open(
                    pssh=b"\x0A\x7A\x00\x6C\x38\x2B",
                    raw=True,
                    offline=True
                )
                keyrequestdata = KeyExchangeRequest.Widevine(
                    keyrequest=cdm.get_license_challenge(msl_keys.cdm_session)
                )
            else:
                assert msl_keys.rsa is not None
                keyrequestdata = KeyExchangeRequest.AsymmetricWrapped(
                    keypairid="superKeyPair",
                    mechanism="JWK_RSA",
                    publickey=msl_keys.rsa.publickey().exportKey(format="DER")
                )

            data = jsonpickle.encode({
                "entityauthdata": EntityAuthentication.Unauthenticated(sender),
                "headerdata": base64.standard_b64encode(MSL.generate_msg_header(
                    message_id=message_id,
                    sender=sender,
                    is_handshake=True,
                    keyrequestdata=keyrequestdata
                ).encode("utf-8")).decode("utf-8"),
                "signature": ""
            }, unpicklable=False)
            data += json.dumps({
                "payload": base64.standard_b64encode(json.dumps({
                    "messageid": message_id,
                    "data": "",
                    "sequencenumber": 1,
                    "endofmsg": True
                }).encode("utf-8")).decode("utf-8"),
                "signature": ""
            })

            res = session.post(
                url=endpoint,
                data=data
            )

            if res.status_code != 200:
                raise Exception(f"Key Exchange failed, response data is unexpected: {res.text}")

            key_exchange = res.json()  # expecting no payloads, so this is fine
            if "errordata" in key_exchange:
                raise Exception("Key Exchange returned an error: " + base64.standard_b64decode(
                    key_exchange["errordata"]
                ).decode("utf-8"))

            # parse the crypto keys
            key_response_data = json.JSONDecoder().decode(base64.standard_b64decode(
                key_exchange["headerdata"]
            ).decode("utf-8"))["keyresponsedata"]

            if key_response_data["scheme"] != str(scheme):
                raise Exception("Key Exchange scheme mismatch occurred")

            key_data = key_response_data["keydata"]
            if scheme == KeyExchangeSchemes.Widevine:
                if isinstance(cdm.device, RemoteDevice):
                    msl_keys.encryption, msl_keys.sign = cdm.device.exchange(
                        cdm.sessions[msl_keys.cdm_session],
                        license_res=key_data["cdmkeyresponse"],
                        enc_key_id=base64.standard_b64decode(key_data["encryptionkeyid"]),
                        hmac_key_id=base64.standard_b64decode(key_data["hmackeyid"])
                    )
                    cdm.parse_license(msl_keys.cdm_session, key_data["cdmkeyresponse"])
                else:
                    cdm.parse_license(msl_keys.cdm_session, key_data["cdmkeyresponse"])
                    keys = cdm.get_keys(msl_keys.cdm_session)
                    msl_keys.encryption = MSL.get_widevine_key(
                        kid=base64.standard_b64decode(key_data["encryptionkeyid"]),
                        keys=keys,
                        permissions=["AllowEncrypt", "AllowDecrypt"]
                    )
                    msl_keys.sign = MSL.get_widevine_key(
                        kid=base64.standard_b64decode(key_data["hmackeyid"]),
                        keys=keys,
                        permissions=["AllowSign", "AllowSignatureVerify"]
                    )
            else:
                assert msl_keys.rsa is not None
                cipher_rsa = PKCS1_OAEP.new(msl_keys.rsa)
                msl_keys.encryption = MSL.base64key_decode(
                    json.JSONDecoder().decode(cipher_rsa.decrypt(
                        base64.standard_b64decode(key_data["encryptionkey"])
                    ).decode("utf-8"))["k"]
                )
                msl_keys.sign = MSL.base64key_decode(
                    json.JSONDecoder().decode(cipher_rsa.decrypt(
                        base64.standard_b64decode(key_data["hmackey"])
                    ).decode("utf-8"))["k"]
                )
            msl_keys.mastertoken = key_response_data["mastertoken"]

            MSL.cache_keys(msl_keys, msl_keys_path)
            log.info("MSL Handshake successful!")
        return cls(
            session=session,
            endpoint=endpoint,
            sender=sender,
            keys=msl_keys,
            message_id=message_id
        )

    @staticmethod
    def load_cache_data(msl_keys_path: Optional[Path] = None) -> Optional[MSLKeys]:
        if not msl_keys_path or not msl_keys_path.is_file():
            return None
        msl_keys = jsonpickle.decode(msl_keys_path.read_text(encoding="utf8"))
        if msl_keys.rsa:
            # noinspection PyTypeChecker
            # expects RsaKey, but is a string, this is because jsonpickle can't pickle RsaKey object
            # so as a workaround it exports to PEM, and then when reading, it imports that PEM back
            # to an RsaKey :)
            msl_keys.rsa = RSA.importKey(msl_keys.rsa)
        # If it's expired or close to, return None as it's unusable
        if msl_keys.mastertoken and ((datetime.utcfromtimestamp(int(json.JSONDecoder().decode(
                base64.standard_b64decode(msl_keys.mastertoken["tokendata"]).decode("utf-8")
        )["expiration"])) - datetime.now()).total_seconds() / 60 / 60) < 10:
            return None
        return msl_keys

    @staticmethod
    def cache_keys(msl_keys: MSLKeys, msl_keys_path: Path) -> None:
        msl_keys_path.parent.mkdir(parents=True, exist_ok=True)
        if msl_keys.rsa:
            # jsonpickle can't pickle RsaKey objects :(
            msl_keys.rsa = msl_keys.rsa.export_key()  # type: ignore[assignment]
        msl_keys_path.write_text(jsonpickle.encode(msl_keys), encoding="utf8")
        if msl_keys.rsa:
            # re-import now
            msl_keys.rsa = RSA.importKey(bytes, msl_keys.rsa)

    @staticmethod
    def generate_msg_header(
        message_id: int, sender: str, is_handshake: bool, userauthdata: Optional[UserAuthentication] = None,
        keyrequestdata: Optional[KeyExchangeRequest] = None, compression: str = "GZIP"
    ) -> str:
        """
        The MSL header carries all MSL data used for entity and user authentication, message encryption
        and verification, and service tokens. Portions of the MSL header are encrypted.
        https://github.com/Netflix/msl/wiki/Messages#header-data

        :param message_id: number against which payload chunks are bound to protect against replay.
        :param sender: ESN
        :param is_handshake: This flag is set true if the message is a handshake message and will not include any
        payload chunks. It will include keyrequestdata.
        :param userauthdata: UserAuthData
        :param keyrequestdata: KeyRequestData
        :param compression: Supported compression algorithms.

        :return: The base64 encoded JSON String of the header
        """
        header_data = {
            "messageid": message_id,
            "renewable": True,  # MUST be True if is_handshake
            "handshake": is_handshake,
            "capabilities": {
                "compressionalgos": [compression] if compression else [],
                "languages": ["en-US"],  # bcp-47
                "encoderformats": ["JSON"]
            },
            "timestamp": int(time.time()),
            # undocumented or unused:
            "sender": sender,
            "nonreplayable": False,
            "recipient": "Netflix",
        }
        if userauthdata:
            header_data["userauthdata"] = userauthdata
        if keyrequestdata:
            header_data["keyrequestdata"] = [keyrequestdata]
        return jsonpickle.encode(header_data, unpicklable=False)

    @staticmethod
    def get_widevine_key(kid: bytes, keys: list[Key], permissions: list[str]) -> Optional[bytes]:
        log = Logger.getLogger("MSL")
        for key in keys:
            if key.kid != kid:
                continue
            if key.type != "OPERATOR_SESSION":
                log.warning(f"Widevine Key Exchange: Wrong key type (not operator session) key {key}")
                continue
            if not set(permissions) <= set(key.permissions):
                log.warning(f"Widevine Key Exchange: Incorrect permissions, key {key}, needed perms {permissions}")
                continue
            return key.key
        return None

    def send_message(
        self, endpoint: str, params: dict[str, str], application_data: dict[str, Any],
        userauthdata: Optional[UserAuthentication] = None
    ) -> tuple[dict, dict]:
        message = self.create_message(application_data, userauthdata)
        res = self.session.post(url=endpoint, data=message, params=params)
        header, payload_data = self.parse_message(res.text)
        if "errordata" in header:
            raise Exception(
                "MSL response message contains an error: {}".format(
                    json.loads(base64.standard_b64decode(header["errordata"].encode("utf-8")).decode("utf-8"))
                )
            )
        return header, payload_data

    def create_message(
        self, application_data: dict[str, Any], userauthdata: Optional[UserAuthentication] = None
    ) -> str:
        self.message_id += 1  # new message must ue a new message id

        headerdata = self.encrypt(self.generate_msg_header(
            message_id=self.message_id,
            sender=self.sender,
            is_handshake=False,
            userauthdata=userauthdata
        ))

        header = json.dumps({
            "headerdata": base64.standard_b64encode(headerdata.encode("utf-8")).decode("utf-8"),
            "signature": self.sign(headerdata).decode("utf-8"),
            "mastertoken": self.keys.mastertoken
        })

        payload_chunks = [self.encrypt(json.dumps({
            "messageid": self.message_id,
            "data": self.gzip_compress(json.dumps(application_data).encode("utf-8")).decode("utf-8"),
            "compressionalgo": "GZIP",
            "sequencenumber": 1,  # todo ; use sequence_number from master token instead?
            "endofmsg": True
        }))]

        message = header
        for payload_chunk in payload_chunks:
            message += json.dumps({
                "payload": base64.standard_b64encode(payload_chunk.encode("utf-8")).decode("utf-8"),
                "signature": self.sign(payload_chunk).decode("utf-8")
            })

        return message

    def decrypt_payload_chunks(self, payload_chunks: Sequence) -> dict:
        """
        Decrypt and extract data from payload chunks

        :param payload_chunks: List of payload chunks
        :return: json object
        """
        raw_data = ""
        assert self.keys.encryption is not None

        for payload_chunk in payload_chunks:
            # todo ; verify signature of payload_chunk["signature"] against payload_chunk["payload"]
            # expecting base64-encoded json string
            payload_chunk = json.loads(base64.standard_b64decode(payload_chunk["payload"]).decode("utf-8"))
            # decrypt the payload
            payload_decrypted = AES.new(
                key=self.keys.encryption,
                mode=AES.MODE_CBC,
                iv=base64.standard_b64decode(payload_chunk["iv"])
            ).decrypt(base64.standard_b64decode(payload_chunk["ciphertext"]))
            payload_decrypted = Padding.unpad(payload_decrypted, 16)
            payload_decrypted = json.loads(payload_decrypted.decode("utf-8"))
            # decode and uncompress data if compressed
            payload_data = base64.standard_b64decode(payload_decrypted["data"])
            if payload_decrypted.get("compressionalgo") == "GZIP":
                payload_data = zlib.decompress(payload_data, 16 + zlib.MAX_WBITS)
            raw_data += payload_data.decode("utf-8")

        data = json.loads(raw_data)
        if "error" in data:
            raise Exception(f"Payload contains an error: {data['error']}")
        return data["result"]

    def parse_message(self, message: str) -> tuple[dict, dict]:
        """
        Parse an MSL message into a header and list of payload chunks

        :param message: MSL message
        :returns: a 2-item tuple containing message and list of payload chunks if available
        """
        parsed_message = json.loads("[{}]".format(message.replace("}{", "},{")))

        header = parsed_message[0]
        encrypted_payload_chunks = parsed_message[1:] if len(parsed_message) > 1 else []
        if encrypted_payload_chunks:
            payload_chunks = self.decrypt_payload_chunks(encrypted_payload_chunks)
        else:
            payload_chunks = {}

        return header, payload_chunks

    @staticmethod
    def gzip_compress(data: bytes) -> bytes:
        out = BytesIO()
        with gzip.GzipFile(fileobj=out, mode="w") as f:
            f.write(data)
        return base64.standard_b64encode(out.getvalue())

    @staticmethod
    def base64key_decode(payload: str) -> bytes:
        length = len(payload) % 4
        if length == 2:
            payload += "=="
        elif length == 3:
            payload += "="
        elif length != 0:
            raise ValueError("Invalid base64 string")
        return base64.urlsafe_b64decode(payload.encode("utf-8"))

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt the given Plaintext with the encryption key
        :param plaintext:
        :return: Serialized JSON String of the encryption Envelope
        """
        iv = get_random_bytes(16)
        return json.dumps({
            "ciphertext": base64.standard_b64encode(
                AES.new(
                    self.keys.encryption,
                    AES.MODE_CBC,
                    iv
                ).encrypt(
                    Padding.pad(plaintext.encode("utf-8"), 16)
                )
            ).decode("utf-8"),
            "keyid": "{}_{}".format(self.sender, json.loads(
                base64.standard_b64decode(self.keys.mastertoken["tokendata"]).decode("utf-8")
            )["sequencenumber"]),
            "sha256": "AA==",
            "iv": base64.standard_b64encode(iv).decode("utf-8")
        })

    def sign(self, text: str) -> bytes:
        """
        Calculates the HMAC signature for the given text with the current sign key and SHA256
        :param text:
        :return: Base64 encoded signature
        """
        return base64.standard_b64encode(HMAC.new(self.keys.sign, text.encode("utf-8"), SHA256).digest())
