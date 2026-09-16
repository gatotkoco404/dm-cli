#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/manual_view.py
Version: v0.01

Menu Download m3u8/mp4 > Manual — 1 link, alur: link -> sub (opsional)
-> naming file -> konfirmasi y/n -> mulai (bag. 4.1 desain).

Catatan desain: Manual di CLI cuma 1 episode per sesi (beda dari versi
web lama yang bisa nambah banyak episode ke antrean sebelum mulai).
handlers/download.py menghitung nama file dari state["anime"] + "-" +
ep["ep"] (format ini sudah final, tidak diubah) — karena flow Manual
CLI cuma minta 1 input naming (bukan anime+ep terpisah), input itu
dipetakan ke state["anime"], dan ep["ep"] dikunci ke "01" (episode
tunggal) supaya nama file tetap rapi, mis. "JudulKu-01.mkv".
"""

from core.state import reset_state
from core import settings
from core.config_parser import sanitize
from core import scheduler
from cli.theme import console
from cli.views import control_view


def show():
    """Entry point menu Manual."""
    console.print("\n[header]Download Manual (m3u8/mp4)[/header]")

    link = console.input("Link m3u8/mp4: ").strip()
    if not link:
        console.print("[error][x][/error] Link tidak boleh kosong, batal.")
        return

    sub    = console.input("Link subtitle (opsional, Enter untuk skip): ").strip()
    naming = console.input("Nama file: ").strip()
    if not naming:
        console.print("[error][x][/error] Nama file tidak boleh kosong, batal.")
        return

    confirm = console.input("Lanjut download? (y/n): ").strip().lower()
    if confirm != "y":
        console.print("[muted][*][/muted] Dibatalkan.")
        return

    ep = {
        "ep":             "01",
        "link":           link,
        "sub":            sub,
        "duration":       "off",
        "status":         "waiting",
        "progress":       0,
        "segment":        0,
        "total_segments": 0,
        "speed":          "",
        "eta":            "",
    }

    cfg = settings.load_settings()
    reset_state(overrides={
        "anime":            sanitize(naming),
        "download_path":    cfg["download_path"],
        "input_batch_path": cfg["input_batch_path"],
        "format":           cfg["format"],
        "timeout":          cfg["timeout"],
        "conn":             cfg["conn"],
        "parallel":         1,   # Manual selalu 1 episode -> paralel gak relevan
        "rate_limit":       cfg["rate_limit"],
        "mode":             "manual",
        "queue":            [ep],
        "current_index":    0,
        "status":           "downloading",
    })

    scheduler.start_worker()
    control_view.show()
