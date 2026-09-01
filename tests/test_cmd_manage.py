import json
import logging
import os
import pathlib
from unittest import mock

import pytest
from audible import Authenticator
from click.testing import CliRunner

from audible_cli.cmds.cmd_manage import cli
from audible_cli.config import Session
from audible_cli.exceptions import AudibleCliException
from audible_cli.utils import detect_auth_file


def test_config_edit_passes_str_path_to_click_edit(tmp_path):
    """Click >= 8.2 takes a `str` or an iterable of them, but not a `Path`."""
    config_file = tmp_path / "config.toml"
    config_file.write_text("[APP]\n")

    session = Session()
    session._config = mock.Mock(filename=pathlib.Path(config_file))

    runner = CliRunner()
    with mock.patch("audible_cli.cmds.cmd_manage.click.edit") as edit:
        result = runner.invoke(cli, ["config", "edit"], obj=session)

    assert result.exit_code == 0, result.output
    edit.assert_called_once_with(filename=os.fspath(config_file))
    assert isinstance(edit.call_args.kwargs["filename"], str)


@pytest.fixture
def narrating():
    """Put the package logger where the top-level group would put it.

    These tests invoke the subgroup directly, so the --verbosity callback
    never runs and the logger keeps its inherited WARNING level, which
    filters out everything a command narrates.
    """
    logger = logging.getLogger("audible_cli")
    level = logger.level
    logger.setLevel(logging.INFO)
    yield
    logger.setLevel(level)


def profile_config(tmp_path, *names):
    """A config file holding one auth-file-less profile per name."""
    lines = ["[APP]", 'primary_profile = "' + names[0] + '"', ""]
    for name in names:
        lines += [
            f"[profile.{name}]",
            f'auth_file = "{name}.json"',
            'country_code = "de"',
            "",
        ]
    config_file = tmp_path / "config.toml"
    config_file.write_text("\n".join(lines))
    return config_file


def test_removing_a_profile_leaves_the_others_alone(tmp_path):
    profile_config(tmp_path, "one", "two", "three")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(cli, ["profile", "remove", "-P", "two"], obj=session)

    assert result.exit_code == 0, result.output
    assert set(session.config.data["profile"]) == {"one", "three"}
    assert "two" not in (tmp_path / "config.toml").read_text()


def test_a_profile_that_is_not_there_does_not_stop_the_others(tmp_path):
    # Reported and carried on, rather than raised: one typo must not
    # leave the rest of the run undone.
    profile_config(tmp_path, "one", "two")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(
        cli, ["profile", "remove", "-P", "nope", "-P", "two"], obj=session
    )

    assert result.exit_code == 0, result.output
    assert "nope doesn't exist" in result.stderr
    assert set(session.config.data["profile"]) == {"one"}


def test_the_removal_is_told_once_and_on_stderr(tmp_path, narrating):
    # Once, on stderr, from the config class -- and nothing on stdout.
    profile_config(tmp_path, "one", "two")
    session = Session()
    session._app_dir = tmp_path

    result = CliRunner().invoke(cli, ["profile", "remove", "-P", "two"], obj=session)

    assert result.stdout == ""
    assert result.stderr.count("removed from config") == 1
    assert result.stderr.count("Config written to") == 1


FILE_PASSWORD = "a password for the test"  # noqa: S105
AUDIBLE_PASSWORD = "a password for the audible account"  # noqa: S105

FAKE_AUTH = {
    "website_cookies": {"session-id": "123"},
    "adp_token": "{enc:AAAA}{key:BBBB}{iv:CCCC}{name:QURQ}{serial:Mg==}",
    "access_token": "Atna|not-a-real-token",
    "refresh_token": "Atnr|not-a-real-token",
    "device_private_key": (
        "-----BEGIN RSA PRIVATE KEY-----\nnot a key\n-----END RSA PRIVATE KEY-----\n"
    ),
    "store_authentication_cookie": {"cookie": "x"},
    "device_info": {"device_name": "A test device"},
    "customer_info": {"user_id": "x"},
    "expires": 9999999999.0,
    "locale_code": "de",
    "with_username": False,
    "activation_bytes": None,
}


def auth_file(tmp_path, name="one.json", password=None):
    """An auth file holding made-up credentials, encrypted or not."""
    profile_config(tmp_path, "one")
    path = tmp_path / name
    path.write_text(json.dumps(FAKE_AUTH, indent=4))

    if password is not None:
        auth = Authenticator.from_file(path)
        auth.to_file(path, password=password, encryption="json", set_default=False)

    return path


def manage(tmp_path, *args, **kwargs):
    session = Session()
    session._app_dir = tmp_path
    return CliRunner().invoke(cli, list(args), obj=session, **kwargs)


