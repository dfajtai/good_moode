"""The radio's status as a grouped character table, like ``top``: what to collect on the Pi, how to read it, how to draw it.

Three parts, kept apart so that the last two are plain functions that can be tested without a Pi:

- ``STATUS_COMMAND``: a shell snippet that runs on the Pi and prints "== section" blocks of ``key=value`` lines
  (and the raw output of ``mpc status`` / ``iw``). The pings, the DNS lookup and the TCP connect run in parallel,
  so even when everything is dead the snippet needs about 3 seconds, not the sum of all timeouts.
- ``parse_status``: turns that text into a dictionary and decides what is a problem (see the thresholds below).
- ``render_status``: draws the dictionary as boxes, ``WIDTH`` characters wide, in the order you want to know things:
  the verdict and the problems first, then playback, program, network, system. A failing cell gets a ``!!``.
"""

import re
from typing import Optional

WIDTH = 100  # characters per line of the table (long values are cut with "...")

# Thresholds for "this is a problem". The counters of the Wi-Fi link and of the interface count from the connection
# / boot, so they are judged as a share of all packets, not as absolute numbers.
WEAK_SIGNAL_DBM = -80      # at or below this the Wi-Fi was dropping out (the logs showed -72 ... -82 dBm)
RETRY_RATIO_WARN = 0.30    # more than 30 % of the transmitted packets needed a retry
ERROR_RATIO_WARN = 0.01    # more than 1 % failed / dropped / errors
SLOW_DNS_MS = 1000         # a DNS lookup slower than this
HOT_CPU_C = 80.0           # the Pi starts to throttle around here

