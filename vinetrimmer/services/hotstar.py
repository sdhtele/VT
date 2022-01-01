from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class Hotstar(BaseService):
    """
    Service code for Star India's Hotstar (aka Disney+ Hotstar) streaming service (https://hotstar.com).

    \b
    Authorization: Credentials
    Security: UHD@L1? HD@L3, doesn't seem to care about releases.

    \b
    Tips: - The library of contents can be viewed without logging in at https://hotstar.com
          - The homepage hosts domestic programming; Disney+ content is at https://hotstar.com/in/disneyplus
    """

    ALIASES = ["HS", "hotstar"]
    GEOFENCE = ["in"]

    @staticmethod
    @click.command(name="Hotstar", short_help="https://hotstar.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.option("-c", "--channels", default="5.1", type=click.Choice(["5.1", "2.0"], case_sensitive=False),
                  help="Audio Codec")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Hotstar:
        return Hotstar(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool, channels: str):
        self.title = title
        self.movie = movie
        self.channels = channels
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"] or "EC3"
        self.range = ctx.parent.params["range_"]

        self.profile = ctx.obj.profile

        self.device_id: str
        self.hotstar_auth = None
        self.hdntl = None
        self.token: str
        self.license_api: Optional[str] = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        headers = {
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.5",
            "hotstarauth": self.hotstar_auth,
            "X-HS-UserToken": self.token,
            "X-HS-Platform": self.config["device"]["platform"]["name"],
            "X-HS-AppVersion": self.config["device"]["platform"]["version"],
            "X-Country-Code": "in",
            "x-platform-code": "PCTV"
        }
        res = self.session.get(
            url=self.config["endpoints"]["movie_title"] if self.movie else self.config["endpoints"]["tv_title"],
            headers=headers,
            params={"contentId": self.title}
        )
        try:
            data = res.json()["body"]["results"]["item"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title manifest: {res.text}")

        if data["assetType"] == "MOVIE":
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=data["title"],
                year=data["year"],
                original_lang=data["langObjs"][0]["iso3code"],
                source=self.ALIASES[0],
                service_data=data
            )

        res = self.session.get(
            url=self.config["endpoints"]["tv_episodes"],
            headers=headers,
            params={
                "eid": data["id"],
                "etid": "2",
                "tao": "0",
                "tas": "1000"
            }
        )
        try:
            data = res.json()["body"]["results"]["assets"]["items"]
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load episodes list: {res.text}")
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=data["title"],
            year=x.get("year"),
            season=x.get("seasonNo"),
            episode=x.get("episodeNo"),
            episode_name=x.get("title"),
            original_lang=x["langObjs"][0]["iso3code"],
            source=self.ALIASES[0],
            service_data=x
        ) for x in data]

    def get_tracks(self, title: Title) -> Tracks:
        res = self.session.get(
            url=self.config["endpoints"]["manifest"].format(id=title.service_data["contentId"]),
            params={
                # TODO: Perhaps set up desired-config to actual desired playback set values?
                "desired-config": "|".join([
                    "audio_channel:stereo",
                    "dynamic_range:sdr",
                    "encryption:widevine",
                    "ladder:tv",
                    "package:dash",
                    "resolution:hd",
                    "subs-tag:HotstarPremium",
                    "video_codec:vp9"
                ]),
                "device-id": self.device_id,
                "os-name": self.config["device"]["os"]["name"],
                "os-version": self.config["device"]["os"]["version"]
            },
            headers={
                "Accept": "*/*",
                "Accept-Language": "en-GB,en;q=0.5",
                "hotstarauth": self.hotstar_auth,
                "X-HS-UserToken": self.token,
                "X-HS-Platform": self.config["device"]["platform"]["name"],
                "X-HS-AppVersion": self.config["device"]["platform"]["version"],
                "X-Request-Id": "03bc5e28-dddf-4eb4-84cd-0727e44bfdaa",
                "X-Country-Code": "in"
            }
        )
        try:
            playback_sets = res.json()["data"]["playBackSets"]
        except json.JSONDecodeError:
            raise ValueError(f"Manifest fetch failed: {res.text}")

        # transform tagsCombination into `tags` key-value dictionary for easier usage
        playback_sets = [dict(
            **x,
            tags=dict(y.split(":") for y in x["tagsCombination"].lower().split(";"))
        ) for x in playback_sets]

        playback_set = next((
            x for x in playback_sets if
            # generally minimum requirements for this script:
            # x["tags"].get("subscription") == "hotstarpremium" and  # Needed?
            x["tags"].get("encryption") == "widevine" and  # widevine, fairplay, playready
            x["tags"].get("package") == "dash" and  # dash, hls
            x["tags"].get("container") == "fmp4" and  # fmp4, fmp4br, ts
            x["tags"].get("ladder") == "tv" and  # tv, phone
            x["tags"].get("video_codec").endswith(self.vcodec.lower()) and  # dvh265, h265, h264 - vp9?
            # user defined, may not be available in the tags list:
            x["tags"].get("resolution") in ["4k", None] and  # max is fine, -q can choose lower if wanted
            x["tags"].get("dynamic_range") in [self.range.lower(), None] and  # dv, hdr10, sdr - hdr10+?
            x["tags"].get("audio_codec") in [self.acodec.lower(), None] and  # ec3, aac - atmos?
            x["tags"].get("audio_channel") in [{"5.1": "dolby51", "2.0": "stereo"}[self.channels], None]
        ), None)
        if not playback_set:
            raise ValueError("Wanted playback set is unavailable for this title...")

        self.license_api = playback_set["licenceUrl"]

        # TODO: Old Base URL logic, needs testing if changes work before discarding completely.
        # base_url = os.path.dirname(playback_set["playbackUrl"][:playback_set["playbackUrl"].rfind("?")]) + "/"
        # base_url = base_url.replace(".hotstar.com", ".akamaized.net")

        tracks = Tracks.from_mpd(
            uri=playback_set["playbackUrl"].replace(".hotstar.com", ".akamaized.net"),
            session=self.session,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )
        for track in tracks:
            track.needs_proxy = True
        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> bytes:
        assert self.license_api is not None
        return self.session.post(
            url=self.license_api,
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "Origin": "https://www.hotstar.com",
            "Referer": "https://www.hotstar.com/in"
        })
        self.log.info("Logging into Hotstar")
        self.device_id = str(uuid.uuid4())
        self.log.info(f" + Created Device ID: {self.device_id}")
        self.hotstar_auth, self.hdntl = self.get_akamai()
        self.log.info(f" + Calculated HotstarAuth: {self.hotstar_auth}")
        self.session.cookies.set("hdntl", self.hdntl)
        self.log.info(f" + Calculated HDNTL: {self.hdntl}")
        self.token = self.get_token()
        print("Obtained tokens")

    @staticmethod
    def get_akamai() -> tuple:
        enc_key = b"\x05\xfc\x1a\x01\xca\xc9\x4b\xc4\x12\xfc\x53\x12\x07\x75\xf9\xee"
        st = int(time.time())
        exp = st + 6000
        res = f"st={st}~exp={exp}~acl=/*"
        res += "~hmac=" + hmac.new(enc_key, res.encode(), hashlib.sha256).hexdigest()
        res2 = f"exp={exp}~acl=/*"
        res2 += "~data=hdntl~hmac=" + hmac.new(enc_key, res.encode(), hashlib.sha256).hexdigest()
        return res, res2

    def get_token(self) -> str:
        token_cache_path = self.get_cache("token_{profile}.json".format(profile=self.profile))
        if token_cache_path.is_file():
            token = json.loads(token_cache_path.read_text(encoding="utf8"))
            if token.get("exp", 0) > int(time.time()):
                # not expired, lets use
                self.log.info(" + Using cached auth tokens...")
                return token["uid"]
            # expired, refresh
            self.log.info(" + Refreshing and using cached auth tokens...")
            return self.save_token(self.refresh(token["uid"], token["sub"]["deviceId"]), token_cache_path)
        # get new token
        return self.save_token(self.login(), token_cache_path)

    @staticmethod
    def save_token(token: str, to: Path) -> str:
        # decode the jwt data component
        data = json.loads(base64.b64decode(token.split(".")[1] + "===").decode("utf-8"))
        data["uid"] = token
        data["sub"] = json.loads(data["sub"])
        # lets cache the token
        to.parent.mkdir(parents=True, exist_ok=True)
        to.write_text(json.dumps(data), encoding="utf8")
        # finally return the token
        return token

    def refresh(self, user_id_token: str, device_id: str) -> str:
        res = self.session.get(
            url=self.config["endpoints"]["refresh"],
            headers={"userIdentity": user_id_token, "deviceId": device_id}
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to refresh token, response was not JSON: {res.text}")
            raise
        if "errorCode" in data:
            self.log.exit(f" - Token Refresh failed: {data['description']} [{data['errorCode']}]")
            raise
        return data["description"]["userIdentity"]

    def login(self) -> str:
        """
        Log in to HOTSTAR and return a JWT User Identity token.
        :returns: JWT User Identity token.
        """
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        res = self.session.post(
            url=self.config["endpoints"]["login"],
            json={
                "isProfileRequired": "false",
                "userData": {
                    "deviceId": self.device_id,
                    "password": self.credentials.password,
                    "username": self.credentials.username,
                    "usertype": "email"
                },
                "verification": {}
            },
            headers={
                "hotstarauth": self.hotstar_auth,
                "content-type": "application/json"
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to get auth token, response was not JSON: {res.text}")
            raise
        if "errorCode" in data:
            self.log.exit(f" - Login failed: {data['description']} [{data['errorCode']}]")
            raise
        return data["description"]["userIdentity"]
