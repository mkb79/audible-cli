"""Removes encryption of aax and aaxc files.

This is a proof-of-concept and for testing purposes only.

No error handling.
Need further work. Some options do not work or options are missing.

Needs at least ffmpeg 4.4
"""

import json
import operator
import pathlib
import re
import subprocess
import tempfile
import typing as t
from enum import Enum
from functools import reduce
from glob import glob
from shutil import which

import click
from click import echo, secho

from audible_cli.decorators import pass_session
from audible_cli.exceptions import AudibleCliException


class ChapterError(AudibleCliException):
    """Base class for all chapter errors."""


class SupportedFiles(Enum):
    AAX = ".aax"
    AAXC = ".aaxc"

    @classmethod
    def get_supported_list(cls):
        return list({item.value for item in cls})

    @classmethod
    def is_supported_suffix(cls, value):
        return value in cls.get_supported_list()

    @classmethod
    def is_supported_file(cls, value):
        return pathlib.PurePath(value).suffix in cls.get_supported_list()


def _get_input_files(
    files: tuple[str] | list[str], recursive: bool = True
) -> list[pathlib.Path]:
    filenames = []
    for filename in files:
        # if the shell does not do filename globbing
        expanded = list(glob(filename, recursive=recursive))

        if (
            len(expanded) == 0
            and "*" not in filename
            and not SupportedFiles.is_supported_file(filename)
        ):
            raise click.BadParameter("{filename}: file not found or supported.")

        expanded_filter = filter(
            lambda x: SupportedFiles.is_supported_file(x), expanded
        )
        expanded = [pathlib.Path(x).resolve() for x in expanded_filter]
        filenames.extend(expanded)

    return filenames


def recursive_lookup_dict(key: str, dictionary: dict[str, t.Any]) -> t.Any:
    if key in dictionary:
        return dictionary[key]
    for value in dictionary.values():
        if isinstance(value, dict):
            try:
                item = recursive_lookup_dict(key, value)
            except KeyError:
                continue
            else:
                return item

    raise KeyError


def get_aaxc_credentials(voucher_file: pathlib.Path):
    if not voucher_file.exists() or not voucher_file.is_file():
        raise AudibleCliException(f"Voucher file {voucher_file} not found.")

    voucher_dict = json.loads(voucher_file.read_text())
    try:
        key = recursive_lookup_dict("key", voucher_dict)
        iv = recursive_lookup_dict("iv", voucher_dict)
    except KeyError:
        raise AudibleCliException(f"No key/iv found in file {voucher_file}.") from None

    return key, iv


