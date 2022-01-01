from __future__ import annotations

import base64
import json
import re
from datetime import datetime
from typing import Any, Optional, Union
from urllib.parse import unquote

import click
import m3u8
from click import Context
from pymp4.parser import Box

from vinetrimmer.objects import AudioTrack, MenuTrack, TextTrack, Title, Track, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils.collections import as_list


class AppleTVPlus(BaseService):
    """
    Service code for Apple's TV Plus streaming service (https://tv.apple.com).

    \b
    WIP: decrypt and removal of bumper/dub cards

    \b
    Authorization: Cookies
    Security: UHD@L1 FHD@L1 HD@L3
    """

    ALIASES = ["ATVP", "appletvplus", "appletv+"]
    GEOFENCE = ["us"]

    VIDEO_CODEC_MAP = {
        "H264": ["avc"],
        "H265": ["hvc", "hev", "dvh"]
    }
    AUDIO_CODEC_MAP = {
        "AAC": ["HE", "stereo"],
        "AC3": ["ac3"],
        "EC3": ["ec3", "atmos"]
    }

    @staticmethod
    @click.command(name="AppleTVPlus", short_help="https://tv.apple.com")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> AppleTVPlus:
        return AppleTVPlus(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]

        self.extra_server_parameters = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        r = None
        for i in range(2):
            r = self.session.get(
                url=self.config["endpoints"]["title"].format(type={0: "shows", 1: "movies"}[i], id=self.title),
                params=self.config["device"]
            )
            if r.status_code != 404:
                break
        if not r:
            self.log.exit(f" - Title ID '{self.title}' could not be found.")
            raise
        try:
            title_information = r.json()["data"]["content"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title manifest: {r.text}")

        if title_information["type"] == "Movie":
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=title_information["title"],
                year=datetime.utcfromtimestamp(title_information["releaseDate"] / 1000).year,
                original_lang=title_information["originalSpokenLanguages"][0]["locale"],
                source=self.ALIASES[0],
                service_data=title_information
            )

        r = self.session.get(
            url=self.config["endpoints"]["tv_episodes"].format(id=self.title),
            params=self.config["device"]
        )
        try:
            episodes = r.json()["data"]["episodes"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load episodes list: {r.text}")

        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=episode["showTitle"],
            season=episode["seasonNumber"],
            episode=episode["episodeNumber"],
            episode_name=episode.get("title"),
            original_lang=title_information["originalSpokenLanguages"][0]["locale"],
            source=self.ALIASES[0],
            service_data=episode
        ) for episode in episodes]

    def get_tracks(self, title: Title) -> Tracks:
        res = self.session.get(
            url=self.config["endpoints"]["manifest"].format(id=title.service_data["id"]),
            params=self.config["device"]
        )
        try:
            stream_data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load stream data: {res.text}")
        stream_data = stream_data["data"]["content"]["playables"][0]

        if not stream_data["isEntitledToPlay"]:
            self.log.exit(" - User is not entitled to play this title")
            raise

        self.extra_server_parameters = stream_data["assets"]["fpsKeyServerQueryParameters"]

        tracks = Tracks.from_m3u8(
            m3u8.load(stream_data["assets"]["hlsUrl"], headers=self.session.headers),
            lang=title.original_lang,
            source=self.ALIASES[0]
        )
        for track in tracks:
            track_data = track.extra  # type: Union[m3u8.Media, m3u8.Playlist]
            if isinstance(track, AudioTrack):
                track.encrypted = True
                bitrate = re.search(r"&g=(\d+?)&", track_data.uri)
                if bitrate:
                    track.bitrate = int(bitrate[1][-3::]) * 1000  # e.g. 128->128,000, 2448->448,000
                else:
                    raise ValueError(f"Unable to get a bitrate value for Track {track.id}")
                track.codec = track.codec.replace("_vod", "")
            if isinstance(track, TextTrack):
                track.codec = "vtt"

        tracks.videos = [
            x for x in tracks.videos if
            x.codec[:3] in self.VIDEO_CODEC_MAP[self.vcodec]
        ]

        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio
                if x.codec.split("-")[0] in self.AUDIO_CODEC_MAP[self.acodec]
            ]

        sdh_tracks = [x.language for x in tracks.subtitles if x.sdh]
        tracks.subtitles = [x for x in tracks.subtitles if x.language not in sdh_tracks or x.sdh]

        return Tracks([
            # multiple CDNs, only want one
            x for x in tracks
            if any(
                cdn in as_list(x.url)[0].split("?")[1].split("&") for cdn in ["cdn=ak", "cdn=vod-ak-aoc.tv.apple.com"]
            )
        ])

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, track: Track, **_: Any) -> bytes:
        res = self.session.post(
            url=self.config["endpoints"]["license"],
            json={
                "extra-server-parameters": self.extra_server_parameters,
                "challenge": base64.b64encode(challenge).decode(),
                "key-system": "com.widevine.alpha",
                "uri": f"data:text/plain;base64,{base64.b64encode(Box.build(track.pssh)).decode()}",
                "license-action": "start"
            }
        ).json()
        if "license" not in res:
            self.log.exit(f" - Unable to obtain license (error code: {res['errorCode']})")
            raise
        return res["license"]

    # Service specific functions

    def configure(self) -> None:
        environment = self.get_environment_config()
        if not environment:
            raise ValueError("Failed to get AppleTV+ WEB TV App Environment Configuration...")
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
            "Authorization": f"Bearer {environment['MEDIA_API']['token']}",
            "media-user-token": self.session.cookies.get_dict()["media-user-token"],
            "x-apple-music-user-token": self.session.cookies.get_dict()["media-user-token"]
        })

    def get_environment_config(self) -> Optional[dict]:
        """Loads environment config data from WEB App's <meta> tag."""
        res = self.session.get("https://tv.apple.com").text
        env = re.search(r'web-tv-app/config/environment"[\s\S]*?content="([^"]+)', res)
        if not env:
            return None
        return json.loads(unquote(env[1]))
