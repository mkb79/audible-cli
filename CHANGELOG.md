# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Fixed

- `audible api` no longer drops a query written into the endpoint: `api "library?num_results=5"` used to ask for the library without it (#311)
- `audible api` no longer repeats a query parameter into oblivion: giving the same key twice used to keep only the last (#311)
- A malformed `--query`, `--body` or `--indent` is now a usage error instead of a traceback and exit 3 (#311)
- `audible api` honours `--timeout` like every other command, rather than the library default of 10 seconds (#311)
- `--version` no longer exits 1 with the error glued to the version when the update check cannot reach GitHub (#309)
- A podcast episode that is already downloaded no longer costs a voucher on every run (#299)
- A cover size a title does not offer is reported as a warning, not an error (#300)
- A request to Audible's CDE host is tried up to three times when it gets no answer (#298)
- A failed download job now names the command, the title and the ASIN (#298)
- AAXC downloads of podcast episodes are no longer rejected over their `audio/mp3` content type (#297)
- The content type is compared by its media type, so parameters and casing no longer decide the outcome (#297)

### Changed

- `audible api` takes a path, not a URL. An absolute URL used to be sent as given, with the request signed for that host; the endpoint is now rejected before any credentials are loaded. The other hosts audible-cli talks to move to the new `audible request` (#311)
- `audible api` requires a JSON response. A 200 carrying something else — an HTML maintenance page, say — used to be quoted into a JSON string and reported as success (#311)
- `audible api` refuses what it cannot send: a body on a `GET` or a `DELETE`, an endpoint that names nothing, a fragment. The body used to be parsed and then left behind (#311)
- `audible api -f dict` is gone. It printed the answer as a Python literal, which no json reader takes, and `--indent` was ignored for it; with `--output` it failed with a `TypeError`. `--format` accepts only `json` now, and says it is on its way out (#311)
- `audible api --param` is now spelled `--query`/`-q`. The old spelling still works and says so; `-p` no longer belongs to this command, because it is the password option on the group (#311)
- Log output now goes to stderr, so stdout carries only what a command produces; `audible download … > log.txt` needs `2>` or the new `--log-file` from now on (#306)
- Nine remaining status messages became log messages, so `--verbosity` reaches them too (#306)
- `--version` prints the version alone; the update notice goes to stderr (#306)
- Prompts and the sentences explaining them moved to stderr with the rest, so a redirected stdout no longer swallows half of `quickstart` (#306)
- Progress bars now hold their line: a ruled block at the bottom of the terminal with one numbered line per worker and one for the queue, which follows the window through a resize and keeps the queue line when there is no room for the rest; `--no-progress` turns it off (#302)
- A title too long for the window loses its middle instead of the bar and the counts it was pushing off the line (#302)

### Added

- `audible request URL` sends one request to any host audible-cli talks to and writes the answer as it arrived. It is for what `audible api` is not: the website, the delivery host and Amazon's CDE service, bodies that are not JSON, and answers that are not either. The host has to be one of `api`, `www`, `cds` or `api.amazon` of a marketplace, or `cde-ta-g7g.amazon.com` — the request carries the credentials of the profile, and a url naming anything else is refused before they are loaded (#NNN)
- `audible api --body-file` reads the request body from a file, or from standard input with `-` (#311)
- `audible api --header/-H` sends a request header, the same name as often as needed; the headers that carry the authentication or describe the body are refused (#311)
- `audible api --dump-header/-D` writes the status line and the response headers to a file, which is where `total-count` and `continuation-token` come from (#311)
- `--log-file PATH` writes the log to a file as well, with timestamp, module and line (#306)
- `NO_COLOR` and `FORCE_COLOR` decide whether diagnostics are coloured (#306)
- Standalone builds for arm64 on Linux and Windows; the macOS build stays Apple silicon only (#296)
- Release download names now carry the architecture, for example `audible_win_arm64.zip`; the previous names stay until 15 November 2026 (#296)
- A `pycryptodome` extra for platforms `cryptography` publishes no wheel for (#296)
- Documentation for running audible-cli in CI with GitHub Actions via the `setup-audible-cli` action, storing only the two device credentials needed for ADP signing (#303)

## [0.5.1] - 2026-08-15

### Fixed

- `build_auth_file()` no longer raises an `AttributeError` when its `filename` is passed as a `str` (#273)
- The `convert-oa-file` plugin works again, after failing to even load since v0.2 (#286)
- The size of a finished audiobook download is checked again (#291)

## [0.5.0] - 2026-08-05

### Breaking

- `parse_api_datetime()` and `datetime_type` now return timezone-aware UTC values instead of naive ones, so plugins comparing them against naive datetimes raise `TypeError` (#266)

### Changed

- API timestamps, library date filters and voucher deadlines are now timezone-aware UTC; naive `--start-date`/`--end-date` input is interpreted as UTC (#266)
- Replaced `datetime.utcnow()` and `datetime.utcfromtimestamp()`, deprecated since Python 3.12 (#266)
- Without `--ignore-errors`, the download command now really aborts on the first failure: running downloads finish, queued ones are skipped (#235)
- The download command exits non-zero when a download job raised an error, also with `--ignore-errors`; failures that are only logged, such as an unknown ASIN, still exit zero (#256)
- `--jobs` now rejects values below 1 instead of accepting `0` (#235)

### Fixed

- Accept API timestamps with and without fractional seconds, fixing the `--start-date`/`--end-date` crash on podcast episodes (#264)
- Keep items whose date cannot be determined instead of crashing with an `AttributeError` (#268)
- Skip the voucher refresh check when `refresh_date` is empty or null, not only when the key is absent (#268)
- Treat an unknown `publication_datetime` as published rather than crashing (#268)
- Reach `LicenseDenied` and `NoDownloadUrl` as intended when `license_denial_reasons`, `content_metadata` or `content_url` are null (#268)
- `audible manage config edit` no longer crashes with `TypeError: 'PosixPath' object is not iterable` (#248)
- The download command no longer hangs forever after failed downloads (#235, #239)

## [0.4.0] - 2026-07-20

### Added

- The `--page-size` option as a replacement for the now deprecated `--bunch-size` option (#241)
- Optional `cryptography` extra (`pip install "audible-cli[cryptography]"`) that enables audible's Rust-accelerated crypto backend (#263)
- Prebuilt release binaries now bundle the Rust-accelerated `cryptography` backend, statically linked via PyInstaller (#263)

### Changed

- The `--bunch-size` option is now deprecated and will be replaced with option `--page-size` some releases later (#241)
- Raised the minimum supported Python to `>=3.11,<3.15` (dropped Python 3.10, added 3.14) (#263)
- Bumped `audible` to `>=0.11.0` and `httpx` to `>=0.27.2,<0.29` (#263)
- Raised minimum versions for `aiofiles`, `click`, `Pillow`, `tabulate`, `tqdm`, `questionary`, `packaging`, and `colorama` (#263)

### Fixed

- Allow the `audio/mp4` content type when downloading audiobooks (#255)

## [0.3.3] - 2025-08-14

### Added

- The `—-filename-length` option is added to the download command. Defaults to 230.
- Added the `asin_only` mode to function `models.BaseItem.create_base_filename` to output the asin as the base filename. 

### Changed

- The function `models.BaseItem.create_base_filename` now limits the output length to 230 chars by default. The output length can be changed by using the `max_length` argument.
- The `—-filename-mode` option of the download command now accepts `asin_only` as mode. 

### Misc

- switch from setup.py to pyproject.toml
- use uv for development package management

## [0.3.2] - 2025-05-24

### Bugfix

- Fixing `[Errno 18] Invalid cross-device link` when downloading files using the `--output-dir` option. This error is fixed by creating the resume file on the same location as the target file.

### Added

- The `--chapter-type` option is added to the download command. Chapter can now be 
  downloaded as `flat` or `tree` type. `tree` is the default. A default chapter type 
  can be set in the config file.

### Changed

- Improved podcast ignore feature in download command
- make `--ignore-podcasts` and `--resolve-podcasts` options of download command mutual 
  exclusive
- Switched from a HEAD to a GET request without loading the body in the downloader 
  class. This change improves the program's speed, as the HEAD request was taking 
  considerably longer than a GET request on some Audible pages.
- `models.LibraryItem.get_content_metadatata` now accept a `chapter_type` argument. 
  Additional keyword arguments to this method are now passed through the metadata 
  request.
- Update httpx version range to >=0.23.3 and <0.28.0.
- fix typo from `resolve_podcats` to `resolve_podcasts`
- `models.Library.resolve_podcats` is now deprecated and will be removed in a future version

### Removed

- Python 3.6 - 3.9 compatibility

## [0.3.1] - 2024-03-19

### Bugfix

- fix a `TypeError` on some Python versions when calling `importlib.metadata.entry_points` with group argument

## [0.3.0] - 2024-03-19

### Added

- Added a resume feature when downloading aaxc files.
- New `downlaoder` module which contains a rework of the Downloader class.
- If necessary, large audiobooks are now downloaded in parts.
- Plugin command help page now contains additional information about the source of 
  the plugin.
- Command help text now starts with ´(P)` for plugin commands.

### Changed

- Rework plugin module
- using importlib.metadata over setuptools (pkg_resources) to get entrypoints

## [0.2.6] - 2023-11-16

### Added

- Update marketplace choices in `manage auth-file add` command. Now all available marketplaces are listed.

### Bugfix

- Avoid tqdm progress bar interruption by logger’s output to console.
- Fixing an issue with unawaited coroutines when the download command exited abnormal.

### Changed

- Update httpx version range to >=0.23.3 and <0.26.0. 
 
### Misc

- add `freeze_support` to pyinstaller entry script (#78)

## [0.2.5] - 2023-09-26

### Added

- Dynamically load available marketplaces from the `audible package`. Allows to implement a new marketplace without updating `audible-cli`.

## [0.2.4] - 2022-09-21

### Added

- Allow download multiple cover sizes at once. Each cover size must be provided with the `--cover-size` option


### Changed

- Rework start_date and end_date option

### Bugfix

- In some cases, the purchase date is None. This results in an exception. Now check for purchase date or date added and skip, if date is missing

## [0.2.3] - 2022-09-06

### Added

- `--start-date` and `--end-date` option to `download` command
- `--start-date` and `--end-date` option to `library export` and `library list` command
- better error handling for license requests
- verify that a download link is valid
- make sure an item is published before downloading the aax, aaxc or pdf file
- `--ignore-errors` flag of the download command now continue, if an item failed to download

## [0.2.2] - 2022-08-09

### Bugfix

- PDFs could not be found using the download command (#112)

## [0.2.1] - 2022-07-29

### Added

- `library` command now outputs the `extended_product_description` field

### Changed

- by default a licenserequest (voucher) will not include chapter information by default
- moved licenserequest part from `models.LibraryItem.get_aaxc_url` to its own `models.LibraryItem.get_license` function
- allow book titles with hyphens (#96)
- if there is no title fallback to an empty string (#98)
- reduce `response_groups` for the download command to speed up fetching the library (#109)

### Fixed

- `Extreme` quality is not supported by the Audible API anymore (#107)
- download command continued execution after error (#104)
- Currently, paths with dots will break the decryption (#97)
- `models.Library.from_api_full_sync` called `models.Library.from_api` with incorrect keyword arguments

### Misc

- reworked `cmd_remove-encryption` plugin command (e.g. support nested chapters, use chapter file for aaxc files)
- added explanation in README.md for creating a second profile

## [0.2.0] - 2022-06-01

### Added

- `--aax-fallback` option to `download` command to download books in aax format and fallback to aaxc, if the book is not available as aax
- `--annotation` option to `download` command to get bookmarks and notes
- `questionary` package to dependencies
- `add` and `remove` subcommands to wishlist
- `full_response_callback` to `utils`
- `export_to_csv` to `utils`
- `run_async` to `decorators`
- `pass_client` to `decorators`
- `profile_option` to `decorators`
- `password_option` to `decorators`
- `timeout_option` to `decorators`
- `bunch_size_option` to `decorators`
- `ConfigFile.get_profile_option` get the value for an option for a given profile
- `Session.selected.profile` to get the profile name for the current session
- `Session.get_auth_for_profile` to get an auth file for a given profile
- `models.BaseItem.create_base_filename` to build a filename in given mode
- `models.LibraryItem.get_annotations` to get annotations for a library item

### Changed

- bump `audible` to v0.8.2 to fix a bug in httpx
- rework plugin examples in `plugin_cmds`
- rename `config.Config` to `config.ConfigFile`
- move `click_verbosity_logger` from `_logging` to `decorators` and rename it to `verbosity_option`
- move `wrap_async` from `utils` to `decorators`
- move `add_param_to_session` from `config` to `decorators`
- move `pass_session` from `config` to `decorators`
- `download` command let you now select items when using `--title` option

### Fixed

- the `library export` and `wishlist export` command will now export to `csv` correctly
- 

## [0.1.3] - 2022-03-27

### Bugfix

- fix a bug with the registration url

## [0.1.2] - 2022-03-27

### Bugfix

- bump Audible to v0.7.1 to fix a bug when register a new device with pre-Amazon account

## [0.1.1] - 2022-03-20

### Added

- the `--version` option now checks if an update for `audible-cli` is available
- build macOS releases in `onedir` mode

### Bugfix

- fix a bug where counting an item if the download fails
- fix an issue where some items could not be downloaded do tue wrong content type
- fix an issue where an aax downloaded failed with a `codec doesn't support full file assembly` message

## [0.1.0] - 2022-03-11

### Added

- add the `api` command to make requests to the AudibleAPI
- a counter of downloaded items for the download command
- the `--verbosity/-v` option; default is INFO
- the `--bunch-size` option to the download, library export and library list subcommand; this is only needed on slow internet connections
- `wishlist` subcommand
- the `--resolve-podcasts` flag to download subcommand; all episodes of a podcast will be fetched at startup, so a single episode can be searched via his title or asin
- the `--ignore-podcasts` flag to download subcommand; if a podcast contains multiple episodes, the podcast will be ignored
- the`models.Library.resolve_podcasts` method to append all podcast episodes to given library.
- the `models.LibraryItem.get_child_items` method to get all episodes of a podcast item or parts for a MultiPartBook.
- the`models.BaseItem` now holds a list of `response_groups` in the `_response_groups` attribute. 
- the`--format` option to `library export` subcommand
- the `models.Catalog` class
- the `models.Library.from_api_full_sync` method to fetch the full library

### Changed

- the `--aaxc` flag of the download command now try to check if a voucher file exists before a `licenserequest` is make (issue #60)
- the `--aaxc` flag of the download command now downloads mp3/m4a files if the `aaxc` format is not available and the `licenserequest` offers this formats
- the `download` subcommand now download podcasts
- *Remove sync code where async code are available. All plugins should take care about this!!!*
- Bump `audible` to v0.7.0
- rebuild `models.LibraryItem.get_aax_url` to build the aax download url in another way 
- `models.BaseItem.full_title` now contains publication name for podcast episodes
- `models.LibraryItem` now checks the customer rights when calling `LibraryItem._is_downloadable`
- `models.BaseItem` and `models.BaseList` now holds the `api_client` instead the `locale` and `auth`
- rename `models.Wishlist.get_from_api` to `models.Wishlist.from_api`
- rename `models.Library.get_from_api` to `models.Library.from_api`; this method does not fetch the full library for now

### Misc

- bump click to v8

### Bugfix

- removing an error using the `--output` option of the `library export` command
- fixing some other bugs

## [0.0.9] - 2022-01-18

### Bugfix

- bugfix error adding/removing auth file

## [0.0.8] - 2022-01-15

### Bugfix

- bugfix errors in utils.py

## [0.0.7] - 2022-01-15

### Bugfix

- utils.py: Downloading pdf files was broken. Downloader now follows a redirect when downloading a file.

### Added

- Add spec file to create binary with pyinstaller
- Add binary for some platforms
- Add timeout option to download command

### Changed
- models.py: If no supported codec is found when downloading aax files, no url
  is returned now.
- utils.py: Downloading a file with the `Downloader` class now checks the 
  response status code, the content type and compares the file size.
- models.py: Now all books are fetched if the library is greater than 1000.
  This works for the download and library command.

## [0.0.6] - 2022-01-07

### Bugfix

- cmd_library.py: If library does not contain a cover url, audible-cli
  has raised an Exception. Now the cover url field will set to '-' if no
  cover url is available.