def test_an_auth_file_can_be_encrypted_after_the_fact(tmp_path, narrating):
    path = auth_file(tmp_path)

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert detect_auth_file(path) == "json"
    assert result.stdout == ""
    assert "one.json is encrypted now" in result.stderr


def test_the_round_trip_changes_nothing(tmp_path):
    path = auth_file(tmp_path)
    before = json.loads(path.read_text())

    manage(tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD)
    manage(tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD)

    assert json.loads(path.read_text()) == before


def test_an_encrypted_auth_file_can_be_decrypted(tmp_path, narrating):
    path = auth_file(tmp_path, password=FILE_PASSWORD)

    result = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert detect_auth_file(path) == "plain"
    assert json.loads(path.read_text())["adp_token"] == FAKE_AUTH["adp_token"]
    assert result.stdout == ""
    assert "not encrypted any more" in result.stderr


def test_encrypting_twice_says_to_decrypt_first(tmp_path):
    path = auth_file(tmp_path, password=FILE_PASSWORD)
    before = path.read_bytes()

    result = manage(
        tmp_path,
        "auth-file",
        "encrypt",
        "-f",
        "one.json",
        "-p",
        FILE_PASSWORD + " but different",
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "encrypted already" in str(result.exception)
    assert path.read_bytes() == before


def test_decrypting_what_is_not_encrypted_says_so(tmp_path):
    path = auth_file(tmp_path)
    before = path.read_bytes()

    result = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "is not encrypted" in str(result.exception)
    assert path.read_bytes() == before


def test_the_wrong_password_leaves_the_file_alone(tmp_path):
    # The file is the device registration: a failed attempt may not cost
    # it, and a wrong password is not a reason to ask for another one.
    path = auth_file(tmp_path, password=FILE_PASSWORD)
    before = path.read_bytes()

    result = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", "not the password"
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "does not open" in str(result.exception)
    assert path.read_bytes() == before


def test_what_is_missing_is_asked_for(tmp_path, narrating):
    # So that a password need not be typed where the shell keeps it.
    path = auth_file(tmp_path)

    result = manage(
        tmp_path,
        "auth-file",
        "encrypt",
        input=f"one.json\n{FILE_PASSWORD}\n{FILE_PASSWORD}\n",
    )

    assert result.exit_code == 0, result.output
    assert "name for the auth file" in result.stderr
    assert "password for the auth file" in result.stderr
    assert result.stdout == ""
    assert detect_auth_file(path) == "json"


def test_a_password_that_was_asked_for_opens_the_file_again(tmp_path):
    # Round trip through the questions rather than through the options.
    path = auth_file(tmp_path)
    before = json.loads(path.read_text())

    manage(
        tmp_path,
        "auth-file",
        "encrypt",
        input=f"one.json\n{FILE_PASSWORD}\n{FILE_PASSWORD}\n",
    )
    result = manage(
        tmp_path, "auth-file", "decrypt", input=f"one.json\n{FILE_PASSWORD}\n"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text()) == before


def test_the_password_is_not_repeated_back_when_taking_it_off(tmp_path):
    # A wrong one cannot destroy anything, so asking twice would only
    # be in the way.
    path = auth_file(tmp_path, password=FILE_PASSWORD)

    result = manage(
        tmp_path, "auth-file", "decrypt", input=f"one.json\n{FILE_PASSWORD}\n"
    )

    assert result.exit_code == 0, result.output
    assert "Repeat for confirmation" not in result.stderr
    assert detect_auth_file(path) == "plain"


@pytest.mark.parametrize("command", ["encrypt", "decrypt"])
def test_an_option_that_is_given_is_not_asked_about(tmp_path, command):
    auth_file(tmp_path, password=None if command == "encrypt" else FILE_PASSWORD)

    result = manage(
        tmp_path, "auth-file", command, "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert "Please enter" not in result.stderr


def test_the_permissions_survive_the_rewrite(tmp_path):
    path = auth_file(tmp_path)
    path.chmod(0o600)

    manage(tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD)

    assert path.stat().st_mode & 0o777 == 0o600


def test_nothing_is_left_beside_the_file(tmp_path):
    auth_file(tmp_path)

    manage(tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml", "one.json"]


def test_removing_with_the_wrong_password_says_so(tmp_path):
    # It used to reach `Authenticator.from_file` unguarded, where a wrong
    # password is a traceback about PKCS7 padding.
    path = auth_file(tmp_path, password=FILE_PASSWORD)

    result = manage(
        tmp_path, "auth-file", "remove", "-f", "one.json", "-p", "not the password"
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "does not open" in str(result.exception)
    assert path.exists()


def test_the_prompt_hands_the_password_on(tmp_path):
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-au",
            "someone",
            "-ap",
            "an audible password",
            "-cc",
            "de",
            input=f"{FILE_PASSWORD}\n{FILE_PASSWORD}\n",
        )

    assert build.call_args.kwargs["file_password"] == FILE_PASSWORD


def test_an_empty_answer_leaves_the_auth_file_open(tmp_path):
    # None rather than the empty string: the option is documented as
    # optional, and `build_auth_file` takes None for "no encryption".
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        result = manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-au",
            "someone",
            "-ap",
            "an audible password",
            "-cc",
            "de",
            input="\n\n",
        )

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["file_password"] is None
    assert "password for the auth file" in result.stderr
    assert result.stdout == ""


def test_the_option_skips_the_prompt(tmp_path):
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        result = manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-p",
            FILE_PASSWORD,
            "-au",
            "someone",
            "-ap",
            "an audible password",
            "-cc",
            "de",
        )

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["file_password"] == FILE_PASSWORD
    assert "password for the auth file" not in result.stderr


@pytest.mark.parametrize("breaks", ["audible.Authenticator.to_file", "shutil.copymode"])
def test_a_write_that_fails_leaves_the_auth_file_as_it_was(tmp_path, breaks):
    # The file is the device registration. Writing it in place would put
    # it at risk of every error between the first byte and the last. The
    # second case gets as far as a written file beside it, which is the
    # one that has to be cleaned up again -- under whichever name
    # `mkstemp` gave it, so the whole directory is checked.
    path = auth_file(tmp_path)
    before = path.read_bytes()

    with mock.patch(breaks, side_effect=OSError("no room")):
        result = manage(
            tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
        )

    assert result.exit_code != 0
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml", "one.json"]


@pytest.mark.parametrize("command", ["encrypt", "decrypt"])
def test_an_empty_password_is_refused(tmp_path, command):
    # An empty value passes for a given option, and would then read as
    # "no password" and write the file in the open while saying so.
    path = auth_file(tmp_path)
    before = path.read_bytes()

    result = manage(tmp_path, "auth-file", command, "-f", "one.json", "-p", "")

    assert result.exit_code == 2
    assert "cannot be empty" in result.stderr
    assert path.read_bytes() == before


def test_an_external_login_is_not_asked_about_the_account(tmp_path):
    # The browser does the login, and `from_login_external` is never
    # handed a username or a password, so asking for them here would be
    # asking for something to throw away.
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        result = manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-cc",
            "de",
            "--external-login",
            input="\n\n",
        )

    assert result.exit_code == 0, result.output
    assert "audible username" not in result.stderr
    assert build.call_args.kwargs["username"] is None
    assert build.call_args.kwargs["password"] is None


