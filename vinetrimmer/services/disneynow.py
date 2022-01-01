from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Track, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match
from vinetrimmer.utils.adobepass import AdobePassVT
from vinetrimmer.utils.collections import flatten


class DisneyNOW(BaseService):
    """
    Service code for the DisneyNOW streaming service (https://disneynow.com).

    \b
    Authorization: AdobePass
    Security: HD@L3
    """

    ALIASES = ["DSNY", "disneynow"]

    @staticmethod
    @click.command(name="DisneyNOW", short_help="https://disneynow.com")
    @click.argument("title", type=str)
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> DisneyNOW:
        return DisneyNOW(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str):
        self.title = title
        super().__init__(ctx)

        assert ctx.parent is not None

        self.playback_params: dict = {}
        self.license_url: str

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        # TODO: Add proper request error handling to all requests, check by response rather than empty JSON
        titles = []

        r = self.session.get(self.config["endpoints"]["video"].format(id=self.title.upper())).json()
        if not r:
            self.log.exit(" - Unable to get metadata. Is the title ID correct?")
            raise

        show = self.session.get(self.config["endpoints"]["show"].format(id=r["show"]["id"])).json()

        for module in show["modules"]:
            # TODO: Movie support?
            if module["name"] in ["tilegroup_show_season_multiple", "show_latest_clips"]:
                r = self.session.get(module["resource"].format(start="0", size="2000")).json()
                for tile in r.get("tiles", []):
                    titles.append(Title(
                        id_=tile["video"]["id"],
                        type_=Title.Types.TV,
                        name=tile["video"]["show"]["title"],
                        season=int(tile["video"]["seasonnumber"]),
                        episode=int(tile["video"]["episodenumber"]),
                        episode_name=tile["video"]["title"],
                        original_lang=tile["video"]["show"]["language"],
                        source=self.ALIASES[0],
                        service_data=tile
                    ))

        return titles

    def get_tracks(self, title: Title) -> Tracks:
        adobe_client = AdobePassVT(self.credentials, self.get_cache)

        requester_id = "DisneyChannels"

        auth = adobe_client._extract_mvpd_auth(
            url=self.config["endpoints"]["web"].format(
                name=title.name.lower(),
                season=title.season,
                episode=title.episode,
                id=title.service_data["video"]["id"]
            ),
            video_id=title.service_data["video"]["id"],
            requestor_id=requester_id,
            resource="Disney"
        )

        manifest = self.session.post(
            url=self.config["endpoints"]["manifest"],
            data={
                "video_id": title.service_data["video"]["id"],
                "video_type": title.service_data["video"]["type"],
                "brand": title.service_data["video"]["brand"],
                "device": "001",
                "app_name": "webplayer-dxd",
                "video_player": "html5",
                "token": auth,
                "token_type": "ap",
                "adobe_requestor_id": requester_id,
            }
        ).json()

        tracks = Tracks.from_mpd(
            uri=manifest["video"]["assets"]["asset"][0]["value"],
            session=self.session,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )
        tracks.subtitles.clear()  # subs from mpd is not used as they are in an mp4 container

        video_res = self.session.get(
            self.config["endpoints"]["video"].format(
                brand="011",
                id=title.service_data["video"]["id"]
            )
        ).json()

        resource_url = next((x["resource"] for x in video_res["modules"] if x["name"] == "video_player_vod"), None)
        if resource_url:
            r = self.session.get(resource_url).json()["video"]
            for cc in r.get("closedCaption", {}).get("sources", []):
                # TODO: What if there's no TTML, but there is something else we could use?
                if cc["type"] == "ttml":
                    language = cc.get("language", "en-US")
                    tracks.add(TextTrack(
                        id_=hashlib.md5(cc["value"].encode()).hexdigest()[0:6],
                        source=self.ALIASES[0],
                        url=cc["value"],
                        # metadata
                        codec="ttml",
                        language=language,
                        is_original_lang=is_close_match(language, [title.original_lang]),
                        forced=False,
                        sdh=True  # TODO: find out if sub is SDH/CC
                    ))

        video_tracks = defaultdict(list)
        for video in tracks.videos:
            video_tracks[(video.extra[0].get("id"), video.bitrate)].append(video)
        for group in video_tracks.values():
            group[0].url = list(flatten(x.url for x in group))
            for track in group[1:]:
                tracks.videos.remove(track)

        audio_tracks = defaultdict(list)
        for audio in tracks.audio:
            audio_tracks[(audio.extra[0].get("id"), audio.bitrate)].append(audio)
        for group in audio_tracks.values():
            group[0].url = list(flatten(x.url for x in group))
            for track in group[1:]:
                tracks.audio.remove(track)

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, track: Track, **_: Any) -> bytes:
        return self.session.post(
            url=self.config["endpoints"]["license"],
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({
            "appversion": self.config["appversion"],
            "X-Forwarded-For": "3.3.3.3"  # Geoblock Bypass
        })
