from __future__ import annotations

import hashlib
from typing import Any, Union

import click
from click import Context
from langcodes import Language

from vinetrimmer.objects import MenuTrack, TextTrack, Title, Track, Tracks
from vinetrimmer.services.BaseService import BaseService
from vinetrimmer.utils import is_close_match
from vinetrimmer.utils.pyhulu import Device, HuluClient


class Hulu(BaseService):
    """
    Service code for the Hulu streaming service (https://hulu.com).

    \b
    Authorization: Cookies
    Security: UHD@L3
    """

    ALIASES = ["HULU"]
    GEOFENCE = ["us"]

    AUDIO_CODEC_MAP = {
        "AAC": "mp4a",
        "EC3": "ec-3"
    }

    @staticmethod
    @click.command(name="Hulu", short_help="https://hulu.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> Hulu:
        return Hulu(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        assert ctx.parent is not None

        self.vcodec = ctx.parent.params["vcodec"]
        self.acodec = ctx.parent.params["acodec"]

        self.device: Device
        self.playback_params: dict = {}
        self.hulu_client: HuluClient
        self.license_url: str

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        titles = []

        if self.movie:
            r = self.session.get(self.config["endpoints"]["movie"].format(id=self.title)).json()
            title_data = r["details"]["vod_items"]["focus"]["entity"]
            titles.append(Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=title_data["name"],
                year=int(title_data["premiere_date"][:4]),
                source=self.ALIASES[0],
                service_data=title_data
            ))
        else:
            r = self.session.get(self.config["endpoints"]["series"].format(id=self.title)).json()
            if r.get("code", 200) != 200:
                if "Invalid uuid for param 'entity_id'" in r["message"]:
                    self.log.exit(f" - Failed to get titles, Did you mean {'-'.join(self.title.split('-')[-5:])}?")
                    raise
                self.log.exit(f" - Failed to get titles for {self.title}: {r['message']} [{r['code']}]")
                raise

            season_data = next((x for x in r["components"] if x["name"] == "Episodes"), None)
            if not season_data:
                self.log.exit(" - Unable to get episodes. Maybe you need a proxy?")
                raise

            for season in season_data["items"]:
                episodes = self.session.get(
                    self.config["endpoints"]["season"].format(
                        id=self.title,
                        season=season["id"].rsplit("::", 1)[1]
                    )
                ).json()
                for episode in episodes["items"]:
                    titles.append(Title(
                        id_=f"{season['id']}::{episode['season']}::{episode['number']}",
                        type_=Title.Types.TV,
                        name=episode["series_name"],
                        season=int(episode["season"]),
                        episode=int(episode["number"]),
                        episode_name=episode["name"],
                        source=self.ALIASES[0],
                        service_data=episode
                    ))

        playlist = self.hulu_client.load_playlist(titles[0].service_data["bundle"]["eab_id"])
        for title in titles:
            title.original_lang = Language.get(playlist["video_metadata"]["language"])

        return titles

    def get_tracks(self, title: Title) -> Tracks:
        playlist = self.hulu_client.load_playlist(title.service_data["bundle"]["eab_id"])
        self.license_url = playlist["wv_server"]

        tracks = Tracks.from_mpd(
            uri=playlist["stream_url"],
            session=self.session,
            lang=title.original_lang,
            source=self.ALIASES[0]
        )

        video_pssh = next(x.pssh for x in tracks.videos if x.pssh)
        for track in tracks.audio:
            if not track.pssh:
                track.pssh = video_pssh

        if self.acodec:
            tracks.audio = [
                x for x in tracks.audio if
                x.codec[:4] == self.AUDIO_CODEC_MAP[self.acodec]
            ]

        for sub_lang, sub_url in playlist["transcripts_urls"]["webvtt"].items():
            tracks.add(TextTrack(
                id_=hashlib.md5(sub_url.encode()).hexdigest()[0:6],
                source=self.ALIASES[0],
                url=sub_url,
                # metadata
                codec="vtt",
                language=sub_lang,
                is_original_lang=title.original_lang and is_close_match(sub_lang, [title.original_lang]),
                forced=False,  # TODO: find out if sub is forced
                sdh=False  # TODO: find out if sub is SDH/CC, it's actually quite likely to be true
            ))

        return tracks

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **_: Any) -> None:
        return None  # will use common privacy cert

    def license(self, challenge: bytes, track: Track, **_: Any) -> bytes:
        return self.session.post(
            url=self.license_url,
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.device = Device(
            device_code=self.config["device"]["FireTV4K"]["code"],
            device_key=self.config["device"]["FireTV4K"]["key"]
        )
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
        })
        self.playback_params = {
            "all_cdn": False,
            "region": "US",
            "language": "en",
            "interface_version": "1.9.0",
            "network_mode": "wifi",
            "play_intent": "resume",
            "playback": {
                "version": 2,
                "video": {
                    "codecs": {
                        "values": [x for x in self.config["codecs"]["video"] if x["type"] == self.vcodec],
                        "selection_mode": self.config["codecs"]["video_selection"]
                    }
                },
                "audio": {
                    "codecs": {
                        "values": self.config["codecs"]["audio"],
                        "selection_mode": self.config["codecs"]["audio_selection"]
                    }
                },
                "drm": {
                    "values": self.config["drm"]["schemas"],
                    "selection_mode": self.config["drm"]["selection_mode"],
                    "hdcp": self.config["drm"]["hdcp"]
                },
                "manifest": {
                    "type": "DASH",
                    "https": True,
                    "multiple_cdns": False,
                    "patch_updates": True,
                    "hulu_types": True,
                    "live_dai": True,
                    "secondary_audio": True,
                    "live_fragment_delay": 3
                },
                "segments": {
                    "values": [{
                        "type": "FMP4",
                        "encryption": {
                            "mode": "CENC",
                            "type": "CENC"
                        },
                        "https": True
                    }],
                    "selection_mode": "ONE"
                }
            }
        }
        self.hulu_client = HuluClient(
            device=self.device,
            session=self.session,
            version=self.config["device"].get("device_version"),
            **self.playback_params
        )
