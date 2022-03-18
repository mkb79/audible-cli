# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

### Added

- the `--version` option now checks if an update for `audible-cli` is available.

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
