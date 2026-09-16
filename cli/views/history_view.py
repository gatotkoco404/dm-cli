#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/history_view.py
Version: v0.02

Menu History — tampilkan 10 entri riwayat download terakhir (bag. 4.6
desain). Port dari route GET /history lama (yang dulu cuma return JSON
mentah ke frontend), sekarang langsung dirender jadi tabel rich di
terminal.
"""

from rich.table import Table

from core import history
from cli.theme import console, safe


def show():
    """Entry point menu History."""
    entries = history.load_recent(10)

    console.print("\n[header]Riwayat Download (10 terakhir)[/header]")

    if not entries:
        console.print("[muted][*][/muted] Belum ada riwayat download.")
        return

    table = Table(border_style="border")
    table.add_column("No",    justify="right", style="muted")
    table.add_column("Anime")
    table.add_column("EP",    justify="center")
    table.add_column("File")
    table.add_column("Waktu", style="muted")

    for i, entry in enumerate(entries, start=1):
        table.add_row(
            str(i),
            safe(entry.get("anime", "-")),
            entry.get("ep", "-"),
            safe(entry.get("file", "-")),
            entry.get("time", "-"),
        )

    console.print(table)
