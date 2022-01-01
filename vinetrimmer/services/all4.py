from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any, Optional, Union

import click
from click import Context
from Cryptodome.Cipher import AES

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Tracks
from vinetrimmer.objects.tracks import FPS
from vinetrimmer.services.BaseService import BaseService


class All4(BaseService):
    """
    Service code for Channel 4's All4 video-on-demand service (https://channel4.com).

    \b
    Authorization: Credentials
    Security: HD@L3, doesn't care about releases.
    """

    ALIASES = ["ALL4", "channel4", "c4", "4od"]
    GEOFENCE = ["gb", "ie"]

    @staticmethod
    @click.command(name="All4", short_help="https://channel4.com")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> All4:
        return All4(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        self.login_data: dict[str, str] = {}
        self.license_asset: Optional[int] = None
        self.license_url: str
        self.license_token: Optional[str] = None
        self.manifest_url: str

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(
            self.config["endpoints"]["title"].format(title=self.title),
            params={
                "client": self.config["client"]["name"],
                "deviceGroup": self.config["device"]["device_type"],
                "include": "extended-restart"
            },
            headers={
                "Authorization": f"Bearer {self.login_data['accessToken']}"
            }
        ).json()

        if res["brand"]["programmeType"] == "FM":
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=res["brand"]["title"],
                year=res["brand"]["summary"].split(" ")[0].strip().strip("()"),
                original_lang="en",  # TODO: Don't assume English
                source=self.ALIASES[0],
                service_data=res["brand"]["episodes"][0]
            )

        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=res["brand"]["title"],
            season=t["seriesNumber"],
            episode=t["episodeNumber"],
            episode_name=t["originalTitle"],
            original_lang="en",  # TODO: Don't assume English
            source=self.ALIASES[0],
            service_data=t
        ) for t in res["brand"]["episodes"]]

    def get_tracks(self, title: Title) -> Tracks:
        if self.config["client"]["name"] == "c4":
            # PC/WEB
            res = self.session.get(
                "https://ais.channel4.com/asset/" + title.service_data["assetInfo"]["streaming"]["assetId"],
                params={
                    "client": self.config["client"]["name"],
                    "uuid": "f6d8b7b0-3ef5-4764-88dd-af5d7a136611",
                    "rand": "1623754347"
                }
            ).json()
        else:
            # Others, probably Android
            res = self.session.get(
                title.service_data["assetInfo"]["streaming"]["vodBSHref"],
                headers={
                    "Authorization": f"Bearer {self.login_data['accessToken']}"
                }
            ).json()

        manifest = sorted([
            s
            for x in res["videoProfiles"] if x["name"].split("-")[0] in ["widevine", "dashwv"]
            for s in x["streams"]
        ], key=lambda s: s.get("bitRate") or s.get("bitrate") or 0)[-1]

        self.license_asset = int(title.service_data["assetInfo"]["streaming"]["assetId"])
        self.license_token, self.license_url = self.decrypt_token(
            token=manifest["token"],
            key=self.config["keys"][self.config["client"]["name"]]
        )
        self.manifest_url = manifest["uri"]

        self.log.debug(f" + Decrypted Token: {self.license_token}, {self.license_url}")

        tracks = Tracks.from_mpds(
            data=self.session.get(self.manifest_url).text,
            url=self.manifest_url,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

        for video in tracks.videos:
            if not video.fps:
                video.fps = FPS.parse(video.extra[0].find("SegmentBase").get("timescale"))

        tracks.videos[0].extra = res

        if len(tracks.subtitles) == 0:
            for sub_url in [x["url"] for x in res["subtitles"]]:
                if sub_url[-4:] not in [".vtt", ".srt"]:
                    continue
                tracks.add(TextTrack(
                    id_=hashlib.md5(sub_url.encode()).hexdigest()[0:6],
                    source=self.ALIASES[0],
                    url=sub_url,
                    # metadata
                    codec=sub_url[-3:],
                    language=title.original_lang,  # TODO: Don't assume
                    is_original_lang=True,  # TODO: Don't assume
                    forced=False,
                    sdh=True  # seems to be, API has no way of automating
                ))
                break  # only add one, service only does one

        for track in tracks.audio:
            role = track.extra[1].find("Role")
            if role is not None and role.get("value") == "alternative":
                track.descriptive = True

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        track = title.tracks.videos[0]

        chapters = [MenuTrack(
            number=i + 1,
            title=f"Chapter {i + 1:02}",
            timecode=datetime.utcfromtimestamp(ms / 1000).strftime("%H:%M:%S.%f")[:-3]
        ) for i, ms in enumerate(x["breakOffset"] for x in track.extra["adverts"]["breaks"])]

        if "endCredits" in track.extra:
            chapters.append(MenuTrack(
                number=len(chapters) + 1,
                title="Credits",
                timecode=datetime.utcfromtimestamp(
                    (track.extra["endCredits"]["squeezeIn"] / 1000)
                ).strftime("%H:%M:%S.%f")[:-3]
            ))

        return chapters

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> str:
        lic = self.session.post(
            self.license_url,
            data=json.dumps({
                "message": base64.standard_b64encode(challenge).decode("utf8"),  # expects base64
                "token": self.license_token,
                "request_id": self.license_asset,
                "video": {"type": "ondemand", "url": self.manifest_url},
                # "acSerial": 1,
                # "video": {"type": "ondemand"}
            }),
            headers={"Content-Type": "application/json"}
        )
        if lic.status_code != 200:
            self.log.exit(f"Failed to get license! Error: {lic.json()['status']['type']}")
            raise
        return lic.json()["license"]  # base64

    # Service specific functions

    def configure(self) -> None:
        self.log.info(" + Logging in")
        self.login_data = self.login()
        self.session.headers.update({
            "X-C4-Platform-Name": self.config["device"]["platform_name"],
            "X-C4-Device-Type": self.config["device"]["device_type"],
            "X-C4-Device-Name": self.config["device"]["device_name"],
            "X-C4-App-Version": self.config["device"]["app_version"],
            # "X-C4-Optimizely-Datafile": self.config["device"]["optimizely_datafile"]
        })

    def login(self) -> dict:
        # TODO: Implement caching and refreshing
        data = {
            "client_id": self.config["client"]["id"],
            "client_secret": self.config["client"]["secret"]
        }
        if self.credentials:
            data["grant_type"] = "password"
            data["username"] = self.credentials.username
            data["password"] = self.credentials.password
        else:
            data["grant_type"] = "client_credentials"
        r = self.session.post(
            self.config["endpoints"]["login"],
            data=data
        )
        try:
            res = r.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to log in: {r.text}")

        if "error" in res:
            self.log.exit(f"Failed to log in: {res['errorMessage']}")
            raise

        return res

    @staticmethod
    def decrypt_token(token: Union[str, bytes], key: dict) -> tuple[str, str]:
        """Decrypt a token provided by the API."""
        if isinstance(token, str):
            token = base64.standard_b64decode(token)
        cipher = AES.new(
            key=key["key"].encode("utf8"),
            iv=key["iv"].encode("utf8"),
            mode=AES.MODE_CBC
        )
        data = cipher.decrypt(token)[:-2]
        license_api, dec_token = data.decode().split("|")
        return dec_token.strip(), license_api.strip()
