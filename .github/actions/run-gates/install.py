"""Create a runner-local environment; only pip downloads may be cached."""

from pathlib import Path
import stat
import subprocess
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


def install(root: Path) -> None:
    environment = root / ".venv"
    requirements = root / ".github/actions/run-gates/requirements.txt"
    for use_cache in (True, False):
        validate_environment(root, environment)
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = environment / "bin/python"
        if not python.is_file():
            python = environment / "Scripts/python.exe"
        command = [str(python), "-m", "pip", "install", "-r", str(requirements)]
        if not use_cache:
            command.append("--no-cache-dir")
        try:
            subprocess.run(command, check=True)
            subprocess.run([str(python), "-m", "pip", "check"], check=True)
            return
        except subprocess.CalledProcessError:
            if not use_cache:
                raise
            # Clear partial installs too. A broken cache must not poison a runner,
            # but a second failure remains a hard failure before lint/tests.
            print("Install validation failed; retrying fresh without pip cache", flush=True)


if __name__ == "__main__":
    install(Path(__file__).resolve().parents[3])
