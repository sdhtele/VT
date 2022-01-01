from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Tracks, VideoTrack
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match
from vinetrimmer.utils.xml import load_xml


class ParamountPlus(BaseService):
    """
    Service code for Paramount's Paramount+ streaming service (https://paramountplus.com).

    \b
    Authorization: Cookies
    Security: UHD@L3, doesn't care about releases.

    \b
    Tips: - The library of contents can be viewed without logging in at https://paramountplus.com/shows/
            See the footer for links to movies, news, etc. A US IP is required to view.
    """

    ALIASES = ["PMTP", "paramountplus", "paramount+"]
    GEOFENCE = ["us"]

    VIDEO_CODEC_MAP = {
        "H264": ["avc"],
        "H265": ["hvc", "dvh"]
    }
    AUDIO_CODEC_MAP = {
        "AAC": "mp4a",
        "AC3": "ac-3",
        "EC3": "ec-3"
    }

    @staticmethod
    @click.command(name="ParamountPlus", short_help="https://paramountplus.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.option("-c", "--clips", is_flag=True, default=False,
                  help="Download clips instead of episodes (for TV shows)")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> ParamountPlus:
        return ParamountPlus(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool, clips: bool):
        self.title = title
        self.movie = movie
        self.clips = clips
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]
        self.range = ctx.parent.params["range_"]
        self.wanted = ctx.parent.params["wanted"]

        # Note: possible android HMAC key: d67afc830dab717fd163bfcb0b8b88423e9a1a3b

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        if self.movie:
            res = self.session.get(
                self.config["endpoints"]["movie"].format(title_id=self.title),
                params={
                    "includeTrailerInfo": "true",
                    "includeContentInfo": "true",
                    "locale": "en-us",
                    "at": "ABBBye7409f2yP+sJyziMaOLgwl1Q9ZiRsT+hbp3El42FI4dQwcgQ1LPZAJ9nbk21co="  # ?
                }
            )
            res.raise_for_status()
            data = res.json()
            if not data["success"]:
                self.log.exit(f" - Failed to get title information: {data['message']}")
            title = data["movie"]["movieContent"]
            return Title(
                id_=title["tmsprogramID"],
                type_=Title.Types.MOVIE,
                name=title["title"],
                year=title["_airDateISO"][:4],  # todo: find a way to get year, this api doesnt return it
                original_lang="en",  # TODO: Don't assume
                source=self.ALIASES[0],
                service_data=title
            )

        if not self.title.isnumeric():
            self.log.exit(" - TV shows require a numeric title ID. For movies, use -m.")
            raise
        res = self.session.get(
            self.config["endpoints"]["tv"].format(title_id=self.title),
            params={
                "platformType": "apps",
                "begin": "0",
                "rows": "1",
                "locale": "en-us",
                "at": "ABB8PNPZ6DFZVBGYeQAKF72Ok/Vsy00GFYa0biVKwjJSfZL7gy0kGuQZbLowk3sSE+U="
            }
        )
        res.raise_for_status()
        data = res.json()
        if not data["success"]:
            self.log.exit(f" - Failed to get title sections: {data['message']}")
        section = next((x["sectionId"] for x in data["videoSectionMetadata"] if x["title"] == "Full Episodes"), None)

        res = self.session.get(
            self.config["endpoints"]["section"].format(id=section),
            params={
                "begin": "0",
                "rows": "99",
                "locale": "en-us",
                "at": "ABDyTOBvk3BsHkb4L9DwUizQIKEKG9DCYZTviDDnG7K1/18OnFFEwynsmfwi0mOZQlY="
            }
        )
        res.raise_for_status()
        data = res.json()
        if not data["success"]:
            self.log.exit(f" - Failed to get section information: {data['message']}")
        episodes = data["sectionItems"]["itemList"]

        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=t["seriesTitle"],
            season=t["seasonNum"] if t["fullEpisode"] else 0,
            episode=t["episodeNum"] if t["fullEpisode"] else t["positionNum"],
            episode_name=t["label"],
            original_lang="en",  # TODO: Don't assume
            source=self.ALIASES[0],
            service_data=t
        ) for t in episodes if t["fullEpisode"] is not self.clips]

    def get_tracks(self, title: Title) -> Tracks:
        """
        Get Paramount+ tracks using CBS ThunderPlayer and link.theplatform.com endpoints.

        CBS ThunderPlayer may end up being removed or protected at some point as it was
        originally used solely for CBS All-Access which is now Paramount+.

        Using the Stream PID returned from the ThunderPlayer endpoint, we can get stream
        information as SMIL format from link.theplatform.com endpoint. This endpoint is
        not exclusive to CBS or Viacom/Paramount and is used by other companies too,
        e.g. adultswim.com.
        """
        r = self.session.get(
            "https://can.cbs.com/thunder/player/videoPlayerService.php",
            params={
                "partner": "cbs",
                "contentId": title.service_data["contentId"]
            }
        )

        root = load_xml(r.text)

        tracks = Tracks()

        for item in root.find("items").findall("item"):
            if item.findtext("isServiceAllowed") == "false":
                self.log.exit(" - The account does not have the rights to this title.")
                raise

            asset_type = item.findtext("assetType")
            pid = item.findtext("pid")

            if asset_type.startswith("HLS") or asset_type.endswith("PRECON"):
                continue

            if self.range == "SDR" and "HDR" in asset_type:
                continue

            if asset_type == "DASH_CENC_HDR":
                # DASH_CENC_HDR has some decryption and general download problems (kid and size mismatch errors).
                # DASH_CENC_HDR10 seems to be the same, even same bitrate and file size, so use that.
                continue

            r = self.session.get(
                url=f"https://link.theplatform.com/s/dJ5BDC/{pid}",
                params={"format": "SMIL", "manifest": "m3u", "Tracking": "true", "mbr": "true"}
            )

            meta = load_xml(r.text).find("body").find("seq")

            meta = meta.findall("switch")
            if not meta:
                continue  # split/clipped, so multiple endpoints for one full episode, annoying, just skip
            meta = sorted(meta, key=lambda t: int(t.find("video").get("system-bitrate")))[-1]

            if not tracks.subtitles:
                # we don't grab the subs from the mpd as that one is in an mp4 container
                ttml = [
                    x for x in meta.find("ref").findall("param")
                    if x.get("name") in ("sMPTE-TTCCURL", "ClosedCaptionURL")  # not using WEBVTT as no lang info
                ]
                for name, src in [(x.get("name"), x.get("value")) for x in ttml]:
                    if not src:
                        continue
                    tt = load_xml(self.session.get(src).text)
                    if tt.find("Error"):
                        if tt.find("Error").findtext("Code") == "NoSuchKey":
                            self.log.warning(f" - Failed to retrieve subtitle {name}, it doesn't exist, ignoring...")
                            continue
                        self.log.exit(f" - Failed to retrieve subtitle {name}: {tt.find('Error').find('Details')}")
                        raise
                    tt_lang = tt.xpath("./@xml:lang")[0]
                    tracks.subtitles.append(TextTrack(
                        id_=Path(src).name.split(".")[0],
                        source=self.ALIASES[0],
                        url=src,
                        # metadata
                        codec="tt",
                        language=tt_lang,
                        is_original_lang=title.original_lang and is_close_match(tt_lang, [title.original_lang])
                    ))
                    break

            meta = meta.find("video")

            if asset_type in ["WIFI", "3G", "Downloadable"]:
                # MP4 direct one-url downloads with no codec info, should be unencrypted, rare to be available
                tracks.add(VideoTrack(
                    id_=f"{asset_type}-{pid}",
                    source=self.ALIASES[0],
                    url=meta.get("src"),
                    # metadata
                    codec="avc.assumed",  # TODO: No idea any way to get codec, it doesnt say
                    language="en",  # TODO: don't assume
                    is_original_lang=True,  # TODO: don't assume
                    bitrate=meta.get("system-bitrate"),
                    width=int(meta.get("width")),
                    height=int(meta.get("height")),
                    fps=None,
                    # decryption
                    encrypted=False,
                    # extra
                    extra=meta
                ))
                continue

            if meta.get("src").lower().endswith(".m3u8"):
                continue  # probably a free access clear key protected manifest

            mpd_tracks = Tracks.from_mpd(
                uri=meta.get("src"),
                session=self.session,
                lang=title.original_lang,
                source=self.ALIASES[0]
            )

            mpd_tracks.subtitles.clear()  # again, subs from mpd is not used as they are in an mp4 container
            for track in mpd_tracks:
                track.id = track.id + asset_type
                track.codec += " " + asset_type
                if isinstance(track, VideoTrack):
                    track.hdr10 = track.codec[:4] in ("hvc1", "hev1") and "HDR" in asset_type
                    track.dv = track.codec[:4] in ("dvh1", "dvhe")

            tracks.add(mpd_tracks)

        tracks.videos = [
            x for x in tracks.videos
            if x.codec[:3] in self.VIDEO_CODEC_MAP[self.vcodec]
        ]

        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio
                if x.codec[:4] == self.AUDIO_CODEC_MAP[self.acodec]
            ]

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, title: Title, **_: Any) -> bytes:
        bearer_path = title.service_data.get("url") or title.service_data.get("href")
        if not bearer_path:
            self.log.exit(" - Unable to get path for bearer token")
            raise

        r = self.session.post(
            url="https://cbsi.live.ott.irdeto.com/widevine/getlicense",
            params={
                "CrmId": "cbsi",
                "AccountId": "cbsi",
                "SubContentType": "Default",
                "ContentId": title.service_data["contentId"]
            },
            headers={
                # TODO: Can this be cached? It has an expire timestamp, but other than that
                #       is it reusable? Is it per-title? per-episode? per-request? one-time use?
                "Authorization": f"Bearer {self.get_auth_bearer(bearer_path)}"
            },
            data=challenge  # expects bytes
        )

        if r.headers["Content-Type"].startswith("application/json"):
            res = r.json()
            self.log.exit(f" - Failed to get license, {res['message']} [{res['code']}]")
            raise

        return r.content

    # Service specific functions

    def get_prop(self, prop: str) -> str:
        res = self.session.get("https://www.paramountplus.com")
        res.raise_for_status()
        prop_re = prop.replace(".", r"\.")
        search = re.search(rf"{prop_re} ?= ?[\"']?([^\"';]+)", res.text)
        if not search:
            raise ValueError(f"Could not find {prop} prop on Paramount+ Homepage. Cookies may be expired.")
        return search.group(1)

    def is_logged_in(self) -> bool:
        return self.get_prop("CBS.UserAuthStatus") == "true"

    def is_subscribed(self) -> bool:
        return self.get_prop("CBS.Registry.user.sub_status") == "SUBSCRIBER"

    def get_auth_bearer(self, path: str) -> str:
        res = self.session.get(urllib.parse.urljoin("https://www.paramountplus.com", path))
        res.raise_for_status()
        match = re.search(r'"Authorization": ?"Bearer ([^\"]+)', res.text)
        if not match:
            raise ValueError("Could not find Authorization header from Player DRM config data")
        return match.group(1)

    def configure(self) -> None:
        self.session.headers.update({
            "Accept-Language": "en-US,en;q=0.5",
            "Origin": "https://www.paramountplus.com"
        })

        if not self.is_logged_in():
            self.log.exit(" - Not logged in; Cookies may be outdated.")
            raise

        if not self.is_subscribed():
            self.log.exit(" - Profile does not have an active subscription.")
            raise
