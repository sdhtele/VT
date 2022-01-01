from enum import Enum


class Scheme(Enum):
    def __str__(self) -> str:
        return str(self.value)


class EntityAuthenticationSchemes(Scheme):
    """https://github.com/Netflix/msl/wiki/Entity-Authentication-%28Configuration%29"""
    Unauthenticated = "NONE"
    UnauthenticatedSuffixed = "NONE_SUFFIXED"
    Provisioned = "PROVISIONED"
    PreSharedKeys = "PSK"
    ModelGroupKeys = "MGK"
    RSA = "RSA"
    X509 = "X509"
    NPTicket = "NPTICKET"
    Widevine = "WIDEVINE"
    TrustedProxy = "TRUSTED_PROXY"
    MasterTokenProtected = "MT_PROTECTED"
    # Attested = "ATTESTED"  # not yet fully designed by netflix/msl@github


class UserAuthenticationSchemes(Scheme):
    """https://github.com/Netflix/msl/wiki/User-Authentication-%28Configuration%29"""
    EmailPassword = "EMAIL_PASSWORD"
    EmailPasswordHash = "EMAIL_PASSWORDHASH"
    MDX = "MDX"
    NetflixIDCookies = "NETFLIXID"
    SSO = "SSO"
    UserIDTokens = "USER_ID_TOKEN"


class KeyExchangeSchemes(Scheme):
    """https://github.com/Netflix/msl/wiki/Key-Exchange-%28Configuration%29"""
    AsymmetricWrapped = "ASYMMETRIC_WRAPPED"
    SymmetricWrapped = "SYMMETRIC_WRAPPED"
    JSONWebEncryptionKeyLadder = "JWE_LADDER"
    JSONWebKeyKeyLadder = "JWK_LADDER"
    DiffieHellman = "DH"
    AuthenticatedDiffieHellman = "AUTHENTICATED_DH"
    Widevine = "WIDEVINE"
