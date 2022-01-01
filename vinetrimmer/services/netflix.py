from __future__ import annotations

import base64
import json
import random
import re
import string
import subprocess
import time
from typing import Any, NoReturn, Optional, Union

import click
import jsonpickle
from click import Context
from langcodes import Language
from pymp4.parser import Box

from vinetrimmer.objects import AudioTrack, MenuTrack, TextTrack, Title, Track, Tracks, VideoTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match
from vinetrimmer.utils.collections import as_list, flatten
from vinetrimmer.utils.MSL import MSL
from vinetrimmer.utils.MSL.schemes import KeyExchangeSchemes
from vinetrimmer.utils.MSL.schemes.UserAuthentication import UserAuthentication
from vinetrimmer.utils.Widevine.device import LocalDevice


class Netflix(BaseService):
    """
    Service code for the Netflix streaming service (https://netflix.com).

    \b
    Authorization: Cookies if ChromeCDM, Cookies + Credentials otherwise.
    Security: UHD@L1 HD@L3*, heavily monitors UHD, but doesn't seem to care about <= FHD.

    *MPL: FHD with Android L3, sporadically available with ChromeCDM
     HPL: 1080p with ChromeCDM, 720p/1080p with other L3 (varies per title)

    \b
    Tips: - The library of contents as well as regional availability is available at https://unogs.com
            However, Do note that Netflix locked everyone out of being able to automate the available data
            meaning the reliability and amount of information may be reduced.
          - You could combine the information from https://unogs.com with https://justwatch.com for further data
          - The ESN you choose is important to match the CDM you provide
          - Need 4K manifests? Try use an Nvidia Shield-based ESN with the system ID changed to yours. The "shield"
            term gives it 4K, and the system ID passes the key exchange verifications as it matches your CDM. They
            essentially don't check if the device is actually a Shield by the verified system ID.
          - ESNs capable of 4K manifests can provide HFR streams for everything other than H264. Other ESNs can
            seemingly get HFR from the VP9 P2 profile or higher. I don't think H264 ever gets HFR.

    TODO: Implement the MSL v2 API response's `crop_x` and `crop_y` values with Matroska's cropping metadata
    """

    ALIASES = ["NF", "netflix"]
    NF_LANG_MAP = {
        "European Spanish": "es-150"
    }

    @staticmethod
    @click.command(name="Netflix", short_help="https://netflix.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.option("--meta-lang", type=str, help="Language to use for metadata")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Netflix:
        return Netflix(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool, meta_lang: Optional[str]):
        self.title = title
        self.movie = movie
        self.meta_lang = meta_lang

        assert ctx.parent is not None

        if ctx.parent.params["proxy"] and len("".join(i for i in ctx.parent.params["proxy"] if not i.isdigit())) == 2:
            self.GEOFENCE.append(ctx.parent.params["proxy"])

        super().__init__(ctx)

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]
        self.range = ctx.parent.params["range_"]
        self.quality = ctx.parent.params["quality"]

        self.cdm = ctx.obj.cdm

        # general
        self.download_proxied = len(self.GEOFENCE) > 0  # needed if the title is unavailable at home ip
        self.profiles: Union[dict, list[str]] = []

        # MSL
        self.msl: Optional[MSL] = None
        self.esn: Optional[str] = None
        self.userauthdata: Optional[UserAuthentication] = None

        # Web API values
        self.react_context: dict = {}

        # DRM/Manifest values
        self.session_id = None

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        assert self.title is not None
        metadata = self.get_metadata(self.title)["video"]
        if self.movie:
            titles = [Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=metadata["title"],
                year=metadata["year"],
                source=self.ALIASES[0],
                service_data=metadata
            )]
        else:
            episodes = [episode for season in [
                [dict(x, **{"season": season["seq"]}) for x in season["episodes"]]
                for season in metadata["seasons"]
            ] for episode in season]
            titles = [Title(
                id_=self.title,
                type_=Title.Types.TV,
                name=metadata["title"],
                season=episode.get("season"),
                episode=episode.get("seq"),
                episode_name=episode.get("title"),
                source=self.ALIASES[0],
                service_data=episode
            ) for episode in episodes]

        manifest = self.get_manifest(titles[0], self.profiles)
        original_language = self.get_original_language(manifest)

        for title in titles:
            title.original_lang = original_language

        return titles

    def get_tracks(self, title: Title) -> Tracks:
        if self.vcodec == "H264":
            tracks = Tracks()
            # if h264, get both MPL and HPL tracks as they alternate in terms of bitrate rank
            for profile in ("MPL", "HPL"):
                if profile == "MPL" and self.cdm.device.type == LocalDevice.Types.CHROME:
                    # Chrome can't request MPL alone, but it can request both
                    # (in which case NF decides which one to return) or just HPL
                    manifest = self.get_manifest(
                        title,
                        self.config["profiles"]["H264"]["MPL"] +
                        self.config["profiles"]["H264"]["HPL"]
                    )
                else:
                    manifest = self.get_manifest(title, self.config["profiles"]["H264"][profile])
                manifest_tracks = self.manifest_as_tracks(manifest, title.original_lang)
                license_url = manifest["links"]["license"]["href"]

                if self.cdm.device.security_level == 3 and self.cdm.device.type == LocalDevice.Types.ANDROID:
                    max_quality = max(x.height for x in manifest_tracks.videos)
                    if profile == "MPL" and max_quality >= 720:
                        manifest_sd = self.get_manifest(title, self.config["profiles"]["H264"]["BPL"])
                        license_url_sd = manifest_sd["links"]["license"]["href"]
                        if "SD_LADDER" in manifest_sd["video_tracks"][0]["streams"][0]["tags"]:
                            # will throw kid mismatch, skip -nyu
                            continue
                        license_url = license_url_sd
                    if profile == "HPL" and max_quality >= 1080:
                        if "SEGMENT_MAP_2KEY" in manifest["video_tracks"][0]["streams"][0]["tags"]:
                            # 1080p license restricted from Android L3, 720p license will work for 1080p -nyu
                            manifest_720 = self.get_manifest(title, self.config["profiles"]["H264"]["720p"])
                            license_url = manifest_720["links"]["license"]["href"]
                        else:
                            # no 2key? then it will throw kid mismatch, skip -nyu
                            continue

                for track in manifest_tracks:
                    if track.encrypted:
                        track.extra["license_url"] = license_url
                tracks.add(manifest_tracks, warn_only=True)
            return tracks
        manifest = self.get_manifest(title, self.profiles)
        manifest_tracks = self.manifest_as_tracks(manifest, title.original_lang)
        license_url = manifest["links"]["license"]["href"]
        for track in manifest_tracks:
            if track.encrypted:
                track.extra["license_url"] = license_url
            if isinstance(track, VideoTrack):
                # TODO: Needs something better than this
                track.hdr10 = track.codec.split("-")[1] == "hdr"  # hevc-hdr, vp9-hdr
                track.dv = track.codec.startswith("hevc-dv")
        return manifest_tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> str:
        return self.config["certificate"]

    def license(self, challenge: bytes, track: Track, session_id: bytes, **_: Any) -> str:
        if not self.msl:
            self.log.exit(" - Cannot get license, MSL Client has not been created yet.")
            raise
        header, payload_data = self.msl.send_message(
            endpoint=self.config["endpoints"]["licence"],
            params={},
            application_data={
                "version": 2,
                "url": track.extra["license_url"],
                "id": 15429961788811,  # ?
                "esn": self.esn,
                "languages": ["en-US"],
                "uiVersion": self.react_context["serverDefs"]["data"]["uiVersion"],
                "clientVersion": self.react_context["playerModel"]["data"]["config"]["core"]["initParams"][
                    "clientVersion"],
                "params": [{
                    "sessionId": base64.standard_b64encode(session_id).decode("utf-8"),
                    "clientTime": int(time.time()),
                    "challengeBase64": base64.b64encode(challenge).decode("utf-8"),  # expects base64
                    "xid": str(int((int(time.time()) + 0.1612) * 1000))  # ?
                }],
                "echo": "sessionId"
            },
            userauthdata=self.userauthdata
        )
        if not payload_data:
            self.log.exit(f" - Failed to get license: {header['message']} [{header['code']}]")
            raise
        if "error" in payload_data[0]:
            self.log.exit(f" - Failed to get license: {payload_data}")
        return payload_data[0]["licenseResponseBase64"]

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({"Origin": "https://netflix.com"})
        self.profiles = self.get_profiles()
        self.log.info("Initializing a Netflix MSL Client")
        # Grab ESN based on CDM from secrets if no ESN argument provided
        if "esn_map" in self.config and str(self.cdm.device.system_id) in self.config["esn_map"]:
            self.esn = self.config["esn_map"][str(self.cdm.device.system_id)]
        if not self.esn:
            self.log.exit(" - No ESN specified")
            raise
        self.log.info(f" + ESN: {self.esn}")
        scheme = {
            LocalDevice.Types.CHROME: KeyExchangeSchemes.AsymmetricWrapped,
            LocalDevice.Types.ANDROID: KeyExchangeSchemes.Widevine
        }[self.cdm.device.type]
        self.msl = MSL.handshake(
            scheme=scheme,
            session=self.session,
            endpoint=self.config["endpoints"]["manifest"],
            sender=self.esn,
            cdm=self.cdm,
            msl_keys_path=self.get_cache("msl_{id}_{esn}_{scheme}.json".format(
                id=self.cdm.device.system_id,
                esn=self.esn,
                scheme=scheme
            ))
        )
        self.log.info(f" + Handshaked with MSL with the scheme: {scheme}")
        if not self.session.cookies:
            self.log.exit(" - No cookies provided, cannot log in.")
            raise
        if self.cdm.device.type == LocalDevice.Types.CHROME:
            self.userauthdata = UserAuthentication.NetflixIDCookies(
                netflixid=self.session.cookies.get_dict()["NetflixId"],
                securenetflixid=self.session.cookies.get_dict()["SecureNetflixId"]
            )
        else:
            if not self.credentials:
                self.log.exit(" - Credentials are required for Android CDMs, and none were provided.")
                raise
            # need to get cookies via an android-like way
            # outdated
            # self.android_login(credentials.username, credentials.password)
            # need to use EmailPassword for userauthdata, it specifically checks for this
            self.userauthdata = UserAuthentication.EmailPassword(
                email=self.credentials.username,
                password=self.credentials.password
            )
        self.log.info(" + Created MSL UserAuthentication data")
        self.react_context = self.get_react_context()
        self.log.info(" + Obtained Netflix Webpage React Context data")

    def get_profiles(self) -> Union[dict, list[str]]:
        if self.range in ("HDR10", "DV") and self.vcodec not in ("H265", "VP9"):
            self.vcodec = "H265"
        profiles = self.config["profiles"][self.vcodec]
        if self.range and self.range.replace("DV", "DV5") in profiles:
            return profiles[self.range.replace("DV", "DV5")]
        return profiles

    def get_react_context(self) -> dict:
        """
        Netflix uses a "BUILD_IDENTIFIER" value on some API's, e.g. the Shakti (metadata) API.
        This value isn't given to the user through normal means so REGEX is needed.
        It's obtained by grabbing the body of a logged-in netflix homepage.
        The value changes often but doesn't often matter if it's only a bit out of date.

        It also uses a Client Version for various MPL calls.

        :returns: reactContext nodejs-parsed json-loaded dictionary
        """
        cache_loc = self.get_cache("web_data.json")
        if not cache_loc.is_file():
            src = self.session.get("https://www.netflix.com/browse").text
            match = re.search(r"netflix.reactContext = ({.+});</script><script>window.", src, re.MULTILINE)
            if not match:
                self.log.exit(" - Failed to retrieve reactContext data, cookies might be outdated.")
                raise
            react_context_raw = match.group(1)
            node = subprocess.Popen(["node", "-"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            stdout, _ = node.communicate(f"console.log(JSON.stringify({react_context_raw}))".encode("utf-8"))
            react_context = json.loads(stdout.decode("utf-8"))["models"]
            react_context["requestHeaders"]["data"] = {
                re.sub(r"\B([A-Z])", r"-\1", k): str(v) for k, v in react_context["requestHeaders"]["data"].items()
            }
            react_context["abContext"]["data"]["headers"] = {
                k: str(v) for k, v in react_context["abContext"]["data"]["headers"].items()
            }
            react_context["requestHeaders"]["data"] = {
                k: str(v) for k, v in react_context["requestHeaders"]["data"].items()
            }
            react_context["playerModel"]["data"]["config"]["core"]["initParams"]["clientVersion"] = (
                react_context["playerModel"]["data"]["config"]["core"]["assets"]["core"].split("-")[-1][:-3]
            )
            cache_loc.parent.mkdir(parents=True, exist_ok=True)
            cache_loc.write_text(jsonpickle.encode(react_context), encoding="utf8")
            return react_context
        data = jsonpickle.decode(cache_loc.read_text(encoding="utf8"))
        return data

    def get_metadata(self, title_id: str) -> dict:
        """
        Obtain Metadata information about a title by it's ID.
        :param title_id: Title's ID.
        :returns: Title Metadata.
        """

        """
        # Wip non-working code for the newer shakti metadata replacement
        metadata = self.session.post(
            url=self.config["endpoints"]["website"].format(
                build_id=self.react_context["serverDefs"]["data"]["BUILD_IDENTIFIER"]
            ),
            params={
                # features
                "webp": self.react_context["browserInfo"]["data"]["features"]["webp"],
                "drmSystem": self.config["configuration"]["drm_system"],
                # truths
                "isVolatileBillboardsEnabled": self.react_context["truths"]["data"]["volatileBillboardsEnabled"],
                "routeAPIRequestsThroughFTL": self.react_context["truths"]["data"]["routeAPIRequestsThroughFTL"],
                "isTop10Supported": self.react_context["truths"]["data"]["isTop10Supported"],
                "categoryCraversEnabled": self.react_context["truths"]["data"]["categoryCraversEnabled"],
                "hasVideoMerchInBob": self.react_context["truths"]["data"]["hasVideoMerchInBob"],
                "persoInfoDensity": self.react_context["truths"]["data"]["enablePersoInfoDensityToggle"],
                "contextAwareImages": self.react_context["truths"]["data"]["contextAwareImages"],
                # ?
                "falcor_server": "0.1.0",
                "withSize": True,
                "materialize": True,
                "original_path": quote_plus(
                    f"/shakti/{self.react_context['serverDefs']['data']['BUILD_IDENTIFIER']}/pathEvaluator"
                )
            },
            headers=dict(
                **self.react_context["abContext"]["data"]["headers"],
                **{
                    "X-Netflix.Client.Request.Name": "ui/falcorUnclassified",
                    "X-Netflix.esn": self.react_context["esnGeneratorModel"]["data"]["esn"],
                    "x-netflix.nq.stack": self.react_context["serverDefs"]["data"]["stack"],
                    "x-netflix.request.client.user.guid": (
                        self.react_context["memberContext"]["data"]["userInfo"]["guid"]
                    )
                },
                **self.react_context["requestHeaders"]["data"]
            ),
            data={
                "path": json.dumps([
                    [
                        "videos",
                        70155547,
                        [
                            "bobSupplementalMessage",
                            "bobSupplementalMessageIcon",
                            "bookmarkPosition",
                            "delivery",
                            "displayRuntime",
                            "evidence",
                            "hasSensitiveMetadata",
                            "interactiveBookmark",
                            "maturity",
                            "numSeasonsLabel",
                            "promoVideo",
                            "releaseYear",
                            "seasonCount",
                            "title",
                            "userRating",
                            "userRatingRequestId",
                            "watched"
                        ]
                    ],
                    [
                        "videos",
                        70155547,
                        "seasonList",
                        "current",
                        "summary"
                    ]
                ]),
                "authURL": self.react_context["memberContext"]["data"]["userInfo"]["authURL"]
            }
        )

        print(metadata.headers)
        print(metadata.text)
        exit()
        """

        try:
            metadata = self.session.get(
                self.react_context["playerModel"]["data"]["config"]["ui"]["initParams"]["apiUrl"] + "/metadata",
                params={
                    "movieid": title_id,
                    "drmSystem": self.config["configuration"]["drm_system"],
                    "isWatchlistEnabled": False,
                    "isShortformEnabled": False,
                    "isVolatileBillboardsEnabled": self.react_context["truths"]["data"]["volatileBillboardsEnabled"],
                    "languages": self.meta_lang
                }
            ).json()
        except json.JSONDecodeError:
            self.log.exit(f" - Failed to fetch Metadata for {title_id}, perhaps it's available in another region?")
            raise
        else:
            if "status" in metadata and metadata["status"] == "error":
                self.log.exit(
                    f" - Failed to fetch Metadata for {title_id}, cookies might be expired."
                    f" Error: {metadata['message']}"
                )
                raise
            return metadata

    def get_manifest(self, title: Title, video_profiles: Union[dict, list[str]]) -> dict:
        if isinstance(video_profiles, dict):
            video_profiles = list(video_profiles.values())
        audio_profiles = self.config["profiles"]["Audio"]
        if self.acodec:
            audio_profiles = audio_profiles[self.acodec]
        if isinstance(audio_profiles, dict):
            audio_profiles = list(audio_profiles.values())
        profiles = sorted(set(flatten(as_list(
            # as list then flatten in case any of these profiles are a list of lists
            # list(set()) used to remove any potential duplicates
            self.config["profiles"]["H264"]["BPL"],  # always required for some reason
            video_profiles,
            audio_profiles,
            self.config["profiles"]["SUBS"]
        ))))
        self.log.debug("Profiles:\n\t" + "\n\t".join(profiles))

        params = {}
        if self.cdm.device.type == LocalDevice.Types.CHROME:
            params = {
                "reqAttempt": 1,
                "reqPriority": 10,
                "reqName": "manifest",
                "clienttype": self.react_context["playerModel"]["data"]["config"]["ui"]["initParams"]["uimode"],
                "uiversion": self.react_context["serverDefs"]["data"]["BUILD_IDENTIFIER"],
                "browsername": self.react_context["playerModel"]["data"]["config"]["core"]["initParams"]["browserInfo"][
                    "name"],
                "browserversion":
                    self.react_context["playerModel"]["data"]["config"]["core"]["initParams"]["browserInfo"]["version"],
                "osname":
                    self.react_context["playerModel"]["data"]["config"]["core"]["initParams"]["browserInfo"]["os"][
                        "name"],
                "osversion":
                    self.react_context["playerModel"]["data"]["config"]["core"]["initParams"]["browserInfo"]["os"][
                        "version"]
            }

        assert self.msl is not None
        _, payload_chunks = self.msl.send_message(
            endpoint=self.config["endpoints"]["manifest"],
            params=params,
            application_data={
                "version": 2,
                "url": "/manifest",
                "id": int(time.time()),
                "esn": self.esn,
                "languages": ["en-US"],
                "uiVersion": self.react_context["playerModel"]["data"]["config"]["ui"]["initParams"]["uiVersion"],
                "clientVersion": self.react_context["playerModel"]["data"]["config"]["core"]["initParams"][
                    "clientVersion"],
                "params": {
                    "type": "standard",  # ? PREPARE
                    "viewableId": title.service_data.get("episodeId", title.service_data["id"]),
                    "profiles": profiles,
                    "flavor": "STANDARD",  # ? PRE_FETCH, SUPPLEMENTAL
                    "drmType": self.config["configuration"]["drm_system"],
                    "drmVersion": self.config["configuration"]["drm_version"],
                    "usePsshBox": True,
                    "isBranching": False,  # ? possibly for interactive titles like Minecraft Story
                    "useHttpsStreams": True,
                    "supportsUnequalizedDownloadables": True,  # ?
                    "imageSubtitleHeight": 1080,
                    "uiVersion": self.react_context["playerModel"]["data"]["config"]["ui"]["initParams"]["uiVersion"],
                    "uiPlatform": self.react_context["playerModel"]["data"]["config"]["ui"]["initParams"]["uiPlatform"],
                    "clientVersion": self.react_context["playerModel"]["data"]["config"]["core"]["initParams"][
                        "clientVersion"],
                    "supportsPreReleasePin": True,  # ?
                    "supportsWatermark": True,  # ?
                    "showAllSubDubTracks": True,
                    "videoOutputInfo": [{
                        # todo ; make this return valid, but "secure" values, maybe it helps
                        "type": "DigitalVideoOutputDescriptor",
                        "outputType": "unknown",
                        "supportedHdcpVersions": self.config["configuration"]["supported_hdcp_versions"],
                        "isHdcpEngaged": self.config["configuration"]["is_hdcp_engaged"]
                    }],
                    "titleSpecificData": {
                        title.service_data.get("episodeId", title.service_data["id"]): {"unletterboxed": True}
                    },
                    "preferAssistiveAudio": False,
                    "isUIAutoPlay": False,
                    "isNonMember": False,
                    # "desiredVmaf": "plus_lts",  # ?
                    # "maxSupportedLanguages": 2,  # ?
                }
            },
            userauthdata=self.userauthdata
        )
        if "errorDetails" in payload_chunks:
            raise Exception(f"Manifest call failed: {payload_chunks['errorDetails']}")
        return payload_chunks

    def manifest_as_tracks(self, manifest: dict, original_language: Optional[Language] = None) -> Tracks:
        # filter audio_tracks so that each stream is an entry instead of each track
        manifest["audio_tracks"] = [x for y in [
            [dict(t, **d) for d in t["streams"]]
            for t in manifest["audio_tracks"]
        ] for x in y]
        return Tracks(
            # VIDEO
            [VideoTrack(
                id_=x["downloadable_id"],
                source=self.ALIASES[0],
                url=x["urls"][0]["url"],
                # metadata
                codec=x["content_profile"],
                language=original_language,
                is_original_lang=bool(original_language),  # Can only assume yes if original lang is available
                bitrate=x["bitrate"] * 1000,
                width=x["res_w"],
                height=x["res_h"],
                fps=(float(x["framerate_value"]) / x["framerate_scale"]) if "framerate_value" in x else None,
                # switches/options
                needs_proxy=self.download_proxied,
                needs_repack=False,
                # decryption
                encrypted=x["isDrm"],
                pssh=Box.parse(base64.b64decode(manifest["video_tracks"][0]["drmHeader"]["bytes"])) if x[
                    "isDrm"] else None,
                kid=x["drmHeaderId"] if x["isDrm"] else None,
            ) for x in manifest["video_tracks"][0]["streams"]],
            # AUDIO
            [AudioTrack(
                id_=x["downloadable_id"],
                source=self.ALIASES[0],
                url=x["urls"][0]["url"],
                # metadata
                codec=x["content_profile"],
                language=self.NF_LANG_MAP.get(x["languageDescription"], x["language"]),
                is_original_lang=is_close_match(
                    self.NF_LANG_MAP.get(x["languageDescription"], x["language"]),
                    [original_language]
                ),
                bitrate=x["bitrate"] * 1000,
                channels=x["channels"],
                descriptive=x.get("rawTrackType", "").lower() == "assistive",
                # switches/options
                needs_proxy=self.download_proxied,
                needs_repack=False,
                # decryption
                encrypted=x["isDrm"],
                pssh=Box.parse(base64.b64decode(x["drmHeader"]["bytes"])) if x["isDrm"] else None,
                kid=x.get("drmHeaderId") if x["isDrm"] else None,  # TODO: haven't seen enc audio, needs testing
            ) for x in manifest["audio_tracks"]],
            # SUBTITLE
            [TextTrack(
                id_=list(x["downloadableIds"].values())[0],
                source=self.ALIASES[0],
                url=next(iter(next(iter(x["ttDownloadables"].values()))["downloadUrls"].values())),
                # metadata
                codec=next(iter(x["ttDownloadables"].keys())),
                language=self.NF_LANG_MAP.get(x["languageDescription"], x["language"]),
                is_original_lang=is_close_match(
                    self.NF_LANG_MAP.get(x["languageDescription"], x["language"]),
                    [original_language]
                ),
                forced=x["isForcedNarrative"],
                # switches/options
                needs_proxy=self.download_proxied,
                # text track options
                sdh=x["rawTrackType"] == "closedcaptions"
            ) for x in manifest["timedtexttracks"] if not x["isNoneTrack"]]
        )

    @staticmethod
    def get_original_language(manifest: dict) -> Language:
        for language in manifest["audio_tracks"]:
            if language["languageDescription"].endswith(" [Original]"):
                return Language.get(language["language"])
        # e.g. get `en` from "A:1:1;2;en;0;|V:2:1;[...]"
        return Language.get(manifest["defaultTrackOrderList"][0]["mediaId"].split(";")[2])


class ESN:
    def __init__(self, prefix: str, random_len: int, random_choice: str = string.ascii_uppercase + string.digits):
        self.prefix = re.sub(r"[^A-Za-z0-9=-]", "=", prefix) + "-"
        self.random = "".join([random.choice(random_choice) for _ in range(random_len)]).upper()

    def __str__(self) -> str:
        return self.prefix + self.random

    @classmethod
    def android_smartphone(cls, manufacturer: str = "samsung", model: str = "SM-G950F", system_id: int = 7169) -> ESN:
        """
        Real examples from Netflix app:
        NFANDROID1-PRV-P-SAMSUSM-G950F-7169-  # samsung  SM-G950F  7169
        NFANDROID1-PRV-P-HUAWECLT-L09-7833-   # HUAWEI   CLT-L09   7833
        NFANDROID1-PRV-P-ONEPLHD1913-15072-   # OnePlus  HD1913    15072

        :param manufacturer: getprop ro.product.manufacturer
        :param model: getprop ro.product.model
        :param system_id: Widevine device system ID
        """
        return cls(
            prefix="NFANDROID1-PRV-P-{manufacturer}{model}-{system_id}-".format(
                manufacturer=manufacturer[:5].upper(),
                model=model[:45].upper().replace(" ", "="),
                system_id=system_id
            ),
            random_len=65,
            random_choice=string.hexdigits
        )

    @classmethod
    def android_tv(cls, nrdp_modelgroup: str = "SONYANDROIDTV2017", manufacturer: str = "Sony",
                   model: str = "BRAVIA 4K GB", system_id: int = 6566) -> ESN:
        """
        Real examples from Netflix app:
        # SONYANDROIDTV2017          Sony    BRAVIA 4K GB       6566
        NFANDROID2-PRV-SONYANDROIDTV2017-SONY=BRAVIA=4K=GB-6566-
        # NVIDIASHIELDANDROIDTV2019  NVIDIA  SHIELD Android TV  13062
        NFANDROID2-PRV-NVIDIASHIELDANDROIDTV2019-NVIDISHIELD=ANDROID=TV-13062-
        # FIRESTICK2018              Amazon  AFTMM              8415
        NFANDROID2-PRV-FIRESTICK2018-AMAZOAFTMM-8415-
        # FIRESTICK2016              Amazon  AFTT               6590
        NFANDROID2-PRV-FIRETVSTICK2016-AMAZOAFTT-6590-
        # ??? the fuck is this shit, seems to be LG DTV app
        LGTV20165=51005261954

        :param nrdp_modelgroup: getprop ro.nrdp.modelgroup (netflix-ready device platform)
        :param manufacturer: getprop ro.product.manufacturer
        :param model: getprop ro.product.model
        :param system_id: Widevine device system ID
        """
        return cls(
            prefix="NFANDROID2-PRV-{nrdp_modelgroup}-{manufacturer}={model}-{system_id}-".format(
                nrdp_modelgroup=nrdp_modelgroup,
                manufacturer=manufacturer,
                model=model.replace(" ", "="),
                system_id=system_id
            ),
            random_len=64,
            random_choice=string.hexdigits
        )

    @classmethod
    def browser(cls, browser: str = "Firefox", operating_system: str = "Windows") -> ESN:
        browser = browser.lower().replace(" ", "")
        operating_system = (operating_system.lower().replace(" ", "")
                            .replace("windows8.0", "windows8")
                            .replace("windows6.0", "windows6")
                            .replace("macos", "mac"))

        prefix = ""
        if browser in ["edge", "ie", "internetexplorer"]:
            prefix = "NFCDIE-" + {
                "mac": "04",
                "windows10": "03",
                "windowsphone": "02",
                "windows8.1": "02",
                "windows8": "02",
                "windows7": "02"
                # 01: internet explorer <= 11
                # 02: old (microsoft) edge
                # 03: chromium edge (for windows)
                # 04: chromium edge (for mac)
            }.get(operating_system, "???")
        elif browser == "safari":
            prefix = {
                "windowsvista": "SLW32",
                "windows6": "SLW32",
                "mac": "NFCDSF-01"
                # SLW32: safari <= 5 on windows vista
                # 01: mac os
            }.get(operating_system, "???")
        elif browser == "opera":
            prefix = "NFCDOP-" + {
                "windows": "01",
                "mac": "01"
            }.get(operating_system, "???")
        elif browser in ["chrome", "chromium"]:
            prefix = "NFCDCH-" + {
                "windows": {"chrome": "02", "chromium": "01"}[browser],
                "mac": "MC",
                "linux": "LX",
                "android": "AP"
                # 01: chromium
                # 02: chrome (windows)
                # 03: chrome (mac)
                # LX: chrome (linux)
                # AP: chrome (android)
            }.get(operating_system, "???")
        elif browser == "firefox":
            prefix = "NFCDFF-" + {
                "windows": "02",
                "mac": "MC",
                "linux": "LX"
                # 02: windows
                # 03: mac
                # LX: linux
            }.get(operating_system, "???")
        if prefix.endswith("-???"):
            raise NotImplementedError(
                "The OS ({}) and Browser ({}) combination used is not yet implemented or unavailable.".format(
                    operating_system,
                    browser,
                ),
            )
        return cls(
            prefix=prefix,
            random_len=30
        )

    @classmethod
    def android_tablet(cls) -> NoReturn:
        """
        return cls(
            prefix="NFANDROID1-PRV-T-",
            random_len=64
        )
        """
        raise NotImplementedError("Have yet to look into Android tablets...")

    @classmethod
    def android_set_top_box(cls) -> NoReturn:
        """
        return cls(
            prefix="NFANDROID1-PRV-B-",
            random_len=64
        )
        """
        raise NotImplementedError("Have yet to look into Android set-top-boxes...")

    @classmethod
    def chrome_os(cls) -> NoReturn:
        """
        return cls(
            prefix="NFANDROID1-PRV-C-",
            random_len=64
        )
        """
        raise NotImplementedError("Have yet to look into Chrome OS...")
