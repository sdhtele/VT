LANGUAGE_MUX_MAP = {
    # List of language tags that cannot be used by mkvmerge and need replacements.
    # Try get the replacement to be as specific locale-wise as possible.
    # A bcp47 as the replacement is recommended.
    "cmn": "zh",
    "cmn-Hant": "zh-Hant",
    "cmn-Hans": "zh-Hans",
    "none": "und",
    "yue": "zh-yue",
    "yue-Hant": "zh-yue-Hant",
    "yue-Hans": "zh-yue-Hans"
}

TERRITORY_MAP = {
    "Hong Kong SAR China": "Hong Kong"
}

# The max distance of languages to be considered "same", e.g. en, en-US, en-AU
LANGUAGE_MAX_DISTANCE = 5
