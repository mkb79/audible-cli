"""
This is a proof-of-concept and for testing purposes only. No error handling. 
Need further work. Some options do not work or options are missing.

Needs at least ffmpeg 4.4
"""


import json
import operator
import pathlib
import re
import subprocess
from functools import reduce
from shutil import which

import click
from audible_cli.decorators import pass_session
from click import echo, secho


class ApiMeta:
    def __init__(self, api_meta):
        if not isinstance(api_meta, dict):
            api_meta = pathlib.Path(api_meta).read_text("utf-8")
        self._meta_raw = api_meta
        self._meta_parsed = self._parse_meta()

    def _parse_meta(self):
        if isinstance(self._meta_raw, dict):
            return self._meta_raw
        return json.loads(self._meta_raw)

    def count_chapters(self):
        return len(self.get_chapters())

    def get_chapters(self):
        def extract_chapters(initial, current):
            if "chapters" in current:
                return initial + [current] + current['chapters']
            else:
                return initial + [current]

        return list(reduce(extract_chapters, self._meta_parsed["content_metadata"]["chapter_info"]["chapters"], []))

    def get_intro_duration_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "brandIntroDurationMs"]

    def get_outro_duration_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "brandOutroDurationMs"]

    def get_runtime_length_ms(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "runtime_length_ms"]

    def is_accurate(self):
        return self._meta_parsed["content_metadata"]["chapter_info"][
            "is_accurate"]


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
                    self._write_section(fp, section,
                                        self._ffmeta_parsed[section][chapter],
                                        d)
            else:
                self._write_section(fp, section, self._ffmeta_parsed[section],
                                    d)

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

    def update_chapters_from_api_meta(self, api_meta, separate_intro_outro=True):
        if not isinstance(api_meta, ApiMeta):
            api_meta = ApiMeta(api_meta)

        if not api_meta.is_accurate():
            echo("Metadata from API is not accurate. Skip.")
            return

        # assert api_meta.count_chapters() == self.count_chapters()

        echo(f"Found {self.count_chapters()} chapters to prepare.")

        api_chapters = api_meta.get_chapters()
        if separate_intro_outro:
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

            api_chapters.append({
                "length_ms": intro_dur_ms,
                "start_offset_ms": 0,
                "start_offset_sec": 0,
                "title": "Intro"
            })
            api_chapters.append({
                "length_ms": outro_dur_ms,
                "start_offset_ms": api_meta.get_runtime_length_ms() - outro_dur_ms,
                "start_offset_sec": round((api_meta.get_runtime_length_ms() - outro_dur_ms) / 1000),
                "title": "Outro"
            })
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
                "title": chapter["title"]
            }
        self._ffmeta_parsed["CHAPTER"] = new_chapters


def decrypt_aax(
        files, activation_bytes, rebuild_chapters, ignore_missing_chapters,
        separate_intro_outro
):
    for file in files:
        outfile = file.with_suffix(".m4b")

        if outfile.exists():
            secho(f"Skip {file.name}: already decrypted", fg="blue")
            continue

        can_rebuild_chapters = False
        if rebuild_chapters:
            metafile = file.with_suffix(".meta")
            metafile_new = file.with_suffix(".new.meta")
            base_filename = file.stem.rsplit("-", 1)[0]
            chapter_file = file.with_name(base_filename + "-chapters.json")

            has_chapters = False
            try:
                content_metadata = json.loads(chapter_file.read_text())
            except:
                secho(f"No chapter data found for {file.name}", fg="red")
            else:
                echo(f"Using chapters from {chapter_file.name}")
                has_chapters = True

            if has_chapters:
                if not content_metadata["content_metadata"]["chapter_info"][
                        "is_accurate"]:
                    secho(f"Chapter data are not accurate", fg="red")
                else:
                    can_rebuild_chapters = True

        if rebuild_chapters and not can_rebuild_chapters and not ignore_missing_chapters:
            secho(f"Skip {file.name}: chapter data can not be rebuild", fg="red")
            continue

        if can_rebuild_chapters:
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-activation_bytes", activation_bytes,
                "-i", str(file),
                "-f", "ffmetadata",
                str(metafile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)

            ffmeta_class = FFMeta(metafile)
            ffmeta_class.update_chapters_from_api_meta(
                content_metadata, separate_intro_outro
            )
            ffmeta_class.write(metafile_new)
            click.echo("Replaced all titles.")

            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-activation_bytes", activation_bytes,
                "-i", str(file),
                "-i", str(metafile_new),
                "-map_metadata", "0",
                "-map_chapters", "1",
                "-c", "copy",
                str(outfile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)
            metafile.unlink()
            metafile_new.unlink()
        else:
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-activation_bytes", activation_bytes,
                "-i", str(file),
                "-c", "copy",
                str(outfile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)

        echo(f"File decryption successful: {outfile.name}")


