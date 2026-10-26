#!/usr/bin/env python3
"""Remote control of the Raspberry Pi over SSH (runs on the PC, not on the Pi).

Connection details are read from the .env file in the repo root:

    PI_HOST=192.168.0.80
    PI_USER=fajtai
    PI_PASSWORD=...
    PI_PORT=22            (optional)

Usage (from the repo root):

    python scripts/local/pi_remote.py         # interactive shell, type "help"
    python scripts/local/pi_remote.py status  # run one command and exit
    python scripts/local/pi_remote.py log     # download the logs into ./pi_logs/<time>/
    python scripts/local/pi_remote.py tail diag 100
    python scripts/local/pi_remote.py shutdown

Commands:

    status                 uptime, temperature, throttling, Wi-Fi, mpc status, our processes
    log                    download the logs (playback_diag.log*, playback_logger.err,
                           state_machine.log, moode.log, mpd and kernel journals)
    tail [name|path] [n]   last n lines of a log; names: diag, state, err, moode
    get <remote> [local]   download a file
    put <local> <remote>   upload a file
    setup [--logging] [--reset-stations]
                           first-time setup of a fresh moOde: I2C, venv, files, pip install, logging
                           switch, ready-script; --reset-stations also deletes moOde's default stations
    deploy [--logging]     upload good_moode/ and scripts/pi/*.sh to ~/scripts, chmod +x, install
                           ready-script.sh into moOde (for updates)
    restart                restart the OLED program on the Pi (as root; the logger too if logging is on)
    shutdown | reboot      power off / reboot the Pi (asks for confirmation)
    run <command>          run any command;  sudo <command>  runs it as root
    help | exit

Logging (the program's log file and the diagnostics logger) is OFF unless deploy / setup get
--logging: it writes to the SD card. The switch is the flag file ~/scripts/logging.on on the Pi;
every deploy sets it to what the command line says.
Add --yes to skip the confirmation questions. The first connection stores the Pi's host key in
~/.good_moode_known_hosts; later connections fail loudly if the key changes.
Needs: pip install paramiko
"""

import io
import posixpath
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import paramiko
except ImportError:
    sys.exit(
        f"paramiko is not installed in this Python ({sys.executable}).\n"
        "Run the tool with the project environment instead:\n"
        "    poetry run python scripts/local/<tool>.py\n"
        "or use the .venv interpreter (in VS Code: Python: Select Interpreter -> .venv).\n"
        "The environment is created by: python scripts/local/setup_dev_env.py"
    )

ROOT = Path(__file__).resolve().parents[2]  # repo root (this file is scripts/local/pi_remote.py)
ENV_FILE = ROOT / ".env"
KNOWN_HOSTS = Path.home() / ".good_moode_known_hosts"  # host keys we have already seen (trust on first use)
LOG_DIR = ROOT / "pi_logs"  # `log` downloads into a time-stamped folder here

# moOde runs this script after every boot; it is where our program gets started.
MOODE_READY_SCRIPT = "/var/local/www/commandw/ready-script.sh"

# The logging switch on the Pi: run_app.sh and ready-script.sh test for this file in ~/scripts.
LOGGING_FLAG = "logging.on"

