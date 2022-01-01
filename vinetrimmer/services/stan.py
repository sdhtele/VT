from __future__ import annotations

import json
from hashlib import md5
from typing import Any, Union
from uuid import UUID

import click
from click import Context
from pymp4.parser import Box

from vinetrimmer.objects import AudioTrack, MenuTrack, TextTrack, Title, Tracks, VideoTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import Cdm


class Stan(BaseService):
    """
    Service code for Nine Digital's Stan. streaming service (https://stan.com.au).

    \b
    Authorization: Credentials
    Security: UHD@L3, doesn't care about releases.
    """

    ALIASES = ["STAN"]
    GEOFENCE = ["au"]

    AUDIO_CODEC_MAP = {
        "AAC": "mp4a",
        "AC3": "ac-3",
        "EC3": "ec-3"
    }

    @staticmethod
    @click.command(name="Stan", short_help="https://stan.com.au")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.option("-t", "--device-type", default="tv", type=click.Choice(["tv", "web"]),
                  help="Device type.")
    @click.option("-q", "--vquality", default="uhd", type=click.Choice(["uhd", "hd"]),
                  help="Quality to request from the manifest, combine --vquality uhd with --device-type tv for UHD L3.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Stan:
        return Stan(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool, device_type: str, vquality: str):
        self.title = title
        self.movie = movie
        self.device_type = device_type
        self.vquality = vquality
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"].lower()
        self.acodec = ctx.parent.params["acodec"]
        self.range = ctx.parent.params["range_"]

        self.api_config: dict[str, dict] = {}
        self.login_data: dict[str, str] = {}
        self.license_api = None
        self.license_cd = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(f"{self.api_config['cat']['v12']}/programs/{self.title}.json")
        try:
            data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title manifest: {res.text}")
        if "audioTracks" in data:
            data["original_language"] = [x["language"]["iso"] for x in data["audioTracks"] if x["type"] == "main"]
            if len(data["original_language"]) > 0:
                data["original_language"] = data["original_language"][0]
        if "original_language" not in data:
            data["original_language"] = None
        if self.movie:
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=data["title"],
                year=data.get("releaseYear"),
                source=self.ALIASES[0],
                service_data=data
            )
        titles = []
        for season in data["seasons"]:
            res = self.session.get(season["url"])
            try:
                season_data = res.json()
            except json.JSONDecodeError:
                raise ValueError(f"Failed to load season manifest: {res.text}")
            for episode in season_data["entries"]:
                episode["title_year"] = data["releaseYear"]
                episode["original_language"] = data["original_language"]
                titles.append(episode)
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=data["title"],
            year=x.get("title_year", x.get("releaseYear")),
            season=x.get("tvSeasonNumber"),
            episode=x.get("tvSeasonEpisodeNumber"),
            episode_name=x.get("title"),
            source=self.ALIASES[0],
            service_data=x
        ) for x in titles]

    def get_tracks(self, title: Title) -> Tracks:
        program_data = self.session.get(
            f"{self.api_config['cat']['v12']}/programs/{title.service_data['id']}.json"
        ).json()

        res = self.session.get(
            url=program_data["streams"][self.vquality]["dash"]["auto"]["url"],
            params={
                "jwToken": self.login_data["jwToken"],
                "format": "json",
                "capabilities.drm": "widevine",
                "videoCodec": self.vcodec
            }
        )
        try:
            stream_data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load stream data: {res.text}")
        if "media" not in stream_data:
            raise ValueError(f"Failed to load stream data: {stream_data}")
        stream_data = stream_data["media"]

        if self.vquality == "uhd":
            self.license_api = stream_data["fallbackDrm"]["licenseServerUrl"]
        else:
            self.license_api = stream_data["drm"]["licenseServerUrl"]
            self.license_cd = stream_data["drm"]["customData"]

        original_language = title.service_data["original_language"]
        if not original_language:
            original_language = [x for x in title.service_data["audioTracks"] if x["type"] == "main"]
            if original_language:
                original_language = original_language[0]["language"]["iso"]
            else:
                original_language = title.service_data["languages"][0]

        tracks = Tracks.from_mpds(
            data=self.session.get(
                url=self.config["endpoints"]["manifest"],
                params={
                    "url": stream_data["videoUrl"],
                    "audioType": "all"
                }
            ).text,
            url=self.config["endpoints"]["manifest"],
            lang=original_language,
            source=self.ALIASES[0]
        )
        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio
                if x.codec[:4] == self.AUDIO_CODEC_MAP[self.acodec]
            ]
        if "captions" in stream_data:
            for sub in stream_data["captions"]:
                tracks.add(TextTrack(
                    id_=md5(sub["url"].encode()).hexdigest()[0:6],
                    source=self.ALIASES[0],
                    url=sub["url"],
                    # metadata
                    codec=sub["type"].split("/")[-1],
                    language=sub["language"],
                    is_original_lang=sub["language"].startswith(original_language),
                    cc="(cc)" in sub["name"].lower()
                ))

        # craft pssh with the key_id
        # TODO: is doing this still necessary? since the code now tries grabbing PSSH from
        #       the first chunk of data of the track, it might be available from that.
        pssh = Box.parse(Box.build(dict(
            type=b"pssh",
            version=0,
            flags=0,
            system_ID=Cdm.uuid,
            # \x12\x10 is decimal ascii representation of \f\n (\r\n)
            init_data=b"\x12\x10" + UUID(stream_data["drm"]["keyId"]).bytes
        )))

        for track in tracks:
            track.needs_proxy = True
            if isinstance(track, VideoTrack):
                track.hdr10 = self.range == "HDR10"
            if isinstance(track, (VideoTrack, AudioTrack)):
                track.encrypted = True
                if not track.pssh:
                    track.pssh = pssh

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **kwargs: Any) -> Union[bytes, str]:
        # TODO: Hardcode the certificate
        return self.license(**kwargs)

    def license(self, challenge: bytes, **_: Any) -> Union[bytes, str]:
        assert self.license_api is not None
        lic = self.session.post(
            url=self.license_api,
            headers={} if self.device_type == "tv" else {
                "dt-custom-data": self.license_cd
            },
            data=challenge  # expects bytes
        )
        try:
            if "license" in lic.json():
                return lic.json()["license"]  # base64 str?
        except json.JSONDecodeError:
            return lic.content  # bytes

        raise ValueError(f"Failed to obtain license: {lic.text}")

    # Service specific functions

    def configure(self) -> None:
        print("Retrieving API configuration...")
        self.api_config = self.get_config()
        print("Logging in...")
        self.login_data = self.login()

    def get_config(self) -> dict:
        res = self.session.get(
            self.config["endpoints"]["config"].format(type='web/app' if self.device_type == 'web' else 'tv/android'))
        try:
            return res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to obtain Stan API configuration: {res.text}")

    def login(self) -> dict:
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        self.session.get(self.config["endpoints"]["homepage"])  # need cookies
        res = self.session.post(
            url=self.api_config["login"]["v1"] + self.config["endpoints"]["login"].format(
                type="web/account" if self.device_type == "web" else "app"
            ),
            data=(
                {
                    "source": self.config["meta"]["login_source"],
                    "email": self.credentials.username,
                    "password": self.credentials.password
                } if self.device_type == "web" else {
                    "source": self.config["meta"]["login_source"],
                    "email": self.credentials.username,
                    "password": self.credentials.password,
                    "manufacturer": "NVIDIA",
                    "os": "Android-9",
                    "model": "SHIELD Android TV",
                    "stanName": "Stan-AndroidTV",
                    "stanVersion": "3.2.1",
                    "type": "console",
                    "videoCodecs": "h264,decode,h263,h265,hevc,mjpeg,mpeg2v,mp4,mpeg4,vc1,vp8,vp9",
                    "audioCodecs": "omx.dolby.ac3.decoder,omx.dolby.eac3.decoder,aac",
                    "drm": "widevine",
                    "captions": "ttml",
                    "screenSize": "3840x2160",
                    "hdcpVersion": "2.2",
                    "colorSpace": {"HDR10": "hdr10", "DV": "hdr"}.get(self.range, "sdr"),
                    "features": "hevc"
                }
            ),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:81.0) Gecko/20100101 Firefox/81.0"
                    if self.device_type == "web" else
                    "Dalvik/2.1.0 (Linux; U; Android 9; SHIELD Android TV Build/PPR1.180610.011)"
                )
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to log in: {res.text}")
        if "errors" in data:
            raise ValueError(f"An error occurred while logging in: {data['errors']}")
        return data
