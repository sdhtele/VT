import re
import unicodedata
from enum import Enum
from typing import Any, Iterator, Optional, Union

from langcodes import Language
from pymediainfo import MediaInfo
from unidecode import unidecode

from vinetrimmer import config
from vinetrimmer.objects.tracks import Tracks
from vinetrimmer.utils import Logger

VIDEO_CODEC_MAP = {
    "AVC": "H.264",
    "HEVC": "H.265"
}
DYNAMIC_RANGE_MAP = {
    "HDR10": "HDR",
    "HDR10+": "HDR",
    "Dolby Vision": "DV"
}
AUDIO_CODEC_MAP = {
    "E-AC-3": "DDP",
    "AC-3": "DD"
}


class Title:
    def __init__(
        self, id_: str, type_: "Title.Types", name: Optional[str] = None, year: Optional[int] = None,
        season: Optional[int] = None, episode: Optional[int] = None, episode_name: Optional[str] = None,
        original_lang: Optional[Union[str, Language]] = None, source: Optional[str] = None,
        service_data: Optional[Any] = None, tracks: Optional[Tracks] = None, filename: Optional[str] = None
    ) -> None:
        self.id = id_
        self.type = type_
        self.name = name
        self.year = int(year or 0)
        self.season = int(season or 0)
        self.episode = int(episode or 0)
        self.episode_name = episode_name
        self.original_lang = Language.get(original_lang) if original_lang else None
        self.source = source
        self.service_data: Any = service_data or {}
        self.tracks = tracks or Tracks()
        self.filename = filename

        if not self.filename:
            # auto generated initial filename
            self.filename = self.parse_filename()

    def parse_filename(self, media_info: Optional[MediaInfo] = None, folder: bool = False) -> str:
        if media_info:
            video_track = next(iter(media_info.video_tracks), None)
            audio_track = next(iter(media_info.audio_tracks), None)
        else:
            video_track = None
            audio_track = None

        # create the initial filename string
        filename = f"{str(self.name).replace('$', 'S')} "  # e.g. `Arli$$` -> `ArliSS`
        if self.type == Title.Types.MOVIE:
            filename += f"{self.year or ''} "
        else:
            if self.season is not None:
                filename += f"S{str(self.season).zfill(2)}"
            if self.episode is None or folder:
                filename += " "  # space after S00
            else:
                filename += f"E{str(self.episode).zfill(2)} "
            if self.episode_name and not folder:
                filename += f"{self.episode_name or ''} "
        if video_track:
            res = video_track.height
            aspect = [int(float(x)) for x in video_track.other_display_aspect_ratio[0].split(":")]
            if len(aspect) == 1:
                aspect.append(1)
            aspect_w, aspect_h = aspect
            if aspect_w / aspect_h not in (16 / 9, 4 / 3):
                # We want the resolution represented in a 4:3 or 16:9 canvas
                # if it's not 4:3 or 16:9, calculate as if it's inside a 16:9 canvas
                # otherwise the track's height value is fine
                # We are assuming this title is some weird aspect ratio so most
                # likely a movie or HD source, so it's most likely widescreen so
                # 16:9 canvas makes the most sense
                res = int(video_track.width * (9 / 16))
            filename += f"{res}p "
        filename += f"{self.source} WEB-DL "
        if audio_track:
            filename += f"{AUDIO_CODEC_MAP.get(audio_track.format) or audio_track.format}"
            filename += f"{float(sum({'LFE': 0.1}.get(x, 1) for x in audio_track.channel_layout.split(' '))):.1f} "
            if audio_track.format_additionalfeatures and "JOC" in audio_track.format_additionalfeatures:
                filename += "Atmos "
        if video_track:
            if video_track.hdr_format_commercial:
                filename += f"{DYNAMIC_RANGE_MAP.get(video_track.hdr_format_commercial)} "
            elif ("HLG" in (video_track.transfer_characteristics or "")
                  or "HLG" in (video_track.transfer_characteristics_original or "")):
                filename += "HLG "
            if float(video_track.frame_rate) > 30:
                filename += "HFR "
            filename += f"{VIDEO_CODEC_MAP.get(video_track.format) or video_track.format}"
        filename = filename.rstrip().rstrip(".")  # remove whitespace and last right-sided . if needed
        if config.config.tag:
            filename += f"-{config.config.tag}"  # group tag

        return self.normalize_filename(filename)

    @staticmethod
    def normalize_filename(filename: str) -> str:
        # replace all non-ASCII characters with ASCII equivalents
        filename = unidecode(filename)
        filename = "".join(c for c in filename if unicodedata.category(c) != "Mn")

        # remove or replace further characters as needed
        filename = filename.replace("/", " & ")  # e.g. amazon multi-episode titles
        filename = re.sub(r"[:; ]", ".", filename)  # structural chars to .
        filename = re.sub(r"[\\*!?¿,'\"()<>|$#]", "", filename)  # unwanted chars
        filename = re.sub(r"[. ]{2,}", ".", filename)  # replace 2+ neighbour dots and spaces with .
        return filename

    def is_wanted(self, wanted: list) -> bool:
        if self.type != Title.Types.TV or not wanted:
            return True
        return f"{self.season}x{self.episode}" in wanted

    class Types(Enum):
        MOVIE = 1
        TV = 2


class Titles(list):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.title_name = None

        if self:
            self.title_name = self[0].name

    def print(self) -> None:
        log = Logger.getLogger("Titles")
        log.info(f"Title: {self.title_name}")
        if any(x.type == Title.Types.TV for x in self):
            log.info(f"Total Episodes: {len(self)}")
            log.info(
                "By Season: {}".format(
                    ", ".join(list(dict.fromkeys(
                        f"{x.season} ({len([y for y in self if y.season == x.season])})"
                        for x in self if x.type == Title.Types.TV
                    )))
                )
            )

    def order(self) -> None:
        """This will order the Titles to be oldest first."""
        self.sort(key=lambda t: int(t.year or 0))
        self.sort(key=lambda t: int(t.episode or 0))
        self.sort(key=lambda t: int(t.season or 0))

    def with_wanted(self, wanted: list) -> Iterator[Title]:
        """Yield only wanted tracks."""
        for title in self:
            if title.is_wanted(wanted):
                yield title
