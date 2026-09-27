"""Banner logo cố định trên đầu dashboard (không trôi khi refresh)."""
from __future__ import annotations

CYAN = "\033[96m"
WHITE = "\033[97m"
DIM = "\033[2m"
RESET = "\033[0m"

LOGO = (
    f"{CYAN}__   __           _         _                 _     {RESET}\n"
    f"{CYAN}\\ \\ / /   _  ___ | |_ _ __ | |__   __ _ _ __ | |__  {RESET}\n"
    f"{CYAN} \\ V / | | |/ _ \\| __| '_ \\| '_ \\ / _` | '_ \\| '_ \\ {RESET}\n"
    f"{CYAN}  | |  | |_| | (_) | |_| | | | | | | (_| | | | | | | |{RESET}\n"
    f"{CYAN}  |_|   \\__,_|\\___/ \\__|_| |_|_| |_|\\__,_|_| |_|_| |_|{RESET}\n"
)

SUB = f"{DIM}proxy-pool-runner | crawl -> check -> pool -> workers{RESET}"
