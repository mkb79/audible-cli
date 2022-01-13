# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## Unreleased

### Added

- Add spec file to create binary with pyinstaller
- Add binary for some platforms
- Add timeout option to download command

### Changed
- models.py: If no supported codec is found when downloading aax files, no url
  is returned now.
- utils.py: Downloading a file with the `Downloader` class now checks the 
  response status code. If the status code is not okay, the error message is
  printed out. The downloaded tmp file is kept in download dir.
- models.py: Now all books are fetched if the library is greater than 1000.
  This works for the download and library command.

## [0.0.6] - 2022-01-07

### Bugfix

- cmd_library.py: If library does not contain a cover url, audible-cli
  has raised an Exception. Now the cover url field will set to '-' if no
  cover url is available.
