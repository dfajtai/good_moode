#!/usr/bin/env python3
"""Remote control of the Raspberry Pi over SSH (runs on the PC, not on the Pi).

Connection details are read from the .env file in the repo root:

    PI_HOST=192.168.0.80
    PI_USER=fajtai
    PI_PASSWORD=...
    PI_PORT=22            (optional)

Usage (from the repo root):

    python tools/pi_remote.py                 # interactive shell, type "help"
    python tools/pi_remote.py status          # run one command and exit
    python tools/pi_remote.py log             # download the logs into ./pi_logs/<time>/
    python tools/pi_remote.py tail diag 100
    python tools/pi_remote.py shutdown

Commands:

    status                 uptime, temperature, throttling, Wi-Fi, mpc status, our processes
    log                    download the logs (playback_diag.log*, playback_logger.err,
                           state_machine.log, moode.log, mpd and kernel journals)
    tail [name|path] [n]   last n lines of a log; names: diag, state, err, moode
    get <remote> [local]   download a file
    put <local> <remote>   upload a file
    deploy                 upload scripts/*.py|sh to ~/scripts, chmod +x, and install
                           ready-script.sh into moOde (like the README's last step)
    restart                restart the OLED program and the logger on the Pi (as root)
    shutdown | reboot      power off / reboot the Pi (asks for confirmation)
    run <command>          run any command;  sudo <command>  runs it as root
    help | exit

The first connection stores the Pi's host key in ~/.good_moode_known_hosts;
later connections fail loudly if the key changes. Needs: pip install paramiko
"""

import posixpath
import shlex
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import paramiko
except ImportError:
    sys.exit("Hiányzik a paramiko: pip install paramiko")

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
KNOWN_HOSTS = Path.home() / ".good_moode_known_hosts"
LOG_DIR = ROOT / "pi_logs"

DEPLOY_FILES = ("moode_state_machine.py", "playback_logger.py", "run_app.sh", "run_logger.sh", "ready-script.sh")
MOODE_READY_SCRIPT = "/var/local/www/commandw/ready-script.sh"

