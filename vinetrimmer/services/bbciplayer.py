from __future__ import annotations

import hashlib
import re
from hashlib import md5
from typing import Any, Optional, Union

import click
import m3u8
from click import Context

from vinetrimmer.config import directories
from vinetrimmer.objects import AudioTrack, MenuTrack, TextTrack, Title, Track, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match
from vinetrimmer.utils.collections import SSLSecurity, as_list


class BBCiPlayer(BaseService):
    """
    Service code for the BBC iPlayer streaming service (https://www.bbc.co.uk/iplayer).

    \b
    Authorization: None
    Security: None
    """

    ALIASES = ["iP", "bbciplayer", "bbc", "iplayer"]
    GEOFENCE = ["gb"]

    @staticmethod
    @click.command(name="BBCiPlayer", short_help="https://www.bbc.co.uk/iplayer")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> BBCiPlayer:
        return BBCiPlayer(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]

        if (ctx.parent.params.get("quality") or 0) > 1080 and self.vcodec != "H265":
            self.log.info(" + Switched video codec to H265 to be able to get 2160p video track")
            self.vcodec = "H265"

        self.playback_params: dict = {}
        self.license_url: str

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        titles = []

        r = self.get_metadata(self.title, 1)

        if not r:
            self.log.exit(" - Unable to get manifest. Is the title ID correct?")
            raise

        if self.movie:
            return Title(
                id_=r["id"],
                type_=Title.Types.MOVIE,
                name=r["title"]["default"],
                year=None,  # TODO
                original_lang="en",  # TODO: Don't assume English
                source=self.ALIASES[0],
                service_data=r
            )

        for season in r["slices"]:
            episodes = self.get_metadata(self.title, 200, 1, season["id"])

            for episode in episodes["entities"]["results"]:
                titles.append(Title(
                    id_=episode["episode"]["id"],
                    type_=Title.Types.TV,
                    name=episode["episode"]["title"]["default"],
                    season=int(next(iter(re.findall(r"^Series (\d+)", episode["episode"]["subtitle"]["default"])), 0)),
                    episode=int(next((x for x in next(iter(
                        re.findall(r"(\d+)\.|Episode (\d+)", episode["episode"]["subtitle"]["slice"] or "")
                    ), ()) if x), 0)),
                    episode_name=next(
                        iter(re.findall(r"\d+\. (.+)", episode["episode"]["subtitle"]["slice"] or "")),
                        episode["episode"]["subtitle"]["slice"] or episode["episode"]["subtitle"]["default"]
                    ),
                    original_lang="en",  # TODO: Don't assume English
                    source=self.ALIASES[0],
                    service_data=episode["episode"]
                ))

        return titles

    def get_tracks(self, title: Title) -> Tracks:
        playlist = self.session.get(
            url=self.config["endpoints"]["playlist"].format(
                pid=title.service_data["id"],
            )
        ).json()

        with SSLSecurity(level=1):
            manifest = self.session.get(
                url=self.config["endpoints"]["manifest"].format(
                    vpid=playlist["defaultAvailableVersion"]["smpConfig"]["items"][0]["vpid"],
                    mediaset="iptv-uhd" if self.vcodec == "H265" else "iptv-all"
                ),
                cert=str(directories.package_root / "certs" / "bbciplayer.pem")
            ).json()

        if "result" in manifest:
            self.log.exit(f" - Failed to get manifest [{manifest['result']}]")
            raise

        connection = {}
        for video in [x for x in manifest["media"] if x["kind"] == "video"]:
            connections = sorted(video["connection"], key=lambda x: x["priority"])
            # TODO: Does it specifically have to be akamai CDN?
            if self.vcodec == "H265":
                connection = connections[0]
            else:
                connection = next(
                    x for x in connections
                    if x["supplier"] == "mf_akamai" and x["transferFormat"] == "dash"
                )
            # TODO: Should we get tracks for each video media? Each connection is a mirror but
            #       each video media is a separate manifest with differing max resolution and max bitrate.
            break

        if self.vcodec == "H264":
            if connection["transferFormat"] == "dash":
                connection["href"] = "/".join(
                    connection["href"].replace("dash", "hls").split("?")[0].split("/")[0:-1] + ["hls", "master.m3u8"]
                )
                connection["transferFormat"] = "hls"
            elif connection["transferFormat"] == "hls":
                connection["href"] = "/".join(
                    connection["href"].replace(".hlsv2.ism", "").split("?")[0].split("/")[0:-1] + ["hls", "master.m3u8"]
                )

            if connection["transferFormat"] != "hls":
                raise ValueError(f"Unsupported video media transfer format {connection['transferFormat']!r}")

        if connection["transferFormat"] == "dash":
            tracks = Tracks.from_mpd(
                uri=connection["href"],
                session=self.session,
                lang=title.original_lang,
                source=self.ALIASES[0]
            )
        elif connection["transferFormat"] == "hls":
            tracks = Tracks.from_m3u8(
                m3u8.loads(self.session.get(connection["href"]).text, connection["href"]),
                lang=title.original_lang,
                source=self.ALIASES[0]
            )
        else:
            raise ValueError(f"Unsupported video media transfer format {connection['transferFormat']!r}")

        for video in tracks.videos:
            # TODO: Is there a way to detect this instead of assuming?
            video.hlg = video.codec and video.codec.startswith("hev1") and not (video.hdr10 or video.dv)

            if any(re.search(r"-audio_\w+=\d+", x) for x in as_list(video.url)):
                # create audio stream from the video stream
                audio_url = re.sub(r"-video=\d+", "", as_list(video.url)[0])
                audio = AudioTrack(
                    # use audio_url not video url, as to ignore video bitrate in ID
                    id_=md5(audio_url.encode()).hexdigest()[0:7],
                    source=self.ALIASES[0],
                    url=audio_url,
                    # metadata
                    codec=video.extra.stream_info.codecs.split(",")[0],
                    language=video.language,  # TODO: Get from `#EXT-X-MEDIA` audio groups section
                    is_original_lang=is_close_match(video.language, [title.original_lang]),
                    bitrate=int(next(iter(re.findall(r"-audio_\w+=(\d+)", as_list(video.url)[0])), 0)),
                    channels=None,  # TODO: Get from `#EXT-X-MEDIA` audio groups section
                    descriptive=False,  # Not available
                    # switches/options
                    descriptor=Track.Descriptor.M3U,
                    # decryption
                    encrypted=video.encrypted,
                    pssh=video.pssh,
                    # extra
                    extra=video.extra
                )
                if not tracks.exists(by_id=audio.id):
                    # some video streams use the same audio, so natural dupes exist
                    tracks.add(audio)
                # remove audio from the video stream
                video.url = [re.sub(r"-audio_\w+=\d+", "", x) for x in as_list(video.url)]
                video.codec = video.extra.stream_info.codecs.split(",")[1]
                video.bitrate = int(next(iter(re.findall(r"-video=(\d+)", as_list(video.url)[0])), 0))

        for caption in [x for x in manifest["media"] if x["kind"] == "captions"]:
            connection = sorted(caption["connection"], key=lambda x: x["priority"])[0]
            tracks.add(TextTrack(
                id_=hashlib.md5(connection["href"].rsplit("?", 1)[0].encode()).hexdigest()[0:6],
                source=self.ALIASES[0],
                url=connection["href"],
                # metadata
                codec=caption["type"].split("/")[-1].replace("ttaf+xml", "ttml"),
                language=TextTrack.parse(self.session.get(connection["href"]).content, "ttml").get_languages()[0],
                is_original_lang=True,  # TODO: Don't assume
                forced=False,  # doesn't seem to be any
                sdh=True  # seems to always be SDH
            ))
            # TODO: Is there actually more than one sub that's useful?
            break

        for track in tracks:
            track.encrypted = False  # TODO: automatically detect HLS encryption
            track.needs_proxy = True

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, track: Track, **_: Any) -> None:
        return None  # Unencrypted

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
        })

    def get_metadata(self, pid: str, per_page: int, page: Optional[int] = 1, series_id: Optional[str] = None) -> dict:
        variables = {
            "id": pid,
            "page": page,
            "perPage": per_page,
        }
        if series_id:
            variables["sliceId"] = series_id
        return self.session.post(
            url=self.config["endpoints"]["metadata"],
            headers={
                "Content-Type": "application/json"
            },
            json={
                "id": "5692d93d5aac8d796a0305e895e61551",
                "variables": variables,
            },
        ).json()["data"]["programme"]
