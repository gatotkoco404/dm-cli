#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/multi_view.py
Version: v0.03

Menu Download m3u8/mp4 > Multi — baca file config (.txt/.json) dari
folder input_batch_path (settings), parse jadi antrean banyak episode,
lalu mulai download paralel (bag. 4.2 desain).

Perubahan dari v0.02: tambah mode Referer — kalau file .json batch
punya field "referer" terisi, field itu disalin ke tiap episode di
queue (dibaca handlers/download.py, ditambahkan sebagai --referer ke
command yt-dlp). File .txt / json tanpa field referer tidak terpengaruh
sama sekali (behavior lama).
"""

import os

from core.state import reset_state
from core import settings
from core.config_parser import sanitize, parse_config_content, extract_referer
from core import scheduler
from cli.theme import console, safe
from cli.views import control_view


_VALID_EXT = (".txt", ".json")


def show():
    """Entry point menu Multi."""
    console.print("\n[header]Download Multi (m3u8/mp4)[/header]")

    cfg       = settings.load_settings()
    batch_dir = cfg.get("input_batch_path", "")

    if not batch_dir or not os.path.isdir(batch_dir):
        console.print(f"[error][x][/error] Folder input_batch tidak ditemukan: {safe(batch_dir)}")
        console.print("[muted][*][/muted] Cek/ubah dulu di menu Pengaturan.")
        return

    files = sorted(
        f for f in os.listdir(batch_dir)
        if f.lower().endswith(_VALID_EXT) and os.path.isfile(os.path.join(batch_dir, f))
    )

    if not files:
        console.print(f"[warning][![/warning] Tidak ada file .txt/.json di {safe(batch_dir)}")
        return

    console.print(f"\nFile ditemukan di {safe(batch_dir)}:")
    for i, fname in enumerate(files, start=1):
        console.print(f"[muted]{i}.[/muted] {safe(fname)}")

    choice = console.input("\nPilih nomor file: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(files)):
        console.print("[error][x][/error] Pilihan tidak valid, batal.")
        return

    selected_file = files[int(choice) - 1]
    full_path     = os.path.join(batch_dir, selected_file)
    filetype      = "json" if selected_file.lower().endswith(".json") else "txt"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        anime_title, episodes = parse_config_content(content, filetype)
        referer               = extract_referer(content, filetype)
    except Exception as e:
        console.print(f"[error][x][/error] Gagal parse file: {safe(e)}")
        return

    if not episodes:
        console.print("[warning][![/warning] Tidak ada episode terbaca dari file ini.")
        return

    # mode Referer — cuma aktif kalau field "referer" di json terisi,
    # kalau tidak/null, episode di-download seperti biasa (tanpa flag
    # --referer di command yt-dlp, lihat handlers/download.py).
    if referer:
        for ep in episodes:
            ep["referer"] = referer
        console.print(f"[muted][*][/muted] Mode Referer aktif: {safe(referer)}")

    if not anime_title.strip():
        anime_title = console.input("Nama anime/drama (file tidak punya Title): ").strip()
        if not anime_title:
            console.print("[error][x][/error] Nama anime tidak boleh kosong, batal.")
            return

    console.print(f"[success][+][/success] {len(episodes)} episode dimuat dari \"{safe(selected_file)}\".")

    confirm = console.input("Lanjut download? (y/n): ").strip().lower()
    if confirm != "y":
        console.print("[muted][*][/muted] Dibatalkan.")
        return

    reset_state(overrides={
        "anime":            sanitize(anime_title),
        "download_path":    cfg["download_path"],
        "input_batch_path": batch_dir,
        "config_file":      full_path,
        "format":           cfg["format"],
        "timeout":          cfg["timeout"],
        "conn":             cfg["conn"],
        "parallel":         cfg["parallel"],
        "rate_limit":       cfg["rate_limit"],
        "mode":             "multi",
        "queue":            episodes,
        "current_index":    0,
        "status":           "downloading",
    })

    scheduler.start_worker()
    control_view.show()
