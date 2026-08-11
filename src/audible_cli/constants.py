
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

AVAILABLE_MARKETPLACES = [
    market["country_code"] for market in LOCALE_TEMPLATES.values()
]
