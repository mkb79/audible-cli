"""
This script replaces the chapter titles from a ffmetadata file with the one
extracted from an API metadata/voucher file

Example:

.. code-block:: shell

   # download a book and the chapter file
   audible download -a {ASIN_OF_BOOK} --chapter

   # extract the metadata from aax file
   ffmpeg -i {NAME_OF_DOWNLOADED_AAX_FILE} -f ffmetadata {FFMETADATAFILE}

   # replace chapter titles from extracted metadatafile
   python update_chapter_titles.py -f {FFMETADATAFILE} \
                                   -a {NAME_OF_DOWNLOADED_CHAPTER_FILE} \
                                   -o {NEW_FFMETADATAFILE}

   # insert new metadata in file and convert it to m4b
   # ffmpeg 4.1 and above support copying album art with `-c copy`
   ffmpeg -activation_bytes {ACTIVATION_BYTES} \
          -i {NAME_OF_DOWNLOADED_AAX_FILE} \
          -i {NEW_FFMETADATAFILE} \
          -map_metadata 0 \
          -map_chapters 1 \
          -c copy \
          {NAME_OF_TARGET_M4B_FILE.m4b}

"""

import json
import operator
import pathlib
import re
import sys

import click
from click import echo


class ApiMeta:
    def __init__(self, api_meta_file):
        self._meta_raw = pathlib.Path(api_meta_file).read_text("utf-8")
        self._meta_parsed = self._parse_meta()

    def _parse_meta(self):
        data = json.loads(self._meta_raw)
        return data.get("content_license", data)

    def count_chapters(self):
        return len(self.get_chapters())

    def get_chapters(self):
        return self._meta_parsed["content_metadata"]["chapter_info"]["chapters"]

    def get_intro_duration_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "brandIntroDurationMs"
        ]

    def get_outro_duration_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "brandOutroDurationMs"
        ]

    def get_runtime_length_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "runtime_length_ms"
        ]


class FFMeta:
    SECTION = re.compile(r"\[(?P<header>[^]]+)\]")
    OPTION = re.compile(r"(?P<option>.*?)\s*(?:(?P<vi>=)\s*(?P<value>.*))?$")

    def __init__(self, ffmeta_file):
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

    def _write_section(self, fp, section_name, section_items, delimiter):
        """Write a single section to the specified `fp`."""
        if section_name is not None:
            fp.write(f"[{section_name}]\n")

        for key, value in section_items.items():
            if value is None:
                fp.write(f"{key}\n")
            else:
                fp.write(f"{key}{delimiter}{value}\n")

    def update_title_from_api_meta(self, api_meta):
        if not isinstance(api_meta, ApiMeta):
            api_meta = ApiMeta(api_meta)

        assert api_meta.count_chapters() == self.count_chapters()

        echo(f"Found {self.count_chapters()} chapters to prepare.")

        num_chap = 0
        for chapter in api_meta.get_chapters():
            num_chap += 1
            for key, value in chapter.items():
                if key == "title":
                    self.set_chapter_option(num_chap, "title", value)

    def update_chapters_from_api_meta(self, api_meta, separate_branding=True):
        """Replace all chapter data from api meta file.

        This replaces TIMEBASE, START, END and title. If api meta files contains
        more chapters than ffmetadata file, the additionell chapters are added.
        If separate_branding is True Audible Branding Intro and Outro will become
        there own chapter.
        """
        if not isinstance(api_meta, ApiMeta):
            api_meta = ApiMeta(api_meta)

        if api_meta.count_chapters() != self.count_chapters():
            # This happens on some of my books
            # but runtime is identical +- few ms
            echo("Missmatch between chapter numbers.")
            click.confirm("Do you want to continue?", abort=True)

        echo(f"Found {self.count_chapters()} chapters to prepare.")

        api_chapters = api_meta.get_chapters()
        if separate_branding:
            echo("Separate Audible Brand Intro and Outro to own Chapter.")
            api_chapters.sort(key=operator.itemgetter("start_offset_ms"))

            first = api_chapters[0]
            intro_dur_ms = api_meta.get_intro_duration_ms()
            first["start_offset_ms"] = intro_dur_ms
            first["start_offset_sec"] = round(first["start_offset_ms"] / 1000)
            first["length_ms"] -= intro_dur_ms

            last = api_chapters[-1]
            outro_dur_ms = api_meta.get_outro_duration_ms()
            last["length_ms"] -= outro_dur_ms

            api_chapters.append(
                {
                    "length_ms": intro_dur_ms,
                    "start_offset_ms": 0,
                    "start_offset_sec": 0,
                    "title": "Intro",
                }
            )
            api_chapters.append(
                {
                    "length_ms": outro_dur_ms,
                    "start_offset_ms": api_meta.get_runtime_length_ms() - outro_dur_ms,
                    "start_offset_sec": round(
                        (api_meta.get_runtime_length_ms() - outro_dur_ms) / 1000
                    ),
                    "title": "Outro",
                }
            )
            api_chapters.sort(key=operator.itemgetter("start_offset_ms"))

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


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--ffmeta",
    "-f",
    type=click.Path(exists=True, file_okay=True, readable=True),
    required=True,
    help="ffmetadata file extracted from file with ffmpeg",
)
@click.option(
    "--apimeta",
    "-a",
    type=click.Path(exists=True, file_okay=True, readable=True),
    required=True,
    help="metadata from api",
)
@click.option(
    "--outfile",
    "-o",
    type=click.Path(exists=False, file_okay=True),
    required=True,
    help="filename to store prepared ffmeta",
)
@click.option(
    "--separate-branding",
    "-s",
    is_flag=True,
    help="Separate Intro and Outro branding into own chapters",
)
def cli(ffmeta, apimeta, outfile, separate_branding):
    ffmeta_class = FFMeta(ffmeta)
    if separate_branding:
        ffmeta_class.update_chapters_from_api_meta(apimeta)
    else:
        ffmeta_class.update_title_from_api_meta(apimeta)
    ffmeta_class.write(outfile)
    click.echo(f"Replaced all titles. Save file to {outfile}")


def main(*args, **kwargs):
    try:
        cli(*args, **kwargs)
    except KeyboardInterrupt:
        sys.exit("\nERROR: Interrupted by user")


if __name__ == "__main__":
    sys.exit(main())
