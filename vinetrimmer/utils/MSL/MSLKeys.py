from typing import Optional

from Cryptodome.PublicKey.RSA import RsaKey

from vinetrimmer.utils.MSL.MSLObject import MSLObject


class MSLKeys(MSLObject):

    def __init__(self, encryption: Optional[bytes] = None, sign: Optional[bytes] = None, rsa: Optional[RsaKey] = None,
                 mastertoken: Optional[dict] = None, cdm_session=None):
        self.encryption = encryption
        self.sign = sign
        self.rsa = rsa
        self.mastertoken = mastertoken
        self.cdm_session = cdm_session
