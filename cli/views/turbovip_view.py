#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/turbovip_view.py
Version: v0.01

Menu Download TurboVIP (TBV). Pola SAMA seperti cli/views/multi_view.py
(list file batch dari input_batch_path -> pilih -> parse -> konfirmasi
-> start), bedanya:
  - Cuma terima file .json (format TBV selalu json — title/episode_count/
    episodes:[{episode, m3u8}]), beda dari mode Multi yang terima .txt/.json.
  - Parser/state/scheduler yang dipakai KHUSUS TBV (core/tbv_parser,
    core/tbv_state, core/tbv_scheduler) — sama sekali tidak menyentuh
    core/state.py atau core/scheduler.py milik pipeline lama, jadi
    aman dipakai walau ada sesi manual/multi/other yang belum selesai.
"""

import os

from core.tbv_state import reset_state
from core import settings, tbv_parser
from core import tbv_scheduler
from cli.theme import console, safe
from cli.views import tbv_control_view


def show():
    """Entry point menu Download TurboVIP."""
    console.print("\n[header]Download TurboVIP[/header]")

    cfg       = settings.load_settings()
    batch_dir = cfg.get("input_batch_path", "")

    if not batch_dir or not os.path.isdir(batch_dir):
        console.print(f"[error][x][/error] Folder input_batch tidak ditemukan: {safe(batch_dir)}")
        console.print("[muted][*][/muted] Cek/ubah dulu di menu Pengaturan.")
        return

    files = sorted(
        f for f in os.listdir(batch_dir)
        if f.lower().endswith(".json") and os.path.isfile(os.path.join(batch_dir, f))
    )

    if not files:
        console.print(f"[warning][![/warning] Tidak ada file .json di {safe(batch_dir)}")
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

    data, error = tbv_parser.load_batch_json(full_path)
    if error:
        console.print(f"[error][x][/error] {safe(error)}")
        return

    queue = tbv_parser.build_queue(data)
    if not queue:
        console.print("[warning][![/warning] Tidak ada episode valid (link m3u8 kosong?) di file ini.")
        return

    title = data.get("title", "") or "(tanpa judul)"
    console.print(
        f"[success][+][/success] \"{safe(title)}\" — {len(queue)} episode "
        f"dimuat dari \"{safe(selected_file)}\"."
    )

    confirm = console.input("Lanjut download? (y/n): ").strip().lower()
    if confirm != "y":
        console.print("[muted][*][/muted] Dibatalkan.")
        return

    reset_state(overrides={
        "title":            data.get("title", ""),
        "batch_file":       full_path,
        "download_path":    cfg["download_path"],
        "input_batch_path": batch_dir,
        "format":           cfg["format"],
        "timeout":          cfg["timeout"],
        "rate_limit":       cfg["rate_limit"],
        "parallel":         cfg["parallel"] if len(queue) > 1 else 1,
        "queue":            queue,
        "current_index":    0,
        "status":           "downloading",
    })

    tbv_scheduler.start_worker()
    tbv_control_view.show()