# The `status` snippet. @HOST@, @PORT@ and @PIN@ are the stream's host and port and the relay's GPIO number; they are
# filled in from good_moode/config.py. The [x] trick in the pgrep pattern keeps pgrep from finding this very shell.
STATUS_COMMAND = r"""
export LC_ALL=C PATH="$PATH:/usr/sbin:/sbin"
t=$(mktemp -d)
echo "== host"
echo "name=$(hostname)"
echo "time=$(date +%T)"
echo "uptime=$(uptime -p 2>/dev/null | sed 's/^up //')"
echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "== system"
echo "temp=$(vcgencmd measure_temp 2>/dev/null | sed "s/temp=//; s/'C//")"
echo "throttled=$(vcgencmd get_throttled 2>/dev/null | sed 's/throttled=//')"
echo "memory=$(free -m | awk '/^Mem:/ {print $3 "/" $2}')"
echo "disk=$(df -h / | awk 'NR==2 {print $5 " of " $2}')"
echo "== playback"
echo "now=$(mpc current 2>/dev/null)"
u=$(mpc -f "%file%" current 2>/dev/null | head -1); [ -z "$u" ] && u=$(mpc -f "%file%" playlist 2>/dev/null | head -1)
echo "stream=$u"
echo "== mpc"
mpc status 2>&1
echo "== program"
echo "pid=$(pgrep -f '[g]ood_moode|[m]oode_state_machine' | head -1)"
echo "relay=$(pinctrl get @PIN@ 2>/dev/null || echo unavailable)"
echo "logging=$([ -f ~/scripts/logging.on ] && echo on || echo off)"
echo "== moode"
grep -E '^(moOde release|RaspiOS|Linux kernel|Pi model|Worker responsiveness|CPU governor)' /var/local/www/sysinfo.txt 2>/dev/null
echo "== audio"
alsa=0
for f in /proc/asound/card*/pcm0p/sub0/hw_params; do
  c=${f#/proc/asound/}; c=${c%%/*}
  if grep -q '^format' "$f" 2>/dev/null; then echo "alsa|$c|$(grep -E '^(format|channels|rate):' "$f" | tr '\n' ' ')"; alsa=1; fi
done
[ "$alsa" = 0 ] && echo "alsa|none|closed"
echo "mpd_version=$(mpd --version 2>/dev/null | head -1 | sed 's/Music Player Daemon //; s/ (.*//')"
echo "mpd_uptime=$(mpc stats 2>/dev/null | sed -n 's/^Uptime: *//p')"
mpc outputs 2>/dev/null | sed -n 's/^Output [0-9]* (\(.*\)) is enabled/output=\1/p'
echo "== wifi"
iw dev wlan0 link 2>/dev/null
echo "power_save=$(iw dev wlan0 get power_save 2>/dev/null | sed 's/Power save: //')"
echo "== station"
iw dev wlan0 station dump 2>/dev/null
echo "== net"
ifc=$(ip route 2>/dev/null | awk '/^default/ {print $5; exit}')
gw=$(ip route 2>/dev/null | awk '/^default/ {print $3; exit}')
echo "interface=$ifc"
echo "ip=$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "gateway=$gw"
echo "dns_server=$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf 2>/dev/null)"
for k in rx_packets rx_errors rx_dropped tx_packets tx_errors tx_dropped; do echo "$k=$(cat /sys/class/net/$ifc/statistics/$k 2>/dev/null)"; done
ping_one() {
  r=$(ping -c3 -W1 -i0.3 "$2" 2>&1)
  lo=$(echo "$r" | sed -n 's/^\([0-9]*\) packets transmitted, \([0-9]*\) received.* \([0-9.]*\)% packet loss.*/\1 \2 \3/p')
  rt=$(echo "$r" | sed -n 's#^.* = \([0-9.]*\)/\([0-9.]*\)/\([0-9.]*\)/.*#\1 \2 \3#p')
  echo "ping|$1|$2|${lo:-0 0 100}|${rt:--}"
}
if [ -n "$gw" ]; then ping_one Router "$gw" > "$t/1" & else echo "ping|Router|-|0 0 0|-" > "$t/1"; fi
ping_one Internet 1.1.1.1 > "$t/2" &
ping_one "Stream host" @HOST@ > "$t/3" &
( s=$(date +%s%N); a=$(timeout 4 getent hosts @HOST@ | awk '{print $1; exit}'); e=$(date +%s%N); echo "dns|@HOST@|$a|$(( (e-s)/1000000 ))" ) > "$t/4" &
( s=$(date +%s%N); if timeout 3 bash -c 'exec 3<>/dev/tcp/@HOST@/@PORT@' 2>/dev/null; then r=ok; else r=fail; fi; e=$(date +%s%N); echo "tcp|@HOST@|@PORT@|$r|$(( (e-s)/1000000 ))" ) > "$t/5" &
wait
cat "$t/1" "$t/2" "$t/3" "$t/4" "$t/5"
rm -rf "$t"
"""


# ---------------------------------------------------------------- reading

