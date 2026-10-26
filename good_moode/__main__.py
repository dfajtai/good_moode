"""``python -m good_moode`` starts the OLED / button / relay program."""

from .state_machine import main

if __name__ == "__main__":
    raise SystemExit(main())
