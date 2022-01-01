from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class Peacock(BaseService):
    """
    Service code for NBC's Peacock streaming service (https://peacocktv.com).

    \b
    Authorization: Cookies
    Security: UHD@-- FHD@L3, doesn't care about releases.

    \b
    Tips: - The library of contents can be viewed without logging in at https://www.peacocktv.com/stream/tv
            See the footer for links to movies, news, etc. A US IP is required to view.

    \b
    TODO: Movies are not yet supported
    """

    ALIASES = ["PCOK", "peacock"]
    GEOFENCE = ["us"]

    @staticmethod
    @click.command(name="Peacock", short_help="https://peacocktv.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Peacock:
        return Peacock(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        self.profile = ctx.obj.profile

        self.service_config = None
        self.hmac_key: bytes
        self.tokens: dict
        self.license_api = None
        self.license_bt = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        # Title is a slug, example: `/tv/the-office/4902514835143843112`.
        res = self.session.get(
            url=self.config["endpoints"]["node"],
            params={
                "slug": self.title,
                "represent": "(items(items))"
            },
            headers={
                "Accept": "*",
                "Referer": f"https://www.peacocktv.com/watch/asset{self.title}",
                "X-SkyOTT-Device": self.config["client"]["device"],
                "X-SkyOTT-Platform": self.config["client"]["platform"],
                "X-SkyOTT-Proposition": self.config["client"]["proposition"],
                "X-SkyOTT-Provider": self.config["client"]["provider"],
                "X-SkyOTT-Territory": self.config["client"]["territory"],
                "X-SkyOTT-Language": "en"
            }
        )
        if not res.ok:
            self.log.exit(f" - HTTP Error {res.status_code}: {res.reason}")
            raise
        data = res.json()
        if self.movie:
            raise NotImplementedError("Movies have not been implemented")
        titles = []
        for season in data["relationships"]["items"]["data"]:
            for episode in season["relationships"]["items"]["data"]:
                titles.append(episode)
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=data["attributes"]["title"],
            year=x["attributes"].get("year"),
            season=x["attributes"].get("seasonNumber"),
            episode=x["attributes"].get("episodeNumber"),
            episode_name=x["attributes"].get("title"),
            original_lang="en",  # TODO: Don't assume
            source=self.ALIASES[0],
            service_data=x
        ) for x in titles]

    def get_tracks(self, title: Title) -> Tracks:
        content_id = title.service_data["attributes"]["formats"]["HD"]["contentId"]
        variant_id = title.service_data["attributes"]["providerVariantId"]

        sky_headers = {
            # order of these matter!
            "X-SkyOTT-Agent": ".".join([
                self.config["client"]["proposition"].lower(),
                self.config["client"]["device"].lower(),
                self.config["client"]["platform"].lower()
            ]),
            "X-SkyOTT-PinOverride": "false",
            "X-SkyOTT-Provider": self.config["client"]["provider"],
            "X-SkyOTT-Territory": self.config["client"]["territory"],
            "X-SkyOTT-UserToken": self.tokens["userToken"]
        }

        body = json.dumps({
            "device": {
                # maybe get these from the config endpoint?
                "capabilities": [
                    {
                        "protection": "WIDEVINE",
                        "container": "ISOBMFF",
                        "transport": "DASH",
                        "acodec": "AAC",
                        "vcodec": "H264"
                    },
                    {
                        "protection": "NONE",
                        "container": "ISOBMFF",
                        "transport": "DASH",
                        "acodec": "AAC",
                        "vcodec": "H264"
                    }
                ],
                "maxVideoFormat": "HD",
                "model": self.config["client"]["platform"],
                "hdcpEnabled": "true"
            },
            "client": {
                "thirdParties": ["FREEWHEEL", "YOSPACE"]  # CONVIVA
            },
            "contentId": content_id,
            "providerVariantId": variant_id,
            "parentalControlPin": "null"
        }, separators=(",", ":"))

        manifest = self.session.post(
            url=self.config["endpoints"]["vod"],
            data=body,
            headers=dict(**sky_headers, **{
                "Accept": "application/vnd.playvod.v1+json",
                "Content-Type": "application/vnd.playvod.v1+json",
                "X-Sky-Signature": self.create_signature_header(
                    method="POST",
                    path="/video/playouts/vod",
                    sky_headers=sky_headers,
                    body=body,
                    timestamp=int(time.time())
                )
            })
        ).json()
        if "errorCode" in manifest:
            self.log.exit(f" - An error occurred: {manifest['description']} [{manifest['errorCode']}]")
            raise

        self.license_api = manifest["protection"]["licenceAcquisitionUrl"]
        self.license_bt = manifest["protection"]["licenceToken"]

        tracks = Tracks.from_mpd(
            uri=manifest["asset"]["endpoints"][0]["url"],
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
            headers={
                "Accept": "*",
                "X-Sky-Signature": self.create_signature_header(
                    method="POST",
                    path="/" + self.license_api.split("://", 1)[1].split("/", 1)[1],
                    sky_headers={},
                    body="",
                    timestamp=int(time.time())
                )
            },
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({"Origin": "https://www.peacocktv.com"})
        self.log.info("Getting Peacock Client configuration")
        if self.config["client"]["platform"] != "PC":
            self.service_config = self.session.get(
                url=self.config["endpoints"]["config"].format(
                    territory=self.config["client"]["territory"],
                    provider=self.config["client"]["provider"],
                    proposition=self.config["client"]["proposition"],
                    device=self.config["client"]["platform"],
                    version=self.config["client"]["config_version"],
                )
            ).json()
        self.hmac_key = bytes(self.config["security"]["signature_hmac_key_v4"], "utf-8")
        self.log.info("Getting Authorization Tokens")
        self.tokens = self.get_tokens()
        self.log.info("Verifying Authorization Tokens")
        if not self.verify_tokens():
            self.log.exit(" - Failed! Cookies might be outdated.")
            raise

    @staticmethod
    def calculate_sky_header_md5(headers: dict) -> str:
        if len(headers.items()) > 0:
            headers_str = "\n".join(list(map(lambda x: f"{x[0].lower()}: {x[1]}", headers.items()))) + "\n"
        else:
            headers_str = "{}"
        return str(hashlib.md5(headers_str.encode()).hexdigest())

    @staticmethod
    def calculate_body_md5(body: str) -> str:
        return str(hashlib.md5(body.encode()).hexdigest())

    def calculate_signature(self, msg: str) -> str:
        digest = hmac.new(self.hmac_key, bytes(msg, "utf-8"), hashlib.sha1).digest()
        return str(base64.b64encode(digest), "utf-8")

    def create_signature_header(self, method: str, path: str, sky_headers: dict, body: str, timestamp: int) -> str:
        data = "\n".join([
            method.upper(),
            path,
            "",  # important!
            self.config["client"]["client_sdk"],
            "1.0",
            self.calculate_sky_header_md5(sky_headers),
            str(timestamp),
            self.calculate_body_md5(body)
        ]) + "\n"

        signature_hmac = self.calculate_signature(data)

        return self.config["security"]["signature_format"].format(
            client=self.config["client"]["client_sdk"],
            signature=signature_hmac,
            timestamp=timestamp
        )

    def get_tokens(self) -> dict:
        # try get cached tokens
        tokens_cache_path = self.get_cache("tokens_{profile}_{id}.json".format(
            profile=self.profile,
            id=self.config["client"]["id"]
        ))
        if tokens_cache_path.is_file():
            tokens = json.loads(tokens_cache_path.read_text(encoding="utf8"))
            tokens_expiration = tokens.get("tokenExpiryTime", None)
            if tokens_expiration and datetime.strptime(tokens_expiration, "%Y-%m-%dT%H:%M:%S.%fZ") > datetime.now():
                return tokens
        # Get all SkyOTT headers
        sky_headers = {
            # order of these matter!
            "X-SkyOTT-Agent": ".".join([
                self.config["client"]["proposition"],
                self.config["client"]["device"],
                self.config["client"]["platform"]
            ]).lower(),
            "X-SkyOTT-Device": self.config["client"]["device"],
            "X-SkyOTT-Platform": self.config["client"]["platform"],
            "X-SkyOTT-Proposition": self.config["client"]["proposition"],
            "X-SkyOTT-Provider": self.config["client"]["provider"],
            "X-SkyOTT-Territory": self.config["client"]["territory"]
        }
        # Call personas endpoint to get the accounts personaId
        personas = self.session.get(
            url=self.config["endpoints"]["personas"],
            headers=dict(**sky_headers, **{
                "Accept": "application/vnd.persona.v1+json",
                "Content-Type": "application/vnd.persona.v1+json",
                "X-SkyOTT-TokenType": self.config["client"]["auth_scheme"]
            })
        ).json()
        if "message" and "code" in personas:
            self.log.exit(f" - Unable to get persona ID: {personas['message']} [{personas['code']}]")
            raise
        persona = personas["personas"][0]["personaId"]
        # Craft the body data that will be sent to the tokens endpoint, being minified and order matters!
        body = json.dumps({
            "auth": {
                "authScheme": self.config["client"]["auth_scheme"],
                "authIssuer": self.config["client"]["auth_issuer"],
                "provider": self.config["client"]["provider"],
                "providerTerritory": self.config["client"]["territory"],
                "proposition": self.config["client"]["proposition"],
                "personaId": persona
            },
            "device": {
                "type": self.config["client"]["device"],
                "platform": self.config["client"]["platform"],
                "id": self.config["client"]["id"],
                "drmDeviceId": self.config["client"]["drm_device_id"]
            }
        }, separators=(",", ":"))
        # Ok, we are ready to call the tokens endpoint, finally...
        tokens = self.session.post(
            url=self.config["endpoints"]["tokens"],
            headers=dict(**sky_headers, **{
                "Accept": "application/vnd.tokens.v1+json",
                "Content-Type": "application/vnd.tokens.v1+json",
                "X-Sky-Signature": self.create_signature_header(
                    method="POST",
                    path="/auth/tokens",
                    sky_headers=sky_headers,
                    body=body,
                    timestamp=int(time.time())
                )
            }),
            data=body
        ).json()
        # let's cache the tokens
        tokens_cache_path.parent.mkdir(parents=True, exist_ok=True)
        tokens_cache_path.write_text(json.dumps(tokens), encoding="utf8")
        # finally return the tokens
        return tokens

    def verify_tokens(self) -> bool:
        """Verify the tokens by calling the /auth/users/me endpoint and seeing if it works"""
        sky_headers = {
            # order of these matter!
            "X-SkyOTT-Device": self.config["client"]["device"],
            "X-SkyOTT-Platform": self.config["client"]["platform"],
            "X-SkyOTT-Proposition": self.config["client"]["proposition"],
            "X-SkyOTT-Provider": self.config["client"]["provider"],
            "X-SkyOTT-Territory": self.config["client"]["territory"],
            "X-SkyOTT-UserToken": self.tokens["userToken"]
        }
        me = self.session.get(
            url=self.config["endpoints"]["me"],
            headers=dict(**sky_headers, **{
                "Accept": "application/vnd.userinfo.v2+json",
                "Content-Type": "application/vnd.userinfo.v2+json",
                "X-Sky-Signature": self.create_signature_header(
                    method="GET",
                    path="/auth/users/me",
                    sky_headers=sky_headers,
                    body="",
                    timestamp=int(time.time())
                )
            })
        )
        return me.status_code == 200
