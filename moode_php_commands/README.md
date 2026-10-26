# moode_php_commands

**These files are copies from the moOde audio player system, not code of this project.**

They are the unmodified PHP command handlers that moOde serves under its
`command/` HTTP endpoint (`http://<moode-host>/command/?cmd=...`), copied from
a running moOde installation. They are kept here only as a reference for the
API that the `good_moode` package calls, for example
`http://localhost/command/?cmd=get_volume` (see `good_moode/config.py`) and the
`set_volume` / `toggle_play_pause` commands used in
`experiment/rotary_encoder_test.py`.

- Do not edit them here; changes would not reach the Pi, and the next moOde
  update replaces them anyway.
- License and authorship: GPL-3.0-or-later, Copyright 2014 The moOde audio
  player project / Tim Curtis, and Copyright 2013 The tsunamp player ui /
  Andrea Coiutti & Simone De Gregori. See the SPDX headers in each file.
- Upstream: <https://github.com/moode-player/moode>

The files are not used by any code in this repository.
