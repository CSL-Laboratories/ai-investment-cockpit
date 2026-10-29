"""Create a runner-local environment; only pip downloads may be cached."""

from pathlib import Path
import os
import stat
import subprocess
import tempfile
import venv


def validate_environment(root: Path, environment: Path) -> None:
    """Never let clear=True traverse a symlink or Windows directory junction."""
    try:
        info = environment.lstat()
    except FileNotFoundError:
        info = None
    # Path.is_junction() is unavailable on Python 3.11. Reparse attributes also
    # cover Windows links that is_symlink() does not recognize as symlinks.
    if info is not None and (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise ValueError("The CI virtualenv must not be a link or reparse point")
    if environment.resolve() != root.resolve() / ".venv":
        raise ValueError("The CI virtualenv must stay inside this checkout")


def pip_cwd(root: Path) -> Path:
    """Directory for `python -m pip` that is not the checkout (CSL-471)."""
    root = root.resolve()
    candidates = []
    raw = os.environ.get("RUNNER_TEMP")
    if raw:
        candidates.append(Path(raw))
    candidates.append(Path(tempfile.gettempdir()))
    for path in candidates:
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            if resolved.is_dir():
                return resolved
    raise ValueError("pip cwd must not be inside the checkout")


def pip_command(python: Path, *args: str) -> list[str]:
    return [str(python), "-P", "-m", "pip", *args]


def install(root: Path) -> None:
    environment = root / ".venv"
    requirements = root / ".github/actions/run-gates/requirements.txt"
    for use_cache in (True, False):
        validate_environment(root, environment)
        cwd = pip_cwd(root)
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = environment / "bin/python"
        if not python.is_file():
            python = environment / "Scripts/python.exe"
        command = pip_command(python, "install", "-r", str(requirements))
        if not use_cache:
            command.append("--no-cache-dir")
        try:
            subprocess.run(command, check=True, cwd=cwd)
            subprocess.run(pip_command(python, "check"), check=True, cwd=cwd)
            return
        except subprocess.CalledProcessError:
            if not use_cache:
                raise
            # Clear partial installs too. A broken cache must not poison a runner,
            # but a second failure remains a hard failure before lint/tests.
            print("Install validation failed; retrying fresh without pip cache", flush=True)


if __name__ == "__main__":
    install(Path(__file__).resolve().parents[3])
