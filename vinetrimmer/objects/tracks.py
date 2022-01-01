from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import math
import re
import shutil
import subprocess
import urllib.parse
import uuid
from collections import defaultdict
from copy import copy
from enum import Enum
from hashlib import md5
from io import BytesIO
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, TypeVar, Union

import m3u8
import pycaption
import requests
import validators
from construct import Container
from langcodes import Language
from langcodes.tag_parser import LanguageTagError
from m3u8 import M3U8
from pymp4.parser import Box, MP4
from requests import Session
from requests.structures import CaseInsensitiveDict

from vinetrimmer import config
from vinetrimmer.constants import LANGUAGE_MUX_MAP, TERRITORY_MAP
from vinetrimmer.utils import Cdm, FPS, Logger, get_boxes, get_closest_match, is_close_match
from vinetrimmer.utils.collections import as_list
from vinetrimmer.utils.io import aria2c, download_range, saldl
from vinetrimmer.utils.subprocess import ffprobe
from vinetrimmer.utils.Widevine.protos.widevine_pb2 import WidevineCencHeader
from vinetrimmer.utils.xml import load_xml

# For use in signatures of functions which take one specific type of track at a time
# (it can't be a list that contains e.g. both VideoTrack and AudioTrack)
TrackT = TypeVar("TrackT", bound="Track")

# For general use in lists that can contain mixed types of tracks.
# list[Track] won't work because list is invariant.
AnyTrack = Union["VideoTrack", "AudioTrack", "TextTrack"]


class Track:
    class Descriptor(Enum):
        URL = 1  # Direct URL, nothing fancy
        M3U = 2  # https://en.wikipedia.org/wiki/M3U (and M3U8)
        MPD = 3  # https://en.wikipedia.org/wiki/Dynamic_Adaptive_Streaming_over_HTTP

    def __init__(
        self, id_: str, source: str, url: Union[str, list[str]], codec: Optional[str], language: Union[Language, str],
        is_original_lang: bool = False, descriptor: Descriptor = Descriptor.URL, needs_proxy: bool = False,
        needs_repack: bool = False, encrypted: bool = False, pssh: Optional[Container] = None,
        kid: Optional[str] = None, key: Optional[str] = None, extra: Optional[Any] = None
    ) -> None:
        self.id = id_
        self.source = source
        self.url = url
        # required basic metadata
        self.codec = codec
        self.language = Language.get(language)
        self.is_original_lang = bool(is_original_lang)
        # optional io metadata
        self.descriptor = descriptor
        self.needs_proxy = bool(needs_proxy)
        self.needs_repack = bool(needs_repack)
        # decryption
        self.encrypted = bool(encrypted)
        self.pssh: Container = pssh
        self.kid = kid
        self.key = key
        # extra data
        self.extra: Any = extra or {}  # allow anything for extra, but default to a dict

        # should only be set internally
        self._location: Optional[Path] = None

    def __repr__(self) -> str:
        return "{name}({items})".format(
            name=self.__class__.__name__,
            items=", ".join([f"{k}={repr(v)}" for k, v in self.__dict__.items()])
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Track) and self.id == other.id

    def get_track_name(self) -> Optional[str]:
        """Return the base Track Name. This may be enhanced in sub-classes."""
        if (self.language.language or "").lower() == (self.language.territory or "").lower():
            self.language.territory = None  # e.g. en-en, de-DE
        if self.language.territory == "US":
            self.language.territory = None
        reduced = self.language.simplify_script()
        extra_parts = []
        if reduced.script is not None:
            extra_parts.append(reduced.script_name(max_distance=25))
        if reduced.territory is not None:
            territory = reduced.territory_name(max_distance=25)
            extra_parts.append(TERRITORY_MAP.get(territory, territory))
        return ", ".join(extra_parts) or None

    def get_data_chunk(self, session: Optional[Session] = None) -> Optional[bytes]:
        """Get the data chunk from the track's stream."""
        if not session:
            session = Session()

        url = None

        if self.descriptor == self.Descriptor.M3U:
            master = m3u8.loads(session.get(as_list(self.url)[0]).text, uri=self.url)
            for segment in master.segments:
                if not segment.init_section:
                    continue
                if self.source == "DSNP" and re.match(r"^[a-zA-Z0-9]{4}-(BUMPER|DUB_CARD)/", segment.init_section.uri):
                    continue
                url = ("" if re.match("^https?://", segment.init_section.uri) else segment.init_section.base_uri)
                url += segment.init_section.uri
                break

        if not url:
            url = as_list(self.url)[0]

        if self.needs_proxy:
            proxy = next(iter(session.proxies.values()), None)
        else:
            proxy = None

        # assuming 20000 bytes is enough to contain the pssh/kid box
        return download_range(url, 20000, proxy=proxy)

    def get_pssh(self, session: Optional[Session] = None) -> bool:
        """
        Get the PSSH of the track.

        Parameters:
            session: Requests Session, best to provide one if cookies/headers/proxies are needed.

        Returns:
            True if PSSH is now available, False otherwise. PSSH will be stored in Track.pssh
            automatically.
        """
        if self.pssh or not self.encrypted:
            return True

        if not session:
            session = Session()

        boxes = []

        if self.descriptor == self.Descriptor.M3U:
            # if an m3u, try get from playlist
            master = m3u8.loads(session.get(as_list(self.url)[0]).text, uri=self.url)
            boxes.extend([
                Box.parse(base64.b64decode(x.uri.split(",")[-1]))
                for x in (master.session_keys or master.keys)
                if x and x.keyformat.lower() == Cdm.urn
            ])

        data = self.get_data_chunk(session)
        if data:
            boxes.extend(list(get_boxes(data, b"pssh")))

        for box in boxes:
            if box.system_ID == Cdm.uuid:
                self.pssh = box
                return True

        for box in boxes:
            if box.system_ID == uuid.UUID("{9a04f079-9840-4286-ab92-e65be0885f95}"):
                xml_str = Box.build(box)[42:].decode("utf-16-le")
                xml_str = xml_str[xml_str.index("<"):]

                xml = load_xml(xml_str).find("DATA")  # root: WRMHEADER

                kid = xml.findtext("KID")  # v4.0.0.0
                if not kid:  # v4.1.0.0
                    kid = next(iter(xml.xpath("PROTECTINFO/KID/@VALUE")), None)
                if not kid:  # v4.3.0.0
                    kid = next(iter(xml.xpath("PROTECTINFO/KIDS/KID/@VALUE")), None)  # can be multiple?

                self.pssh = Box.parse(Box.build(dict(
                    type=b"pssh",
                    version=0,
                    flags=0,
                    system_ID=Cdm.uuid,
                    init_data=b"\x12\x10" + base64.b64decode(kid)
                )))
                return True

        return False

    def get_kid(self, session: Optional[Session] = None) -> bool:
        """
        Get the KID (encryption key id) of the Track.
        The KID corresponds to the Encrypted segments of an encrypted Track.

        Parameters:
            session: Requests Session, best to provide one if cookies/headers/proxies are needed.

        Returns:
            True if KID is now available, False otherwise. KID will be stored in Track.kid
            automatically.
        """
        if self.kid or not self.encrypted:
            return True

        boxes = []

        data = self.get_data_chunk(session)

        if data:
            # try get via ffprobe, needed for non mp4 data e.g. WEBM from Google Play
            probe = ffprobe(data)
            if probe:
                kid = (probe.get("streams") or [{}])[0].get("tags", {}).get("enc_key_id")
                if kid:
                    kid = binascii.hexlify(base64.b64decode(kid)).decode()
                    if kid != "00" * 16:
                        self.kid = kid
                        return True
            # get tenc and pssh boxes if available
            boxes.extend(list(get_boxes(data, b"tenc")))
            boxes.extend(list(get_boxes(data, b"pssh")))

        # get the track's pssh box if available
        if self.get_pssh():
            boxes.append(self.pssh)

        # loop all found boxes and try find a KID
        for box in sorted(boxes, key=lambda b: b.type == b"tenc", reverse=True):
            if box.type == b"tenc":
                kid = box.key_ID.hex
                if kid != "00" * 16:
                    self.kid = kid
                    return True
            if box.type == b"pssh":
                if box.system_ID == Cdm.uuid:
                    # Note: assumes only the first KID of a list is wanted
                    if getattr(box, "key_IDs", None):
                        kid = box.key_IDs[0].hex
                        if kid != "00" * 16:
                            self.kid = kid
                            return True
                    cenc_header = WidevineCencHeader()
                    cenc_header.ParseFromString(box.init_data)
                    if getattr(cenc_header, "key_id", None):
                        kid = binascii.hexlify(cenc_header.key_id[0]).decode()
                        if kid != "00" * 16:
                            self.kid = kid
                            return True

        return False

    def download(
        self, out: Path, name: Optional[str] = None, headers: Optional[CaseInsensitiveDict] = None,
        proxy: Optional[str] = None
    ) -> Path:
        """
        Download the Track and apply any necessary post-edits like Subtitle conversion.

        Parameters:
            out: Output Directory Path for the downloaded track.
            name: Override the default filename format.
                Expects to contain `{type}`, `{id}`, and `{enc}`. All of them must be used.
            headers: Headers to use when downloading.
            proxy: Proxy to use when downloading.

        Returns:
            Where the file was saved, as a Path object.
        """
        if out.is_file():
            raise ValueError("Path must be to a directory and not a file")

        out.mkdir(parents=True, exist_ok=True)

        name = (name or "{type}_{id}_{enc}").format(
            type=self.__class__.__name__,
            id=self.id,
            enc="enc" if self.encrypted else "dec"
        ) + ".mp4"
        save_path = out / name

        if self.descriptor == self.Descriptor.M3U:
            master = m3u8.loads(
                requests.get(
                    as_list(self.url)[0],
                    headers=headers,
                    proxies={"all": proxy} if self.needs_proxy and proxy else None
                ).text,
                uri=as_list(self.url)[0]
            )

            durations = []
            duration = 0
            for segment in master.segments:
                if segment.discontinuity:
                    durations.append(duration)
                    duration = 0
                duration += segment.duration
            durations.append(duration)
            largest_continuity = durations.index(max(durations))

            discontinuity = 0
            has_init = False
            segments = []
            for segment in master.segments:
                if segment.discontinuity:
                    discontinuity += 1
                    has_init = False
                if self.source == "DSNP" and re.search(
                    r"[a-zA-Z0-9]{4}-(BUMPER|DUB_CARD)/",
                    segment.uri + (segment.init_section.uri if segment.init_section else '')
                ):
                    continue
                if self.source == "ATVP" and discontinuity != largest_continuity:
                    # the amount of pre and post-roll sections change all the time
                    # only way to know which section to get is by getting the largest
                    continue
                if segment.init_section and not has_init:
                    segments.append(
                        ("" if re.match("^https?://", segment.init_section.uri) else segment.init_section.base_uri) +
                        segment.init_section.uri
                    )
                    has_init = True
                segments.append(
                    ("" if re.match("^https?://", segment.uri) else segment.base_uri) +
                    segment.uri
                )
            self.url = segments

        if self.source == "CORE":
            asyncio.run(saldl(
                self.url,
                save_path,
                headers,
                proxy if self.needs_proxy else None
            ))
        else:
            asyncio.run(aria2c(
                self.url,
                save_path,
                headers,
                proxy if self.needs_proxy else None
            ))

        if save_path.stat().st_size <= 3:  # Empty UTF-8 BOM == 3 bytes
            raise IOError(
                "Download failed, the downloaded file is empty. "
                f"This {'was' if self.needs_proxy else 'was not'} downloaded with a proxy." +
                (
                    " Perhaps you need to set `needs_proxy` as True to use the proxy for this track."
                    if not self.needs_proxy else ""
                )
            )

        self._location = save_path
        return save_path

    def delete(self) -> None:
        if self._location:
            self._location.unlink()
            self._location = None

    def repackage(self) -> None:
        if not self._location:
            raise ValueError("Cannot repackage a Track that has not been downloaded.")
        fixed_file = f"{self._location}_fixed.mkv"
        subprocess.run([
            "ffmpeg", "-hide_banner",
            "-loglevel", "panic",
            "-i", self._location,
            # Following are very important!
            "-map_metadata", "-1",  # don't transfer metadata to output file
            "-fflags", "bitexact",  # only have minimal tag data, reproducible mux
            "-codec", "copy",
            fixed_file
        ], check=True)
        self.swap(fixed_file)

    def locate(self) -> Optional[Path]:
        return self._location

    def move(self, target: Union[str, Path]) -> bool:
        if not self._location:
            return False
        target = Path(target)
        ok = Path(shutil.move(self._location, target)).resolve() == target.resolve()
        if ok:
            self._location = target
        return ok

    def swap(self, target: Union[str, Path]) -> bool:
        target = Path(target)
        if not target.exists() or not self._location:
            return False
        self._location.unlink()
        target.rename(self._location)
        return True


