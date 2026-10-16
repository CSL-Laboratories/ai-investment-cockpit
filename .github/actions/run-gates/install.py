"""Create a runner-local environment; only pip downloads may be cached."""

from pathlib import Path
import subprocess
import venv


def install(root: Path) -> None:
    environment = root / ".venv"
    requirements = root / ".github/actions/run-gates/requirements.txt"
    # Refuse symlinks rather than clearing a directory outside this checkout.
    if environment.is_symlink():
        raise ValueError("The CI virtualenv must not be a symlink")
    for use_cache in (True, False):
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
