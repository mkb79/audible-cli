import asyncio
import json
import pathlib

# Generous ceiling: this is a stream copy (no re-encode), so even a very
# long audiobook normally finishes in well under a minute — this bounds a
# wedged/hung ffmpeg process, not expected runtime.
DECRYPT_TIMEOUT_SECONDS = 20 * 60


class DecryptError(Exception):
    """Raised when ffmpeg fails to decrypt/remux a downloaded audiobook."""


def _recursive_lookup(key: str, data: dict) -> str:
    if key in data:
        return data[key]
    for value in data.values():
        if isinstance(value, dict):
            try:
                return _recursive_lookup(key, value)
            except KeyError:
                continue
    raise KeyError(key)


def extract_voucher_credentials(voucher_path: pathlib.Path) -> tuple[str, str]:
    """Pull the audible_key/audible_iv out of a .voucher file.

    LibraryItem.get_license() (src/audible_cli/models.py) already decrypts
    the voucher's license response before download_aaxc ever writes it to
    disk, so the key/iv are already in the clear here — no separate
    activation-bytes fetch needed.
    """
    voucher = json.loads(voucher_path.read_text())
    try:
        key = _recursive_lookup("key", voucher)
        iv = _recursive_lookup("iv", voucher)
    except KeyError as exc:
        raise DecryptError(f"No key/iv found in voucher {voucher_path.name}") from exc
    return key, iv


async def decrypt_aaxc(source: pathlib.Path, key: str, iv: str) -> pathlib.Path:
    """Strip DRM and remux an AAXC file into a playable .m4b alongside it.

    Stream-copies (-c copy) rather than re-encoding — this only removes the
    DRM wrapper and changes the container, it doesn't touch audio data.
    """
    target = source.with_suffix(".m4b")
    tmp_target = target.with_suffix(".m4b.tmp")

    cmd = [
        "ffmpeg",
        # "error" rather than "quiet", so a failure's reason reaches stderr
        # and the DecryptError message below instead of an empty string.
        "-v",
        "error",
        "-nostats",
        "-y",
        "-audible_key",
        key,
        "-audible_iv",
        iv,
        "-i",
        str(source),
        "-c",
        "copy",
        # The output name ends in .tmp, so ffmpeg can't infer the container
        # from the extension and exits 234 (EINVAL) without being told.
        "-f",
        "mp4",
        str(tmp_target),
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=DECRYPT_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        tmp_target.unlink(missing_ok=True)
        raise DecryptError(f"ffmpeg timed out decrypting {source.name}") from None

    if proc.returncode != 0:
        tmp_target.unlink(missing_ok=True)
        raise DecryptError(
            f"ffmpeg failed decrypting {source.name} (exit {proc.returncode}): "
            f"{stderr.decode(errors='replace')[-2000:]}"
        )

    tmp_target.rename(target)
    return target