class ApiChapterInfo:
    def __init__(self, content_metadata: dict[str, t.Any]) -> None:
        chapter_info = self._parse(content_metadata)
        self._chapter_info = chapter_info

    @classmethod
    def from_file(cls, file: pathlib.Path | str) -> "ApiChapterInfo":
        file = pathlib.Path(file)
        if not file.exists() or not file.is_file():
            raise ChapterError(f"Chapter file {file} not found.")
        content_string = pathlib.Path(file).read_text("utf-8")
        content_json = json.loads(content_string)
        return cls(content_json)

    @staticmethod
    def _parse(content_metadata: dict[str, t.Any]) -> dict[str, t.Any]:
        if "chapters" in content_metadata:
            return content_metadata

        try:
            return recursive_lookup_dict("chapter_info", content_metadata)
        except KeyError:
            raise ChapterError("No chapter info found.") from None

    def count_chapters(self):
        return len(self.get_chapters())

    def get_chapters(self, separate_intro_outro=False, remove_intro_outro=False):
        def extract_chapters(initial, current):
            if "chapters" in current:
                return initial + [current] + current["chapters"]
            else:
                return [*initial, current]

        chapters = list(
            reduce(
                extract_chapters,
                self._chapter_info["chapters"],
                [],
            )
        )

        if separate_intro_outro:
            return self._separate_intro_outro(chapters)
        elif remove_intro_outro:
            return self._remove_intro_outro(chapters)

        return chapters

    def get_intro_duration_ms(self):
        return self._chapter_info["brandIntroDurationMs"]

    def get_outro_duration_ms(self):
        return self._chapter_info["brandOutroDurationMs"]

    def get_runtime_length_ms(self):
        return self._chapter_info["runtime_length_ms"]

    def is_accurate(self):
        return self._chapter_info["is_accurate"]

    def _separate_intro_outro(self, chapters):
        echo("Separate Audible Brand Intro and Outro to own Chapter.")
        chapters.sort(key=operator.itemgetter("start_offset_ms"))

        first = chapters[0]
        intro_dur_ms = self.get_intro_duration_ms()
        first["start_offset_ms"] = intro_dur_ms
        first["start_offset_sec"] = round(first["start_offset_ms"] / 1000)
        first["length_ms"] -= intro_dur_ms

        last = chapters[-1]
        outro_dur_ms = self.get_outro_duration_ms()
        last["length_ms"] -= outro_dur_ms

        chapters.append(
            {
                "length_ms": intro_dur_ms,
                "start_offset_ms": 0,
                "start_offset_sec": 0,
                "title": "Intro",
            }
        )
        chapters.append(
            {
                "length_ms": outro_dur_ms,
                "start_offset_ms": self.get_runtime_length_ms() - outro_dur_ms,
                "start_offset_sec": round(
                    (self.get_runtime_length_ms() - outro_dur_ms) / 1000
                ),
                "title": "Outro",
            }
        )
        chapters.sort(key=operator.itemgetter("start_offset_ms"))

        return chapters

    def _remove_intro_outro(self, chapters):
        echo("Delete Audible Brand Intro and Outro.")
        chapters.sort(key=operator.itemgetter("start_offset_ms"))

        intro_dur_ms = self.get_intro_duration_ms()
        outro_dur_ms = self.get_outro_duration_ms()

        first = chapters[0]
        first["length_ms"] -= intro_dur_ms

        for chapter in chapters[1:]:
            chapter["start_offset_ms"] -= intro_dur_ms
            chapter["start_offset_sec"] -= round(chapter["start_offset_ms"] / 1000)

        last = chapters[-1]
        last["length_ms"] -= outro_dur_ms

        return chapters


