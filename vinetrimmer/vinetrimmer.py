import logging
from importlib.metadata import metadata

import click

from vinetrimmer.commands import cfg, dl, wvd
from vinetrimmer.config import directories, filenames
from vinetrimmer.utils import Logger


@click.group(context_settings=dict(
    help_option_names=["-?", "-h", "--help"],
    max_content_width=116,  # max PEP8 line-width, -4 to adjust for initial indent
))
@click.option("--debug", is_flag=True, default=False, help="Enable DEBUG level logs.")
def main(debug: bool) -> None:
    """
    Vinetrimmer is the most convenient command-line program to
    download videos from Widevine DRM-protected video platforms.

    \b
    TODO: - Supply -w to Services to allow them to only get Title data for the requested episodes
            to reduce the amount of processing time and requests needed.
    """
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO)
    log = Logger.getLogger(level=logging.DEBUG if debug else logging.INFO)

    meta = metadata('vinetrimmer')

    log.info(f"Vinetrimmer version {meta['version']} Copyright (c) 2019-2021 the Vinetrimmer Contributors")
    log.info("Convenient Widevine-DRM Downloader and Decrypter.")
    log.info(meta["home-page"])
    log.info(f"[Root Config]     : {filenames.root_config}")
    log.info(f"[Service Configs] : {directories.service_configs}")
    log.info(f"[Cookies]         : {directories.cookies}")
    log.info(f"[WVDs]            : {directories.wvds}")
    log.info(f"[Cache]           : {directories.cache}")
    log.info(f"[Logs]            : {directories.logs}")
    log.info(f"[Temp Files]      : {directories.temp}")
    log.info(f"[Downloads]       : {directories.downloads}")

    # tldextract uses filelock, set to info level, annoying
    logging.getLogger("filelock").setLevel(logging.WARNING)


main.add_command(cfg)
main.add_command(dl)
main.add_command(wvd)


if __name__ == "__main__":
    main()