def _sections(text: str) -> dict[str, list[str]]:
    """Split the snippet's output into its "== name" blocks (name -> lines)."""
    sections: dict[str, list[str]] = {}
    current: Optional[list[str]] = None
    for line in text.splitlines():
        if line.startswith("== "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
    return sections


def _kv(lines: list[str]) -> dict[str, str]:
    """The ``key=value`` lines of a block (the value may be empty or contain "=")."""
    out: dict[str, str] = {}
    for line in lines:
        if "=" in line and not line.startswith(("ping|", "dns|", "tcp|")):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def _find(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _int(value: Optional[str]) -> Optional[int]:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_status(text: str, placeholder: str = "", program_stream: str = "", relay_pin: int = 26) -> dict:
    """Read the output of ``STATUS_COMMAND`` into a dictionary and work out the problems.

    ``placeholder`` is the station's fixed "no track info" title (good_moode/config.py): a title that contains it is
    marked as such but is not a problem. ``program_stream`` is the stream the program reads titles from; a different
    active stream is pointed out. The result has the groups ``host``, ``playback``, ``program``, ``wifi``, ``net``
    and ``system``, the list ``problems`` (texts) and the set ``flags`` (names of the cells that get a "!!").
    A problem is: the program does not run, MPD stopped with an error, a weak Wi-Fi signal, many Wi-Fi retries or a
    beacon loss, the router or the internet not answering, the stream host not resolving (or slowly), no TCP
    connection to the stream, many dropped packets, under-voltage / throttling, a hot CPU. A ping to the stream host
    is shown but not judged: servers often drop ICMP, the DNS lookup and the TCP connect are the real test.
    """
    sec = _sections(text)
    problems: list[str] = []
    flags: set[str] = set()

    def problem(message: str, flag: str) -> None:
        problems.append(message)
        flags.add(flag)

    host = _kv(sec.get("host", []))
    system = _kv(sec.get("system", []))
    playback_kv = _kv(sec.get("playback", []))
    program_kv = _kv(sec.get("program", []))
    net_kv = _kv(sec.get("net", []))

    # ---- playback
    mpc = "\n".join(sec.get("mpc", []))
    state = "playing" if "[playing]" in mpc else "paused" if "[paused]" in mpc else "stopped"
    error = _find(mpc, r"^ERROR: (.+)$")
    if error:
        problem(f"MPD error: {error}", "state")
    title = playback_kv.get("now", "")
    stream = playback_kv.get("stream", "")
    playback = {
        "state": state,
        "volume": _int(_find(mpc, r"volume:\s*(\d+)%")),
        "title": title,
        "placeholder": bool(title) and bool(placeholder) and placeholder.casefold() in title.casefold(),
        "stream": stream,
        "queue": _find(mpc, r"#(\d+/\d+)"),
        "program_stream": program_stream if stream and program_stream and stream != program_stream else "",
    }

    # ---- program
    pid = program_kv.get("pid", "")
    relay_level = _find(program_kv.get("relay", ""), r"\|\s*(hi|lo)\b")
    program = {
        "running": bool(pid),
        "pid": pid,
        "relay": {"hi": "ON", "lo": "OFF"}.get(relay_level or "", "?"),
        "relay_pin": relay_pin,
        "logging": program_kv.get("logging", "off"),
    }
    if not program["running"]:
        problem("the good_moode program is NOT running", "program")

    # ---- moOde's own status (sysinfo.txt), ALSA and MPD
    info_kv = _kv(sec.get("moode", []))
    audio_lines = sec.get("audio", [])
    audio_kv = _kv(audio_lines)
    alsa = None
    for line in audio_lines:
        parts = line.split("|")
        if parts[0] == "alsa" and len(parts) >= 3 and parts[1] != "none":
            alsa = {"card": parts[1], "format": _find(parts[2], r"format:\s*(\S+)"),
                    "channels": _int(_find(parts[2], r"channels:\s*(\d+)")), "rate": _int(_find(parts[2], r"rate:\s*(\d+)"))}
    uptime = re.search(r"(\d+) days?, (\d+):(\d+):(\d+)", audio_kv.get("mpd_uptime", ""))
    moode = {
        "release": info_kv.get("moOde release", ""), "os": info_kv.get("RaspiOS", ""), "kernel": info_kv.get("Linux kernel", ""),
        "model": info_kv.get("Pi model", ""), "governor": info_kv.get("CPU governor", ""), "worker": info_kv.get("Worker responsiveness", ""),
        "alsa": alsa,
        "mpd_version": audio_kv.get("mpd_version", ""),
        "mpd_uptime": audio_kv.get("mpd_uptime", ""),
        "mpd_uptime_min": (int(uptime.group(1)) * 1440 + int(uptime.group(2)) * 60 + int(uptime.group(3))) if uptime else None,
        "outputs": [line.split("=", 1)[1] for line in audio_lines if line.startswith("output=")],
    }

    # ---- Wi-Fi
    link = "\n".join(sec.get("wifi", []))
    station = "\n".join(sec.get("station", []))
    wifi = {
        "ssid": _find(link, r"SSID:\s*(.+)"),
        "freq": _int(_find(link, r"freq:\s*(\d+)")),
        "signal": _int(_find(station, r"^\s*signal:\s*(-?\d+)") or _find(link, r"signal:\s*(-?\d+)")),
        "signal_avg": _int(_find(station, r"signal avg:\s*(-?\d+)")),
        "tx_rate": _float(_find(station, r"tx bitrate:\s*([\d.]+)") or _find(link, r"tx bitrate:\s*([\d.]+)")),
        "rx_rate": _float(_find(station, r"rx bitrate:\s*([\d.]+)")),
        "power_save": _find(_kv(sec.get("wifi", [])).get("power_save", ""), r"(.+)") or "?",
        "connected": _int(_find(station, r"connected time:\s*(\d+)")),
        "tx_retries": _int(_find(station, r"tx retries:\s*(\d+)")),
        "tx_failed": _int(_find(station, r"tx failed:\s*(\d+)")),
        "tx_packets": _int(_find(station, r"tx packets:\s*(\d+)")),
        "beacon_loss": _int(_find(station, r"beacon loss:\s*(\d+)")),
    }
    interface = net_kv.get("interface", "")
    best_signal = wifi["signal_avg"] if wifi["signal_avg"] is not None else wifi["signal"]
    if interface.startswith("wlan") and wifi["signal"] is None:
        problem("no Wi-Fi link information (not connected?)", "wifi.signal")
    elif best_signal is not None and best_signal <= WEAK_SIGNAL_DBM:
        problem(f"weak Wi-Fi signal ({best_signal} dBm)", "wifi.signal")
    packets = wifi["tx_packets"] or 0
    if packets and (wifi["tx_retries"] or 0) / packets > RETRY_RATIO_WARN:
        problem(f"{wifi['tx_retries']} of {packets} Wi-Fi packets needed a retry", "wifi.link")
    if packets and (wifi["tx_failed"] or 0) / packets > ERROR_RATIO_WARN:
        problem(f"{wifi['tx_failed']} of {packets} Wi-Fi packets failed", "wifi.link")
    if (wifi["beacon_loss"] or 0) > 0:
        problem(f"the router's beacons were lost {wifi['beacon_loss']} time(s)", "wifi.link")

    # ---- network
    counters = {key: _int(net_kv.get(key)) for key in
                ("rx_packets", "rx_errors", "rx_dropped", "tx_packets", "tx_errors", "tx_dropped")}
    pings, dns, tcp = [], None, None
    for line in sec.get("net", []):
        parts = line.split("|")
        if parts[0] == "ping" and len(parts) >= 5:
            sent, recv, loss = (_float(x) or 0 for x in (parts[3].split() + ["0", "0", "0"])[:3])
            rtt = parts[4].split() if parts[4] != "-" else []
            pings.append({"name": parts[1], "target": parts[2], "sent": int(sent), "recv": int(recv), "loss": loss,
                          "min": rtt[0] if len(rtt) == 3 else "-", "avg": rtt[1] if len(rtt) == 3 else "-",
                          "max": rtt[2] if len(rtt) == 3 else "-"})
        elif parts[0] == "dns" and len(parts) >= 4:
            dns = {"host": parts[1], "address": parts[2], "ms": _int(parts[3])}
        elif parts[0] == "tcp" and len(parts) >= 5:
            tcp = {"host": parts[1], "port": parts[2], "ok": parts[3] == "ok", "ms": _int(parts[4])}
    net = {"interface": interface, "ip": net_kv.get("ip", ""), "gateway": net_kv.get("gateway", ""),
           "dns_server": net_kv.get("dns_server", ""), "counters": counters, "pings": pings, "dns": dns, "tcp": tcp}

    router_ok = True
    for ping in pings:
        if ping["name"] == "Router":
            if ping["target"] == "-":
                router_ok = False
                problem("no default route: the Pi has no router", "ping.Router")
            elif ping["loss"] > 0:
                router_ok = ping["recv"] > 0
                problem("the router did not answer the ping" if ping["recv"] == 0
                        else f"router ping: {ping['loss']:.0f} % packet loss", "ping.Router")
    for ping in pings:
        if ping["name"] == "Internet" and ping["loss"] > 0 and router_ok:   # if the router is dead, the rest is a given
            problem("the internet (1.1.1.1) did not answer the ping" if ping["recv"] == 0
                    else f"internet ping: {ping['loss']:.0f} % packet loss", "ping.Internet")
    if dns:
        if not dns["address"]:
            problem(f"{dns['host']} does not resolve (DNS)", "dns")
        elif (dns["ms"] or 0) >= SLOW_DNS_MS:
            problem(f"DNS is slow ({dns['ms']} ms)", "dns")
    if tcp and not tcp["ok"]:
        problem(f"cannot connect to {tcp['host']}:{tcp['port']}", "tcp")
    for side in ("rx", "tx"):
        total = counters[f"{side}_packets"] or 0
        bad = (counters[f"{side}_errors"] or 0) + (counters[f"{side}_dropped"] or 0)
        if total and bad / total > ERROR_RATIO_WARN:
            problem(f"{bad} of {total} {side.upper()} packets on {interface or 'the interface'} dropped or in error",
                    "net.counters")

    # ---- system
    temp = _float(system.get("temp"))
    throttled = system.get("throttled", "")
    if temp is not None and temp >= HOT_CPU_C:
        problem(f"the CPU is at {temp:.1f} C (it throttles around {HOT_CPU_C:.0f} C)", "system.temp")
    if throttled and _int_hex(throttled):
        problem(f"under-voltage or throttling reported ({throttled})", "system.power")
    system_info = {"temp": temp, "throttled": throttled, "memory": system.get("memory", "?"), "disk": system.get("disk", "?")}

    return {"host": host, "playback": playback, "program": program, "wifi": wifi, "net": net,
            "system": system_info, "moode": moode, "problems": problems, "flags": flags}


def _int_hex(value: str) -> int:
    try:
        return int(value, 16)
    except ValueError:
        return 0


# ---------------------------------------------------------------- drawing

def _fit(text: str, width: int) -> str:
    """Pad or cut (with "...") a text to exactly ``width`` characters: every line of the table has the same width."""
    if len(text) <= width:
        return text.ljust(width)
    return text[:max(width - 3, 0)] + "..."[:width]


def _wrap(label: str, parts: list[str], inner: int, label_width: int = 10, sep: str = "   ") -> list[str]:
    """Rows "label  part   part ..." that never get wider than ``inner``.

    When the parts do not fit on one row they continue on the next row(s), indented under the first part (the
    label is only on the first row), so nothing is cut off and the table keeps its fixed width.
    """
    rows: list[str] = []
    current = ""
    for part in parts:
        if not part:
            continue
        candidate = part if not current else current + sep + part
        if current and label_width + len(candidate) > inner:
            rows.append(current)
            current = part
        else:
            current = candidate
    if current or not rows:
        rows.append(current)
    return [(label if i == 0 else "").ljust(label_width) + row for i, row in enumerate(rows)]


def _box(title: str, rows: list[str], width: int) -> list[str]:
    inner = width - 4
    top = "+- " + title + " " + "-" * (width - len(title) - 5) + "+"
    return [top] + ["| " + _fit(row, inner) + " |" for row in rows] + ["+" + "-" * (width - 2) + "+"]


def _short_uptime(text: str) -> str:
    """"1 week, 2 days, 19 hours, 52 minutes" -> "1 w 2 d 19 h 52 min" (the header must stay short)."""
    short = re.sub(r"\bweeks?\b", "w", re.sub(r"\bdays?\b", "d", re.sub(r"\bhours?\b", "h", re.sub(r"\bminutes?\b", "min", text))))
    return short.replace(",", "") or "?"


def _duration(seconds: int) -> str:
    return f"{seconds // 3600} h {seconds % 3600 // 60} min" if seconds >= 3600 else f"{seconds // 60} min" if seconds >= 60 else f"{seconds} s"


def render_status(info: dict, width: int = WIDTH) -> list[str]:
    """Draw ``parse_status``'s result as lines of text: plain ASCII, every line exactly ``width`` characters wide."""
    flags: set[str] = info["flags"]
    mark = lambda name: " !!" if name in flags else ""   # noqa: E731
    host, pb, pr, wifi, net, sysi, moode = (info[k] for k in ("host", "playback", "program", "wifi", "net", "system", "moode"))
    problems: list[str] = info["problems"]
    inner = width - 4

    lines = [f"good_moode status - {host.get('name', '?')} ({net.get('ip') or '?'})   {host.get('time', '?')}   "
             f"up {_short_uptime(host.get('uptime', ''))}   load {host.get('load', '?')}",
             "STATUS: OK" if not problems else f"STATUS: {len(problems)} PROBLEM{'S' if len(problems) > 1 else ''}"]
    lines += [f"  !! {p}" for p in problems]
    lines = [_fit(line, width) for line in lines] + [""]

    # PLAYBACK
    volume = pb["volume"]
    bar = f"[{'#' * round(20 * volume / 100)}{'.' * (20 - round(20 * volume / 100))}]" if volume is not None else ""
    rows = [f"{'State    ' + pb['state'] + mark('state'):<34}Volume   {f'{volume}%  {bar}' if volume is not None else '?'}"]
    rows.append(f"Title    {pb['title'] or '(nothing playing)'}")
    if pb["placeholder"]:
        rows.append("         (placeholder: the station sends no real track info)")
    rows.append(f"Stream   {pb['stream'] or '(none)'}" + (f"   (queue #{pb['queue']})" if pb['queue'] else ""))
    if pb["program_stream"]:
        rows.append(f"         (the program reads titles from {pb['program_stream']})")
    lines += _box("PLAYBACK", rows, width) + [""]

    # PROGRAM
    running = f"running (pid {pr['pid']})" if pr["running"] else "NOT running"
    lines += _box("PROGRAM", [f"{'good_moode  ' + running + mark('program'):<36}"
                              f"{'Relay GPIO' + str(pr['relay_pin']) + '  ' + pr['relay']:<26}Logging  {pr['logging']}"],
                  width) + [""]

    # NETWORK (long rows continue on a second row instead of being cut)
    wifi_parts = [f"\"{wifi['ssid']}\"" if wifi["ssid"] else "(no link)",
                  f"{wifi['freq']} MHz" if wifi["freq"] else "",
                  (f"signal {wifi['signal']} dBm" + (f" (avg {wifi['signal_avg']})" if wifi["signal_avg"] is not None else "")
                   + mark("wifi.signal")) if wifi["signal"] is not None else "",
                  (f"{wifi['tx_rate']:g}/{wifi['rx_rate']:g} Mbit/s" if wifi["rx_rate"] is not None else f"{wifi['tx_rate']:g} Mbit/s")
                  if wifi["tx_rate"] is not None else "",
                  f"power save {wifi['power_save']}"]
    rows = _wrap("Wi-Fi", wifi_parts, inner)
    link_parts = []
    if wifi["connected"] is not None:
        link_parts.append(f"connected {_duration(wifi['connected'])}")
    for label, key in (("tx retries", "tx_retries"), ("tx failed", "tx_failed"), ("beacon loss", "beacon_loss")):
        if wifi[key] is not None:
            link_parts.append(f"{label} {wifi[key]}")
    if link_parts:
        link_parts[-1] += mark("wifi.link")
        rows += _wrap("Link", link_parts, inner)
    rows += _wrap("Address", [f"IP {net['ip'] or '?'}", f"router {net['gateway'] or '-'}", f"DNS server {net['dns_server'] or '?'}"], inner)
    rows.append("")
    rows.append(f"{'Ping':<14}{'Address':<28}{'Sent':>4} {'Recv':>5} {'Loss':>6} {'Min':>7} {'Avg':>7} {'Max':>7}   (ms)")
    for ping in net["pings"]:
        rows.append(f"{ping['name']:<14}{ping['target']:<28}{ping['sent']:>4} {ping['recv']:>5} {ping['loss']:>5.0f}% "
                    f"{ping['min']:>7} {ping['avg']:>7} {ping['max']:>7}{mark('ping.' + ping['name'])}")
    rows.append("")
    dns, tcp = net["dns"], net["tcp"]
    if dns:
        rows.append(f"{'DNS lookup':<14}{dns['host']:<28}" + (f"ok  {dns['address']}  {dns['ms']} ms" if dns["address"] else "FAILED") + mark("dns"))
    if tcp:
        rows.append(f"{'TCP connect':<14}{tcp['host'] + ':' + tcp['port']:<28}" + (f"ok in {tcp['ms']} ms" if tcp["ok"] else f"FAILED after {tcp['ms']} ms") + mark("tcp"))
    c = net["counters"]
    if any(v is not None for v in c.values()):
        rows.append("")
        rows.append(f"{'Interface':<14}{'':<28}{'Packets':>9} {'Errors':>7} {'Dropped':>8}")
        rows.append(f"{net['interface'] or '':<14}{'RX':<28}{c['rx_packets']:>9} {c['rx_errors']:>7} {c['rx_dropped']:>8}" + mark("net.counters"))
        rows.append(f"{'':<14}{'TX':<28}{c['tx_packets']:>9} {c['tx_errors']:>7} {c['tx_dropped']:>8}")
    lines += _box("NETWORK", rows, width) + [""]

    # SYSTEM
    temp = f"{sysi['temp']:.1f} C" if sysi["temp"] is not None else "?"
    power = "ok" if not _int_hex(sysi["throttled"]) else f"flags {sysi['throttled']}"
    lines += _box("SYSTEM", _wrap("", [f"CPU temp {temp}" + mark("system.temp"), f"Power {power}" + mark("system.power"),
                                       f"Memory {sysi['memory']} MB", f"Disk {sysi['disk']}"], inner, label_width=0, sep="     ")
                  , width) + [""]

    # MOODE / MPD: moOde's own status files, what ALSA really outputs, and MPD
    alsa = moode["alsa"]
    if not any((moode["release"], moode["mpd_version"])):
        lines += _box("MOODE / MPD", ["(no moOde information found on the Pi)"], width)
        return lines
    rows = _wrap("moOde", [moode["release"] and f"release {moode['release']}", moode["os"] and f"OS {moode['os']}",
                           moode["kernel"] and f"kernel {moode['kernel']}"], inner)
    rows += _wrap("Hardware", [moode["model"], moode["governor"] and f"CPU governor {moode['governor']}",
                               moode["worker"] and f"worker {moode['worker']}"], inner)
    if alsa:
        rows += _wrap("ALSA", [alsa["card"], alsa["format"] or "", f"{alsa['rate']} Hz" if alsa["rate"] else "",
                               f"{alsa['channels']} ch" if alsa["channels"] else ""], inner)
    else:
        rows += _wrap("ALSA", ["output closed (nothing is being played to the DAC)"], inner)
    recent = moode["mpd_uptime_min"] is not None and moode["mpd_uptime_min"] < 2
    rows += _wrap("MPD", [moode["mpd_version"], moode["mpd_uptime"] and f"up {_duration(moode['mpd_uptime_min'] * 60) if moode['mpd_uptime_min'] is not None else moode['mpd_uptime']}" + (" (restarted recently)" if recent else ""),
                          ("output " + ", ".join(moode["outputs"])) if moode["outputs"] else ""], inner)
    lines += _box("MOODE / MPD", rows, width)
    return lines