class FFMeta:
    SECTION = re.compile(r"\[(?P<header>[^]]+)\]")
    OPTION = re.compile(r"(?P<option>.*?)\s*(?:(?P<vi>=)\s*(?P<value>.*))?$")

    def __init__(self, ffmeta_file: str | pathlib.Path) -> None:
        self._ffmeta_raw = pathlib.Path(ffmeta_file).read_text("utf-8")
        self._ffmeta_parsed = self._parse_ffmeta()

    def _parse_ffmeta(self):
        parsed_dict = {}
        start_section = "_"
        cursec = parsed_dict[start_section] = {}
        num_chap = 0

        for line in iter(self._ffmeta_raw.splitlines()):
            mo = self.SECTION.match(line)
            if mo:
                sec_name = mo.group("header")
                if sec_name == "CHAPTER":
                    num_chap += 1
                    if sec_name not in parsed_dict:
                        parsed_dict[sec_name] = {}
                    cursec = parsed_dict[sec_name][num_chap] = {}
                else:
                    cursec = parsed_dict[sec_name] = {}
            else:
                match = self.OPTION.match(line)
                cursec.update({match.group("option"): match.group("value")})

        return parsed_dict

    def count_chapters(self):
        return len(self._ffmeta_parsed["CHAPTER"])

    def set_chapter_option(self, num, option, value):
        chapter = self._ffmeta_parsed["CHAPTER"][num]
        for chapter_option in chapter:
            if chapter_option == option:
                chapter[chapter_option] = value

    def write(self, filename):
        fp = pathlib.Path(filename).open("w", encoding="utf-8")
        d = "="

        for section in self._ffmeta_parsed:
            if section == "_":
                self._write_section(fp, None, self._ffmeta_parsed[section], d)
            elif section == "CHAPTER":
                # TODO: Tue etwas
                for chapter in self._ffmeta_parsed[section]:
                    self._write_section(
                        fp, section, self._ffmeta_parsed[section][chapter], d
                    )
            else:
                self._write_section(fp, section, self._ffmeta_parsed[section], d)

    @staticmethod
    def _write_section(fp, section_name, section_items, delimiter):
        """Write a single section to the specified `fp`."""
        if section_name is not None:
            fp.write(f"[{section_name}]\n")

        for key, value in section_items.items():
            if value is None:
                fp.write(f"{key}\n")
            else:
                fp.write(f"{key}{delimiter}{value}\n")

    def update_chapters_from_chapter_info(
        self,
        chapter_info: ApiChapterInfo,
        force_rebuild_chapters: bool = False,
        separate_intro_outro: bool = False,
        remove_intro_outro: bool = False,
    ) -> None:
        if not chapter_info.is_accurate():
            echo("Metadata from API is not accurate. Skip.")
            return

        if chapter_info.count_chapters() != self.count_chapters():
            if force_rebuild_chapters:
                echo("Force rebuild chapters due to chapter mismatch.")
            else:
                raise ChapterError("Chapter mismatch")

        echo(f"Found {chapter_info.count_chapters()} chapters to prepare.")

        api_chapters = chapter_info.get_chapters(
            separate_intro_outro, remove_intro_outro
        )

        num_chap = 0
        new_chapters = {}
        for chapter in api_chapters:
            chap_start = chapter["start_offset_ms"]
            chap_end = chap_start + chapter["length_ms"]
            num_chap += 1
            new_chapters[num_chap] = {
                "TIMEBASE": "1/1000",
                "START": chap_start,
                "END": chap_end,
                "title": chapter["title"],
            }
        self._ffmeta_parsed["CHAPTER"] = new_chapters

    def get_start_end_without_intro_outro(
        self,
        chapter_info: ApiChapterInfo,
    ):
        intro_dur_ms = chapter_info.get_intro_duration_ms()
        outro_dur_ms = chapter_info.get_outro_duration_ms()
        total_runtime_ms = chapter_info.get_runtime_length_ms()

        start_new = intro_dur_ms
        duration_new = total_runtime_ms - intro_dur_ms - outro_dur_ms

        return start_new, duration_new


def _get_voucher_filename(file: pathlib.Path) -> pathlib.Path:
    return file.with_suffix(".voucher")


def _get_chapter_filename(file: pathlib.Path) -> pathlib.Path:
    base_filename = file.stem.rsplit("-", 1)[0]
    return file.with_name(base_filename + "-chapters.json")


def _get_ffmeta_file(file: pathlib.Path, tempdir: pathlib.Path) -> pathlib.Path:
    metaname = file.with_suffix(".meta").name
    metafile = tempdir / metaname
    return metafile