class VideoTrack(Track):
    def __init__(self, *args: Any, bitrate: Union[str, int, float], width: int, height: int,
                 fps: Optional[Union[str, int, float]] = None, hdr10: bool = False, hlg: bool = False, dv: bool = False,
                 **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        # required
        self.bitrate = int(math.ceil(float(bitrate))) if bitrate else None
        self.width = int(width)
        self.height = int(height)
        # optional
        self.fps = FPS.parse(str(fps)) if fps else None
        self.hdr10 = bool(hdr10)
        self.hlg = bool(hlg)
        self.dv = bool(dv)

    def __str__(self) -> str:
        fps = f"{self.fps:.3f}" if self.fps else "Unknown"
        return " | ".join([
            "VID",
            f"[{self.codec}, {'HDR10' if self.hdr10 else 'HLG' if self.hlg else 'DV' if self.dv else 'SDR'}]",
            f"{self.language}",
            f"{self.width}x{self.height} @ {self.bitrate // 1000 if self.bitrate else '?'} kb/s, {fps} FPS"
        ])

    def ccextractor(
        self, track_id: Any, out_path: Union[Path, str], language: Language, original: bool = False
    ) -> Optional[TextTrack]:
        """Return a TextTrack object representing CC track extracted by CCExtractor."""
        if not self._location:
            raise ValueError("You must download the track first.")

        executable = shutil.which("ccextractor") or shutil.which("ccextractorwin")
        if not executable:
            raise EnvironmentError("ccextractor executable was not found.")

        out_path = Path(out_path)

        try:
            subprocess.run([
                executable,
                "-trim", "-noru", "-ru1",
                self._location, "-o", out_path
            ], check=True)
        except subprocess.CalledProcessError as e:
            if e.returncode == 10:  # No captions found
                return None
            raise

        if out_path.exists():
            if out_path.stat().st_size <= 3:
                # An empty UTF-8 file with BOM is 3 bytes.
                # If the subtitle file is empty, mkvmerge will fail to mux.
                out_path.unlink(missing_ok=True)
                return None
            cc_track = TextTrack(
                id_=track_id,
                source=self.source,
                url="",  # doesn't need to be downloaded
                codec="srt",
                language=language,
                is_original_lang=original,  # TODO: Figure out if this is the original title language
                cc=True
            )
            cc_track._location = out_path
            return cc_track

        return None

    def extract_c608(self) -> list[TextTrack]:
        """
        Extract Apple-Style c608 box (CEA-608) subtitle using ccextractor.

        This isn't much more than a wrapper to the track.ccextractor function.
        All this does, is actually check if a c608 box exists and only if so
        does it actually call ccextractor.

        Even though there is a possibility of more than one c608 box, only one
        can actually be extracted. Not only that but it's very possible this
        needs to be done before any decryption as the decryption may destroy
        some of the metadata.

        TODO: Need a test file with more than one c608 box to add support for
              more than one CEA-608 extraction.
        """
        if not self._location:
            raise ValueError("You must download the track first.")
        with self._location.open("rb") as f:
            # assuming 20KB is enough to contain the c608 box.
            # ffprobe will fail, so a c608 box check must be done.
            c608_count = len(list(get_boxes(f.read(20000), b"c608")))
        if c608_count > 0:
            # TODO: Figure out the real language, it might be different
            #       CEA-608 boxes doesnt seem to carry language information :(
            # TODO: Figure out if the CC language is original lang or not.
            #       Will need to figure out above first to do so.
            track_id = f"ccextractor-{self.id}"
            cc_lang = self.language
            cc_track = self.ccextractor(
                track_id=track_id,
                out_path=str(config.filenames.subtitles).format(
                    id=track_id,
                    language_code=cc_lang
                ),
                language=cc_lang,
                original=False
            )
            if not cc_track:
                return []
            return [cc_track]
        return []


class AudioTrack(Track):
    def __init__(self, *args: Any, bitrate: Union[str, int, float], channels: Optional[Union[str, int, float]] = None,
                 descriptive: bool = False, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # required
        self.bitrate = int(math.ceil(float(bitrate))) if bitrate else None
        self.channels = self.parse_channels(channels) if channels else None
        # optional
        self.descriptive = bool(descriptive)

    @staticmethod
    def parse_channels(channels: Union[str, float]) -> str:
        """
        Converts a string to a float-like string which represents audio channels.
        It does not handle values that are incorrect/out of bounds or e.g. 6.0->5.1, as that
        isn't what this is intended for.
        E.g. "3" -> "3.0", "2.1" -> "2.1", ".1" -> "0.1".
        """
        # TODO: Support all possible DASH channel configurations (https://datatracker.ietf.org/doc/html/rfc8216)
        if channels == "A000":
            return "2.0"
        if channels == "F801":
            return "5.1"

        if str(channels).isdigit():
            # This is to avoid incorrectly transforming channels=6 to 6.0, for example
            return f"{channels}ch"

        try:
            return str(float(channels))
        except ValueError:
            return str(channels)

    def get_track_name(self) -> Optional[str]:
        """Return the base Track Name."""
        track_name = super().get_track_name() or ""
        flag = self.descriptive and "Descriptive"
        if flag:
            if track_name:
                flag = f" ({flag})"
            track_name += flag
        return track_name or None

    def __str__(self) -> str:
        return " | ".join(filter(
            lambda x: bool(x),
            [
                "AUD",
                f"[{self.codec}]",
                f"{self.channels}" if self.channels else None,
                f"{self.bitrate // 1000 if self.bitrate else '?'} kb/s",
                f"{self.language}",
                self.get_track_name()
            ]
        ))


class TextTrack(Track):
    def __init__(self, *args: Any, cc: bool = False, sdh: bool = False, forced: bool = False, **kwargs: Any):
        """
        Information on Subtitle Types:
            https://bit.ly/2Oe4fLC (3PlayMedia Blog on SUB vs CC vs SDH).
            However, I wouldn't pay much attention to the claims about SDH needing to
            be in the original source language. It's logically not true.

            CC == Closed Captions. Source: Basically every site.
            SDH = Subtitles for the Deaf or Hard-of-Hearing. Source: Basically every site.
            HOH = Exact same as SDH. Is a term used in the UK. Source: https://bit.ly/2PGJatz (ICO UK)

            More in-depth information, examples, and stuff to look for can be found in the Parameter
            explanation list below.

        Parameters:
            cc: Closed Caption.
                - Intended as if you couldn't hear the audio at all.
                - Can have Sound as well as Dialogue, but doesn't have to.
                - Original source would be from an EIA-CC encoded stream. Typically all
                  upper-case characters.
                Indicators of it being CC without knowing original source:
                  - Extracted with CCExtractor, or
                  - >>> (or similar) being used at the start of some or all lines, or
                  - All text is uppercase or at least the majority, or
                  - Subtitles are Scrolling-text style (one line appears, oldest line
                    then disappears).
                Just because you downloaded it as a SRT or VTT or such, doesn't mean it
                 isn't from an EIA-CC stream. And I wouldn't take the streaming services
                 (CC) as gospel either as they tend to get it wrong too.
            sdh: Deaf or Hard-of-Hearing. Also known as HOH in the UK (EU?).
                 - Intended as if you couldn't hear the audio at all.
                 - MUST have Sound as well as Dialogue to be considered SDH.
                 - It has no "syntax" or "format" but is not transmitted using archaic
                   forms like EIA-CC streams, would be intended for transmission via
                   SubRip (SRT), WebVTT (VTT), TTML, etc.
                 If you can see important audio/sound transcriptions and not just dialogue
                  and it doesn't have the indicators of CC, then it's most likely SDH.
                 If it doesn't have important audio/sounds transcriptions it might just be
                  regular subtitling (you wouldn't mark as CC or SDH). This would be the
                  case for most translation subtitles. Like Anime for example.
            forced: Typically used if there's important information at some point in time
                     like watching Dubbed content and an important Sign or Letter is shown
                     or someone talking in a different language.
                    Forced tracks are recommended by the Matroska Spec to be played if
                     the player's current playback audio language matches a subtitle
                     marked as "forced".
                    However, that doesn't mean every player works like this but there is
                     no other way to reliably work with Forced subtitles where multiple
                     forced subtitles may be in the output file. Just know what to expect
                     with "forced" subtitles.
        """
        super().__init__(*args, **kwargs)
        self.cc = bool(cc)
        self.sdh = bool(sdh)
        if self.cc and self.sdh:
            raise ValueError("A text track cannot be both CC and SDH.")
        self.forced = bool(forced)
        if (self.cc or self.sdh) and self.forced:
            raise ValueError("A text track cannot be CC/SDH as well as Forced.")

    def get_track_name(self) -> Optional[str]:
        """Return the base Track Name."""
        track_name = super().get_track_name() or ""
        flag = self.cc and "CC" or self.sdh and "SDH" or self.forced and "Forced"
        if flag:
            if track_name:
                flag = f" ({flag})"
            track_name += flag
        return track_name or None

    @staticmethod
    def parse(data: bytes, codec: str) -> pycaption.CaptionSet:
        # TODO: Use an "enum" for subtitle codecs
        if not isinstance(data, bytes):
            raise ValueError(f"Subtitle data must be parsed as bytes data, not {type(data).__name__}")
        try:
            if codec.startswith("stpp"):
                captions: dict[str, list[str]] = defaultdict(list)
                for segment in (
                    TextTrack.parse(box.data, "ttml")
                    for box in MP4.parse_stream(BytesIO(data)) if box.type == b"mdat"
                ):
                    lang = segment.get_languages()[0]
                    for caption in segment.get_captions(lang):
                        prev_caption = captions and captions[lang][-1]

                        if prev_caption and (prev_caption.start, prev_caption.end) == (caption.start, caption.end):
                            # Merge cues with equal start and end timestamps.
                            #
                            # pycaption normally does this itself, but we need to do it manually here
                            # for the next merge to work properly.
                            prev_caption.nodes += [pycaption.CaptionNode.create_break(), *caption.nodes]
                        elif prev_caption and caption.start <= prev_caption.end:
                            # If the previous cue's end timestamp is less or equal to the current cue's start timestamp,
                            # just extend the previous one's end timestamp to the current one's end timestamp.
                            # This is to get rid of duplicates, as STPP may duplicate cues at segment boundaries.
                            prev_caption.end = caption.end
                        else:
                            captions[lang].append(caption)

                return pycaption.CaptionSet(captions)
            if codec in ["dfxp", "ttml", "tt"]:
                text = data.decode("utf8").replace("tt:", "")
                return pycaption.DFXPReader().read(text)
            if codec in ["vtt", "webvtt", "wvtt"] or codec.startswith("webvtt"):
                text = data.decode("utf8").replace("\r", "").replace("\n\n\n", "\n \n\n").replace("\n\n<", "\n<")
                return pycaption.WebVTTReader().read(text)
        except pycaption.exceptions.CaptionReadSyntaxError:
            raise SyntaxError(f"A syntax error has occurred when reading the \"{codec}\" subtitle")
        except pycaption.exceptions.CaptionReadNoCaptions:
            return pycaption.CaptionSet({"en": []})

        raise ValueError(f"Unknown Subtitle Format \"{codec}\"...")

    @staticmethod
    def convert_to_srt(data: bytes, codec: str) -> str:
        return pycaption.SRTWriter().write(TextTrack.parse(data, codec))

    def download(
        self, out: Path, name: Optional[str] = None, headers: Optional[CaseInsensitiveDict] = None,
        proxy: Optional[str] = None
    ) -> Path:
        save_path = super().download(out, name, headers, proxy)
        if self.codec.lower() != "srt":
            data = save_path.read_bytes()
            save_path.write_text(self.convert_to_srt(data, self.codec), encoding="utf8")
            self.codec = "srt"
        return save_path

    def __str__(self) -> str:
        return " | ".join(filter(
            lambda x: bool(x),
            [
                "SUB",
                f"[{self.codec}]",
                f"{self.language}",
                self.get_track_name()
            ]
        ))


class MenuTrack:
    line_1 = re.compile(r"^CHAPTER(?P<number>\d+)=(?P<timecode>[\d\\.]+)$")
    line_2 = re.compile(r"^CHAPTER(?P<number>\d+)NAME=(?P<title>[\d\\.]+)$")

    def __init__(self, number: int, title: str, timecode: str):
        self.id = f"chapter-{number}"
        self.number = number
        self.title = title
        if "." not in timecode:
            timecode += ".000"
        self.timecode = timecode

    def __bool__(self) -> bool:
        return bool(
            self.number and self.number >= 0 and
            self.title and
            self.timecode
        )

    def __repr__(self) -> str:
        """
        OGM-based Simple Chapter Format intended for use with MKVToolNix.

        This format is not officially part of the Matroska spec. This was a format
        designed for OGM tools that MKVToolNix has since re-used. More Information:
        https://mkvtoolnix.download/doc/mkvmerge.html#mkvmerge.chapters.simple
        """
        return "CHAPTER{num}={time}\nCHAPTER{num}NAME={name}".format(
            num=f"{self.number:02}",
            time=self.timecode,
            name=self.title
        )

    def __str__(self) -> str:
        return " | ".join([
            "CHP",
            f"[{self.number:02}]",
            self.timecode,
            self.title
        ])

    @classmethod
    def loads(cls, data: str) -> MenuTrack:
        """Load chapter data from a string."""
        lines = [x.strip() for x in data.strip().splitlines(keepends=False)]
        if len(lines) > 2:
            return MenuTrack.loads("\n".join(lines))
        one, two = lines

        one_m = cls.line_1.match(one)
        two_m = cls.line_2.match(two)
        if not one_m or not two_m:
            raise SyntaxError(f"An unexpected syntax error near:\n{one}\n{two}")

        one_str, timecode = one_m.groups()
        two_str, title = two_m.groups()
        one_num, two_num = int(one_str.lstrip("0")), int(two_str.lstrip("0"))

        if one_num != two_num:
            raise SyntaxError(f"The chapter numbers ({one_num},{two_num}) does not match.")
        if not timecode:
            raise SyntaxError("The timecode is missing.")
        if not title:
            raise SyntaxError("The title is missing.")

        return cls(number=one_num, title=title, timecode=timecode)

    @classmethod
    def load(cls, path: Union[Path, str]) -> MenuTrack:
        """Load chapter data from a file."""
        if isinstance(path, str):
            path = Path(path)
        return cls.loads(path.read_text(encoding="utf8"))

    def dumps(self) -> str:
        """Return chapter data as a string."""
        return repr(self)

    def dump(self, path: Union[Path, str]) -> int:
        """Write chapter data to a file."""
        if isinstance(path, str):
            path = Path(path)
        return path.write_text(self.dumps(), encoding="utf8")


class Tracks:
    """
    Tracks.
    Stores video, audio, and subtitle tracks. It also stores chapter/menu entries.
    It provides convenience functions for listing, sorting, and selecting tracks.
    """

    TRACK_ORDER_MAP = {
        VideoTrack: 0,
        AudioTrack: 1,
        TextTrack: 2,
        MenuTrack: 3
    }

    def __init__(self, *args: Union[Tracks, list[Track], Track]):
        self.videos: list[VideoTrack] = []
        self.audio: list[AudioTrack] = []
        self.subtitles: list[TextTrack] = []
        self.chapters: list[MenuTrack] = []

        if args:
            self.add(as_list(*args))

    def __iter__(self) -> Iterator[AnyTrack]:
        return iter(as_list(self.videos, self.audio, self.subtitles))

    def __repr__(self) -> str:
        return "{name}({items})".format(
            name=self.__class__.__name__,
            items=", ".join([f"{k}={repr(v)}" for k, v in self.__dict__.items()])
        )

    def __str__(self) -> str:
        rep = ""
        last_track_type = None
        tracks = [*list(self), *self.chapters]
        for track in sorted(tracks, key=lambda t: self.TRACK_ORDER_MAP[type(t)]):
            if type(track) != last_track_type:
                last_track_type = type(track)
                count = sum(type(x) is type(track) for x in tracks)
                rep += "{count} {type} Track{plural}{colon}\n".format(
                    count=count,
                    type=track.__class__.__name__.replace("Track", ""),
                    plural="s" if count != 1 else "",
                    colon=":" if count > 0 else ""
                )
            rep += f"{track}\n"

        return rep.rstrip()

    def exists(self, by_id: Optional[str] = None, by_url: Optional[Union[str, list[str]]] = None) -> bool:
        """Check if a track already exists by various methods."""
        if by_id:  # recommended
            return any(x.id == by_id for x in self)
        if by_url:
            return any(x.url == by_url for x in self)
        return False

    def add(
        self, tracks: Union[Tracks, Sequence[Union[AnyTrack, MenuTrack]], Track, MenuTrack],
        warn_only: bool = False
    ) -> None:
        """Add a provided track to its appropriate array and ensuring it's not a duplicate."""
        if isinstance(tracks, Tracks):
            tracks = [*list(tracks), *tracks.chapters]

        duplicates = 0
        for track in as_list(tracks):
            if self.exists(by_id=track.id):
                if not warn_only:
                    raise ValueError(
                        "One or more of the provided Tracks is a duplicate. "
                        "Track IDs must be unique but accurate using static values. The "
                        "value should stay the same no matter when you request the same "
                        "content. Use a value that has relation to the track content "
                        "itself and is static or permanent and not random/RNG data that "
                        "wont change each refresh or conflict in edge cases."
                    )
                duplicates += 1
                continue

            if isinstance(track, VideoTrack):
                self.videos.append(track)
            elif isinstance(track, AudioTrack):
                self.audio.append(track)
            elif isinstance(track, TextTrack):
                self.subtitles.append(track)
            elif isinstance(track, MenuTrack):
                self.chapters.append(track)
            else:
                raise ValueError("Track type was not set or is invalid.")

        log = Logger.getLogger("Tracks")

        if duplicates:
            log.warning(f" - Found and skipped {duplicates} duplicate tracks...")

    def print(self, level: int = logging.INFO) -> None:
        """Print the __str__ to log at a specified level."""
        log = Logger.getLogger("Tracks")
        for line in str(self).splitlines(keepends=False):
            log.log(level, line)

    def sort_videos(self, by_language: Optional[Sequence[Union[str, Language]]] = None) -> None:
        """Sort video tracks by bitrate, and optionally language."""
        if not self.videos:
            return
        # bitrate
        self.videos = sorted(self.videos, key=lambda x: float(x.bitrate or 0.0), reverse=True)
        # language
        for language in reversed(by_language or []):
            if str(language) == "all":
                language = next((x.language for x in self.videos if x.is_original_lang), "")
            if not language:
                continue
            self.videos = sorted(
                self.videos,
                key=lambda x: "" if is_close_match(language, [x.language]) else str(x.language)
            )

    def sort_audio(self, by_language: Optional[Sequence[Union[str, Language]]] = None) -> None:
        """Sort audio tracks by bitrate, descriptive, and optionally language."""
        if not self.audio:
            return
        # bitrate
        self.audio = sorted(self.audio, key=lambda x: float(x.bitrate or 0.0), reverse=True)
        # descriptive
        self.audio = sorted(self.audio, key=lambda x: str(x.language) if x.descriptive else "")
        # language
        for language in reversed(by_language or []):
            if str(language) == "all":
                language = next((x.language for x in self.audio if x.is_original_lang), "")
            if not language:
                continue
            self.audio = sorted(
                self.audio,
                key=lambda x: "" if is_close_match(language, [x.language]) else str(x.language)
            )

    def sort_subtitles(self, by_language: Optional[Sequence[Union[str, Language]]] = None) -> None:
        """Sort subtitle tracks by sdh, cc, forced, and optionally language."""
        if not self.subtitles:
            return
        # sdh/cc
        self.subtitles = sorted(self.subtitles, key=lambda x: "" if x.sdh or x.cc else str(x.language))
        # forced
        self.subtitles = sorted(self.subtitles, key=lambda x: not x.forced)
        # language
        for language in reversed(by_language or []):
            if str(language) == "all":
                language = next((x.language for x in self.subtitles if x.is_original_lang), "")
            if not language:
                continue
            self.subtitles = sorted(
                self.subtitles,
                key=lambda x: "" if is_close_match(language, [x.language]) else str(x.language)
            )

    def sort_chapters(self) -> None:
        """Sort chapter tracks by chapter number."""
        if not self.chapters:
            return
        # number
        self.chapters = sorted(self.chapters, key=lambda x: x.number)

    @staticmethod
    def select_by_language(languages: list[str], iterable: list[TrackT], one_per_lang: bool = True) -> Iterator[TrackT]:
        """
        Filter a track list by language.

        If one_per_lang is True, only the first matched track will be returned for
        each language. It presumes the first match is what is wanted.

        This means if you intend for it to return the best track per language,
        then ensure the iterable is sorted in ascending order (first = best, last = worst).
        """
        if not iterable:
            return
        if "all" in languages:
            tracks = iterable
        else:
            tracks = list(filter(lambda x: is_close_match(x.language, languages), iterable))
            if not tracks:
                raise ValueError(
                    f"There's no {iterable[0].__class__.__name__}s that match the language(s):\n"
                    f"{', '.join(languages)}"
                )
        if one_per_lang:
            for language in languages:
                match = get_closest_match(language, [x.language for x in tracks])
                if match:
                    yield next(x for x in tracks if x.language == match)
        else:
            for track in tracks:
                yield track

    def select_videos(
        self, by_language: Optional[list[str]] = None, by_quality: Optional[int] = None, by_range: Optional[str] = None,
        one_only: bool = True
    ) -> None:
        """Filter video tracks by language and other criteria."""
        if by_quality:
            # Note: Do not merge these list comprehensions. They must be done separately so the results
            # from the 16:9 canvas check is only used if there's no exact height resolution match.
            videos_quality = [x for x in self.videos if x.height == by_quality]
            if not videos_quality:
                videos_quality = [x for x in self.videos if int(x.width * (9 / 16)) == by_quality]
            if not videos_quality:
                raise ValueError(f"There's no {by_quality}p resolution video track. Aborting.")
            self.videos = videos_quality
        if by_range:
            self.videos = list(filter(
                lambda x: {
                    "HDR10": x.hdr10,
                    "HLG": x.hlg,
                    "DV": x.dv,
                    "SDR": not x.hdr10 and not x.dv
                }.get((by_range or "").upper(), True),
                self.videos
            ))
            if not self.videos:
                raise ValueError(f"There's no {by_range} video track. Aborting.")
        if by_language:
            self.videos = list(self.select_by_language(by_language, self.videos))
        if one_only and self.videos:
            self.videos = [self.videos[0]]

    def select_audio(self, by_language: Optional[list[str]] = None, with_descriptive: bool = True) -> None:
        """Filter audio tracks by language and other criteria."""
        if not with_descriptive:
            self.audio = list(filter(lambda x: not x.descriptive, self.audio))
        if by_language:
            self.audio = list(self.select_by_language(by_language, self.audio))

    def select_subtitles(self, by_language: Optional[list[str]] = None, with_cc: bool = True, with_sdh: bool = True,
                         with_forced: Union[bool, list] = True) -> None:
        """Filter subtitle tracks by language and other criteria."""
        if not with_cc:
            self.subtitles = list(filter(lambda x: not x.cc, self.subtitles))
        if not with_sdh:
            self.subtitles = list(filter(lambda x: not x.sdh, self.subtitles))
        if isinstance(with_forced, list):
            self.subtitles = [
                x for x in self.subtitles
                if not x.forced or is_close_match(x.language, with_forced)
            ]
        if not with_forced:
            self.subtitles = list(filter(lambda x: not x.forced, self.subtitles))
        if by_language:
            self.subtitles = list(self.select_by_language(by_language, self.subtitles, one_per_lang=False))

    def export_chapters(self, to_file: Optional[Union[Path, str]] = None) -> str:
        """Export all chapters in order to a string or file."""
        self.sort_chapters()
        data = "\n".join(map(repr, self.chapters))
        if to_file:
            to_file = Path(to_file)
            to_file.parent.mkdir(parents=True, exist_ok=True)
            to_file.write_text(data, encoding="utf8")
        return data

    # converter code

    @classmethod
    def from_m3u8(
        cls, master: M3U8, lang: Optional[Union[str, Language]] = None, source: Optional[str] = None
    ) -> Tracks:
        """
        Convert a Variant Playlist M3U8 document to a Tracks object with Video, Audio and
        Subtitle Track objects. This is not an M3U8 parser, use https://github.com/globocom/m3u8
        to parse, and then feed the parsed M3U8 object.

        :param master: M3U8 object of the `m3u8` project: https://github.com/globocom/m3u8
        :param lang: Preferably the original-recorded language of the content in ISO alpha 2 format.
            It will be used as a fallback if a track has no language, and for metadata like if
            the track should be a default track.
        :param source: Source tag for the returned tracks.

        The resulting Track objects' URL will be to another M3U8 file, but this time to an
        actual media stream and not to a variant playlist. The m3u8 downloader code will take
        care of that, as the tracks downloader will be set to `M3U8`.

        Don't forget to manually handle the addition of any needed or extra information or values.
        Like `encrypted`, `pssh`, `hdr10`, `dv`, e.t.c. Essentially anything that is per-service
        should be looked at. Some of these values like `pssh` and `dv` will try to be set automatically
        if possible but if you definitely have the values in the service, then set them.
        Subtitle Codec will default to vtt as it has no codec information.

        Example:
            tracks = Tracks.from_m3u8(m3u8.load(url), lang="en")
            # check the m3u8 project for more info and ways to parse m3u8 documents
        """
        if not master.is_variant:
            raise ValueError("Tracks.from_m3u8: Expected a Variant Playlist M3U8 document...")

        # get pssh if available
        # uses master.data.session_keys instead of master.keys as master.keys is ONLY EXT-X-KEYS and
        # doesn't include EXT-X-SESSION-KEYS which is whats used for variant playlist M3U8.
        widevine_urn = "urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
        widevine_keys = [x.uri for x in master.session_keys if x.keyformat.lower() == widevine_urn]
        pssh = widevine_keys[0].split(",")[-1] if widevine_keys else None
        if pssh:
            pssh = base64.b64decode(pssh)
            # noinspection PyBroadException
            try:
                pssh = Box.parse(pssh)
            except Exception:
                pssh = Box.parse(Box.build(dict(
                    type=b"pssh",
                    version=0,  # can only assume version & flag are 0
                    flags=0,
                    system_ID=Cdm.uuid,
                    init_data=pssh
                )))

        return cls(
            # VIDEO
            [VideoTrack(
                id_=md5(str(x).encode()).hexdigest()[0:7],  # 7 chars only for filename length
                source=source,
                url=("" if re.match("^https?://", x.uri) else x.base_uri) + x.uri,
                # metadata
                codec=x.stream_info.codecs.split(",")[0],  # first codec may not be for the video
                language=lang,  # playlists don't state the language, fallback must be used
                is_original_lang=bool(lang),  # TODO: All that can be done is assume yes if lang is provided
                bitrate=x.stream_info.average_bandwidth or x.stream_info.bandwidth,
                width=x.stream_info.resolution[0],
                height=x.stream_info.resolution[1],
                fps=x.stream_info.frame_rate,
                hdr10=(x.stream_info.codecs.split(".")[0] not in ("dvhe", "dvh1")
                       and (x.stream_info.video_range or "SDR").strip('"') != "SDR"),
                hlg=False,  # TODO: Can we get this from the manifest?
                dv=x.stream_info.codecs.split(".")[0] in ("dvhe", "dvh1"),
                # switches/options
                descriptor=Track.Descriptor.M3U,
                # decryption
                encrypted=True,  # TODO: automatically detect HLS encryption
                pssh=pssh,
                # extra
                extra=x
            ) for x in master.playlists],
            # AUDIO
            [AudioTrack(
                id_=md5(str(x).encode()).hexdigest()[0:6],
                source=source,
                url=("" if re.match("^https?://", x.uri) else x.base_uri) + x.uri,
                # metadata
                codec=f"{x.group_id.replace('audio-', '').split('-')[0]}",
                language=x.language,
                is_original_lang=lang and is_close_match(x.language, [lang]),
                bitrate=0,  # TODO: M3U doesn't seem to state bitrate?
                channels=x.channels,
                descriptive="public.accessibility.describes-video" in (x.characteristics or ""),
                # switches/options
                descriptor=Track.Descriptor.M3U,
                # decryption
                encrypted=False,  # don't know for sure if encrypted
                pssh=pssh,
                # extra
                extra=x
            ) for x in master.media if x.type == "AUDIO" and x.uri],
            # SUBTITLES
            [TextTrack(
                id_=md5(str(x).encode()).hexdigest()[0:6],
                source=source,
                url=("" if re.match("^https?://", x.uri) else x.base_uri) + x.uri,
                # metadata
                codec="vtt",  # assuming VTT, codec info isn't shown
                language=x.language,
                is_original_lang=lang and is_close_match(x.language, [lang]),
                forced=x.forced == "YES",
                sdh="public.accessibility.describes-music-and-sound" in (x.characteristics or ""),
                # switches/options
                descriptor=Track.Descriptor.M3U,
                # extra
                extra=x
            ) for x in master.media if x.type == "SUBTITLES"]
        )

    @classmethod
    def from_mpds(
        cls, data: str, source: Optional[str], lang: Optional[Union[str, Language]] = None, url: str = ""
    ) -> Tracks:
        """
        Convert an MPEG-DASH MPD (Media Presentation Description) document to a Tracks object
        with Video, Audio and Subtitle Track objects where available. This isn't using any
        specific MPD parser since it's XML format, lxml sufficed. There is a nice parser
        project but it has issues to do with ContentProtection so I cannot yet use it.

        :param data: The MPD document as a string.
        :param source: Source tag for the returned tracks.
        :param lang: Preferably the original-recorded language of the content in ISO alpha 2 format.
            It will be used as a fallback if a track has no language, and for metadata like if
            the track should be a default track.
        :param url: The original remote url of the MPD document if available. This is used to
            calculate the base URL for the direct URLs.

        Don't forget to manually handle the addition of any needed or extra information or values.
        Like `encrypted`, `pssh`, `hdr10`, `dv`, e.t.c. Essentially anything that is per-service
        should be looked at. Some of these values like `pssh` will try to be set automatically
        if possible but if you definitely have the values in the service, then set them.

        Example:
            url = "http://media.developer.dolby.com/DolbyVision_Atmos/profile8.1_DASH/p8.1.mpd"
            session = requests.Session(headers={"X-Example": "foo"})
            tracks = Tracks.from_mpds(session.get(url).text, url=url, source="DOLBY", lang="en")
        """
        tracks: list[AnyTrack] = []

        root = load_xml(data)
        if root.tag != "MPD":
            raise ValueError("Non-MPD document provided to Tracks.from_mpds")

        for period in root.findall("Period"):
            if source == "HULU" and period.find("SegmentType").get("value") != "content":
                # skip HULU bumpers and such
                continue

            period_base_url = period.findtext("BaseURL") or root.findtext("BaseURL")
            if url and not period_base_url or not re.match("^https?://", period_base_url.lower()):
                period_base_url = urllib.parse.urljoin(url, period_base_url)

            for adaptation_set in period.findall("AdaptationSet"):
                if any(x.get("schemeIdUri") == "http://dashif.org/guidelines/trickmode"
                       for x in adaptation_set.findall("EssentialProperty")
                       + adaptation_set.findall("SupplementalProperty")):
                    # Skip trick mode streams (used for fast forward/rewind)
                    continue

                for rep in adaptation_set.findall("Representation"):
                    # content type
                    try:
                        content_type = next(x for x in [
                            rep.get("contentType"),
                            rep.get("mimeType"),
                            adaptation_set.get("contentType"),
                            adaptation_set.get("mimeType")
                        ] if bool(x))
                    except StopIteration:
                        raise ValueError("No content type value could be found")
                    else:
                        content_type = content_type.split("/")[0]
                    if content_type.startswith("image"):
                        continue  # most likely seek thumbnails
                    # codec
                    codecs = rep.get("codecs") or adaptation_set.get("codecs")
                    if content_type == "text":
                        mime = adaptation_set.get("mimeType")
                        if mime and not mime.endswith("/mp4"):
                            codecs = mime.split("/")[1]
                    # language
                    track_lang: Optional[Language] = None
                    for lang_ in [rep.get("lang"), adaptation_set.get("lang"), str(lang)]:
                        lang_ = (lang_ or "").strip()
                        if not lang_:
                            continue
                        try:
                            t = Language.get(lang_.split("-")[0])
                            if t == Language.get("und") or not t.is_valid():
                                raise LanguageTagError()
                        except LanguageTagError:
                            continue
                        else:
                            track_lang = Language.get(lang_)
                            break
                    if not track_lang and lang:
                        track_lang = Language.get(lang)
                    # content protection
                    protections = rep.findall("ContentProtection") + adaptation_set.findall("ContentProtection")
                    encrypted = bool(protections)
                    pssh = None
                    kid = None
                    for protection in protections:
                        # For HMAX, the PSSH has multiple keys but the PlayReady ContentProtection tag
                        # contains the correct KID
                        kid = protection.get("default_KID")
                        if kid:
                            kid = uuid.UUID(kid).hex
                        else:
                            kid = protection.get("kid")
                            if kid:
                                kid = uuid.UUID(bytes_le=base64.b64decode(kid)).hex
                        if (protection.get("schemeIdUri") or "").lower() != Cdm.urn:
                            continue
                        pssh = protection.findtext("pssh")
                        if pssh:
                            pssh = base64.b64decode(pssh)
                            # noinspection PyBroadException
                            try:
                                pssh = Box.parse(pssh)
                            except Exception:
                                pssh = Box.parse(Box.build(dict(
                                    type=b"pssh",
                                    version=0,  # can only assume version & flag are 0
                                    flags=0,
                                    system_ID=Cdm.uuid,
                                    init_data=pssh
                                )))

                    rep_base_url = rep.findtext("BaseURL")
                    if rep_base_url and source not in ["DSNY"]:
                        # this mpd allows us to download the entire file in one go, no segmentation necessary!
                        if not re.match("^https?://", rep_base_url.lower()):
                            rep_base_url = urllib.parse.urljoin(period_base_url, rep_base_url)
                        query = urllib.parse.urlparse(url).query
                        if query and not urllib.parse.urlparse(rep_base_url).query:
                            rep_base_url += "?" + query
                        track_url = rep_base_url
                    else:
                        # this mpd provides no way to download the entire file in one go :(
                        segment_template = rep.find("SegmentTemplate")
                        if segment_template is None:
                            segment_template = adaptation_set.find("SegmentTemplate")
                        if segment_template is None:
                            raise ValueError("Couldn't find a SegmentTemplate for a Representation.")
                        segment_template = copy(segment_template)

                        # join value with base url
                        for item in ("initialization", "media"):
                            if not segment_template.get(item):
                                continue
                            segment_template.set(
                                item, segment_template.get(item).replace("$RepresentationID$", rep.get("id"))
                            )
                            query = urllib.parse.urlparse(url).query
                            if query and not urllib.parse.urlparse(segment_template.get(item)).query:
                                segment_template.set(item, segment_template.get(item) + "?" + query)
                            if not re.match("^https?://", segment_template.get(item).lower()):
                                segment_template.set(item, urllib.parse.urljoin(
                                    period_base_url if not rep_base_url else rep_base_url, segment_template.get(item)
                                ))

                        # need to be converted from duration string to seconds float
                        def pt_to_sec(d: Union[str, float]) -> float:
                            if isinstance(d, float):
                                return d
                            if d[0:2] != "PT":
                                raise ValueError("Input data is not a valid time string.")
                            d = d[2:].upper()  # skip `PT`
                            m = re.findall(r"([\d.]+.)", d)
                            return sum(
                                float(x[0:-1]) * {"H": 60 * 60, "M": 60, "S": 1}[x[-1].upper()]
                                for x in m
                            )

                        period_duration = period.get("duration")
                        if period_duration:
                            period_duration = pt_to_sec(period_duration)
                        mpd_duration = root.get("mediaPresentationDuration")
                        if mpd_duration:
                            mpd_duration = pt_to_sec(mpd_duration)

                        track_url = []

                        def replace_fields(url: str, **kwargs: Any) -> str:
                            for field, value in kwargs.items():
                                url = url.replace(f"${field}$", str(value))
                                m = re.search(fr"\${re.escape(field)}%([a-z0-9]+)\$", url, flags=re.I)
                                if m:
                                    url = url.replace(m.group(), f"{value:{m.group(1)}}")
                            return url

                        initialization = segment_template.get("initialization")
                        if initialization:
                            # header/init segment
                            track_url.append(replace_fields(
                                initialization,
                                Bandwidth=rep.get("bandwidth"),
                                RepresentationID=rep.get("id")
                            ))

                        start_number = int(segment_template.get("startNumber") or 1)

                        segment_timeline = segment_template.find("SegmentTimeline")
                        if segment_timeline is not None:
                            seg_time_list = []
                            current_time = 0
                            for s in segment_timeline.findall("S"):
                                if s.get("t"):
                                    current_time = int(s.get("t"))
                                for _ in range(1 + (int(s.get("r") or 0))):
                                    seg_time_list.append(current_time)
                                    current_time += int(s.get("d"))
                            seg_num_list = list(range(start_number, len(seg_time_list) + start_number))
                            track_url += [
                                replace_fields(
                                    segment_template.get("media"),
                                    Bandwidth=rep.get("bandwidth"),
                                    Number=n,
                                    RepresentationID=rep.get("id"),
                                    Time=t
                                )
                                for t, n in zip(seg_time_list, seg_num_list)
                            ]
                        else:
                            period_duration = period_duration or mpd_duration
                            segment_duration = (
                                float(segment_template.get("duration")) / float(segment_template.get("timescale") or 1)
                            )
                            total_segments = math.ceil(period_duration / segment_duration)
                            track_url += [
                                replace_fields(
                                    segment_template.get("media"),
                                    Bandwidth=rep.get("bandwidth"),
                                    Number=s,
                                    RepresentationID=rep.get("id"),
                                    Time=s
                                )
                                for s in range(start_number, start_number + total_segments)
                            ]

                    # for some reason it's incredibly common for services to not provide
                    # a good and actually unique track ID, sometimes because of the lang
                    # dialect not being represented in the id, or the bitrate, or such.
                    # this combines all of them as one and hashes it to keep it small(ish).
                    track_id = "{codec}-{lang}-{bitrate}-{extra}".format(
                        codec=codecs,
                        lang=track_lang,
                        bitrate=rep.get("bandwidth") or 0,  # subs may not state bandwidth
                        extra=(adaptation_set.get("audioTrackId") or "") + (rep.get("id") or "") +
                              (period.get("id") or "")
                    )
                    track_id = md5(track_id.encode()).hexdigest()

                    if content_type == "video":
                        tracks.append(VideoTrack(
                            id_=track_id,
                            source=source,
                            url=track_url,
                            # metadata
                            codec=codecs,
                            language=track_lang,
                            is_original_lang=not track_lang or not lang or is_close_match(track_lang, [lang]),
                            bitrate=rep.get("bandwidth"),
                            width=int(rep.get("width") or 0) or adaptation_set.get("width"),
                            height=int(rep.get("height") or 0) or adaptation_set.get("height"),
                            fps=rep.get("frameRate") or adaptation_set.get("frameRate"),
                            hdr10=all([
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:ColourPrimaries"
                                    and x.get("value") == "9"  # BT.2020
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:TransferCharacteristics"
                                    and x.get("value") == "16"  # PQ
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:MatrixCoefficients"
                                    and x.get("value") == "9"  # BT.2020
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                            ]),
                            hlg=all([
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:ColourPrimaries"
                                    and x.get("value") == "9"  # BT.2020
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:TransferCharacteristics"
                                    and x.get("value") == "18"  # HLG
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                                any(
                                    x.get("schemeIdUri") == "urn:mpeg:mpegB:cicp:MatrixCoefficients"
                                    and x.get("value") == "9"  # BT.2020
                                    for x in adaptation_set.findall("SupplementalProperty")
                                ),
                            ]),
                            dv=codecs and codecs.startswith(("dvhe", "dvh1")),
                            # switches/options
                            descriptor=Track.Descriptor.MPD,
                            # decryption
                            encrypted=encrypted,
                            pssh=pssh,
                            kid=kid,
                            # extra
                            extra=(rep, adaptation_set)
                        ))
                    elif content_type == "audio":
                        tracks.append(AudioTrack(
                            id_=track_id,
                            source=source,
                            url=track_url,
                            # metadata
                            codec=codecs,
                            language=track_lang,
                            is_original_lang=not track_lang or not lang or is_close_match(track_lang, [lang]),
                            bitrate=rep.get("bandwidth"),
                            channels=next(iter(
                                rep.xpath("AudioChannelConfiguration/@value")
                                or adaptation_set.xpath("AudioChannelConfiguration/@value")
                            ), None),
                            # switches/options
                            descriptor=Track.Descriptor.MPD,
                            # decryption
                            encrypted=encrypted,
                            pssh=pssh,
                            kid=kid,
                            # extra
                            extra=(rep, adaptation_set)
                        ))
                    elif content_type == "text":
                        tracks.append(TextTrack(
                            id_=track_id,
                            source=source,
                            url=track_url,
                            # metadata
                            codec=codecs,
                            language=track_lang,
                            is_original_lang=not track_lang or not lang or is_close_match(track_lang, [lang]),
                            # switches/options
                            descriptor=Track.Descriptor.MPD,
                            # extra
                            extra=(rep, adaptation_set)
                        ))

        # Add tracks, but warn only. Assume any duplicate track cannot be handled.
        # Since the custom track id above uses all kinds of data, there realistically would
        # be no other workaround.
        self = cls()
        self.add(tracks, warn_only=True)

        return self

    @classmethod
    def from_mpd(cls, *args: Any, uri: Union[str, Path], session: Optional[Session] = None, **kwargs: Any) -> Tracks:
        """
        Convert an MPEG-DASH MPD (Media Presentation Description) document to a Tracks object
        with Video, Audio and Subtitle Track objects where available. This isn't using any
        specific MPD parser since it's XML format, lxml sufficed. There is a nice parser
        project but it has issues to do with ContentProtection so I cannot yet use it.

        :param uri: Remote URL string or a local file Path object.
        :param session: Used for any remote calls, e.g. getting the MPD document from an URL.
            Can be useful for setting custom headers, proxies, etc.

        Don't forget to manually handle the addition of any needed or extra information or values.
        Like `encrypted`, `pssh`, `hdr10`, `dv`, e.t.c. Essentially anything that is per-service
        should be looked at. Some of these values like `pssh` will try to be set automatically
        if possible but if you definitely have the values in the service, then set them.

        Example:
            url = "http://media.developer.dolby.com/DolbyVision_Atmos/profile8.1_DASH/p8.1.mpd"
            session = requests.Session(headers={"X-Example": "foo"})
            tracks = Tracks.from_mpd(
                url,
                session=session,
                # arguments to pass to from_mpds, see from_mpds for more info.
                source="DOLBY",
                lang="en"
            )
        """
        if isinstance(uri, Path):
            # local file
            kwargs.update({"url": str(uri)})
            return cls.from_mpds(uri.read_text("utf8"), *args, **kwargs)
        if validators.url(uri):
            # remote file
            r = (session or requests.Session()).get(uri)
            kwargs.update({"url": r.url})  # The request may redirect, so we need to check the final URL
            return cls.from_mpds(r.text, *args, **kwargs)
        raise ValueError("Unrecognized MPD URI. It must be a local file or a remote URL.")

    def mux(self, prefix: str) -> tuple[Path, int]:
        """
        Takes the Video, Audio and Subtitle Tracks, and muxes them into an MKV file.
        It will attempt to detect Forced/Default tracks, and will try to parse the language codes of the Tracks
        """
        if self.videos:
            muxed_location = self.videos[0].locate()
            if not muxed_location:
                raise ValueError("The provided video track has not yet been downloaded.")
            muxed_location = muxed_location.with_suffix(".muxed.mkv")
        elif self.audio:
            muxed_location = self.audio[0].locate()
            if not muxed_location:
                raise ValueError("A provided audio track has not yet been downloaded.")
            muxed_location = muxed_location.with_suffix(".muxed.mka")
        elif self.subtitles:
            muxed_location = self.subtitles[0].locate()
            if not muxed_location:
                raise ValueError("A provided subtitle track has not yet been downloaded.")
            muxed_location = muxed_location.with_suffix(".muxed.mks")
        elif self.chapters:
            muxed_location = Path(str(config.filenames.chapters).format(filename=prefix))
            if not muxed_location:
                raise ValueError("A provided chapter has not yet been downloaded.")
            muxed_location = muxed_location.with_suffix(".muxed.mkv")
        else:
            raise ValueError("No tracks provided, at least one track must be provided.")

        cl = [
            "mkvmerge",
            "--output",
            str(muxed_location)
        ]

        for i, vt in enumerate(self.videos):
            location = vt.locate()
            if not location:
                raise ValueError("Somehow a Video Track was not downloaded before muxing...")
            cl.extend([
                "--language", "0:{}".format(LANGUAGE_MUX_MAP.get(
                    str(vt.language), str(vt.language)
                )),
                "--default-track", f"0:{i == 0}",
                "--original-flag", f"0:{vt.is_original_lang}",
                "--compression", "0:none",  # disable extra compression
                "(", str(location), ")"
            ])
        for i, at in enumerate(self.audio):
            location = at.locate()
            if not location:
                raise ValueError("Somehow an Audio Track was not downloaded before muxing...")
            cl.extend([
                "--track-name", f"0:{at.get_track_name() or ''}",
                "--language", "0:{}".format(LANGUAGE_MUX_MAP.get(
                    str(at.language), str(at.language)
                )),
                "--default-track", f"0:{i == 0}",
                "--visual-impaired-flag", f"0:{at.descriptive}",
                "--original-flag", f"0:{at.is_original_lang}",
                "--compression", "0:none",  # disable extra compression
                "(", str(location), ")"
            ])
        for st in self.subtitles:
            location = st.locate()
            if not location:
                raise ValueError("Somehow a Text Track was not downloaded before muxing...")
            default = bool(self.audio and is_close_match(st.language, [self.audio[0].language]) and st.forced)
            cl.extend([
                "--track-name", f"0:{st.get_track_name() or ''}",
                "--language", "0:{}".format(LANGUAGE_MUX_MAP.get(
                    str(st.language), str(st.language)
                )),
                "--sub-charset", "0:UTF-8",
                "--forced-track", f"0:{st.forced}",
                "--default-track", f"0:{default}",
                "--hearing-impaired-flag", f"0:{st.sdh}",
                "--original-flag", f"0:{st.is_original_lang}",
                "--compression", "0:none",  # disable extra compression (probably zlib)
                "(", str(location), ")"
            ])
        if self.chapters:
            location = Path(str(config.filenames.chapters).format(filename=prefix))
            self.export_chapters(location)
            cl.extend(["--chapters", str(location)])

        # let potential failures go to caller, caller should handle
        p = subprocess.run(cl)
        return muxed_location, p.returncode
