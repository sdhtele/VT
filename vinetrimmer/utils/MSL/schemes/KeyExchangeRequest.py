from __future__ import annotations

import base64
from typing import Optional, Union

from vinetrimmer.utils.MSL import KeyExchangeSchemes
from vinetrimmer.utils.MSL.MSLObject import MSLObject


# noinspection PyPep8Naming
class KeyExchangeRequest(MSLObject):

    def __init__(self, scheme: KeyExchangeSchemes, keydata: dict):
        """
        Session key exchange data from a requesting entity.
        https://github.com/Netflix/msl/wiki/Key-Exchange-%28Configuration%29

        :param scheme: Key Exchange Scheme identifier
        :param keydata: Key Request data
        """
        self.scheme = str(scheme)
        self.keydata = keydata

    @classmethod
    def AsymmetricWrapped(cls, keypairid: str, mechanism: str, publickey: bytes) -> KeyExchangeRequest:
        """
        Asymmetric wrapped key exchange uses a generated ephemeral asymmetric key pair for key exchange. It will
        typically be used when there is no other data or keys from which to base secure key exchange.

        This mechanism provides perfect forward secrecy but does not guarantee that session keys will only be available
        to the requesting entity if the requesting MSL stack has been modified to perform the operation on behalf of a
        third party.

        > Key Pair ID

        The key pair ID is included as a sanity check.

        > Mechanism & Public Key

        The following mechanisms are associated public key formats are currently supported.

            Field 	    Public  Key Format 	Description
            RSA 	    SPKI 	RSA-OAEP    encrypt/decrypt
            ECC 	    SPKI 	ECIES       encrypt/decrypt
            JWEJS_RSA 	SPKI 	RSA-OAEP    JSON Web Encryption JSON Serialization
            JWE_RSA 	SPKI 	RSA-OAEP    JSON Web Encryption Compact Serialization
            JWK_RSA 	SPKI 	RSA-OAEP    JSON Web Key
            JWK_RSAES 	SPKI 	RSA PKCS#1  JSON Web Key

        :param keypairid: key pair ID
        :param mechanism: asymmetric key type
        :param publickey: public key
        """
        return cls(
            scheme=KeyExchangeSchemes.AsymmetricWrapped,
            keydata={
                "keypairid": keypairid,
                "mechanism": mechanism,
                "publickey": base64.standard_b64encode(publickey).decode("utf-8")
            }
        )

    @classmethod
    def SymmetricWrapped(cls, keyid: str) -> KeyExchangeRequest:
        """
        Symmetric wrapped key exchange uses a pre-shared symmetric key for key exchange. The wrapping algorithm is
        predefined and associated with the wrapping key.

        :param keyid: The key ID identifies the symmetric key that will be used to wrap the session keys.
        """
        return cls(
            scheme=KeyExchangeSchemes.SymmetricWrapped,
            keydata={"keyid": keyid}
        )

    @classmethod
    def JSONWebEncryptionKeyLadder(cls, mechanism: str, wrapdata: Optional[bytes] = None) -> KeyExchangeRequest:
        """
        The JSON Web Encryption (JWE) key ladder uses the Web Crypto API unwrap function for key exchange. The keying
        material is specified within a JSON Web Key (JWK) that specifies algorithm, usage, and extractable attributes.
        The JWK is then wrapped into a JWE structure. This combination will be referred to as JWE+JWK. All keys are
        marked non-extractable.

        When coupled with the pre-shared keys Kpw or model group keys Kdw wrapping key, this scheme guarantees that
        key exchange is being performed for the requesting entity but does not provide perfect forward secrecy.

        Unlike other key exchange schemes, the key ladder returns three keys: an AES-128-KeyWrap wrapping key Kwrap,
        an AES-128-CBC session encryption key Kenc, and an HMAC-SHA256 session HMAC key Khmac. Kwrap will be wrapped
        using AESWrap with Kpw, Kdw, or a previously issued Kwrap. The session keys Kenc and Khmac will be wrapped
        using AESWrap with Kwrap.

        An intermediate wrapping key Kwrap is used to limit the use of a single wrapping key for all unwrap operations
        in much the same way as the session keys are time-limited to restrict their usage. Use of a previously issued
        Kwrap instead of Kpw or Kdw is likely to be more efficient as the Kpw and Kdw keys require a higher level of
        security due to their permanent nature.

        The master token entity is expected to persist the wrapping key and wrap data for use in the next key exchange.

        **N.B. This scheme does not provide perfect forward secrecy and should only be used when it is necessary to
        satisfy other security requirements.**

        > Mechanism

        The PSK and MGK mechanisms indicate the wrapping key returned in the response should be wrapped with Kpw or
        Kdw. The WRAP mechanism indicates the wrapping key returned in the response should be wrapped with a previous
        Kwrap, which will be provided to the responding entity by including the wrap data.

        > Wrap Data

        When using the WRAP mechanism, the wrap data will be included and is a previously issued key Kwrap wrapped
        using AESWrap with an AES-128-KeyWrap key Kissuer only known by responding entity (or entities in a trusted
        services network). The responding entity is capable of unwrapping the wrap data to retrieve the previously
        issued key Kwrap and therefore does not need to remember all Kwrap keys it has issued.

        Since Kissuer allows access to all generated session keys, it should be sufficiently protected and
        periodically rotated. Kissuer must not be available to the requesting entity.

        :param mechanism: mechanism for wrapping and unwrapping the new wrapping key
        :param wrapdata: wrapped previous Kwrap
        """
        keydata = {"mechanism": mechanism}
        if wrapdata:
            keydata["wrapdata"] = base64.standard_b64encode(wrapdata).decode("utf-8")
        return cls(
            scheme=KeyExchangeSchemes.JSONWebEncryptionKeyLadder,
            keydata=keydata
        )

    @classmethod
    def JSONWebKeyKeyLadder(cls, mechanism: str, wrapdata: Optional[bytes] = None) -> KeyExchangeRequest:
        """
        The JSON Web Key (JWK) key ladder uses the Web Crypto API unwrap function for key exchange. The keying
        material is specified within a JSON Web Key (JWK) that specifies algorithm, usage, and extractable attributes.
        All keys are marked non-extractable.

        When coupled with the pre-shared keys Kpw or model group keys Kdw wrapping key, this scheme guarantees that
        key exchange is being performed for the requesting entity but does not provide perfect forward secrecy.

        Unlike other key exchange schemes, the key ladder returns three keys: an AES-128-KeyWrap wrapping key Kwrap,
        an AES-128-CBC session encryption key Kenc, and an HMAC-SHA256 session HMAC key Khmac. Kwrap will be wrapped
        using AESWrap with Kpw, Kdw, or a previously issued Kwrap. The session keys Kenc and Khmac will be wrapped
        using AESWrap with Kwrap.

        An intermediate wrapping key Kwrap is used to limit the use of a single wrapping key for all unwrap operations
        in much the same way as the session keys are time-limited to restrict their usage. Use of a previously issued
        Kwrap instead of Kpw or Kdw is likely to be more efficient as the Kpw and Kdw keys require a higher level of
        security due to their permanent nature.

        The master token entity is expected to persist the wrapping key and wrap data for use in the next key exchange.

        **N.B. This scheme does not provide perfect forward secrecy and should only be used when it is necessary to
        satisfy other security requirements.**

        > Mechanism

        The PSK and MGK mechanisms indicate the wrapping key returned in the response should be wrapped with Kpw or
        Kdw. The WRAP mechanism indicates the wrapping key returned in the response should be wrapped with a previous
        Kwrap, which will be provided to the responding entity by including the wrap data.

        > Wrap Data

        When using the WRAP mechanism, the wrap data will be included and is a previously issued key Kwrap wrapped
        using AESWrap with an AES-128-KeyWrap key Kissuer only known by responding entity (or entities in a trusted
        services network). The responding entity is capable of unwrapping the wrap data to retrieve the previously
        issued key Kwrap and therefore does not need to remember all Kwrap keys it has issued.

        Since Kissuer allows access to all generated session keys, it should be sufficiently protected and
        periodically rotated. Kissuer must not be available to the requesting entity.

        :param mechanism: mechanism for wrapping and unwrapping the new wrapping key
        :param wrapdata: wrapped previous Kwrap
        """
        keydata = {"mechanism": mechanism}
        if wrapdata:
            keydata["wrapdata"] = base64.standard_b64encode(wrapdata).decode("utf-8")
        return cls(
            scheme=KeyExchangeSchemes.JSONWebKeyKeyLadder,
            keydata=keydata
        )

    @classmethod
    def DiffieHellman(cls, parametersid: str, publickey: bytes) -> KeyExchangeRequest:
        """
        Diffie-Hellman key exchange derives the session keys from the computed shared secret. The exchange proceeds
        as a standard Diffie-Hellman exchange after which the shared secret is converted into a byte array. The byte
        array is hashed using SHA-384 and its first 16 bytes used as the AES-128-CBC session encryption key Kenc and
        its last 32 bytes used as the HMAC-SHA256 session HMAC key Khmac. This scheme provides perfect forward secrecy.

        **N.B. The Diffie-Hellman public keys and computed shared secret must be converted into a byte array for
        transport over the wire and for use in the key derivation respectively. The byte array will be the minimum
        number of bytes required for the two’s complement representation in big-endian byte-order (the most
        significant byte is first) including at least one sign bit, with exactly one zero byte in the zeroth element.
        As a result, a shared secret value of zero will be represented by an array of length one containing a single
        byte with a value of zero. This representation is compatible with the Java BigInteger.toByteArray() function
        and BigInteger(byte[]) constructor.**

        > Parameters ID

        The parameters ID identifies the Diffie-Hellman parameters to use for key generation.

        > Public Key

        The public key should contain exactly one zero byte in the zeroth element.
        When creating or upon receipt of key request data this zero byte must be prepended if missing.

        :param parametersid: Diffie-Hellman parameters identifier
        :param publickey: Diffie-Hellman public key
        """
        return cls(
            scheme=KeyExchangeSchemes.DiffieHellman,
            keydata={
                "parametersid": parametersid,
                "publickey": base64.standard_b64encode(publickey).decode("utf-8")
            }
        )

    @classmethod
    def AuthenticatedDiffieHellman(
        cls, mechanism: str, parametersid: str, publickey: bytes, wrapdata: Optional[bytes] = None
    ) -> KeyExchangeRequest:
        """
        Authenticated Diffie-Hellman uses the Web Crypto API generateKey and deriveKey functions for key exchange.
        The exchange proceeds as a standard Diffie-Hellman exchange but the key derivation operation requires an
        additional key as input and returns three keys instead of a single key: an AES-128-CBC session encryption key
        Kenc, an HMAC-SHA256 session HMAC key Khmac, and an AES-128-KeyWrap wrapping key Kwrap. The only purpose of
        Kwrap is to serve as the additional key in subsequent key exchanges.

        The use of an additional key ensures that only the parties with access to the key bits can derive the session
        keys, and thus prevents man-in-the-middle attacks that would otherwise be possible with standard
        Diffie-Hellman. When coupled with the pre-shared keys Kpw or model group keys Kdw wrapping key, this scheme
        guarantees that key exchange is being performed for the requesting entity and also provides perfect forward
        secrecy.

        The wrapping key Kwrap is used to limit use of a single key for all deriveKey operations in much the same way
        as the session keys are time-limited to restrict their usage. Use of a previously issued Kwrap instead of Kpw
        or Kdw is likely to be more efficient as the Kpw and Kdw keys require a higher level of security due to their
        permanent nature.

        The master token entity is expected to persist the derivation key and derive data for use in the next key
        exchange.

        **N.B. The Diffie-Hellman public keys and computed shared secret must be converted into a byte array for
        transport over the wire and for use in the key derivation respectively. The byte array will be the minimum
        number of bytes required for the two’s complement representation in big-endian byte-order (the most
        significant byte is first) including at least one sign bit, with exactly one zero byte in the zeroth element.
        As a result, a shared secret value of zero will be represented by an array of length one containing a single
        byte with a value of zero. This representation is compatible with the Java BigInteger.toByteArray() function
        and BigInteger(byte[]) constructor.**

        > Mechanism

        The PSK and MGK mechanisms indicate the key derivation should use Kpw or Kdw for the additional key. The WRAP
        mechanism indicates the key derivation should use the previous Kwrap for the additional key; Kwrap will be
        provided to the responding entity by including the wrap data.

        > Parameters ID

        The parameters ID identifies the Diffie-Hellman parameters to use for key generation.

        > Public Key

        The public key should contain exactly one zero byte in the zeroth element. When creating or upon receipt of
        key request data this zero byte must be prepended if missing.

        > Wrap Data

        When using the WRAP mechanism, the wrap data will be included and is the previously derived Kwrap wrapped
        using AESWrap with an AES-128-KeyWrap key Kissuer only known by the responding entity (or entities in a
        trusted services network). The responding entity is capable of unwrapping the wrap data to retrieve the
        previously issued key Kwrap and therefore does not need to remember all Kwrap keys it has derived.

        Although access to Kissuer will not allow a third party access to previously or future generated session
        keys, it should be sufficiently protected and periodically rotated. Kissuer must not be available to the
        requesting entity.

        :param mechanism: mechanism for wrapping and unwrapping the new wrapping key
        :param parametersid: Diffie-Hellman parameters identifier
        :param publickey: Diffie-Hellman public key
        :param wrapdata: wrapped previous Kwrap
        """
        keydata = {
            "mechanism": mechanism,
            "parametersid": parametersid,
            "publickey": base64.standard_b64encode(publickey).decode("utf-8")
        }
        if wrapdata:
            keydata["wrapdata"] = base64.standard_b64encode(wrapdata).decode("utf-8")
        return cls(
            scheme=KeyExchangeSchemes.AuthenticatedDiffieHellman,
            keydata=keydata
        )

    @classmethod
    def Widevine(cls, keyrequest: Union[str, bytes]) -> KeyExchangeRequest:
        """
        Google Widevine provides a secure key exchange mechanism. When requested the Widevine component will issue a
        one-time use key request. The Widevine server library can be used to authenticate the request and return
        randomly generated symmetric keys in a protected key response bound to the request and Widevine client library.
        The key response also specifies the key identities, types and their permitted usage.

        The Widevine key request also contains a model identifier and a unique device identifier with an expectation of
        long-term persistence. These values are available from the Widevine client library and can be retrieved from
        the key request by the Widevine server library.

        The Widevine client library will protect the returned keys from inspection or misuse.

        :param keyrequest: Base64-encoded Widevine CDM license challenge (PSSH: b'\x0A\x7A\x00\x6C\x38\x2B')
        """
        if not isinstance(keyrequest, str):
            keyrequest = base64.b64encode(keyrequest).decode()
        return cls(
            scheme=KeyExchangeSchemes.Widevine,
            keydata={"keyrequest": keyrequest}
        )
