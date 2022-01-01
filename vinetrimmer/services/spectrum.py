from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import random
import urllib.parse
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class Spectrum(BaseService):
    """
    Service code for Spectrum's VOD (Video On-Demand) streaming service (https://watch.spectrum.net).

    The encode quality is worse than you might imagine, even fairly small amount of fast movement on
    screen will result in the bit-rate absolutely crashing (aka the confetti effect). This service is
    only good if the content is only found here and nowhere else, they are essentially badly encoded
    low bit-rate HDTV rips without ads. Sometimes the stream will be captured incredibly badly too
    in one case I encountered a 240p letterboxed then pillarboxed (aka window boxed) resulting in 360p.

    \b
    Authorization: Credentials
    Security: UHD@-- HD@L3, doesn't care about releases.

    \b
    TODO: - Due to unpopularity or need of use, this hasn't been getting regular updates so the codebase or
            any API changes may have broken this codebase; It needs testing
    """

    ALIASES = ["SPEC", "spectrum"]

    @staticmethod
    @click.command(name="Spectrum", short_help="https://watch.spectrum.net")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Spectrum:
        return Spectrum(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        self.secret = "cbb3e04b32b74691b738181fdc9444e6"
        self.token_secret = ""
        self.oauth_data = {
            "oauth_consumer_key": "l7xx66025f7d4f4646b0b1cdf24a846ce1a8",
            "oauth_nonce": None,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": None,
            "oauth_token": "",
            "oauth_version": "1.0"
        }
        self.authorization_data: dict
        self.drm_content_id = None
        self.jwt_token = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(
            url=f"https://api.spectrum.net/nns/V1/series/tmsproviderseriesid/{self.title}",
            params={
                "division": "BKN",
                "lineup": "148",
                "profile": "ovp_v11",
                "cacheID": "72",
                "app": "search",
                "deviceOutOfHome": "false",
                "displayOutOfHomeOnly": "false",
                "dvr": "true",
                "dvrManager": "false",
                "flickable": "true",
                "hideOnDemand": "false",
                "macaddress": "E006E6D33EC9",  # "10EA591DCBA5",
                "tuneToChannel": "true",
                "tvodRent": "false",
                "tvodWatch": "false",
                "vodId": "BKN",
                "watchLive": "true",
                "watchOnDemand": "true"
            },
            headers={
                "Authorization": self.get_oauth_header(
                    "GET",
                    f"https://api.spectrum.net/nns/V1/series/tmsproviderseriesid/{self.title}"
                )
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title manifest: {res.text}")
        if self.movie:
            raise NotImplementedError("Movies have not been implemented")
        titles: list = sum([s["episodes"] for s in data["seasons"]], [])
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=data["title"],
            year=x["details"].get("year"),
            season=x["details"].get("season_number"),
            episode=x["details"].get("episode_number"),
            episode_name=x.get("title"),
            source=self.ALIASES[0],
            service_data=x
        ) for x in titles if x["vodAvailableOutOfHome"]]

    def get_tracks(self, title: Title) -> Tracks:
        stream = [x for x in title.service_data["streamList"] if x["defaultStream"]][0]
        stream = stream["streamProperties"]

        self.drm_content_id = stream["drm_content_id"]

        media = self.session.post(
            url=f"https://api.spectrum.net{stream['mediaUrl']}",
            params={
                "csid": "stva_ovp_dash_pc_vod",
                "dai-supported": "true",
                "drm-supported": "true",
                "vast-supported": "true",
                "adID": "fca05fd1-3a8a-4c12-9ec6-423e28029841",
                "secureTransport": "true",
                "use_token": "true"
            },
            json={"drmEncodings": [{"drm": "cenc", "encoding": "dash"}]},
            headers={
                "Authorization": self.get_oauth_header("POST", f"https://api.spectrum.net{stream['mediaUrl']}")
            }
        ).json()

        self.jwt_token = media["jwtToken"]

        # mark this device as inactive/not streaming
        # otherwise if you do a lot of requests you will max out active device count!
        self.session.delete(
            url="https://api.spectrum.net/ipvs/api/smarttv/aegis/v1",
            params={"aegis": media["aegis"]},
            headers={
                "Authorization": self.get_oauth_header("DELETE", "https://api.spectrum.net/ipvs/api/smarttv/aegis/v1")
            }
        )

        return Tracks.from_mpd(
            uri=media["stream_url"],
            session=self.session,
            lang="en",  # TODO: Don't assume
            source=self.ALIASES[0]
        )

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **kwargs: Any) -> bytes:
        # seems to need a different endpoint
        # TODO: Hardcode the certificate
        return self.license(**kwargs)

    def license(self, challenge: bytes, **_: Any) -> bytes:
        lic = self.session.post(
            url="https://spectrum-charter.live.ott.irdeto.com/licenseServer/widevine/v1/twc/license",
            params={
                "CrmId": "twc",
                "AccountId": "twc",
                "ContentId": self.drm_content_id
            },
            headers={
                "Authorization": f"Bearer {self.jwt_token}"
            },
            data=challenge  # expects bytes
        )
        try:
            # if it's json content, then an error occurred
            error = lic.json().get("message")
            raise ValueError(f"Failed to obtain license: {error}")
        except json.JSONDecodeError:
            return lic.content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "device_id": self.generate_nonce()
        })
        self.log.info("Logging into Spectrum")
        self.request_oauth()
        self.log.info(" + Obtained OAuth token")
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        self.authorize()
        self.log.info(" + Obtained Authorization token")

    @staticmethod
    def generate_nonce() -> str:
        format_string = "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx"
        nonce = ""
        for c in format_string:
            if c not in ["x", "y"]:
                nonce += c
                continue
            t = int((16 * random.uniform(0, 1)) // 1)
            nonce += hex(t if c == "x" else 3 & t | 8)[2:]
        return nonce

    @staticmethod
    def sign_oauth_data(http_method: str, url: str, oauth_data: dict, secret: str, token_secret: str) -> str:
        sha1 = hmac.new(
            f"{secret}&{token_secret}".encode(),
            "&".join([urllib.parse.quote(x, safe="~()*!.'") for x in [
                http_method.upper(),
                url.split("?")[0],
                "&".join([f"{k}={v}" for k, v in oauth_data.items()])
            ]]).encode(),
            hashlib.sha1
        )
        return urllib.parse.quote(base64.b64encode(sha1.digest()).decode("utf8"), safe="~()*!.'")

    def get_oauth_header(self, http_method: str, url: str) -> str:
        self.oauth_data["oauth_nonce"] = self.generate_nonce()
        self.oauth_data["oauth_timestamp"] = str(int(datetime.datetime.now().timestamp() * 1000))
        self.oauth_data = dict(sorted(self.oauth_data.items()))
        oauth_signature = self.sign_oauth_data(
            http_method=http_method,
            url=url,
            oauth_data=self.oauth_data,
            secret=self.secret,
            token_secret=self.token_secret
        )
        data = dict(**self.oauth_data, oauth_signature=oauth_signature)
        return "OAuth " + (", ".join([f'{k}="{v}"' for k, v in data.items()]))

    def request_oauth(self) -> None:
        r = self.session.post(
            url="https://api.spectrum.net/auth/oauth/request",
            headers={
                "Authorization": self.get_oauth_header("POST", "https://api.spectrum.net/auth/oauth/request"),
                "Accept": "application/json, text/plain, */*"
            }
        ).text
        res = urllib.parse.parse_qs(r)
        self.token_secret = res["oauth_token_secret"][0]
        self.oauth_data["oauth_token"] = res["oauth_token"][0]

    def authorize(self) -> None:
        r = self.session.post(
            url="https://api.spectrum.net/auth/oauth/device/authorize",
            headers={
                "Authorization": self.get_oauth_header("POST", "https://api.spectrum.net/auth/oauth/device/authorize"),
                "Accept": "application/json, text/plain, */*"
            },
            params={
                "xoauth_device_id": "f05cb227-d564-4eae-b060-f9b1d4ccde77",
                "xoauth_device_type": "ONEAPP-OVP",
                "oauth_token": self.oauth_data["oauth_token"],
                "username": self.credentials.username,
                "password": self.credentials.password,
                "supportSMB": True
            }
        ).text
        res = urllib.parse.parse_qs(r)
        self.authorization_data = {k: v[0] for k, v in res.items()}
        self.oauth_data["oauth_account_type"] = self.authorization_data["xoauth_account_type"]
        self.oauth_data["oauth_verifier"] = self.authorization_data["oauth_verifier"]
        # log
        r = self.session.post(
            url="https://api.spectrum.net/auth/oauth/token",
            headers={
                "Authorization": self.get_oauth_header("POST", "https://api.spectrum.net/auth/oauth/token"),
                "Accept": "application/json, text/plain, */*"
            }
        ).text
        res = urllib.parse.parse_qs(r)
        self.oauth_data["oauth_token"] = res["oauth_token"][0]
