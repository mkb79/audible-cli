APP_NAME: str = "Audible"
CONFIG_FILE: str = "config.toml"
CONFIG_DIR_ENV: str = "AUDIBLE_CONFIG_DIR"
PLUGIN_PATH: str = "plugins"
PLUGIN_DIR_ENV: str = "AUDIBLE_PLUGIN_DIR"
PLUGIN_ENTRY_POINT: str = "audible.cli_plugins"
MINIMUM_FILE_SIZE: int = 65536 # if it's not at least 64KB there is no way it's a real file
DEFAULT_AUTH_FILE_EXTENSION: str = "json"
DEFAULT_AUTH_FILE_ENCRYPTION: str = "json"
DEFAULT_CONFIG_DATA = {
    "title": "Audible Config File",
    "APP": {},
    "profile": {}
}
CODEC_HIGH_QUALITY = "LC_128_44100_stereo"
CODEC_NORMAL_QUALITY = "LC_64_44100_stereo"