class FfmpegFileDecrypter:
    def __init__(
        self,
        file: pathlib.Path,
        target_dir: pathlib.Path,
        tempdir: pathlib.Path,
        activation_bytes: str | None,
        overwrite: bool,
        rebuild_chapters: bool,
        force_rebuild_chapters: bool,
        skip_rebuild_chapters: bool,
        separate_intro_outro: bool,
        remove_intro_outro: bool,
    ) -> None:
        file_type = SupportedFiles(file.suffix)

        credentials = None
        if file_type == SupportedFiles.AAX:
            if activation_bytes is None:
                raise AudibleCliException(
                    "No activation bytes found. Do you ever run "
                    "`audible activation-bytes`?"
                )
            credentials = activation_bytes
        elif file_type == SupportedFiles.AAXC:
            voucher_filename = _get_voucher_filename(file)
            credentials = get_aaxc_credentials(voucher_filename)

        self._source = file
        self._credentials: str | tuple[str] | None = credentials
        self._target_dir = target_dir
        self._tempdir = tempdir
        self._overwrite = overwrite
        self._rebuild_chapters = rebuild_chapters
        self._force_rebuild_chapters = force_rebuild_chapters
        self._skip_rebuild_chapters = skip_rebuild_chapters
        self._separate_intro_outro = separate_intro_outro
        self._remove_intro_outro = remove_intro_outro
        self._api_chapter: ApiChapterInfo | None = None
        self._ffmeta: FFMeta | None = None
        self._is_rebuilded: bool = False

    @property
    def api_chapter(self) -> ApiChapterInfo:
        if self._api_chapter is None:
            try:
                voucher_filename = _get_voucher_filename(self._source)
                self._api_chapter = ApiChapterInfo.from_file(voucher_filename)
            except ChapterError:
                voucher_filename = _get_chapter_filename(self._source)
                self._api_chapter = ApiChapterInfo.from_file(voucher_filename)
            echo(f"Using chapters from {voucher_filename}")
        return self._api_chapter

    @property
    def ffmeta(self) -> FFMeta:
        if self._ffmeta is None:
            metafile = _get_ffmeta_file(self._source, self._tempdir)

            base_cmd = [
                "ffmpeg",
                "-v",
                "quiet",
                "-stats",
            ]
            if isinstance(self._credentials, tuple):
                key, iv = self._credentials
                credentials_cmd = [
                    "-audible_key",
                    key,
                    "-audible_iv",
                    iv,
                ]
            else:
                credentials_cmd = [
                    "-activation_bytes",
                    self._credentials,
                ]
            base_cmd.extend(credentials_cmd)

            extract_cmd = [
                "-i",
                str(self._source),
                "-f",
                "ffmetadata",
                str(metafile),
            ]
            base_cmd.extend(extract_cmd)

            subprocess.check_output(base_cmd, text=True)  # noqa: S603
            self._ffmeta = FFMeta(metafile)

        return self._ffmeta

    def rebuild_chapters(self) -> None:
        if not self._is_rebuilded:
            self.ffmeta.update_chapters_from_chapter_info(
                self.api_chapter,
                self._force_rebuild_chapters,
                self._separate_intro_outro,
                self._remove_intro_outro,
            )
            self._is_rebuilded = True

    def run(self):
        oname = self._source.with_suffix(".m4b").name
        outfile = self._target_dir / oname

        if outfile.exists():
            if self._overwrite:
                secho(f"Overwrite {outfile}: already exists", fg="blue")
            else:
                secho(f"Skip {outfile}: already exists", fg="blue")
                return

        base_cmd = [
            "ffmpeg",
            "-v",
            "quiet",
            "-stats",
        ]
        if self._overwrite:
            base_cmd.append("-y")
        if isinstance(self._credentials, tuple):
            key, iv = self._credentials
            credentials_cmd = [
                "-audible_key",
                key,
                "-audible_iv",
                iv,
            ]
        else:
            credentials_cmd = [
                "-activation_bytes",
                self._credentials,
            ]
        base_cmd.extend(credentials_cmd)

        if self._rebuild_chapters:
            metafile = _get_ffmeta_file(self._source, self._tempdir)
            try:
                self.rebuild_chapters()
                self.ffmeta.write(metafile)
            except ChapterError:
                if self._skip_rebuild_chapters:
                    echo("Skip rebuild chapters due to chapter mismatch.")
                else:
                    raise
            else:
                if self._remove_intro_outro:
                    start_new, duration_new = (
                        self.ffmeta.get_start_end_without_intro_outro(self.api_chapter)
                    )

                    base_cmd.extend(
                        [
                            "-ss",
                            f"{start_new}ms",
                            "-t",
                            f"{duration_new}ms",
                            "-i",
                            str(self._source),
                            "-i",
                            str(metafile),
                            "-map_metadata",
                            "0",
                            "-map_chapters",
                            "1",
                        ]
                    )
                else:
                    base_cmd.extend(
                        [
                            "-i",
                            str(self._source),
                            "-i",
                            str(metafile),
                            "-map_metadata",
                            "0",
                            "-map_chapters",
                            "1",
                        ]
                    )
        else:
            base_cmd.extend(
                [
                    "-i",
                    str(self._source),
                ]
            )

        base_cmd.extend(
            [
                "-c",
                "copy",
                str(outfile),
            ]
        )

        subprocess.check_output(base_cmd, text=True)  # noqa: S603

        echo(f"File decryption successful: {outfile}")