# One shell snippet for the `status` command. Note the [x] trick in the pgrep pattern: a pattern like
# '[g]ood_moode' matches the text "good_moode" but not its own spelling, so pgrep does not report the
# shell that is running this very snippet.
STATUS_COMMAND = r"""
echo "== uptime / load";   uptime
echo "== temperature";     vcgencmd measure_temp 2>/dev/null
echo "== power / throttling (0x0 = ok)"; vcgencmd get_throttled 2>/dev/null
echo "== wifi";            iw dev wlan0 link 2>/dev/null | grep -E 'signal|tx bitrate'
echo "== mpc";             mpc status 2>&1
echo "== our processes";   pgrep -af '[g]ood_moode|[m]oode_state_machine' || echo "(none)"
echo "== logging";         [ -f ~/scripts/logging.on ] && echo "on" || echo "off"
"""


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse simple KEY=VALUE lines (comments and blank lines ignored) and check the required keys.

    Exits with a readable message if the file or one of PI_HOST / PI_USER / PI_PASSWORD is missing.
    """
    if not path.exists():
        sys.exit(f"{path} not found. Create it with PI_HOST, PI_USER and PI_PASSWORD lines.")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in ("PI_HOST", "PI_USER", "PI_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f"Missing in .env: {', '.join(missing)}")
    return env


def confirmed(prompt: str) -> bool:
    """Ask a yes/no question on the console; anything but y / yes counts as no."""
    return input(prompt).strip().lower() in ("y", "yes")


class PiRemote:
    """An SSH/SFTP connection to the Pi with the commands listed in the module docstring.

    The low-level helpers are ``run`` / ``show`` (shell commands) and ``put`` / ``get`` (files);
    the other methods are the ready-made commands built on them. ``execute`` maps a command line
    typed by the user to those methods.
    """

    def __init__(self, host: str, user: str, password: str, port: int = 22) -> None:
        self.host, self.user, self.password, self.port = host, user, password, port
        self.home = f"/home/{user}"
        self.scripts = f"{self.home}/scripts"  # where the program lives on the Pi
        self.client = paramiko.SSHClient()
        if KNOWN_HOSTS.exists():
            self.client.load_host_keys(str(KNOWN_HOSTS))
        # Trust on first use, like ssh: an unknown host key is accepted and stored (see connect()),
        # but a key that differs from the stored one is rejected with BadHostKeyException.
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # ---- connection ----

    def connect(self) -> None:
        """Open the SSH connection (password login only) and remember the Pi's host key."""
        self.client.connect(
            self.host, port=self.port, username=self.user, password=self.password,
            timeout=10, banner_timeout=15, auth_timeout=15,
            look_for_keys=False, allow_agent=False,  # only the .env password, no stray keys
        )
        self.client.save_host_keys(str(KNOWN_HOSTS))

    def close(self) -> None:
        """Close the SSH connection."""
        self.client.close()

    # ---- running commands ----

    def run(self, command: str, sudo: bool = False, timeout: float = 60) -> tuple[int, str]:
        """Run a shell command on the Pi; returns ``(exit_code, stdout + stderr)``.

        With ``sudo=True`` the command runs as root through ``sudo -S``, which reads the password
        from stdin: the password never appears on a command line. The command is wrapped in
        ``bash -c`` so that pipes, ``&&`` and ``if`` work as root too.
        """
        if sudo:
            command = f"sudo -S -p '' bash -c {shlex.quote(command)}"
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if sudo:
            stdin.write(self.password + "\n")
            stdin.flush()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), (out + err).rstrip()

    def show(self, command: str, sudo: bool = False, timeout: float = 60) -> int:
        """Like ``run``, but print the output; returns the exit code."""
        code, out = self.run(command, sudo, timeout)
        if out:
            print(out)
        return code

    # ---- commands ----

    def status(self) -> None:
        """Print uptime, temperature, power/throttling flags, Wi-Fi, mpc status and our processes."""
        self.show(STATUS_COMMAND)

    def resolve(self, name: str) -> str:
        """Map a short log name (diag, err, state, moode) to its path on the Pi.

        Anything else is taken as a path as it is.
        """
        names = {
            "diag": f"{self.scripts}/playback_diag.log",
            "err": f"{self.scripts}/playback_logger.err",
            "state": "/var/log/state_machine.log",
            "moode": "/var/log/moode.log",
        }
        return names.get(name, name)

    def tail(self, name: str = "diag", lines: int = 40) -> None:
        """Print the last ``lines`` lines of a log (short name or path)."""
        self.show(f"tail -n {int(lines)} {shlex.quote(self.resolve(name))}")

    def get(self, remote: str, local: Optional[Path] = None) -> Optional[Path]:
        """Download one file (short log name or path); returns the local path, or None on failure."""
        remote = self.resolve(remote)
        local = local or Path(posixpath.basename(remote))
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote, str(local))
        except OSError as exc:
            print(f"  skipped: {remote} ({exc})")
            return None
        finally:
            sftp.close()
        print(f"  downloaded: {remote} -> {local} ({local.stat().st_size} bytes)")
        return local

    def put(self, local: Path, remote: str) -> bool:
        """Upload one file; returns False (after printing the reason) if it fails."""
        sftp = self.client.open_sftp()
        try:
            if local.suffix == ".sh":
                # Shell scripts must have LF line endings on the Pi, whatever git did on Windows;
                # a CR before the newline would make bash fail with "$'\r': command not found".
                sftp.putfo(io.BytesIO(local.read_bytes().replace(b"\r\n", b"\n")), remote)
            else:
                sftp.put(str(local), remote)
        except OSError as exc:
            print(f"  error: {local} -> {remote} ({exc})")
            return False
        finally:
            sftp.close()
        print(f"  uploaded: {local.name} -> {remote}")
        return True

    def download_logs(self) -> Path:
        """Download every log that exists into ./pi_logs/<timestamp>/ and return that folder.

        Files: the diagnostics log and its rotated copies, the launcher's error file, the OLED
        program's service log and moOde's own log, plus the MPD and kernel journals of this boot.
        """
        target = LOG_DIR / time.strftime("%Y%m%d_%H%M%S")
        target.mkdir(parents=True, exist_ok=True)
        print(f"Downloading logs to: {target}")
        sftp = self.client.open_sftp()
        try:
            wanted = [self.resolve(n) for n in ("diag", "err", "state", "moode")]
            wanted += [f"{self.resolve('diag')}.{i}" for i in range(1, 6)]  # rotated copies
            for remote in wanted:
                try:
                    sftp.get(remote, str(target / posixpath.basename(remote)))
                    print(f"  downloaded: {remote}")
                except OSError:
                    pass  # not there (yet) - normal for the rotated files
        finally:
            sftp.close()
        # The journals are not files we can fetch: ask journalctl and save its output.
        for name, command in (("mpd_journal.txt", "journalctl -u mpd -b --no-pager"),
                              ("kernel_journal.txt", "journalctl -k -b --no-pager")):
            _, out = self.run(command, timeout=60)
            if out:
                (target / name).write_text(out, encoding="utf-8")
                print(f"  saved: {name}")
        files = sorted(p.name for p in target.iterdir())
        print(f"Done, {len(files)} files: {', '.join(files) or '-'}")
        return target

    def upload(self) -> None:
        """Upload the package and the Pi's shell scripts / requirements, and make the scripts executable.

        ``good_moode/**`` (code and fonts) goes to ~/scripts/good_moode/, and ``scripts/pi/*.sh|txt``
        goes to ~/scripts/ next to it. Existing files are overwritten; nothing is deleted.
        """
        print(f"Uploading to: {self.scripts}")
        package = sorted(p for p in (ROOT / "good_moode").rglob("*") if p.is_file() and "__pycache__" not in p.parts)
        remote_dirs = sorted({posixpath.dirname(f"{self.scripts}/{p.relative_to(ROOT).as_posix()}") for p in package})
        self.run("mkdir -p " + " ".join(shlex.quote(d) for d in remote_dirs))
        for src in package:
            self.put(src, f"{self.scripts}/{src.relative_to(ROOT).as_posix()}")
        for src in sorted(p for p in (ROOT / "scripts" / "pi").iterdir() if p.suffix in (".sh", ".txt")):
            self.put(src, f"{self.scripts}/{src.name}")
        self.show(f"chmod u+x {self.scripts}/*.sh && echo '  chmod u+x done'")

    def install_ready_script(self) -> bool:
        """Copy ready-script.sh into moOde, which runs it after every boot; returns True on success."""
        code = self.show(f"cp {self.scripts}/ready-script.sh {MOODE_READY_SCRIPT} && echo '  ready-script.sh installed'", sudo=True)
        if code:
            print("  installing ready-script.sh failed")
        return code == 0

    def set_logging(self, enabled: bool) -> None:
        """Switch logging on or off on the Pi by creating / removing the flag file ~/scripts/logging.on.

        On: run_app.sh logs to /var/log/state_machine.log (created here, writable by the program) and
        ready-script.sh starts the diagnostics logger. Off: nothing is logged, so nothing is written to
        the SD card. The Pi-side scripts read the flag themselves, so they never need editing.
        """
        flag = f"{self.scripts}/{LOGGING_FLAG}"
        if enabled:
            self.show(f"touch {flag} && echo '  logging: ON (the flag file is {flag})'")
            self.show("touch /var/log/state_machine.log && chmod 666 /var/log/state_machine.log", sudo=True)
        else:
            self.show(f"rm -f {flag} && echo '  logging: OFF (nothing is written to the SD card; use --logging to turn it on)'")

    def deploy(self, logging: bool = False) -> None:
        """Upload the files, set the logging switch and install moOde's ready-script (for updates)."""
        self.upload()
        self.set_logging(logging)
        self.install_ready_script()
        print("Done. To make the changes take effect: restart (or reboot).")

    def setup(self, reset_stations: bool = False, assume_yes: bool = False, logging: bool = False) -> None:
        """First-time setup of a freshly installed moOde (the manual steps of the README).

        Safe to repeat: an existing venv is kept, files are overwritten, and nothing is deleted
        unless ``reset_stations`` is set and confirmed (or ``assume_yes`` skips the question).
        With ``assume_yes`` the final reboot question is not asked and no reboot happens.
        ``logging`` sets the logging switch (see ``set_logging``).
        """
        def step(n: int, title: str) -> None:
            print(f"\n[{n}/7] {title}")

        step(1, "Enable I2C (the OLED needs it)")
        # raspi-config's `get_i2c` prints 0 when I2C is enabled; `do_i2c 0` enables it.
        self.show(
            "command -v raspi-config >/dev/null || { echo '  raspi-config not found, enable I2C by hand'; exit 0; }; "
            "if [ \"$(raspi-config nonint get_i2c)\" = 0 ]; then echo '  already enabled'; "
            "else raspi-config nonint do_i2c 0 && echo '  enabled (takes effect after a reboot)'; fi",
            sudo=True,
        )

        step(2, "Folder and virtual environment")
        # An existing .venv is kept (the README's `venv --clear` would wipe it).
        self.show(
            f"mkdir -p {self.scripts} && if [ -d {self.scripts}/.venv ]; then echo '  .venv already exists'; "
            f"else python3 -m venv {self.scripts}/.venv && echo '  .venv created'; fi",
            timeout=300,
        )

        step(3, "Uploading the files")
        self.upload()

        step(4, "Installing the Python libraries (can take a few minutes)")
        code, out = self.run(f"{self.scripts}/.venv/bin/pip install -r {self.scripts}/requirements.txt", timeout=1800)
        print("\n".join(out.splitlines()[-6:]))  # pip is chatty: the last lines tell the result
        if code:
            print("  WARNING: pip install failed (see above)")

        step(5, "Logging switch")
        self.set_logging(logging)

        step(6, "Installing the ready-script into moOde")
        self.install_ready_script()

        step(7, "Deleting moOde's default radio stations")
        if reset_stations:
            db = "/var/local/www/db/moode-sqlite3.db"
            _, count = self.run(f"sqlite3 {db} 'SELECT COUNT(*) FROM cfg_radio;'", sudo=True)
            if assume_yes or confirmed(f"  Delete {count} radio stations from moOde's database? [y/N] "):
                self.show(f"sqlite3 {db} 'DELETE FROM cfg_radio;' && echo '  deleted'", sudo=True)
            else:
                print("  skipped")
        else:
            print("  skipped (use --reset-stations to do it)")

        print("\nDone. One manual step is left: add the station in moOde's web UI:")
        print("  http://stream.radiomost.hu:8200/live.mp3")
        if not assume_yes and confirmed("Reboot the Pi now (needed for I2C and the automatic start)? [y/N] "):
            self.power("reboot", assume_yes=True)
        else:
            print("Reboot later with: python scripts/local/reboot.py")

    def restart(self) -> None:
        """Restart the OLED program (as root, like moOde's ready-script starts it).

        The diagnostics logger is stopped too (same process pattern) and started again only if
        logging is switched on (flag file). The old process name is killed as well, so this also
        works right after migrating from the old single-file ``moode_state_machine.py``.
        """
        command = (
            # [x] trick: '[g]ood_moode' matches "good_moode" but not its own spelling, so pkill
            # cannot kill the `bash -c` that carries this very command line.
            "pkill -f '[m]oode_state_machine.py'; pkill -f '[g]ood_moode'; sleep 1; "
            f"cd {self.scripts}; "
            "setsid bash run_app.sh </dev/null >/dev/null 2>&1 & "
            f"if [ -f {LOGGING_FLAG} ]; then setsid bash run_logger.sh </dev/null >/dev/null 2>&1 & fi; "
            "sleep 2; pgrep -af '[g]ood_moode' || echo '(did not start)'"
        )
        self.show(command, sudo=True, timeout=30)

    def power(self, action: str, assume_yes: bool = False) -> None:
        """Shut down or reboot the Pi (``action`` is "shutdown" or "reboot"); asks first unless ``assume_yes``."""
        label = "shut down" if action == "shutdown" else "reboot"
        if not assume_yes and not confirmed(f"Really {label} the Pi ({self.host})? [y/N] "):
            print("Cancelled.")
            return
        command = "shutdown -h now" if action == "shutdown" else "reboot"
        try:
            self.run(command, sudo=True, timeout=10)
        except Exception:
            pass  # the connection drops while the Pi goes down - expected
        print("Command sent; the connection will drop." + (" The Pi powers off in a few seconds." if action == "shutdown" else ""))

    # ---- dispatch ----

    def execute(self, argv: list[str], assume_yes: bool = False) -> bool:
        """Run one command line (already split into words); returns False when the session should end."""
        if not argv:
            return True
        cmd, args = argv[0].lower(), argv[1:]
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd in ("help", "?"):
            # the "Commands:" part of the module docstring is the help text
            print(__doc__.split("Commands:")[1].split("Add --yes")[0].rstrip())
        elif cmd == "status":
            self.status()
        elif cmd == "log":
            self.download_logs()
        elif cmd == "tail":
            self.tail(args[0] if args else "diag", int(args[1]) if len(args) > 1 else 40)
        elif cmd == "get" and args:
            self.get(args[0], Path(args[1]) if len(args) > 1 else None)
        elif cmd == "put" and len(args) == 2:
            self.put(Path(args[0]), args[1])
        elif cmd == "setup":
            self.setup("--reset-stations" in args, assume_yes, logging="--logging" in args)
        elif cmd == "deploy":
            self.deploy(logging="--logging" in args)
        elif cmd == "restart":
            self.restart()
        elif cmd in ("shutdown", "reboot"):
            self.power(cmd, assume_yes)
            return False  # the connection is gone after this
        elif cmd == "run" and args:
            self.show(" ".join(args))
        elif cmd == "sudo" and args:
            self.show(" ".join(args), sudo=True)
        else:
            print(f"Unknown or incomplete command: {' '.join(argv)}  (type help)")
        return True