def test_a_login_here_is_asked_about_the_account(tmp_path):
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        result = manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-cc",
            "de",
            input=f"\n\nsomeone\n{AUDIBLE_PASSWORD}\n{AUDIBLE_PASSWORD}\n",
        )

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["username"] == "someone"
    assert build.call_args.kwargs["password"] == AUDIBLE_PASSWORD
    assert result.stdout == ""


def test_a_symlink_leads_to_the_file_it_points_at(tmp_path, narrating):
    # Replacing the link would put an encrypted file where the link was
    # and leave the credentials it pointed at in the open.
    target = auth_file(tmp_path, name="elsewhere.json")
    link = tmp_path / "one.json"
    link.symlink_to(target)

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert link.is_symlink()
    assert detect_auth_file(target) == "json"


def test_a_file_with_a_second_name_is_refused(tmp_path):
    # The rewrite gives this name a new file. Every other name would
    # keep pointing at the old, readable one.
    path = auth_file(tmp_path)
    os.link(path, tmp_path / "also-one.json")
    before = path.read_bytes()

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "more than one name" in str(result.exception)
    assert path.read_bytes() == before


def test_json_that_is_not_an_auth_file_is_refused(tmp_path):
    # It reads as an authenticator with nothing in it, and writing that
    # back would replace a file nobody meant to point at.
    profile_config(tmp_path, "one")
    path = tmp_path / "one.json"
    path.write_text('{"locale_code": "de"}')

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "is not an auth file" in str(result.exception)
    assert path.read_text() == '{"locale_code": "de"}'


def test_a_file_that_is_not_json_at_all_is_refused(tmp_path):
    # The library reads anything that is not json as the older bytes
    # encryption, which would send the user off to decrypt a file of
    # nonsense. Both commands say what is actually the matter.
    profile_config(tmp_path, "one")
    path = tmp_path / "one.json"
    path.write_text("this is not json")

    encrypting = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )
    decrypting = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert "is not an auth file" in str(encrypting.exception)
    assert "is not an auth file" in str(decrypting.exception)
    assert path.read_text() == "this is not json"