STATUS_COMMAND = r"""
echo "== uptime / load";   uptime
echo "== homerseklet";     vcgencmd measure_temp 2>/dev/null
echo "== feszultseg/throttle (0x0 = ok)"; vcgencmd get_throttled 2>/dev/null
echo "== wifi";            iw dev wlan0 link 2>/dev/null | grep -E 'signal|tx bitrate'
echo "== mpc";             mpc status 2>&1
echo "== futo programok";  pgrep -af 'moode_state_machine|playback_logger' || echo "(nincs)"
"""


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse simple KEY=VALUE lines (comments and blank lines ignored)."""
    if not path.exists():
        sys.exit(f"Nincs {path}. Hozd létre PI_HOST, PI_USER, PI_PASSWORD sorokkal.")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    missing = [k for k in ("PI_HOST", "PI_USER", "PI_PASSWORD") if not env.get(k)]
    if missing:
        sys.exit(f"A .env-ből hiányzik: {', '.join(missing)}")
    return env


class PiRemote:
    """An SSH/SFTP connection to the Pi with the commands listed in the module docstring."""

    def __init__(self, host: str, user: str, password: str, port: int = 22) -> None:
        self.host, self.user, self.password, self.port = host, user, password, port
        self.home = f"/home/{user}"
        self.scripts = f"{self.home}/scripts"
        self.client = paramiko.SSHClient()
        if KNOWN_HOSTS.exists():
            self.client.load_host_keys(str(KNOWN_HOSTS))
        # trust on first use: unknown host keys are stored, changed ones are rejected
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # ---- connection ----

    def connect(self) -> None:
        self.client.connect(
            self.host, port=self.port, username=self.user, password=self.password,
            timeout=10, banner_timeout=15, auth_timeout=15,
            look_for_keys=False, allow_agent=False,
        )
        self.client.save_host_keys(str(KNOWN_HOSTS))

    def close(self) -> None:
        self.client.close()

    # ---- running commands ----

    def run(self, command: str, sudo: bool = False, timeout: float = 60) -> tuple[int, str]:
        """Run a command; returns ``(exit_code, stdout+stderr)``. The password never leaves stdin."""
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
        code, out = self.run(command, sudo, timeout)
        if out:
            print(out)
        return code

    # ---- commands ----

    def status(self) -> None:
        self.show(STATUS_COMMAND)

    def resolve(self, name: str) -> str:
        """Short log names to remote paths; anything else is taken as a path."""
        names = {
            "diag": f"{self.scripts}/playback_diag.log",
            "err": f"{self.scripts}/playback_logger.err",
            "state": "/var/log/state_machine.log",
            "moode": "/var/log/moode.log",
        }
        return names.get(name, name)

    def tail(self, name: str = "diag", lines: int = 40) -> None:
        self.show(f"tail -n {int(lines)} {shlex.quote(self.resolve(name))}")

    def get(self, remote: str, local: Optional[Path] = None) -> Optional[Path]:
        remote = self.resolve(remote)
        local = local or Path(posixpath.basename(remote))
        sftp = self.client.open_sftp()
        try:
            sftp.get(remote, str(local))
        except OSError as exc:
            print(f"  kihagyva: {remote} ({exc})")
            return None
        finally:
            sftp.close()
        print(f"  letöltve: {remote} -> {local} ({local.stat().st_size} bájt)")
        return local

    def put(self, local: Path, remote: str) -> bool:
        sftp = self.client.open_sftp()
        try:
            sftp.put(str(local), remote)
        except OSError as exc:
            print(f"  hiba: {local} -> {remote} ({exc})")
            return False
        finally:
            sftp.close()
        print(f"  feltöltve: {local.name} -> {remote}")
        return True

    def download_logs(self) -> Path:
        """Download every log that exists into ./pi_logs/<timestamp>/."""
        target = LOG_DIR / time.strftime("%Y%m%d_%H%M%S")
        target.mkdir(parents=True, exist_ok=True)
        print(f"Naplók letöltése ide: {target}")
        sftp = self.client.open_sftp()
        try:
            wanted = [self.resolve(n) for n in ("diag", "err", "state", "moode")]
            wanted += [f"{self.resolve('diag')}.{i}" for i in range(1, 6)]
            for remote in wanted:
                try:
                    sftp.get(remote, str(target / posixpath.basename(remote)))
                    print(f"  letöltve: {remote}")
                except OSError:
                    pass  # not there (yet) - normal for rotated files
        finally:
            sftp.close()
        for name, command in (("mpd_journal.txt", "journalctl -u mpd -b --no-pager"),
                              ("kernel_journal.txt", "journalctl -k -b --no-pager")):
            code, out = self.run(command, timeout=60)
            if out:
                (target / name).write_text(out, encoding="utf-8")
                print(f"  elmentve: {name}")
        files = sorted(p.name for p in target.iterdir())
        print(f"Kész, {len(files)} fájl: {', '.join(files) or '-'}")
        return target

    def deploy(self) -> None:
        print(f"Feltöltés ide: {self.scripts}")
        for name in DEPLOY_FILES:
            src = ROOT / "scripts" / name
            if not src.exists():
                print(f"  hiányzik: {src}")
                continue
            self.put(src, f"{self.scripts}/{name}")
        self.show(f"chmod u+x {self.scripts}/*.sh && echo '  chmod u+x kész'")
        code = self.show(f"cp {self.scripts}/ready-script.sh {MOODE_READY_SCRIPT} && echo '  ready-script.sh telepítve'", sudo=True)
        if code:
            print("  a ready-script.sh telepítése nem sikerült")
        print("Kész. A változások életbe lépéséhez: restart (vagy reboot).")

    def restart(self) -> None:
        """Restart the OLED program and the logger (root, like moOde's ready-script does)."""
        command = (
            "pkill -f moode_state_machine.py; pkill -f playback_logger.py; sleep 1; "
            f"cd {self.scripts}; "
            "setsid bash run_app.sh </dev/null >/dev/null 2>&1 & "
            "setsid bash run_logger.sh </dev/null >/dev/null 2>&1 & "
            "sleep 2; pgrep -af 'moode_state_machine|playback_logger' || echo '(nem indult el)'"
        )
        self.show(command, sudo=True, timeout=30)

    def power(self, action: str, assume_yes: bool = False) -> None:
        label = "kikapcsolod" if action == "shutdown" else "újraindítod"
        if not assume_yes and input(f"Biztosan {label} a Pi-t ({self.host})? [i/N] ").strip().lower() not in ("i", "y"):
            print("Megszakítva.")
            return
        command = "shutdown -h now" if action == "shutdown" else "reboot"
        try:
            self.run(command, sudo=True, timeout=10)
        except Exception:
            pass  # the connection drops while the Pi goes down - expected
        print("Parancs elküldve; a kapcsolat megszakad." + (" A Pi pár másodperc múlva leáll." if action == "shutdown" else ""))

    # ---- dispatch ----

    def execute(self, argv: list[str], assume_yes: bool = False) -> bool:
        """Run one command line; returns False when the session should end."""
        if not argv:
            return True
        cmd, args = argv[0].lower(), argv[1:]
        if cmd in ("exit", "quit", "q"):
            return False
        if cmd in ("help", "?"):
            print(__doc__.split("Commands:")[1].split("The first connection")[0].rstrip())
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
        elif cmd == "deploy":
            self.deploy()
        elif cmd == "restart":
            self.restart()
        elif cmd in ("shutdown", "reboot"):
            self.power(cmd, assume_yes)
            return False
        elif cmd == "run" and args:
            self.show(" ".join(args))
        elif cmd == "sudo" and args:
            self.show(" ".join(args), sudo=True)
        else:
            print(f"Ismeretlen vagy hiányos parancs: {' '.join(argv)}  (help)")
        return True


def connect_from_env() -> PiRemote:
    """Connect to the Pi using the .env settings; exits with a readable message on failure.

    Also used by the ready-made scenarios in ``local_script/``.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # accents survive pipes / cp1252 consoles
    env = load_env()
    pi = PiRemote(env["PI_HOST"], env["PI_USER"], env["PI_PASSWORD"], int(env.get("PI_PORT", "22")))

    print(f"Csatlakozás: {pi.user}@{pi.host} ...")
    try:
        pi.connect()
    except paramiko.BadHostKeyException:
        sys.exit(f"A Pi host kulcsa megváltozott! Ha tudod, hogy miért (pl. új SD-kártya), töröld a {KNOWN_HOSTS} megfelelő sorát.")
    except paramiko.AuthenticationException:
        sys.exit("Hibás felhasználónév vagy jelszó (.env).")
    except Exception as exc:
        sys.exit(f"Nem sikerült csatlakozni: {type(exc).__name__}: {exc}")
    print("Csatlakozva.")
    return pi


def main() -> int:
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
            print('Írd be: help')
            while True:
                try:
                    line = input("pi> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                try:
                    if not pi.execute(shlex.split(line), assume_yes):
                        break
                except Exception as exc:
                    print(f"Hiba: {type(exc).__name__}: {exc}")
    finally:
        pi.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
