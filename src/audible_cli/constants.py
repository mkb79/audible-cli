
from audible.localization import LOCALE_TEMPLATES


APP_NAME: str = "Audible"
CONFIG_FILE: str = "config.toml"
CONFIG_DIR_ENV: str = "AUDIBLE_CONFIG_DIR"
PLUGIN_PATH: str = "plugins"
PLUGIN_DIR_ENV: str = "AUDIBLE_PLUGIN_DIR"
PLUGIN_ENTRY_POINT: str = "audible.cli_plugins"
DEFAULT_AUTH_FILE_EXTENSION: str = "json"
DEFAULT_AUTH_FILE_ENCRYPTION: str = "json"
DEFAULT_CONFIG_DATA: dict[str, str] = {
    "title": "Audible Config File",
    "APP": {},
    "profile": {}
}
CODEC_HIGH_QUALITY: str = "AAX_44_128"
CODEC_NORMAL_QUALITY: str = "AAX_44_64"

# The only two values this client ever sends as the quality of a license or
# metadata request
API_QUALITY_HIGH: str = "High"
API_QUALITY_NORMAL: str = "Normal"

# What `--quality` accepts, and what each value is sent as. "best" never
# crosses the wire under that name: it is a client-side choice that selects
# the best aax entry from an item's `available_codecs`, while the request
# itself goes out as High. Aaxc does not go through that selection for its
# format and takes it from the license response.
QUALITY_TO_API: dict[str, str] = {
    "best": API_QUALITY_HIGH,
    "high": API_QUALITY_HIGH,
    "normal": API_QUALITY_NORMAL,
}
QUALITIES: tuple[str, ...] = tuple(QUALITY_TO_API)

# The chapter styles a metadata request is built with. `--chapter-type` accepts
# one more, "config", which the download command resolves against the profile
# before any request exists.
API_CHAPTER_TYPES: tuple[str, ...] = ("Flat", "Tree")
CLI_CHAPTER_TYPE_CONFIG: str = "config"

# What a finished audio download is allowed to have arrived as. The paths
# differ on purpose: AAX always writes `.aax` because the codec is part of
# the request, while AAXC takes its format from the license and writes
# `.mp3` for MPEG.
#
# Asked for a title it cannot serve — a podcast episode, say — the AAX
# service still answers 302, and behind it sits HTTP 200 with no
# Content-Length and `File Assembly error: Invalid Audio Format.` as
# `text/html`. Measured for every codec, `mp3` included. No other check
# objects to that, so this list is the only thing between an error page and
# a `.aax` file counted as a success.
AAX_CONTENT_TYPES: tuple[str, ...] = (
    "audio/aax",
    "audio/vnd.audible.aax",
    "audio/audible",
    "audio/mp4",
)

# Audible announces the same MP3 episode as the registered `audio/mpeg` or
# as the unregistered `audio/mp3`, so both have to be listed.
AAXC_CONTENT_TYPES: tuple[str, ...] = (
    *AAX_CONTENT_TYPES,
    "audio/mpeg",
    "audio/mp3",
    "audio/x-m4a",
)

# The CDE host drops a connection now and then while downloads stream
# through the same pool.
CDE_ATTEMPTS: int = 3
CDE_FIRST_DELAY: float = 0.5

AVAILABLE_MARKETPLACES = [
    market["country_code"] for market in LOCALE_TEMPLATES.values()
]

#: The hosts of a marketplace that audible-cli has business with: the
#: API, the website a companion file comes from, the content delivery
#: the download is redirected to, and the Amazon endpoint that answers
#: for the user behind the profile.
MARKETPLACE_HOST_TEMPLATES = (
    "api.audible.{domain}",
    "www.audible.{domain}",
    "cds.audible.{domain}",
    "api.amazon.{domain}",
)

#: The one host that is the same for every marketplace. Annotations and
#: the AAX download url come from it.
CDE_HOST = "cde-ta-g7g.amazon.com"

#: Where `audible request` may be pointed. The request carries the
#: credentials of a profile, so this is the list of who is allowed to
#: receive them.
ALLOWED_REQUEST_HOSTS = frozenset(
    {CDE_HOST}
    | {
        template.format(domain=market["domain"])
        for template in MARKETPLACE_HOST_TEMPLATES
        for market in LOCALE_TEMPLATES.values()
    }
)