def connect_from_env() -> PiRemote:
    """Connect to the Pi using the .env settings; exits with a readable message on failure.

    Also used by the ready-made scenarios next to this file (deploy.py, download.py, reboot.py,
    initial_deploy.py).
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # non-ASCII output survives pipes / cp1252 consoles
    env = load_env()
    pi = PiRemote(env["PI_HOST"], env["PI_USER"], env["PI_PASSWORD"], int(env.get("PI_PORT", "22")))

    print(f"Connecting: {pi.user}@{pi.host} ...")
    try:
        pi.connect()
    except paramiko.BadHostKeyException:
        sys.exit(f"The Pi's host key has changed! If you know why (e.g. a new SD card), delete its line from {KNOWN_HOSTS}.")
    except paramiko.AuthenticationException:
        sys.exit("Wrong user name or password (.env).")
    except Exception as exc:
        sys.exit(f"Could not connect: {type(exc).__name__}: {exc}")
    print("Connected.")
    return pi


def main() -> int:
    """Command line entry point: one command given as arguments, or an interactive ``pi>`` shell."""
    if {"-h", "--help", "help"} & set(sys.argv[1:2]):
        print(__doc__)  # no connection needed for the help text
        return 0
    argv = sys.argv[1:]
    assume_yes = "--yes" in argv
    argv = [a for a in argv if a != "--yes"]
    pi = connect_from_env()

    try:
        if argv:  # one-shot mode
            pi.execute(argv, assume_yes)
        else:
            print("Type: help")
            while True:
                try:
                    line = input("pi> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                try:
                    if not pi.execute(shlex.split(line), assume_yes):
                        break
                except Exception as exc:  # a failed command must not end the session
                    print(f"Error: {type(exc).__name__}: {exc}")
    finally:
        pi.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