def decrypt_aaxc(
        files, rebuild_chapters, ignore_missing_chapters, separate_intro_outro
):
    for file in files:
        outfile = file.with_suffix(".m4b")

        if outfile.exists():
            secho(f"Skip {file.name}: already decrypted", fg="blue")
            continue

        voucher_file = file.with_suffix(".voucher")
        voucher = json.loads(voucher_file.read_text())
        voucher = voucher["content_license"]
        audible_key = voucher["license_response"]["key"]
        audible_iv = voucher["license_response"]["iv"]

        can_rebuild_chapters = False
        if rebuild_chapters:
            metafile = file.with_suffix(".meta")
            metafile_new = file.with_suffix(".new.meta")

            has_chapters = False
            if "chapter_info" in voucher.get("content_metadata", {}):
                content_metadata = voucher
                echo(f"Using chapters from {voucher_file}")
                has_chapters = True
            else:
                base_filename = file.stem.rsplit("-", 1)[0]
                chapter_file = file.with_name(base_filename + "-chapters.json")

                try:
                    content_metadata = json.loads(chapter_file.read_text())
                except:
                    secho(f"No chapter data found for {file.name}", fg="red")
                else:
                    echo(f"Using chapters from {chapter_file.name}")
                    has_chapters = True

            if has_chapters:
                if not content_metadata["content_metadata"]["chapter_info"][
                        "is_accurate"]:
                    secho(f"Chapter data are not accurate", fg="red")
                else:
                    can_rebuild_chapters = True

        if rebuild_chapters and not can_rebuild_chapters and not ignore_missing_chapters:
            secho(f"Skip {file.name}: chapter data can not be rebuild", fg="red")
            continue

        if can_rebuild_chapters:
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-audible_key", audible_key,
                "-audible_iv", audible_iv,
                "-i", str(file),
                "-f", "ffmetadata",
                str(metafile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)
    
            ffmeta_class = FFMeta(metafile)
            ffmeta_class.update_chapters_from_api_meta(
                content_metadata, separate_intro_outro
            )
            ffmeta_class.write(metafile_new)
            click.echo("Replaced all titles.")
    
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-audible_key", audible_key,
                "-audible_iv", audible_iv,
                "-i", str(file),
                "-i", str(metafile_new),
                "-map_metadata", "0",
                "-map_chapters", "1",
                "-c", "copy",
                str(outfile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)
            metafile.unlink()
            metafile_new.unlink()
        else:
            cmd = [
                "ffmpeg",
                "-v", "quiet",
                "-stats",
                "-audible_key", audible_key,
                "-audible_iv", audible_iv,
                "-i", str(file),
                "-c", "copy",
                str(outfile)
            ]
            subprocess.check_output(cmd, universal_newlines=True)

        echo(f"File decryption successful: {outfile.name}")


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.command("remove-encryption", context_settings=CONTEXT_SETTINGS)
@click.option(
    "--input", "-i",
    type=click.Path(exists=True, file_okay=True),
    multiple=True,
    help="Input file")
@click.option(
    "--all", "-a",
    is_flag=True,
    help="convert all files in folder"
)
@click.option(
    "--overwrite",
    is_flag=True,
    help="overwrite existing files"
)
@click.option(
    "--rebuild-chapters", "-r",
    is_flag=True,
    help="Rebuild chapters from chapter file"
)
@click.option(
    "--separate-intro-outro", "-s",
    is_flag=True,
    help="Separate Audible Brand Intro and Outro to own Chapter. Only use with `--rebuild-chapters`."
)
@click.option(
    "--ignore-missing-chapters", "-t", 
    is_flag=True,
    help=(
        "Decrypt without rebuilding chapters when chapters are not present. "
        "Otherwise an item is skipped when this option is not provided. Only use with `--rebuild-chapters`."
    )
)
@pass_session
def cli(session, **options):
    if not which("ffmpeg"):
        ctx = click.get_current_context()
        ctx.fail("ffmpeg not found")

    rebuild_chapters = options.get("rebuild_chapters")
    ignore_missing_chapters = options.get("ignore_missing_chapters")
    separate_intro_outro = options.get("separate_intro_outro")

    jobs = {"aaxc": [], "aax":[]}

    if options.get("all"):
        cwd = pathlib.Path.cwd()
        jobs["aaxc"].extend(list(cwd.glob('*.aaxc')))
        jobs["aax"].extend(list(cwd.glob('*.aax')))
        
    else:
        for file in options.get("input"):
            file = pathlib.Path(file).resolve()
            if file.match("*.aaxc"):
                jobs["aaxc"].append(file)
            elif file.match("*.aax"):
                jobs["aax"].append(file)
            else:
                secho(f"file suffix {file.suffix} not supported", fg="red")

    decrypt_aaxc(
        jobs["aaxc"],
        rebuild_chapters,
        ignore_missing_chapters,
        separate_intro_outro
    )

    decrypt_aax(
        jobs["aax"],
        session.auth.activation_bytes, rebuild_chapters,
        ignore_missing_chapters,
        separate_intro_outro
    )
