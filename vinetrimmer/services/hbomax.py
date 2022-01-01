from __future__ import annotations

import json
import re
from hashlib import md5
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Tracks, VideoTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match


class HBOMax(BaseService):
    """
    Service code for HBO's HBO MAX streaming service (https://hbomax.com).

    \b
    Authorization: Credentials
    Security: UHD@L3, doesn't seem to care about releases.

    \b
    Tips: The library of contents can be viewed without logging in at https://play.hbomax.com

    TODO: Implement token caching to reduce the amount of times the login call is made
    """

    ALIASES = ["HMAX", "hbomax"]
    GEOFENCE = ["us"]

    VIDEO_CODEC_MAP = {
        "H264": ["avc1"],
        "H265": ["hvc1", "dvh1"]
    }

    AUDIO_CODEC_MAP = {
        "AAC": "mp4a",
        "AC3": "ac-3",
        "EC3": "ec-3"
    }

    @staticmethod
    @click.command(name="HBOMax", short_help="https://hbomax.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> HBOMax:
        return HBOMax(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]
        self.range = ctx.parent.params["range_"]
        self.lang = ctx.parent.params["lang"]

        self.license_api: str
        self.client_grant: dict
        self.auth_grant: dict
        self.profile_id: str
        self.hevc_hdr_group = ("", 0)

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        res = self.session.get(
            url=self.config["endpoints"]["manifest"].format(title_id=self.title),
            params={
                "device-code": self.config["device"]["name"],
                "product-code": "hboMax",
                "api-version": "v9",
                "country-code": "us",
                "profile-type": "default",
                "client-version": self.config["client"]["desktop"]["client_version"],
                "signed-in": "true",
                "content-space": "hboMaxSvodExperience"
            },
            headers={
                "Authorization": f"{self.auth_grant['token_type']} {self.auth_grant['access_token']}"
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            raise ValueError(f"Failed to load title manifest: {res.text}")

        for i, e in enumerate(data):
            data[i]["body"]["id"] = e["id"]
            data[i] = data[i]["body"]
        main_ref = self.map_references(next(x for x in data if x["id"] == self.title), data)

        if "message" in main_ref:
            self.log.exit(f" - Error from HBO MAX: {main_ref['message']}")
            raise

        if self.movie:
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=main_ref["titles"]["full"],
                year=main_ref["releaseYear"],
                original_lang=next(
                    # TODO: Is this really the original title lang? or manifest lang?
                    x["originalAudioLanguage"]
                    for x in main_ref["edits"]["edit"]
                    if x.get("originalAudioLanguage")
                ),
                source=self.ALIASES[0],
                service_data=main_ref
            )

        if "items" in main_ref:
            main_ref = main_ref["items"]["content_details"][0]["items"]
            if "series" in main_ref:
                main_ref = main_ref["series"][0]
            elif "episode" in main_ref:
                main_ref = {"episodes": main_ref}
            else:
                self.log.exit(" - Unsupported content type")
                raise

        return [
            Title(
                id_=self.title,
                type_=Title.Types.TV,
                name=episode["seriesTitles"]["full"],
                season=season.get("seasonNumber", 1),
                episode=episode.get("numberInSeason") or episode.get("numberInSeries"),
                episode_name=episode["titles"]["full"],
                # TODO: Is this really the original title lang? or manifest lang?
                original_lang=edit.get("originalAudioLanguage"),
                source=self.ALIASES[0],
                service_data=edit
            )
            for season in main_ref.get("seasons", {}).get("season", [main_ref])
            for episode in season["episodes"]["episode"]
            for edit in episode["edits"]["edit"]
        ]

    def get_tracks(self, title: Title) -> Tracks:
        self.refresh()  # make sure the tokens are not expired

        if title.service_data.get("references"):
            # only want viewable reference, rest causes unnecessary requests if left in
            title.service_data["references"] = {"viewable": title.service_data["references"]["viewable"]}
        else:
            title.service_data["references"] = {"viewable": title.service_data["id"]}

        title.service_data = self.map_references(title.service_data, [], list_refs_only=False)
        title_data = list(title.service_data["viewable"].values())[0][0]
        title_data["edits"] = sorted(
            title_data["edits"]["edit"],
            key=lambda e: is_close_match(e["originalAudioLanguage"], self.lang),
            reverse=True
        )
        manifest = {}
        for n, edit in enumerate(title_data["edits"]):
            res = self.session.post(
                url=self.config["endpoints"]["content"],
                json=[
                    {
                        "id": edit["references"]["video"],
                        "headers": {
                            "x-hbo-device-model": self.session.headers["User-Agent"],
                            "x-hbo-download-quality": "HIGHEST",
                            "x-hbo-device-code-override": "DESKTOP",
                            "x-hbo-video-encodes": f"{self.vcodec}|DASH|WDV"
                        }
                    }
                ],
                headers={
                    "Authorization": f"{self.auth_grant['token_type']} {self.auth_grant['access_token']}"
                }
            ).json()[0]["body"]
            if "manifests" not in res:
                self.log.exit(f" - Failed! HBO MAX returned an error: {res['message']} [{res.get('code')}]")
                raise
            res = res["manifests"]
            res = [x for x in res if x["type"] == "urn:video:main"][0]
            if n == 0:
                manifest = res
            else:
                manifest["audioTracks"].extend(res["audioTracks"])
                manifest["textTracks"].extend(res["textTracks"])

        self.license_api = manifest["drm"]["licenseUrl"]

        tracks = Tracks.from_mpd(
            uri=manifest["url"],
            session=self.session,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

        if self.vcodec:
            tracks.videos = [
                x for x in tracks.videos
                if x.codec[:4] in self.VIDEO_CODEC_MAP[self.vcodec]
            ]

        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio
                if x.codec[:4] == self.AUDIO_CODEC_MAP[self.acodec]
            ]

        if "textTracks" in manifest:
            for sub in manifest["textTracks"]:
                if sub["type"] in ["Subtitles-Burned"]:
                    # for some reason there's a pseudo sub track when there's subs burned into the video
                    continue
                sub["displayName"] = sub["displayName"].replace(" CC", "")
                if sub["type"] == "ClosedCaptions":
                    # CC tracks as per usual are actually SDH
                    sub["displayName"] += " (SDH)"
                tracks.add(TextTrack(
                    id_=md5(sub["url"].encode()).hexdigest()[0:6],
                    source=self.ALIASES[0],
                    url=sub["url"],
                    # metadata
                    codec="ttml",
                    language=sub["language"],
                    is_original_lang=title.original_lang and is_close_match(sub["language"], [title.original_lang])
                ))

        for track in tracks:
            track.needs_proxy = True
            if isinstance(track, VideoTrack):
                track.hdr10 = (track.codec[0:4] in ("hvc1", "hev1") and
                               track.codec[self.hevc_hdr_group[1]] == self.hevc_hdr_group[0])
                track.dv = track.codec[0:4] in ("dvh1", "dvhe")

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, **_: Any) -> bytes:
        return self.session.post(
            url=self.license_api,
            params={
                "keygen": "playready",
                "drmKeyVersion": "2"
            },
            headers={
                "Authorization": f"{self.auth_grant['token_type']} {self.auth_grant['access_token']}"
            },
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "Accept": "application/vnd.hbo.v9.full+json",
            "X-Hbo-Client-Version": self.config["client"]["desktop"]["version"],
            "X-Hbo-Device-Name": self.config["device"]["name"],
            "X-Hbo-Device-Os-Version": self.config["device"]["os_version"]
        })
        if not self.title.startswith("urn:"):
            self.title = f"urn:hbo:{'feature' if self.movie else 'series'}:{self.title}"
        self.log.info("Logging into HBO MAX")
        self.client_grant = self.get_client_token()
        self.log.info(
            " + Obtained client_grant grant token "
            f"({self.client_grant['token_type']} that expires in "
            f"{int(self.client_grant['expires_in'] / 60 / 60)} hours)"
        )
        self.auth_grant = self.get_auth_grant()
        self.log.info(
            " + Obtained user_name_password grant token "
            f"({self.auth_grant['token_type']} that expires in "
            f"{int(self.auth_grant['expires_in'] / 60)} minutes)"
        )
        self.profile_id = self.get_profile_id()
        self.log.info(f" + Obtained profile ID: {self.profile_id}")

        if self.range == "HDR10" or (self.vcodec == "H265" and self.range is None):
            self.log.info("Obtaining HEVC HDR Codec Group Information")
            hevc_hdr = re.search(
                r'"hvc1":return"([\w\d]?)"===e\.codecs\[\d?]\.charAt\((\d?)\)',
                self.session.get("https://play.hbomax.com/js/app.js").text.replace(" ", "")
            )
            if not hevc_hdr:
                self.log.exit(" - Failed, did HBO Max change the JS?")
                raise
            self.hevc_hdr_group = hevc_hdr.group(1), int(hevc_hdr.group(2))
            self.log.info(f" + Obtained: [{self.hevc_hdr_group}]")

    def get_client_token(self) -> dict:
        res = self.session.post(
            url=self.config["endpoints"]["tokens"],
            json={
                "client_id": self.config["client"]["android"]["id"],
                "client_secret": self.config["client"]["android"]["id"],
                "scope": "browse video_playback_free",
                "grant_type": "client_credentials",
                "deviceSerialNumber": self.config["device"]["serial_number"],
                "clientDeviceData": {
                    "paymentProviderCode": "blackmarket"
                }
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to retrieve temp client token, response was not JSON: {res.text}")
            raise
        if "access_token" not in data:
            self.log.exit(f" - No access_token in temp client token response: {data}")
            raise
        return data

    def get_auth_grant(self) -> dict:
        if not self.credentials:
            self.log.exit(" - No credentials provided, unable to log in.")
            raise
        r = self.session.post(
            url=self.config["endpoints"]["tokens"],
            json={
                "scope": "browse video_playback device elevated_account_management",
                "grant_type": "user_name_password",
                "username": self.credentials.username,
                "password": self.credentials.password
            },
            headers={
                "Authorization": f"{self.client_grant['token_type']} {self.client_grant['access_token']}"
            }
        )
        try:
            res = r.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to retrieve auth grant token, response was not JSON: {r.text}")
            raise
        if "code" in res and res["code"] == "invalid_credentials":
            self.log.exit(" - The profile's login credentials are invalid!")
            raise
        if "access_token" not in res:
            self.log.exit(f" - No access_token in auth grant token response: {res}")
            raise
        return res

    def get_profile_id(self) -> str:
        res = self.session.post(
            url=self.config["endpoints"]["content"],
            json=[{"id": "urn:hbo:user:me"}],
            headers={
                "Authorization": f"{self.auth_grant['token_type']} {self.auth_grant['access_token']}",
                "X-Hbo-Client-Version": self.config["client"]["android"]["version"]
            }
        )
        try:
            data = res.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to retrieve profile ID, response was not JSON: {res.text}")
            raise
        return data[0]["body"]["userId"]

    def refresh(self) -> None:
        r = self.session.post(
            url=self.config["endpoints"]["tokens"],
            json={
                "scope": "browse video_playback device",
                "grant_type": "refresh_token",
                "refresh_token": self.auth_grant['refresh_token']
            },
            headers={
                "Authorization": f"{self.client_grant['token_type']} {self.client_grant['refresh_token']}"
            }
        )
        try:
            res = r.json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to refresh access token, response was not JSON: {r.text}")
            raise
        if "access_token" not in res:
            self.log.exit(f" - No access_token in refresh response: {res}")
            raise
        self.auth_grant = res
        self.log.info(
            " + Refreshed user_name_password grant token "
            f"({self.auth_grant['token_type']} that expires in "
            f"{int(self.auth_grant['expires_in'] / 60)} minutes)"
        )

    def map_references(self, root: dict, data: list[dict], list_refs_only: bool = True) -> dict:
        """
        Recursively map a reference ID URN with its associated data.

        Parameters:
            root: The primary dictionary from the data parameter to use and return. This
                should be the dictionary you intend to actually use. It can be as low or
                high level nesting as you want, it doesn't care.
            data: A list of dictionaries in which each dictionary should have an "id" key
                reference ID URN that should contain the related data for that reference ID.
                It should contain one dict per reference URN that is referenced in the
                `root` dictionary.
                If data for a reference ID URN cannot be found, it will start a manifest
                endpoint request for that reference ID URN and use its returned data.
        """
        if root.get("references"):
            for table_key, table_value in root["references"].copy().items():
                if not isinstance(table_value, list):
                    if list_refs_only:
                        # most likely not a reference needing to be mapped (yet)
                        # these tend to begin a large list of more data to be mapped that goes TOO deep
                        # if that's the case, map references with list_refs_only=True, then list_refs_only=False
                        # one deep-nested dict object (so that there's way less recursion to do, but same result).
                        continue
                    else:
                        table_value = [table_value]
                if not root.get(table_key):
                    root[table_key] = {}
                for ref_id in table_value:
                    ref_key = ref_id.split(":")[2].replace("-", "_")
                    if not root[table_key].get(ref_key):
                        root[table_key][ref_key] = []
                    ref_data: dict = next((x for x in data if x["id"] == ref_id), {})
                    if not ref_data:
                        r = self.session.get(
                            url=self.config["endpoints"]["manifest"].format(title_id=ref_id),
                            params={
                                "device-code": self.config["device"]["name"],
                                "product-code": "hboMax",
                                "api-version": "v9",
                                "country-code": "us",
                                "profile-type": "default",
                                "signed-in": "true"
                            },
                            headers={
                                "Authorization": f"{self.auth_grant['token_type']} {self.auth_grant['access_token']}"
                            }
                        ).json()
                        for i, e in enumerate(r):
                            r[i]["body"]["id"] = e["id"]
                            r[i] = r[i]["body"]
                        data.extend(r)
                        ref_data = next(x for x in r if x["id"] == ref_id)
                    ref_data = self.map_references(ref_data, data)
                    root[table_key][ref_key].append(ref_data)
                del root["references"][table_key]
            if not root["references"]:
                del root["references"]
        return root