@click.command("decrypt")
@click.argument("files", nargs=-1)
@click.option(
    "--dir",
    "-d",
    "directory",
    type=click.Path(exists=True, dir_okay=True),
    default=pathlib.Path.cwd(),
    help="Folder where the decrypted files should be saved.",
    show_default=True,
)
@click.option(
    "--all",
    "-a",
    "all_",
    is_flag=True,
    help="Decrypt all aax and aaxc files in current folder.",
)
@click.option("--overwrite", is_flag=True, help="Overwrite existing files.")
@click.option(
    "--rebuild-chapters",
    "-r",
    is_flag=True,
    help="Rebuild chapters with chapters from voucher or chapter file.",
)
@click.option(
    "--force-rebuild-chapters",
    "-f",
    is_flag=True,
    help=(
        "Force rebuild chapters with chapters from voucher or chapter file "
        "if the built-in chapters in the audio file mismatch. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--skip-rebuild-chapters",
    "-t",
    is_flag=True,
    help=(
        "Decrypt without rebuilding chapters when chapters mismatch. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--separate-intro-outro",
    "-s",
    is_flag=True,
    help=(
        "Separate Audible Brand Intro and Outro to own Chapter. "
        "Only use with `--rebuild-chapters`."
    ),
)
@click.option(
    "--remove-intro-outro",
    "-c",
    is_flag=True,
    help=("Remove Audible Brand Intro and Outro. Only use with `--rebuild-chapters`."),
)
@pass_session
def cli(
    session,
    files: str,
    directory: pathlib.Path | str,
    all_: bool,
    overwrite: bool,
    rebuild_chapters: bool,
    force_rebuild_chapters: bool,
    skip_rebuild_chapters: bool,
    separate_intro_outro: bool,
    remove_intro_outro: bool,
):
    """Decrypt audiobooks downloaded with audible-cli.

    FILES are the names of the file to decrypt.
    Wildcards `*` and recursive lookup with `**` are supported.

    Only FILES with `aax` or `aaxc` suffix are processed.
    Other files are skipped silently.
    """
    if not which("ffmpeg"):
        ctx = click.get_current_context()
        ctx.fail("ffmpeg not found")

    if (
        force_rebuild_chapters
        or skip_rebuild_chapters
        or separate_intro_outro
        or remove_intro_outro
    ) and not rebuild_chapters:
        raise click.BadOptionUsage(
            "",
            "`--force-rebuild-chapters`, `--skip-rebuild-chapters`, `--separate-intro-outro` "
            "and `--remove-intro-outro` can only be used together with `--rebuild-chapters`",
        )

    if force_rebuild_chapters and skip_rebuild_chapters:
        raise click.BadOptionUsage(
            "",
            "`--force-rebuild-chapters` and `--skip-rebuild-chapters` can "
            "not be used together",
        )

    if separate_intro_outro and remove_intro_outro:
        raise click.BadOptionUsage(
            "",
            "`--separate-intro-outro` and `--remove-intro-outro` can not be used together",
        )

    if all_:
        if files:
            raise click.BadOptionUsage(
                "", "If using `--all`, no FILES arguments can be used."
            )
        files = [f"*{suffix}" for suffix in SupportedFiles.get_supported_list()]

    files = _get_input_files(files, recursive=True)
    with tempfile.TemporaryDirectory() as tempdir:
        for file in files:
            decrypter = FfmpegFileDecrypter(
                file=file,
                target_dir=pathlib.Path(directory).resolve(),
                tempdir=pathlib.Path(tempdir).resolve(),
                activation_bytes=session.auth.activation_bytes,
                overwrite=overwrite,
                rebuild_chapters=rebuild_chapters,
                force_rebuild_chapters=force_rebuild_chapters,
                skip_rebuild_chapters=skip_rebuild_chapters,
                separate_intro_outro=separate_intro_outro,
                remove_intro_outro=remove_intro_outro,
            )
            decrypter.run()
