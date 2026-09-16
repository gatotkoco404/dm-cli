#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/other_audio_view.py
Version: v0.02

Menu Download Other > Audio — submenu Single & Multi (bag. 4.3 & 4.4
desain).

Single : link -> fetch metadata (spinner) -> tabel kualitas MP3 (320k/
         192k/128k dengan estimasi ukuran) -> pilih -> download.
Multi  : baca file .txt dari input_batch_path (1 link per baris) ->
         quality otomatis dari elemen tengah daftar tingkat MP3 yang
         tersedia (generik, bukan hardcode) -> download semua (tanpa
         sub, tanpa naming manual).
"""

import os

from rich.table import Table

from core.state import reset_state
from core import settings
from core.config_parser import parse_link_list
from core.metadata import fetch_video_info
from core import scheduler
from handlers.other import MP3_QUALITY_MAP
from cli.theme import console, safe
from cli.views import control_view


def show():
    """Entry point menu Audio. Tanya Single/Multi dulu."""
    console.print("\n[header]Download Other > Audio[/header]")
    console.print("1. Single")
    console.print("2. Multi")

    choice = console.input("\nPilih nomor: ").strip()
    if choice == "1":
        _show_single()
    elif choice == "2":
        _show_multi()
    else:
        console.print("[error][x][/error] Pilihan tidak valid.")


# =========================================
# SINGLE
# =========================================

def _show_single():
    link = console.input("\nURL: ").strip()
    if not link:
        console.print("[error][x][/error] URL tidak boleh kosong, batal.")
        return

    with console.status("[muted]Mengambil metadata...[/muted]"):
        info = fetch_video_info(link)

    if not info.get("ok"):
        console.print(f"[error][x][/error] Gagal ambil metadata: {safe(info.get('msg', '?'))}")
        return

    mp3_sizes = info.get("mp3_sizes", {})
    tiers     = list(MP3_QUALITY_MAP.keys())   # ["320k", "192k", "128k"]

    console.print(f"\n[success][+][/success] \"{safe(info.get('title', link))}\"")
    table = Table(border_style="border")
    table.add_column("No", justify="right", style="muted")
    table.add_column("Kualitas")
    table.add_column("Estimasi ukuran", style="muted")
    for i, tier in enumerate(tiers, start=1):
        table.add_row(str(i), tier, mp3_sizes.get(tier, "?"))
    console.print(table)

    choice = console.input("\nPilih kualitas: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(tiers)):
        console.print("[error][x][/error] Pilihan tidak valid, batal.")
        return

    quality = tiers[int(choice) - 1]

    ep = {
        "ep":             "",
        "link":           link,
        "sub":            "",
        "mp3_only":       True,
        "quality":        quality,
        "sub_auto":       False,
        "playlist":       False,
        "status":         "waiting",
        "progress":       0,
        "segment":        0,
        "total_segments": 0,
        "speed":          "",
        "eta":            "",
    }

    _start_queue([ep])


# =========================================
# MULTI
# =========================================

def _show_multi():
    cfg       = settings.load_settings()
    batch_dir = cfg.get("input_batch_path", "")

    if not batch_dir or not os.path.isdir(batch_dir):
        console.print(f"[error][x][/error] Folder input_batch tidak ditemukan: {safe(batch_dir)}")
        console.print("[muted][*][/muted] Cek/ubah dulu di menu Pengaturan.")
        return

    # hanya .txt yang ditampilkan (filter ekstensi, bukan deteksi isi —
    # sesuai keputusan: user sudah masuk konteks menu ini duluan)
    files = sorted(
        f for f in os.listdir(batch_dir)
        if f.lower().endswith(".txt") and os.path.isfile(os.path.join(batch_dir, f))
    )

    if not files:
        console.print(f"[warning][![/warning] Tidak ada file .txt di {safe(batch_dir)}")
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

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        links = parse_link_list(content)
    except Exception as e:
        console.print(f"[error][x][/error] Gagal baca file: {safe(e)}")
        return

    if not links:
        console.print("[warning][![/warning] Tidak ada link valid terbaca dari file ini.")
        return

    # elemen tengah dari daftar quality yang tersedia — generik, bukan
    # hardcode "192k" (bag. 4.4 desain)
    tiers   = list(MP3_QUALITY_MAP.keys())
    quality = tiers[len(tiers) // 2]

    console.print(f"[success][+][/success] {len(links)} link dimuat dari \"{safe(selected_file)}\".")
    console.print(f"[muted][*][/muted] Kualitas otomatis: {quality}")

    confirm = console.input("Lanjut download? (y/n): ").strip().lower()
    if confirm != "y":
        console.print("[muted][*][/muted] Dibatalkan.")
        return

    queue = []
    for link in links:
        queue.append({
            "ep":             "",
            "link":           link,
            "sub":            "",
            "mp3_only":       True,
            "quality":        quality,
            "sub_auto":       False,
            "playlist":       False,
            "status":         "waiting",
            "progress":       0,
            "segment":        0,
            "total_segments": 0,
            "speed":          "",
            "eta":            "",
        })

    _start_queue(queue, config_file=full_path)


# =========================================
# SHARED — mulai worker
# =========================================

def _start_queue(queue: list, config_file: str = ""):
    cfg = settings.load_settings()
    reset_state(overrides={
        "anime":            "Downloads",
        "download_path":    cfg["download_path"],
        "input_batch_path": cfg["input_batch_path"],
        "config_file":      config_file,
        "format":           cfg["format"],
        "timeout":          cfg["timeout"],
        "conn":             cfg["conn"],
        "parallel":         cfg["parallel"] if len(queue) > 1 else 1,
        "rate_limit":       cfg["rate_limit"],
        "mode":             "other",
        "queue":            queue,
        "current_index":    0,
        "status":           "downloading",
    })
    scheduler.start_worker()
    control_view.show()
