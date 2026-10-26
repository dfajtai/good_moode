"""Set up the local (PC) development environment with Poetry.

    python scripts/local/setup_dev_env.py
    python scripts/local/setup_dev_env.py --install-poetry   # install Poetry first if it is missing
    python scripts/local/setup_dev_env.py --recreate         # delete .venv and start from scratch

What it does:

1. checks that Python is 3.12 or newer;
2. finds Poetry (or installs it with --install-poetry);
3. creates the virtual environment in <repo>/.venv (git-ignored; editors find it there) and tells
   Poetry to use it (writes poetry.toml, which is tracked so everybody gets the same behaviour);
4. runs `poetry install --extras local`: the project's own dependencies (requests, Pillow) plus
   paramiko (the SSH tools) and reportlab (the wiring PDF generator), and the good_moode package
   itself in editable mode;
5. checks that everything imports.

The `pi` extra (luma.oled, gpiod) is for the Raspberry Pi only; gpiod does not install on Windows,
so it is left out here. Use the environment afterwards with `poetry run python <script>`, or
activate it (.venv\\Scripts\\activate on Windows, source .venv/bin/activate elsewhere).
Safe to repeat: an existing .venv is kept unless --recreate is given.
"""

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root (this file is scripts/local/setup_dev_env.py)
VENV_DIR = ROOT / ".venv"
MIN_PYTHON = (3, 12)  # same as requires-python in pyproject.toml
EXTRAS = ("local",)  # see the module docstring: not "pi"
CHECK_IMPORTS = ("requests", "PIL", "paramiko", "reportlab", "good_moode")


def run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a command in the repo root, echoing it first; raises if it fails (unless ``check=False``)."""
    print("$ " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, **kwargs)


def venv_python() -> Path:
    """Path of the Python interpreter inside .venv (the folder layout differs on Windows)."""
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def find_poetry(install: bool) -> list[str]:
    """Return the command that runs Poetry, installing it first if allowed; exits if there is none.

    Tries the ``poetry`` program on PATH, then ``python -m poetry`` (a user-site install is often
    not on PATH).
    """
    def usable(cmd: list[str]) -> bool:
        try:
            return subprocess.run(cmd + ["--version"], capture_output=True).returncode == 0
        except OSError:
            return False

    candidates = [["poetry"], [sys.executable, "-m", "poetry"]]
    for cmd in candidates:
        if usable(cmd):
            return cmd
    if install and sys.prefix != sys.base_prefix:
        # `pip install --user` does not work inside a virtual environment
        sys.exit("--install-poetry cannot install into a virtual environment. Install Poetry with pipx instead:\n"
                 "    pipx install poetry")
    if install:
        run([sys.executable, "-m", "pip", "install", "--user", "poetry"])
        for cmd in candidates:
            if usable(cmd):
                return cmd
    sys.exit(
        "Poetry was not found. Install it with:\n"
        "    pipx install poetry\n"
        "or, outside a virtual environment, run this script again with --install-poetry."
    )


def ensure_venv(recreate: bool) -> None:
    """Create .venv with the current Python (replacing it if ``recreate``) and check its version."""
    if recreate and VENV_DIR.exists():
        print(f"Deleting {VENV_DIR}")
        shutil.rmtree(VENV_DIR)
    if not VENV_DIR.exists():
        print(f"Creating {VENV_DIR}")
        venv.create(VENV_DIR, with_pip=True)
    out = subprocess.run([str(venv_python()), "-c", "import sys; print(*sys.version_info[:2])"],
                         capture_output=True, text=True)
    version = tuple(int(n) for n in out.stdout.split()) if out.returncode == 0 else (0, 0)
    if version < MIN_PYTHON:
        sys.exit(f"{VENV_DIR} uses Python {version[0]}.{version[1]}, but {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is needed. "
                 "Run again with --recreate.")
    print(f"Virtual environment: {VENV_DIR} (Python {version[0]}.{version[1]})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--install-poetry", action="store_true", help="install Poetry (pip --user) if it is missing")
    ap.add_argument("--recreate", action="store_true", help="delete .venv and create it again")
    args = ap.parse_args()

    if sys.version_info < MIN_PYTHON:
        sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, this is {sys.version_info[0]}.{sys.version_info[1]}.")

    poetry = find_poetry(args.install_poetry)
    ensure_venv(args.recreate)

    # Make Poetry use <repo>/.venv instead of a hidden cache folder; stored in poetry.toml.
    run(poetry + ["config", "virtualenvs.in-project", "true", "--local"])
    run(poetry + ["install", "--no-interaction"] + [arg for extra in EXTRAS for arg in ("--extras", extra)])

    # Final check, inside the environment: every library we need can be imported.
    code = "; ".join(f"import {name}" for name in CHECK_IMPORTS) + "; print('imports ok')"
    result = run(poetry + ["run", "python", "-c", code], check=False)
    if result.returncode:
        sys.exit("Something did not import; see the error above.")

    print("\nDone. Use the environment with:")
    print("    poetry run python scripts/local/deploy.py")
    print("or activate it first:")
    print(r"    .venv\Scripts\activate" if sys.platform == "win32" else "    source .venv/bin/activate")
    if not (ROOT / ".env").exists():
        print("\nNext: copy .env.example to .env and fill in the Pi's address and login.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
