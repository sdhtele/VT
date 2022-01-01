from __future__ import annotations

from typing import Any, Optional, Union

import click
from click import Context

from vinetrimmer.objects import MenuTrack, Title, Tracks
from vinetrimmer.services.BaseService import BaseService


class VideoLand(BaseService):
    """
    Service code for RTL Nederland's Videoland. streaming service (https://videoland.com).

    \b
    Authorization: Cookies
    Security: UHD@-- HD@L3, doesn't care about releases.

    \b
    TODO: - Due to unpopularity or need of use, this hasn't been getting regular updates so the codebase or
            any API changes may have broken this codebase; It needs testing
    """

    ALIASES = ["VL", "videoland"]

    @staticmethod
    @click.command(name="Videoland", short_help="https://videoland.com")
    @click.argument("title", type=str)
    @click.option("-m", "--movie", is_flag=True, default=False, help="Title is a Movie.")
    @click.pass_context
    def cli(ctx: Context, **kwargs: Any) -> VideoLand:
        return VideoLand(ctx, **kwargs)

    def __init__(self, ctx: Context, title: str, movie: bool):
        self.title = title
        self.movie = movie
        super().__init__(ctx)

        self.vl_lic_url: Optional[str] = None
        self.vl_api_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "videoland-platform": "videoland"
        }

        self.configure()

    def get_titles(self) -> Union[Title, list[Title]]:
        metadata = self.session.get(
            url=f"https://www.videoland.com/api/v3/{'movies' if self.movie else 'series'}/{self.title}",
            headers=self.vl_api_headers
        ).json()
        if self.movie:
            return Title(
                id_=self.title,
                type_=Title.Types.MOVIE,
                name=metadata["title"],
                year=metadata["year"],
                source=self.ALIASES[0],
                service_data=metadata
            )
        titles = [
            Ep for Season in [
                [
                    dict(x, **{'season': Season["position"]}) for i, x in self.session.get(
                        url=f"https://www.videoland.com/api/v3/episodes/{self.title}/{Season['id']}",
                        headers=self.vl_api_headers
                    ).json()["details"].items()
                ] for Season in [
                    x for i, x in metadata["details"].items() if x["type"] == "season"
                ]
            ] for Ep in Season
        ]
        return [Title(
            id_=self.title,
            type_=Title.Types.TV,
            name=metadata["title"],
            year=x.get("year"),
            season=x.get("season"),
            episode=x.get("position"),
            source=self.ALIASES[0],
            episode_name=x.get("title")
        ) for x in titles]

    def get_tracks(self, title: Title) -> Tracks:
        manifest = self.session.get(
            url=f"https://www.videoland.com/api/v3/stream/{title.service_data['id']}/widevine?edition=",
            headers=self.vl_api_headers
        ).json()
        if "code" in manifest:
            raise Exception(
                f"Failed to fetch the manifest for \"{title.service_data['id']}\", "
                f"{manifest['code']}, {manifest['error']}"
            )

        self.vl_lic_url = manifest["drm"]["widevine"]["license"]

        return Tracks.from_mpd(
            uri=manifest["stream"]["dash"],
            session=self.session,
            lang="en",  # TODO: Don't assume
            source=self.ALIASES[0]
        )

    def get_chapters(self, title: Title) -> list[MenuTrack]:
        return []

    def certificate(self, **kwargs: Any) -> bytes:
        # TODO: Hardcode the certificate
        return self.license(**kwargs)

    def license(self, challenge: bytes, **_: Any) -> bytes:
        assert self.vl_lic_url is not None
        return self.session.post(
            url=self.vl_lic_url,
            data=challenge  # expects bytes
        ).content

    # Service specific functions

    def configure(self) -> None:
        self.session.headers.update({"Origin": "https://www.videoland.com"})
