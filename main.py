#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
main.py
Version: v0.03

Entry point utama. Jalankan dari Termux:
    python main.py                     -> menu interaktif (behavior lama)
    python main.py manual <link> ...   -> mode headless (Lapisan A, BARU)

Setup lokasi settings.json, session.json, history.json, tbv_session.json,
dan folder tmp staging (semuanya di root aplikasi — lihat BASE_DIR di
bawah), lalu masuk ke menu utama CLI ATAU mode headless tergantung ada
tidaknya argumen command line.

Perubahan dari v0.02: tambah routing ke cli/dispatch.py kalau
len(sys.argv) > 1 — dicek PALING AWAL, SEBELUM banner/menu apa pun
ditampilkan, supaya output headless (terutama --json di `dm-cli status`)
tetap bersih tanpa banner nyelip di depannya.
"""

import sys
import os

from rich.panel import Panel

from core import settings, session, history, staging
from core import tbv_session, tbv_staging
from cli.theme import console
from cli import menu


# =========================================
# PATHS — semua file data & folder tmp staging ada di root aplikasi ini
# =========================================

BASE_DIR          = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE     = os.path.join(BASE_DIR, "settings.json")
SESSION_FILE      = os.path.join(BASE_DIR, "session.json")
HISTORY_FILE      = os.path.join(BASE_DIR, "history.json")
TBV_SESSION_FILE  = os.path.join(BASE_DIR, "tbv_session.json")


def _setup():
    settings.set_path(SETTINGS_FILE)
    session.set_path(SESSION_FILE)
    history.set_path(HISTORY_FILE)
    staging.set_root(BASE_DIR)          # -> BASE_DIR/tmp/

    tbv_session.set_path(TBV_SESSION_FILE)
    tbv_staging.set_root(BASE_DIR)      # -> BASE_DIR/tmp/tbv/


def _print_banner():
    console.print(Panel(
        "[header]MD1 CLI[/header]\n[muted]Anime / Video Downloader — Termux Edition[/muted]",
        border_style="border",
        expand=False,
    ))


# =========================================
# HEADLESS (Lapisan A) — dm-cli <command> ...
# =========================================

def _run_headless():
    """
    Parse argumen & dispatch ke cli/dispatch.py. Import ditunda ke sini
    (bukan di top-level file) biar mode interaktif (tanpa argumen, jalur
    paling sering dipakai) tidak ikut nanggung biaya import argparse
    parser tiap kali start — biaya kecil, tapi bersih untuk dijaga.
    """
    from cli import cli_args, dispatch

    parser = cli_args.build_parser()
    args   = parser.parse_args()

    if not getattr(args, "command", None):
        parser.print_help()
        return

    try:
        dispatch.run(args)
    except KeyboardInterrupt:
        console.print("\n\n[warning][![/warning] Dihentikan paksa (Ctrl+C).")


# =========================================
# MAIN
# =========================================

def main():
    _setup()

    if len(sys.argv) > 1:
        _run_headless()
        return

    _print_banner()
    try:
        menu.run()
    except KeyboardInterrupt:
        console.print("\n\n[warning][![/warning] Dihentikan paksa (Ctrl+C).")


if __name__ == "__main__":
    main()
