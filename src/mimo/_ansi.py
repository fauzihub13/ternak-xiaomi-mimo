"""ANSI color codes — shared utility.

Extracted ke module terpisah supaya tidak terjadi circular import
antara `register.py` dan `bot.py` (bot butuh `build_fingerprint_payload`
dari register; register tidak boleh import balik dari bot).
"""


class C:
    RESET   = "\033[0m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"
    GRAY    = "\033[90m"
    RED     = "\033[91m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    BLUE    = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN    = "\033[96m"
