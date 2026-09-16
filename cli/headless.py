#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/headless.py
Version: v0.01

Progress reporter buat mode headless (dm-cli dipanggil via command line
langsung, bukan lewat menu interaktif). BEDA dari cli/views/control_view.py
& cli/views/tbv_control_view.py:
  - TIDAK pakai rich.Live/raw-mode keypress — TIDAK ada hotkey
    pause/resume/cancel di headless (keputusan desain: cancel cukup
    Ctrl+C/SIGINT; pause/resume lintas-proses butuh IPC yang belum
    dibutuhkan sekarang — lihat diskusi Lapisan A).
  - TIDAK PERNAH nge-prompt input apa pun (termasuk "retry episode
    gagal?") — kalau dipanggil dari script bridge tanpa stdin nempel,
    prompt bakal nge-hang selamanya nunggu input yang gak akan pernah
    datang. Headless SELALU report-lalu-keluar, retry (kalau perlu)
    jadi tanggung jawab pemanggil ulang command yang sama.

DETEKSI TTY: kalau stdout BUKAN terminal interaktif (mis. dipanggil
lewat subprocess dari script bridge), progress TIDAK di-print — caller
diarahkan baca session.json/tbv_session.json (sudah di-snapshot
scheduler tiap beberapa detik) buat progress, BUKAN parsing stdout.
Ini menghindari kerja dobel & output yang gak berguna kalau stdout
memang gak dibaca manusia.
"""

import sys
import time


def wait_headless(is_worker_alive_fn, progress_snapshot_fn, queue_getter_fn, poll_interval: float = 1.0):
    """
    Tunggu worker sampai selesai (blocking). Kalau stdout adalah tty,
    tampilkan progress ringkas per baris (update in-place pakai \\r).
    Kalau bukan tty, tunggu diam-diam saja.
    """
    interactive = sys.stdout.isatty()

    while is_worker_alive_fn():
        if interactive:
            _print_line(progress_snapshot_fn(), queue_getter_fn())
        time.sleep(poll_interval)

    if interactive:
        sys.stdout.write("\n")
        sys.stdout.flush()


def _print_line(snapshot: dict, queue: list):
    active = [
        (i, ep) for i, ep in enumerate(queue)
        if ep.get("status") == "downloading"
    ]
    if not active:
        return

    parts = []
    for i, ep in active:
        prog  = snapshot.get(i, {})
        pct   = prog.get("progress", ep.get("progress", 0)) or 0
        eta   = prog.get("eta", ep.get("eta", ""))
        label = ep.get("ep") or ep.get("episode") or f"#{i + 1}"

        chunk = f"{label} {pct:.0f}%"
        if eta:
            chunk += f" eta {eta}"
        parts.append(chunk)

    line = " | ".join(parts)
    # \r + pad spasi biar sisa baris sebelumnya (kalau lebih panjang) ketutup
    sys.stdout.write("\r" + line.ljust(100)[:100])
    sys.stdout.flush()
