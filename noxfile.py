"""Nox sessions."""

import os
import shlex
import shutil
import sys
from pathlib import Path
from textwrap import dedent

import nox
from nox_uv import session


nox.needs_version = ">= 2023.04.22"
nox.options.default_venv_backend = "uv"
nox.options.error_on_external_run = True
nox.options.sessions = (
    # "pre-commit",
    "safety",
    # "mypy",
    "tests",
    "typeguard",
    "xdoctest",
    "docs-build",
)

PACKAGE = "audible_cli"
PROJECT = nox.project.load_toml("pyproject.toml")
PYTHON_VERSIONS = nox.project.python_versions(PROJECT)
DEFAULT_PYTHON_VERSION = PYTHON_VERSIONS[-1]

# Group declaration
DEV_GROUP = "dev"
DOCS_GROUP = "docs"
MYPY_GROUP = "mypy"
PRE_COMMIT_GROUP = "pre-commit"
SAFETY_GROUP = "safety"
TESTS_GROUP = "tests"
COVERAGE_GROUP = TESTS_GROUP
TYPEGUARD_GROUP = "typeguard"
XDOCTEST_GROUP = "xdocs"


def activate_virtualenv_in_precommit_hooks(s: nox.Session) -> None:
    """Activate virtualenv in hooks installed by pre-commit.

    This function patches git hooks installed by pre-commit to activate the
    session's virtual environment. This allows pre-commit to locate hooks in
    that environment when invoked from git.

    Args:
        s: The Session object.
    """
    assert s.bin is not None  # noqa: S101

    # Only patch hooks containing a reference to this session's bindir. Support
    # quoting rules for Python and bash, but strip the outermost quotes so we
    # can detect paths within the bindir, like <bindir>/python.
    bindirs = [
        bindir[1:-1] if bindir[0] in "'\"" else bindir
        for bindir in (repr(s.bin), shlex.quote(s.bin))
    ]

    virtualenv = s.env.get("VIRTUAL_ENV")
    if virtualenv is None:
        return

    headers = {
        # pre-commit < 2.16.0
        "python": f"""\
            import os
            os.environ["VIRTUAL_ENV"] = {virtualenv!r}
            os.environ["PATH"] = os.pathsep.join((
                {s.bin!r},
                os.environ.get("PATH", ""),
            ))
            """,
        # pre-commit >= 2.16.0
        "bash": f"""\
            VIRTUAL_ENV={shlex.quote(virtualenv)}
            PATH={shlex.quote(s.bin)}"{os.pathsep}$PATH"
            """,
        # pre-commit >= 2.17.0 on Windows forces sh shebang
        "/bin/sh": f"""\
            VIRTUAL_ENV={shlex.quote(virtualenv)}
            PATH={shlex.quote(s.bin)}"{os.pathsep}$PATH"
            """,
    }

    hookdir = Path(".git") / "hooks"
    if not hookdir.is_dir():
        return

    for hook in hookdir.iterdir():
        if hook.name.endswith(".sample") or not hook.is_file():
            continue

        if not hook.read_bytes().startswith(b"#!"):
            continue

        text = hook.read_text()

        if not any(
            (Path("A") == Path("a") and bindir.lower() in text.lower())
            or bindir in text
            for bindir in bindirs
        ):
            continue

        lines = text.splitlines()

        for executable, header in headers.items():
            if executable in lines[0].lower():
                lines.insert(1, dedent(header))
                hook.write_text("\n".join(lines))
                break


@session(name="pre-commit", python=DEFAULT_PYTHON_VERSION, uv_groups=[PRE_COMMIT_GROUP])
def precommit(s: nox.Session) -> None:
    """Lint using pre-commit."""
    default_args = [
        "run",
        "--all-files",
        "--hook-stage=manual",
        "--show-diff-on-failure",
    ]
    args = s.posargs or default_args

    s.run("pre-commit", *args)
    if args and args[0] == "install":
        activate_virtualenv_in_precommit_hooks(s)


@session(python=DEFAULT_PYTHON_VERSION, uv_groups=[SAFETY_GROUP])
def safety(s: nox.Session) -> None:
    """Scan dependencies for insecure packages."""
    # Use uv to generate requirements.txt
    requirement_path = f"{s.virtualenv.location}/requirements.txt"
    s.run_always(
        "uv",
        "export",
        "--no-hashes",
        "--format",
        "requirements-txt",
        "-o",
        requirement_path,
    )
    s.run(
        "safety",
        "check",
        "--full-report",
        f"--file={requirement_path}",
    )


@session(python=PYTHON_VERSIONS, uv_groups=[MYPY_GROUP])
def mypy(s: nox.Session) -> None:
    """Type-check using mypy."""
    default_args = [
        "src/audible_cli",
        "tests",
        "docs/source/conf.py",
        "plugin_cmds",
        "utils",
    ]
    args = s.posargs or default_args

    s.run("mypy", *args)
    if not s.posargs:
        s.run("mypy", f"--python-executable={sys.executable}", "noxfile.py")


@session(python=PYTHON_VERSIONS, uv_groups=[TESTS_GROUP])
def tests(s: nox.Session) -> None:
    """Run the test suite."""
    try:
        s.run(
            "coverage",
            "run",
            "--parallel",
            "-m",
            "pytest",
            *s.posargs,
        )
    finally:
        if s.interactive:
            s.notify("coverage", posargs=[])


@session(python=DEFAULT_PYTHON_VERSION, uv_groups=[COVERAGE_GROUP])
def coverage(s: nox.Session) -> None:
    """Produce the coverage report."""
    default_args = ["report"]
    args = s.posargs or default_args
    if not s.posargs and any(Path().glob(".coverage.*")):
        s.run("coverage", "combine")

    s.run("coverage", *args)


@session(python=DEFAULT_PYTHON_VERSION, uv_groups=[TYPEGUARD_GROUP])
def typeguard(s: nox.Session) -> None:
    """Runtime type checking using Typeguard."""
    s.run("pytest", f"--typeguard-packages={PACKAGE}", *s.posargs)


@session(python=PYTHON_VERSIONS, uv_groups=[XDOCTEST_GROUP])
def xdoctest(s: nox.Session) -> None:
    """Run examples with xdoctest."""
    if s.posargs:
        args = [PACKAGE, *s.posargs]
    else:
        args = [f"--modname={PACKAGE}", "--command=all"]
        if "FORCE_COLOR" in os.environ:
            args.append("--colored=1")

    s.run("python", "-m", "xdoctest", *args)


@session(name="docs-build", python=DEFAULT_PYTHON_VERSION, uv_groups=[DOCS_GROUP])
def docs_build(s: nox.Session) -> None:
    """Build the documentation."""
    default_args = ["docs/source", "docs/_build"]
    args = s.posargs or default_args

    if not s.posargs and "FORCE_COLOR" in os.environ:
        args.insert(0, "--color")

    build_dir = Path("docs", "_build")
    if build_dir.exists():
        shutil.rmtree(build_dir)

    s.run("sphinx-build", *args)


@session(python=DEFAULT_PYTHON_VERSION, uv_groups=[DOCS_GROUP])
def docs(s: nox.Session) -> None:
    """Build and serve the documentation with live reloading on file changes."""
    default_args = ["--open-browser", "docs/source", "docs/_build"]
    args = s.posargs or default_args

    build_dir = Path("docs", "_build")
    if build_dir.exists():
        shutil.rmtree(build_dir)

    s.run("sphinx-autobuild", *args)
