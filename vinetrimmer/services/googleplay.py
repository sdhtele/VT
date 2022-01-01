from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import uuid
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import AudioTrack, MenuTrack, TextTrack, Title, Track, Tracks, VideoTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match


class GooglePlay(BaseService):
    """
    Service code for Google Play Movies (https://play.google.com).

    \b
    Authorization: Cookies
    Security: UHD@L1 HD@L3, Doesn't seem to monitor much, but be cautious.
    """

    ALIASES = ["PLAY", "googleplay"]

    CODEC_MAP = {
        "H264": "avc1",
        "H265": "hvc1",
        "VP9": "vp9",
        "AAC": "mp4a.40",
        "AC3": "mp4a.a5",
        "EC3": "eac3",
        "OPUS": "opus"
    }

    @staticmethod
    @click.command(name="GooglePlay", short_help="https://play.google.com")
    @click.argument("title", type=str)
    @click.option("-e", "--episode", is_flag=True, default=False, help="Title is an individual episode.")
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> GooglePlay:
        return GooglePlay(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, episode: bool, movie: bool):
        self.title = title
        self.episode = episode
        self.movie = movie
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = self.CODEC_MAP.get(ctx.parent.params["vcodec"])
        self.acodec = self.CODEC_MAP.get(ctx.parent.params["acodec"])

        self.asset_type: str = ""
        self.device_data: list[str] = []
        self.video_quality = None
        self.audio_quality = None
        self.hdr_type = None
        self.stream_id = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(
            url=self.config["endpoints"]["titles"],
            params={
                "id": f"yt:{self.asset_type}:{self.title}",
                "if": "mibercg:ANN:HDP:PRIM",
                "devtype": "3",
                "device": "mantis",
                "make": "mantis",
                "model": "AFTMM",
                "product": "mantis",
                "alt": "json",
                "apptype": "1",
                "cr": "US",
                "lr": "en-US",
            }
        ).json()
        if "resource_errors" in res:
            self.log.exit(" - Failed to get titles")
            raise
        res = res["resource"]

        original_language = "en"  # TODO: Don't assume

        if self.movie:
            return [Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=x["metadata"]["title"],
                year=None,  # TODO: Get year
                original_lang=original_language,
                source=self.ALIASES[0],
                service_data=x
            ) for x in res if x["resource_id"]["type"] == "MOVIE"]

        if self.episode:
            return [Title(
                id_=self.title,
                type_=Title.Types.TV,
                name=x["metadata"]["title"],  # todo: get series name for individual episodes
                season=1,  # todo: get season number for individual episodes
                episode=x["metadata"]["sequence_number"],
                episode_name=x["metadata"]["title"],
                original_lang=original_language,
                source=self.ALIASES[0],
                service_data=x
            ) for x in res if x["resource_id"]["type"] == "EPISODE"]

        title = [x["metadata"]["title"] for x in res if x["resource_id"]["type"] == "SHOW"][0]
        seasons = {
            # seasons without an mid are "Complete Series", just filter out
            x["resource_id"]["id"]: x["metadata"]["sequence_number"]
            for x in res if x["resource_id"]["type"] == "SEASON" and x["resource_id"].get("mid")
        }

        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=title,
            season=seasons[t["parent"]["id"]],
            episode=t["metadata"]["sequence_number"],
            episode_name=t["metadata"].get("title"),
            original_lang=original_language,
            source=self.ALIASES[0],
            service_data=t
        ) for t in res if t["resource_id"]["type"] == "EPISODE" and t["parent"]["id"] in seasons]

    def get_tracks(self, title: Title) -> Tracks:
        self.stream_id = title.service_data["resource_id"]["id"]

        stream_info = self.session.post(
            url=self.config["endpoints"]["manifest"],
            data=json.dumps([
                [
                    self.device_data, ["0.1", 2, 3, "0", 1], ["en", "US"], None,
                    [
                        None, [[21760040]], None, None,
                        [
                            [
                                [
                                    "enable_lava_sonic_streams",
                                    [None, None, True]
                                ]
                            ]
                        ]
                    ]
                ],
                [1 if self.movie else 5, self.stream_id], 6, [[1, 2, 4, 5], []], [[1, 2, 3], []], None, None
            ], separators=(",", ":")),
            headers={
                "Content-Type": "application/json+protobuf"
            }
        ).json()

        if "error" in stream_info:
            error = stream_info["error"]
            self.log.exit(f" - Failed to get track info: {error['message']} [{error['code']}]")
            raise

        tracks: list[Track] = []

        for period in stream_info["mpd"]["period"]:
            for adaptation_set in period["adaptationSet"]:
                for rep in adaptation_set["representation"]:
                    language = adaptation_set.get("language", title.original_lang)
                    if adaptation_set["contentType"] == "VIDEO":
                        if self.vcodec not in rep["codecs"]:
                            continue
                        tracks.append(VideoTrack(
                            id_=rep["id"],
                            source=self.ALIASES[0],
                            url=rep["baseUrl"][0],
                            # metadata
                            codec=rep["codecs"],
                            language=language,
                            is_original_lang=title.original_lang and is_close_match(language, [title.original_lang]),
                            bitrate=rep["bandwidth"],
                            width=rep.get("width"),
                            height=rep.get("height"),
                            fps=rep.get("frameRate"),
                            # decryption
                            encrypted=len(adaptation_set.get("contentProtection", [])) > 0
                        ))
                    elif adaptation_set["contentType"] == "AUDIO":
                        if self.acodec and self.acodec not in rep["codecs"]:
                            continue
                        tracks.append(AudioTrack(
                            id_=rep["id"],
                            source=self.ALIASES[0],
                            url=rep["baseUrl"][0],
                            # metadata
                            codec=rep["codecs"],
                            language=language,
                            is_original_lang=title.original_lang and is_close_match(language, [title.original_lang]),
                            bitrate=rep["bandwidth"],
                            # decryption
                            encrypted=len(adaptation_set.get("contentProtection", [])) > 0
                        ))
                    else:
                        continue

            text_map = stream_info.get("timedTexts", {}).get("periodTimedTextMap", {}).get(str(period["id"]))
            if text_map:
                for sub in text_map["formatTimedTextMap"]["WEB_VTT"]["timedTextEntity"]:
                    tracks.append(TextTrack(
                        id_=hashlib.md5(sub["url"].encode()).hexdigest()[0:6],
                        source=self.ALIASES[0],
                        url=sub["url"],
                        # metadata
                        codec=(re.search(r"&fmt=(\w+)", sub["url"]) or [])[1].split("-")[0],
                        language=sub["language"],
                        is_original_lang=title.original_lang and is_close_match(sub["language"], [title.original_lang]),
                        cc=sub["contentType"] == "CLOSED_CAPTION"  # seems to really be CC, not SDH
                    ))

        return Tracks(tracks)

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **kwargs: Any) -> bytes:
        # TODO: Hardcode the certificate
        return self.license(**kwargs)

    def license(self, challenge: bytes, **_: Any) -> bytes:
        res = self.session.post(
            url=self.config["endpoints"]["license"],
            data=json.dumps([
                [self.device_data, ["0.1", 2, 3, "0", 1], ["en", "US"], None, [None, None, None, ""]],
                [
                    [None, None, None],
                    None, None, None, None,
                    [1 if self.movie else 5, self.stream_id],
                    ""
                ],
                [base64.b64encode(challenge).decode("utf8")]
            ], separators=(",", ":")),
            headers={
                "Content-Type": "application/json+protobuf"
            }
        ).json()
        return res[2][1]

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "Origin": "https://play.google.com",
            "Authorization": self.generate_authorization(),
        })
        self.asset_type = "movie" if self.movie else "episode" if self.episode else "show"
        self.device_data = ["Amazon", "AFTMM", "Fire OS", "6.0", "TV", str(uuid.uuid4())]  # TODO: Get more device data

    def generate_authorization(self) -> str:
        timestamp = int(time.time())
        auth_hash = hashlib.sha1("{timestamp} {sapisid} https://play.google.com".format(
            timestamp=timestamp,
            sapisid=self.session.cookies.get("SAPISID")
        ).encode()).hexdigest()
        return f"SAPISIDHASH {timestamp}_{auth_hash}"
