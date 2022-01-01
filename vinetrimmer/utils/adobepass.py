from abc import ABC
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.extractor.adobepass import AdobePassIE

from vinetrimmer.objects import Credential


class AdobePassVT(AdobePassIE, ABC):
    def __init__(self, credential: Credential, get_cache: Callable):
        super().__init__(
            YoutubeDL(
                {
                    "ap_mso": credential.extra,  # See yt_dlp.extractor.adobepass for supported MSO providers
                    "ap_username": credential.username,
                    "ap_password": credential.password,
                    "cachedir": get_cache("adobepass").resolve()
                }
            )
        )
