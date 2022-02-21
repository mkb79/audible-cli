APP_NAME: str = "Audible"
CONFIG_FILE: str = "config.toml"
CONFIG_DIR_ENV: str = "AUDIBLE_CONFIG_DIR"
PLUGIN_PATH: str = "plugins"
PLUGIN_DIR_ENV: str = "AUDIBLE_PLUGIN_DIR"
PLUGIN_ENTRY_POINT: str = "audible.cli_plugins"
DEFAULT_AUTH_FILE_EXTENSION: str = "json"
DEFAULT_AUTH_FILE_ENCRYPTION: str = "json"
DEFAULT_CONFIG_DATA = {
    "title": "Audible Config File",
    "APP": {},
    "profile": {}
}
CODEC_HIGH_QUALITY = "AAX_44_128"
CODEC_NORMAL_QUALITY = "AAX_44_64"
