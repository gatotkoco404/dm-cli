#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/other_video_view.py
Version: v0.02

Menu Download Other > Video — single saja (bag. 4.5 desain). Link ->
fetch metadata (spinner) -> tabel kualitas video (dengan estimasi
ukuran) -> pilih -> download.
"""

from rich.table import Table

from core.state import reset_state
from core import settings
from core.metadata import fetch_video_info
from core import scheduler
from cli.theme import console, safe
from cli.views import control_view


def show():
    """Entry point menu Video (single saja)."""
    console.print("\n[header]Download Other > Video[/header]")

    link = console.input("URL: ").strip()
    if not link:
        console.print("[error][x][/error] URL tidak boleh kosong, batal.")
        return

    with console.status("[muted]Mengambil metadata...[/muted]"):
        info = fetch_video_info(link)

    if not info.get("ok"):
        console.print(f"[error][x][/error] Gagal ambil metadata: {safe(info.get('msg', '?'))}")
        return

    qualities = info.get("qualities", [])
    if not qualities:
        console.print("[warning][![/warning] Tidak ada info kualitas, pakai 'best'.")
        qualities = [{"val": "best", "label": "Best", "size": "?"}]

    console.print(f"\n[success][+][/success] \"{safe(info.get('title', link))}\"")
    table = Table(border_style="border")
    table.add_column("No", justify="right", style="muted")
    table.add_column("Kualitas")
    table.add_column("Estimasi ukuran", style="muted")
    for i, q in enumerate(qualities, start=1):
        table.add_row(str(i), q.get("label", q.get("val", "?")), q.get("size", "?"))
    console.print(table)

    choice = console.input("\nPilih kualitas: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(qualities)):
        console.print("[error][x][/error] Pilihan tidak valid, batal.")
        return

    quality = qualities[int(choice) - 1]["val"]

    ep = {
        "ep":             "",
        "link":           link,
        "sub":            "",
        "mp3_only":       False,
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

    cfg = settings.load_settings()
    reset_state(overrides={
        "anime":            "Downloads",
        "download_path":    cfg["download_path"],
        "input_batch_path": cfg["input_batch_path"],
        "config_file":      "",
        "format":           cfg["format"],
        "timeout":          cfg["timeout"],
        "conn":             cfg["conn"],
        "parallel":         1,
        "rate_limit":       cfg["rate_limit"],
        "mode":             "other",
        "queue":            [ep],
        "current_index":    0,
        "status":           "downloading",
    })

    scheduler.start_worker()
    control_view.show()
