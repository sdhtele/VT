from vinetrimmer.services.all4 import All4
from vinetrimmer.services.amazon import Amazon
from vinetrimmer.services.appletvplus import AppleTVPlus
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.services.bbciplayer import BBCiPlayer
from vinetrimmer.services.braviacore import BraviaCORE
from vinetrimmer.services.crave import Crave
from vinetrimmer.services.ctv import CTV
from vinetrimmer.services.disneynow import DisneyNOW
from vinetrimmer.services.disneyplus import DisneyPlus
from vinetrimmer.services.filmio import Filmio
from vinetrimmer.services.flixole import FlixOle
from vinetrimmer.services.googleplay import GooglePlay
from vinetrimmer.services.hbomax import HBOMax
from vinetrimmer.services.hotstar import Hotstar
from vinetrimmer.services.hulu import Hulu
from vinetrimmer.services.itunes import ITunes
from vinetrimmer.services.netflix import Netflix
from vinetrimmer.services.paramountplus import ParamountPlus
from vinetrimmer.services.peacock import Peacock
from vinetrimmer.services.rakutentv import RakutenTV
from vinetrimmer.services.rtlmost import RTLMost
from vinetrimmer.services.showtime import Showtime
from vinetrimmer.services.spectrum import Spectrum
from vinetrimmer.services.stan import Stan
from vinetrimmer.services.tvnow import TVNOW
from vinetrimmer.services.videoland import VideoLand
from vinetrimmer.services.vudu import Vudu

SERVICE_MAP = {
    "All4": All4.ALIASES,
    "Amazon": Amazon.ALIASES,
    "AppleTVPlus": AppleTVPlus.ALIASES,
    "BBCiPlayer": BBCiPlayer.ALIASES,
    "BraviaCORE": BraviaCORE.ALIASES,
    "Crave": Crave.ALIASES,
    "CTV": CTV.ALIASES,
    "DisneyNOW": DisneyNOW.ALIASES,
    "DisneyPlus": DisneyPlus.ALIASES,
    "Filmio": Filmio.ALIASES,
    "FlixOle": FlixOle.ALIASES,
    "GooglePlay": GooglePlay.ALIASES,
    "HBOMax": HBOMax.ALIASES,
    "Hotstar": Hotstar.ALIASES,
    "Hulu": Hulu.ALIASES,
    "iTunes": ITunes.ALIASES,
    "Netflix": Netflix.ALIASES,
    "ParamountPlus": ParamountPlus.ALIASES,
    "Peacock": Peacock.ALIASES,
    "RakutenTV": RakutenTV.ALIASES,
    "RTLMost": RTLMost.ALIASES,
    "Showtime": Showtime.ALIASES,
    "Spectrum": Spectrum.ALIASES,
    "Stan": Stan.ALIASES,
    "TVNOW": TVNOW.ALIASES,
    "VideoLand": VideoLand.ALIASES,
    "Vudu": Vudu.ALIASES
}


def get_service_key(value: str) -> str:
    """
    Get the Service Key name (e.g. DisneyPlus, not dsnp, disney+, etc.) from the SERVICE_MAP.
    Input value can be of any case-sensitivity and can be either the key itself or an alias.
    """
    value = value.lower()
    for key, aliases in SERVICE_MAP.items():
        if value in map(str.lower, aliases) or value == key.lower():
            return key
    raise ValueError(f"Failed to find a matching Service Key for '{value}'")
