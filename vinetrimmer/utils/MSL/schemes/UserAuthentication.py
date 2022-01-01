from __future__ import annotations

import base64
from typing import Any, Optional

from vinetrimmer.utils.MSL.MSLObject import MSLObject
from vinetrimmer.utils.MSL.schemes import UserAuthenticationSchemes


# noinspection PyPep8Naming
class UserAuthentication(MSLObject):

    def __init__(self, scheme: UserAuthenticationSchemes, authdata: dict):
        """
        Data used to identify and authenticate the user associated with a message.
        https://github.com/Netflix/msl/wiki/User-Authentication-%28Configuration%29

        :param scheme: User Authentication Scheme identifier
        :param authdata: User Authentication data
        """
        self.scheme = str(scheme)
        self.authdata = authdata

    @classmethod
    def EmailPassword(cls, email: str, password: str) -> UserAuthentication:
        """
        Email and password is a standard user authentication scheme in wide use.

        :param email: user email address
        :param password: user password
        """
        return cls(
            scheme=UserAuthenticationSchemes.EmailPassword,
            authdata={
                "email": email,
                "password": password
            }
        )

    @classmethod
    def EmailPasswordHash(cls, email: str, hash_: bytes, nonce: bytes) -> UserAuthentication:
        """
        This user authentication scheme makes use of an email and password but avoids sending the password over the
        wire as an extra security precaution. Instead, a nonce is hashed with the password using SHA-256 and the
        resulting hash value is sent. The recipient can verify the value if it also knows the password.

        > Nonce

        The nonce should be a randomly generated value of sufficient length. Using a value at least as long as the
        hash algorithm block size is recommended.

        > Hash

        The hash of the nonce concatenated with the user password.

        :param email: user email address
        :param hash_: hash of nonce and user password
        :param nonce: random value
        """
        return cls(
            scheme=UserAuthenticationSchemes.EmailPasswordHash,
            authdata={
                "email": email,
                "hash": base64.standard_b64encode(hash_).decode("utf-8"),
                "nonce": base64.standard_b64encode(nonce).decode("utf-8")
            }
        )

    @classmethod
    def MDX(
        cls, pin: str, mdxauthdata: bytes, signature: bytes, mastertoken: Optional[dict] = None,
        cticket: Optional[bytes] = None
    ) -> UserAuthentication:
        """
        MDX is the Netflix Multiple-Device Experience and allows a controller client to issue commands to a target
        client. The MDX user authentication scheme is used by an MDX target to assume the user identity of an MDX
        controller using data provided by the controller. An example is a Playstation 3 target being controlled by
        an iOS controller.

        There are three parties involved: controller, target, and authentication server. One communication channel
        exists between the controller and target, and a MSL communication channel exists between the target and
        authentication server. The controller must have already authenticated itself and its user against the
        authentication server. The target may be unauthenticated against the authentication server.

        There are two operations involved when an MDX session is established between the controller and target: user
        authentication and controller-target pairing. The described MDX user authentication scheme is used to perform
        user authentication. Controller-target pairing occurs at the application layer and is subject to authorization
        and other business logic.

        To ensure the target is authorized to assume the user identity of the controller, a PIN is displayed by the
        target. A user must enter this PIN onto the controller and is included in the MDX authentication data sent by
        the controller to the target. The target also includes its PIN in the user authentication data sent by the
        target to the authentication server using MSL.

        > Master Token or CTicket

        MSL-based controllers may choose to include their master token or include a MSL token construct. Legacy
        NTBA-based controllers will always include their CTicket. (A CTicket is an encrypted token containing
        AES-128-CBC and HMAC-SHA256 session keys similarly to a master token, plus a user identity similarly to a
        user ID token.)

        The MSL token construct is defined as follows:

            1,mastertoken,useridtoken

        or

            1=mastertoken=useridtoken

        where the master token and user ID token encoded representations are Base64-encoded. The existence of commas
        can be used to differentiate a MSL token construct from a Base64-encoded CTicket as Base64-encoded data does
        not include commas. When equal signs are used instead the Base64-encoded master token and user ID token can
        still be properly extracted.

        > MDX Authentication Data

        The MDX authentication data is constructed by the controller and sent to the target to authorize the target
        to assume the controller’s user identity.

        > Signature

        The signature algorithm is HmacSHA256 and is computed over the binary representation of the MDX authentication
        data. The master token or CTicket HMAC-SHA256 session key is used.

        More information with graph examples:
        https://github.com/Netflix/msl/wiki/MDX-User-Authentication

        :param pin: MDX target PIN
        :param mdxauthdata: controller-produced MDX authentication data (mdxauthdata)
        :param signature: controller-produced verification data of the MDX authentication data
        :param mastertoken: controller master token
        :param cticket: controller CTicket or MSL token construct
        """
        authdata: dict[str, Any] = {
            "pin": pin,
            "mdxauthdata": base64.standard_b64encode(mdxauthdata).decode("utf-8"),
            "signature": base64.standard_b64encode(signature).decode("utf-8")
        }
        if mastertoken and cticket:
            raise ValueError("Only mastertoken or cticket may be set, not both")
        if mastertoken:
            authdata["mastertoken"] = mastertoken
        elif cticket:
            authdata["cticket"] = cticket
        return cls(
            scheme=UserAuthenticationSchemes.MDX,
            authdata=authdata
        )

    @classmethod
    def NetflixIDCookies(cls, netflixid: str, securenetflixid: str) -> UserAuthentication:
        """
        Netflix ID HTTP cookies are used when the user has previously logged in to a web site. Possession of the
        cookies serves as proof of user identity, in the same manner as they do when communicating with the web site.

        The Netflix ID cookie and Secure Netflix ID cookie are HTTP cookies issued by the Netflix web site after
        subscriber login. The Netflix ID cookie is encrypted and identifies the subscriber and analogous to a
        subscriber’s username. The Secure Netflix ID cookie is tied to a Netflix ID cookie and only sent over HTTPS
        and analogous to a subscriber’s password.

        In some cases the Netflix ID and Secure Netflix ID cookies will be unavailable to the MSL stack or application.
        If either or both of the Netflix ID or Secure Netflix ID cookies are absent in the above data structure the
        HTTP cookie headers will be queried for it; this is only acceptable when HTTPS is used as the underlying
        transport protocol.

        :param netflixid: Netflix ID cookie
        :param securenetflixid: Secure Netflix ID cookie
        """
        return cls(
            scheme=UserAuthenticationSchemes.NetflixIDCookies,
            authdata={
                "netflixid": netflixid,
                "securenetflixid": securenetflixid
            }
        )

    @classmethod
    def SSO(cls, mechanism: str, token: bytes, email: Optional[str] = None, password: Optional[str] = None,
            netflixid: Optional[str] = None, securenetflixid: Optional[str] = None) -> UserAuthentication:
        """
        The single-sign-on user authentication scheme is used in situations where a third-party provides a unified
        multi-device user experience. Examples include Microsoft Xbox Live, Apple’s iOS, and Samsung Hub.

        > Token

        The SSO token is issued by a third-party and authenticated by the mechanism provided by the third-party. It
        contains the third-party user ID. Possession of the token is considered sufficient proof of the third-party
        user ID.

        > Authentication

        If only an SSO token is provided then only authentication is performed. The user identity associated with the
        third-party user ID, if any, is assumed. If there is no associated user identity then authentication fails.

        > Association

        If an SSO token is provided in conjunction with either email/password or Netflix ID cookies then both
        authentication and association is performed. The email/password or Netflix ID cookies are used to authenticate
        the user and that user identity is then associated with the third-party user ID.

        :param mechanism: SSO mechanism
        :param token: third-party SSO token
        :param email: user email address
        :param password: user password
        :param netflixid: Netflix ID cookie
        :param securenetflixid: Secure Netflix ID cookie
        """
        authdata = {
            "mechanism": mechanism,
            "token": base64.standard_b64encode(token).decode("utf-8")
        }
        if (email or password) and (netflixid or securenetflixid):
            raise ValueError("Only email & password or netflixid & securenetflixid may be set, not both")
        if email and password:
            authdata["email"] = email
            authdata["password"] = password
        elif netflixid and securenetflixid:
            authdata["netflixid"] = netflixid
            authdata["securenetflixid"] = securenetflixid
        return cls(
            scheme=UserAuthenticationSchemes.SSO,
            authdata=authdata
        )

    @classmethod
    def UserIDTokens(cls, mastertoken: dict, useridtoken: dict) -> UserAuthentication:
        """
        The user ID token user authentication scheme can be used to silently re-authenticate a user in the event the
        entity must re-authenticate. Since user ID tokens are bound to master tokens, entity re-authentication will
        cause any previously issued user ID tokens to become invalid. Submission of a master token and user ID token
        pair previously issued to the same entity, can be accepted if the recipient wishes to do so. Acceptance may be
        limited based on various conditions such as the age of the previous tokens or other external state data.

        This scheme must only be permitted for entities that can provide strong cryptographic authentication of their
        identity. Otherwise theft of tokens would allow theft of user identity.

        > Master Token & User ID Token

        A previously issued master token and user ID token pair. The entity identity in the master token must match
        the sending entity identity. The user ID token must be for the authenticating user.

        :param mastertoken: master token
        :param useridtoken: user ID token
        """
        return cls(
            scheme=UserAuthenticationSchemes.UserIDTokens,
            authdata={
                "mastertoken": mastertoken,
                "useridtoken": useridtoken
            }
        )