def test_a_file_that_was_already_there_is_not_overwritten(tmp_path):
    # The temporary name used to be `<name>.new`, which would have taken
    # this file with it.
    path = auth_file(tmp_path)
    bystander = tmp_path / "one.json.new"
    bystander.write_text("someone else's file")

    manage(tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD)

    assert bystander.read_text() == "someone else's file"
    assert detect_auth_file(path) == "json"


def test_a_field_of_the_wrong_type_is_refused(tmp_path):
    # The library validates field by field and raises whatever fits the
    # field it looked at -- a `TypeError` here, a `ValueError` there.
    # None of them may reach the user as a traceback.
    profile_config(tmp_path, "one")
    path = tmp_path / "one.json"
    path.write_text(json.dumps({**FAKE_AUTH, "website_cookies": {"session-id": 123}}))

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "does not open" in str(result.exception)


def test_the_older_encryption_is_recognised_and_can_be_taken_off(tmp_path, narrating):
    # `audible` wrote auth files as raw bytes before it wrote them as
    # json. They are not json at all, so only their salt header tells
    # them apart from a file of nonsense.
    path = auth_file(tmp_path)
    auth = Authenticator.from_file(path)
    auth.to_file(path, password=FILE_PASSWORD, encryption="bytes", set_default=False)

    encrypting = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )
    decrypting = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert "encrypted already" in str(encrypting.exception)
    assert decrypting.exit_code == 0, decrypting.output
    assert json.loads(path.read_text())["adp_token"] == FAKE_AUTH["adp_token"]


def test_a_file_holding_only_tokens_counts_as_an_auth_file(tmp_path):
    # A login that registered no device leaves these two behind, and
    # they are as much an auth file as a registration is.
    profile_config(tmp_path, "one")
    path = tmp_path / "one.json"
    path.write_text(
        json.dumps(
            {
                "access_token": FAKE_AUTH["access_token"],
                "refresh_token": FAKE_AUTH["refresh_token"],
                "locale_code": "de",
                "expires": FAKE_AUTH["expires"],
            }
        )
    )

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert detect_auth_file(path) == "json"


@pytest.mark.parametrize(
    ("content", "shape"),
    [
        (b"$\x03\xe8$" + b"x" * 44, "bytes"),
        (b"$\x03\xe8$too short", None),
        (b"y" * 48, None),
        (b"this is not json", None),
        (b"", None),
        (b"[1, 2, 3]", None),
    ],
)
def test_the_older_format_is_told_apart_from_nonsense(tmp_path, content, shape):
    # Its salt header and its whole cipher blocks are all there is to
    # go by. The library takes anything that is not json for it.
    path = tmp_path / "file"
    path.write_bytes(content)

    assert detect_auth_file(path) == shape


def test_an_empty_answer_is_asked_again(tmp_path):
    # A prompt without a default keeps asking while the answer is empty,
    # so the guard that catches `-p ""` is never reached from here.
    path = auth_file(tmp_path)

    result = manage(
        tmp_path,
        "auth-file",
        "encrypt",
        input=f"one.json\n\n{FILE_PASSWORD}\n{FILE_PASSWORD}\n",
    )

    assert result.exit_code == 0, result.output
    assert detect_auth_file(path) == "json"


def test_an_empty_password_option_says_no_encryption(tmp_path):
    # What the changelog tells a script to pass instead of answering the
    # question it now asks.
    profile_config(tmp_path, "one")

    with mock.patch("audible_cli.cmds.cmd_manage.build_auth_file") as build:
        result = manage(
            tmp_path,
            "auth-file",
            "add",
            "-f",
            "new.json",
            "-p",
            "",
            "-au",
            "someone",
            "-ap",
            AUDIBLE_PASSWORD,
            "-cc",
            "de",
        )

    assert result.exit_code == 0, result.output
    assert build.call_args.kwargs["file_password"] is None
    assert "password for the auth file" not in result.stderr


def test_an_encrypted_file_without_the_extra_field_is_still_one(tmp_path):
    # `info` is written along with the encryption, but only `salt`, `iv`
    # and `ciphertext` are read back, so a file without it opens.
    path = auth_file(tmp_path, password=FILE_PASSWORD)
    document = json.loads(path.read_text())
    del document["info"]
    path.write_text(json.dumps(document))

    result = manage(
        tmp_path, "auth-file", "decrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert result.exit_code == 0, result.output
    assert detect_auth_file(path) == "plain"


def test_fields_that_are_there_but_empty_are_no_auth_file(tmp_path):
    profile_config(tmp_path, "one")
    path = tmp_path / "one.json"
    path.write_text(json.dumps({"adp_token": None, "device_private_key": None}))

    result = manage(
        tmp_path, "auth-file", "encrypt", "-f", "one.json", "-p", FILE_PASSWORD
    )

    assert isinstance(result.exception, AudibleCliException)
    assert "is not an auth file" in str(result.exception)
