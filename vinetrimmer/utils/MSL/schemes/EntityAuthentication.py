from __future__ import annotations

import base64
from typing import NoReturn

from vinetrimmer.utils.MSL import EntityAuthenticationSchemes
from vinetrimmer.utils.MSL.MSLObject import MSLObject


# noinspection PyPep8Naming
class EntityAuthentication(MSLObject):

    def __init__(self, scheme: EntityAuthenticationSchemes, authdata: dict):
        """
        Data used to identify and authenticate the entity associated with a message.
        https://github.com/Netflix/msl/wiki/Entity-Authentication-%28Configuration%29

        :param scheme: Entity Authentication Scheme identifier
        :param authdata: Entity Authentication data
        """
        self.scheme = str(scheme)
        self.authdata = authdata

    @classmethod
    def Unauthenticated(cls, identity: str) -> EntityAuthentication:
        """
        The unauthenticated entity authentication scheme does not provide encryption or authentication and only
        identifies the entity. Therefore entity identities can be harvested and spoofed. The benefit of this
        authentication scheme is that the entity has control over its identity. This may be useful if the identity is
        derived from or related to other data, or if retaining the identity is desired across state resets or in the
        event of MSL errors requiring entity re-authentication.
        """
        return cls(
            scheme=EntityAuthenticationSchemes.Unauthenticated,
            authdata={"identity": identity}
        )

    @classmethod
    def UnauthenticatedSuffixed(cls, root: str, suffix: str) -> EntityAuthentication:
        """
        The unauthenticated suffixed entity authentication scheme does not provide encryption or authentication and
        only identifies the entity. Therefore entity identities can be harvested and spoofed. The benefit of this
        authentication scheme is that the entity has control over its identity. This may be useful if the identity is
        derived from or related to other data, or if retaining the identity is desired across state resets or in the
        event of MSL errors requiring entity re-authentication.

        The root may be a shared value common to multiple entities with a suffix that uniquely identifies each entity.
        The entity identity is then constructed by concatenating the root and suffix with a `.` character.
        """
        return cls(
            scheme=EntityAuthenticationSchemes.UnauthenticatedSuffixed,
            authdata={
                "root": root,
                "suffix": suffix
            }
        )

    @classmethod
    def Provisioned(cls) -> EntityAuthentication:
        """
        The provisioned entity authentication scheme does not provide encryption or authentication. It also does not
        identify the entity. When coupled with the appropriate key exchange mechanism the entity can request its
        identity from a remote entity. This scheme can prevent identities from being harvested or spoofed, but does
        not allow an entity to have control over its identity or necessarily retain its identity across state resets
        or in the event of MSL errors requiring entity re-authentication.
        """
        return cls(
            scheme=EntityAuthenticationSchemes.Provisioned,
            authdata={}
        )

    @classmethod
    def PSKorMGK(cls, scheme: EntityAuthenticationSchemes, identity: str) -> EntityAuthentication:
        """
        The pre-shared keys and model group keys entity authentication schemes provide encryption and authentication
        using a pair of AES-128-CBC and HMAC-SHA256 keys. A third AES-128-KeyWrap key is also available. The keys are
        unique per entity identity, usually permanent, and shared out-of-band.

        The pre-shared encryption and authentication keys are randomly generated and named Kpe and Kph respectively.
        The wrapping key is named Kpw.

        The model group encryption and authentication keys are derived and named Kde and Kdh respectively. The
        wrapping key is named Kdw.

        The model group keys Kde and Kdh are derived from the entity identity and a model group master key. A model
        group is defined as a group of similar devices and each model group has its own master key Kmgm, which is
        either an AES-128-ECB or 3DES-ECB key. Access to Kmgm allows the keys to be derived on demand, as the entity
        identity is not secret. For this reason access to Kmgm should be strictly controlled.

            bytes = encrypt(Kmgm, SHA-384(identity))
            Kde = bytes[0...15]
            Kdh = bytes[16...47]

        For increased strength against potential key collision attacks, the following options may be used instead:

          -  PBKDF2 with a fixed unique salt per model and Kmgm as the password.
          -  HKDF with a fixed unique salt per model and Kmgm as the HMAC key.
          -  AES-CBC or 3DES-CBC with a fixed unique initialization vector per model and Kmgm as the encryption key.

        For both pre-shared keys and model group keys the wrapping key is derived from the encryption and HMAC keys as
        follows.

            salt = 02 76 17 98 4f 62 27 53 9a 63 0b 89 7c 01 7d 69
            info = 80 9f 82 a7 ad df 54 8d 3e a9 dd 06 7f f9 bb 91
            wrappingKey = trunc_128(HMAC-SHA256(HMAC-SHA256(salt, encryptionKey||hmacKey), info))

        > Encryption

        The encryption algorithm is AES/CBC/PKCS5Padding and the initialization vector is randomly chosen. Ciphertext
        is encapsulated within a version 1 MSL ciphertext envelope.

        > Authentication

        The authentication algorithm is HmacSHA256 and is computed over the binary representation of the encryption
        envelope and included as raw bytes within a version 1 MSL signature envelope.
        """
        if not isinstance(scheme, EntityAuthenticationSchemes):
            raise ValueError("scheme must be an EntityAuthenticationScheme")
        if scheme not in [EntityAuthenticationSchemes.PreSharedKeys, EntityAuthenticationSchemes.ModelGroupKeys]:
            raise ValueError("scheme must be either PreSharedKeys or ModelGroupKeys")
        return cls(
            scheme=scheme,
            authdata={"identity": identity}
        )

    @classmethod
    def RSA(cls, identity: str, pubkeyid: str) -> EntityAuthentication:
        """
        The RSA entity authentication scheme only provides authentication using an RSA key pair. The public key is
        shared out-of-band or over an authenticated channel and identified by a public key ID.

        This authentication scheme is suitable for use by trusted services servers where the public key is provided to
        the client out-of-band. Encryption of application data is possible once the client has been issued a master
        token.

        > Authentication

        The RSA signature is computed using SHA256withRSA and included as raw bytes within a version 1 MSL signature
        envelope.
        """
        return cls(
            scheme=EntityAuthenticationSchemes.RSA,
            authdata={
                "identity": identity,
                "pubkeyid": pubkeyid
            }
        )

    @classmethod
    def X509(cls, x509certificate: str) -> EntityAuthentication:
        """
        The X.509 entity authentication scheme only provides authentication using an RSA or ECC key pair. The
        certificate subject canonical name is considered the device identity. A certificate authority trust store may
        be used to restrict acceptance of certificates.

        Encryption of application data is possible once the client has been issued a master token.

        > Authentication

        The signature is computing using SHA256withRSA or SHA256withECDSA and included as raw bytes within a version
        1 MSL signature envelope.

        :param x509certificate: Base64-encoded X.509 certificate (i.e. PEM formatted)
        """
        return cls(
            scheme=EntityAuthenticationSchemes.X509,
            authdata={
                "x509certificate": x509certificate
            }
        )

    @classmethod
    def NPTicket(cls, npticket: bytes) -> EntityAuthentication:
        """
        The NP-Ticket entity authentication scheme is used by the Sony Playstation 3. It only provides authentication
        using an RSA key pair dynamically generated on the console. The entity identity and public key are provided to
        the remote entity within an NP-Ticket.

        > NP-Ticket

        An NP-Ticket is an expiring data container constructed and signed by the Playstation Network service on behalf
        of an authorized console. The NP-Ticket can be authenticated by a Sony-provided library after which the RSA
        public key and identity can be extracted.

        > Authentication

        The RSA signature is computed using SHA1withRSA and included as raw bytes within a version 1 MSL signature
        envelope.
        """
        return cls(
            scheme=EntityAuthenticationSchemes.NPTicket,
            authdata={
                "npticket": base64.standard_b64encode(npticket).decode("utf-8")
            }
        )

    @classmethod
    def Widevine(cls, devtype: str, keyrequest: str) -> EntityAuthentication:
        """
        The Widevine entity authentication scheme is used by devices with the Widevine CDM. It does not provide
        encryption or authentication and only identifies the entity. Therefore entity identities can be harvested
        and spoofed. The entity identity is composed from the provided device type and Widevine key request data. The
        Widevine CDM properties can be extracted from the key request data.

        When coupled with the Widevine key exchange scheme, the entity identity can be cryptographically validated by
        comparing the entity authentication key request data against the key exchange key request data.

        Note that the local entity will not know its entity identity when using this scheme.

        > Devtype

        An arbitrary value identifying the device type the local entity wishes to assume. The data inside the Widevine
        key request may be optionally used to validate the claimed device type.

        :param devtype: Local entity device type
        :param keyrequest: Widevine key request
        """
        return cls(
            scheme=EntityAuthenticationSchemes.Widevine,
            authdata={
                "devtype": devtype,
                "keyrequest": keyrequest
            }
        )

    @classmethod
    def TrustedProxy(
        cls, identity: bytes, signature: bytes, proxyscheme: str, proxyauthdata: dict
    ) -> EntityAuthentication:
        """
        The trusted proxy entity authentication scheme provides a means by which a trusted intermediary can communicate
        on behalf of a third entity. For all intents and purposes the authenticated entity identity is the third
        entity’s identity. However authentication is performed against the proxy. Therefore, trust in the third party’s
        entity identity is only as strong as trust in the proxy. It is strongly recommended that the proxy authenticate
        the third entity.

        The proxy may use any other entity authentication scheme, however it is important to restrict the trusted proxy
        authentication scheme to specific proxy entity identities that are trusted and to specific proxy entity
        authentication schemes to prevent abuse.

        Encryption and authentication is provided if the proxy’s entity authentication scheme provides encryption and
        authentication.

        > Identity & Signature

        The third entity identity is encrypted with the proxy’s entity encryption mechanism. The verification data is
        computed over the encrypted identity, using the proxy’s entity authentication mechanism.

        > Encryption

        The encryption mechanism is equal to the encryption provided by the proxy entity authentication scheme.

        > Authentication

        The authentication mechanism is equal to the authentication provided by the proxy entity authentication scheme.

        :param identity: encrypted third entity identity
        :param signature: verification data of the encrypted third entity identity
        :param proxyscheme: proxy entity authentication scheme
        :param proxyauthdata: proxy entity authentication data
        """
        return cls(
            scheme=EntityAuthenticationSchemes.TrustedProxy,
            authdata={
                "identity": base64.standard_b64encode(identity).decode("utf-8"),
                "signature": base64.standard_b64encode(signature).decode("utf-8"),
                "proxyscheme": proxyscheme,
                "proxyauthdata": proxyauthdata
            }
        )

    @classmethod
    def MasterTokenProtected(cls, mastertoken, authdata: bytes, signature: bytes) -> EntityAuthentication:
        """
        The master token protected entity authentication scheme is used to securely include entity authentication data
        in a message.

        Normally a message header includes entity authentication data in the clear which is necessary for the recipient
        to identify the authentication scheme and parse the authentication data without any prior knowledge. However
        this means the scheme and data can also be identified and parsed by any third party observer. By encrypting
        and integrity protecting this data with session keys previously established under a different identity, an
        entity may provide its true identity to a recipient without fear of unauthorized observation.

        The message will be encrypted and/or signed with the crypto context associated with the encapsulated entity
        authentication data. This is necessary because messages are authenticated by decrypting and/or verifying the
        message data, and it is the encapsulated entity identity that needs to be verified.

        Since master token protected entity authentication data may encapsulate entity authentication data of any
        arbitrary authentication scheme, there is no way to know if the crypto context used provides encryption or
        integrity protection. Therefore the master token protected entity authentication scheme cannot promise to
        provide encryption or integrity protection, even if both of those message properties may actually apply.

        > Master Token

        A previously issued master token. The session keys associated with this master token will be used to encrypt
        and verify the encapsulated entity authentication data.

        > Authentication Data

        The encapsulated entity authentication data that identifies the sending entity. The encoded data has been
        encrypted with the master token’s encryption key and algorithm.

        > Signature

        The verification data computed over the encrypted encapsulated encryption data, using the master token’s
        signature key and algorithm.

        Example:
        https://github.com/Netflix/msl/wiki/Master-Token-Protected-Entity-Authentication#example-entity-authentication

        :param mastertoken: previously established master token
        :param authdata: encrypted entity authentication data
        :param signature: verification data of the encrypted entity authentication data
        """
        return cls(
            scheme=EntityAuthenticationSchemes.Unauthenticated,
            authdata={
                "mastertoken": mastertoken,
                "authdata": base64.standard_b64encode(authdata).decode("utf-8"),
                "signature": base64.standard_b64encode(signature).decode("utf-8"),
            }
        )

    @classmethod
    def Attested(cls) -> NoReturn:
        raise NotImplementedError(
            "Attested Entity Authentication is not finished design work. "
            "More information: https://github.com/Netflix/msl/wiki/Attested-Entity-Authentication"
        )
