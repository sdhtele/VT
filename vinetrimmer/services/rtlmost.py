from __future__ import annotations

import json
import random
import re
import string
from typing import Any

import click
import m3u8
from click import Context
from langcodes import Language

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils.drmtoday import DRMTODAY_RESPONSE_CODES


class RTLMost(BaseService):
    """
    Service code for RTL Most (https://www.rtlmost.hu/).

    \b
    Authorization: Credentials
    Security: UHD@-- HD@L3
    """

    ALIASES = ["RTLM", "rtlmost", "rtlmp"]

    @staticmethod
    @click.command(name="RTLMost", short_help="https://rtlmost.hu")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> RTLMost:
        return RTLMost(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        self.configure()

    def get_titles(self) -> list[Title]:
        m = re.search(r"-?([cp])_(\d+)$", self.title)
        if not m:
            self.log.exit(" - Invalid title ID")
            raise

        content_type, title_id = m.groups()

        if content_type == "c":  # clip
            title = self.get_clip_info(title_id)
        else:  # program
            title = self.get_program_info(title_id)

        titles = []

        for clip in title["clips"]:
            season = (clip.get("product", {}).get("season")
                      or next(iter(re.findall(r"(\d+)\. évad", clip["title"])), None))

            episode = (clip.get("product", {}).get("episode")
                       or next(iter(re.findall(r"(\d+)\. rész", clip["title"])), None))

            if season or episode:
                titles.append(Title(
                    id_=clip["id"],
                    type_=Title.Types.TV,
                    name=clip["program"]["title"],
                    season=season,
                    episode=episode,
                    source=self.ALIASES[0],
                    service_data=clip
                ))
            else:
                titles.append(Title(
                    id_=clip["id"],
                    type_=Title.Types.MOVIE,
                    name=clip["title"],
                    year=clip["product"]["year_copyright"],  # TODO: This seems to be usually/always null
                    source=self.ALIASES[0],
                    service_data=clip
                ))

        return titles

    def get_tracks(self, title: Title) -> Tracks:
        assets = title.service_data.get("assets")
        if not assets:
            assets = self.get_clip_info(title.service_data["id"])["clips"][0]["assets"]
        if not assets:
            self.log.exit(" - Video not available")
            raise

        assets = [x for x in assets if x["type"] in ("usp_hls_h264", "usp_dashcenc_h264")]
        if not assets:
            self.log.exit(" - No suitable streams found")
            raise

        asset = sorted(assets, key=lambda x: x["video_quality"])[0]  # hd, sd

        manifest_url = asset["full_physical_path"]

        if asset["type"] == "usp_hls_h264":
            # Unencrypted HLS
            tracks = Tracks.from_m3u8(
                master=m3u8.load(manifest_url),
                lang="en",  # TODO: Don't assume
                source=self.ALIASES[0]
            )
        else:
            # DASH CENC
            tracks = Tracks.from_mpd(
                uri=manifest_url,
                lang="en",  # TODO: Don't assume
                source=self.ALIASES[0]
            )

        for track in tracks:
            if track.language == Language.get("fr"):
                # TODO: Is there a better way to get the actual language?
                # The service often lies about the audio being French,
                # when it's usually/always Hungarian instead.
                track.language = Language.get("hu")

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []  # TODO

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, *, challenge: bytes, title: Title, **_: Any) -> str:
        r = self.session.get(self.config["endpoints"]["jwt"], headers={
            "x-auth-device-id": self.session.cookies["rtlhuDeviceId"],
            "X-Auth-gigya-signature": self.tokens["UIDSignature"],
            'X-Auth-gigya-signature-timestamp': self.tokens["signatureTimestamp"],
            'X-Auth-gigya-uid': self.tokens["UID"],
            'X-Client-Release': "4.128.7",
            "x-customer-name": "rtlhu"
        })
        if not r.ok:
            self.log.exit(f" - Failed to get JWT: HTTP Error {r.status_code}: {r.reason}")
            raise
        jwt = r.json()["token"]

        r = self.session.get(
            self.config["endpoints"]["license_token"].format(uid=self.tokens["UID"], clip_id=title.id),
            headers={
                "Authorization": f"Bearer {jwt}"
            }
        )
        if not r.ok:
            self.log.exit(f" - Failed to get license request token: HTTP Error {r.status_code}: {r.reason}")
            raise
        res = r.json()

        r = self.session.post(self.config["endpoints"]["license"], headers={
            "x-dt-auth-token": res["token"]
        }, data=challenge)
        if not r.ok:
            code = r.headers.get("x-dt-resp-code")
            if code:
                self.log.exit(f" - DRMtoday Error: {DRMTODAY_RESPONSE_CODES.get(code, 'Unknown Error')} ({code})")
                raise
            self.log.exit(f" - HTTP Error {r.status_code}: {r.reason}")
        return r.json()["license"]

    # Service-specific functions

    def configure(self) -> None:
        # TODO: Cache tokens

        self.log.info(" + Registering device")
        r = self.session.get(self.config["endpoints"]["device_registration"])
        if not r.ok:
            self.log.exit(f" - HTTP Error {r.status_code}: {r.reason}")
            raise

        self.log.info(" + Logging in")
        context_id = f"R{''.join(random.choices(string.digits, k=10))}"
        r = self.session.post(self.config["endpoints"]["login"], params={
            "context": context_id,
            "saveResponseID": context_id
        }, data={
            "loginID": self.credentials.username,
            "password": self.credentials.password,
            "sessionExpiration": -2,
            "targetEnv": "jssdk",
            "include": "profile,data",
            "includeUserInfo": "true",
            "lang": "hu",
            "APIKey": self.config["api_key"],
            "sdk": "js_latest",
            "authMode": "cookie",
            "pageURL": "https://www.rtlmost.hu/",
            "format": "jsonp",
            "callback": "gigya.callback",
            "context": "R1978336255",
            "utf8": "&#x2713;"
        })
        if not r.ok:
            self.log.exit(f" - HTTP Error {r.status_code}: {r.reason}")
            raise

        self.log.info(" + Obtaining auth tokens")
        res = json.loads(self.session.get(self.config["endpoints"]["tokens"], params={
            "APIKey": self.config["api_key"],
            "saveResponseID": context_id,
            "pageURL": "https://www.rtlmost.hu/",
            "noAuth": "true",
            "sdk": "js_latest",
            "format": "jsonp",
            "callback": "gigya.callback",
            "context": context_id
        }).text[15:-2])
        if res["statusCode"] == 200:
            self.tokens = res
        else:
            self.log.exit(f"- Failed: {res}")
            raise

    def get_clip_info(self, clip_id: str) -> dict:
        r = self.session.get(self.config["endpoints"]["clip_info"].format(clip_id=clip_id), params={
            "csa": "0",
            "with": "clips,freemiumpacks,program_images,service_display_images,extra_data,program_subcats"
        }, headers={
            "x-6play-freemium": "1",
            "x-auth-device-id": self.session.cookies["rtlhuDeviceId"],
            "x-auth-gigya-signature": self.tokens["UIDSignature"],
            "x-auth-gigya-signature-timestamp": self.tokens["signatureTimestamp"],
            "x-auth-gigya-uid": self.tokens["UID"],
            "x-client-release": "m6group_web-4.128.7",
            "x-customer-name": "rtlhu"
        })
        if not r.ok:
            self.log.exit(f" - Failed to get clip info: HTTP Error {r.status_code}: {r.reason}")
            raise
        return r.json()

    def get_program_info(self, program_id: str) -> dict:
        page = 1
        clips = []

        while True:
            r = self.session.get(self.config["endpoints"]["program_info"].format(program_id=program_id), params={
                "csa": "5",
                "with": "clips,freemiumpacks,expiration",
                "type": "vi,vc,playlist",
                "limit": "100",
                "offset": str((page - 1) * 100)
            }, headers={
                "x-auth-device-id": self.session.cookies["rtlhuDeviceId"],
                "x-auth-gigya-signature": self.tokens["UIDSignature"],
                "x-auth-gigya-signature-timestamp": self.tokens["signatureTimestamp"],
                "x-auth-gigya-uid": self.tokens["UID"],
                "x-client-release": "m6group_web-4.128.7",
                "x-customer-name": "rtlhu"
            })
            if not r.ok:
                self.log.exit(f" - Failed to get program info: HTTP Error {r.status_code}: {r.reason}")
                raise
            items = r.json()
            for item in items:
                clips.append(item["clips"][0])
            if len(items) < page * 100:
                # last page
                return {"clips": clips}
            page += 1
