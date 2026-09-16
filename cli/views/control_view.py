#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/control_view.py
Version: v0.03

Dashboard kontrol download — render progress semua slot secara live,
plus baca hotkey p/r/c TANPA perlu Enter (bag. 5 desain). Ini file
yang paling berisiko secara teknis di seluruh proyek (sudah di-flag
dari awal diskusi desain) karena menggabungkan 2 hal yang sama-sama
"pegang" terminal:
  1. rich.Live — redraw tabel dashboard terus-menerus
  2. Baca keypress tunggal tanpa Enter (perlu terminal di cbreak mode)

CARA KERJA:
  - termios/tty (POSIX only — jalan di Termux/Linux, TIDAK di Windows)
    dipakai buat set stdin ke cbreak mode: baca 1 karakter langsung,
    tanpa nunggu Enter, tanpa echo baris penuh.
  - select.select() dipakai buat polling non-blocking dengan timeout
    pendek (REFRESH_INTERVAL) — ini SEKALIGUS jadi detak redraw
    dashboard (tiap timeout habis tanpa keypress, dashboard di-update)
    DAN jadi mekanisme baca hotkey, jadi tidak perlu 2 loop terpisah.
  - Waktu user tekan p/r/c di mode multi-slot dan perlu input nomor
    target: live.stop() + balik ke mode terminal normal dulu (biar
    console.input() bisa jalan dengan echo/backspace normal), habis
    itu balik lagi ke cbreak mode + live.start().

KONTROL PAUSE/RESUME/CANCEL: TIDAK ADA logic baru di sini — semua
tinggal manggil fungsi yang sudah ada di core/scheduler.py:
  - 1 nomor spesifik -> hold_episode() / resume_episode() / cancel_episode()
  - "a" (semua) atau mode single-slot -> pause_all() / resume_all() / cancel_all()
"""

import sys
import time
import select
import termios
import tty

from rich.live import Live
from rich.table import Table

from core.state import get_state
from core import scheduler
from cli import notifier
from cli.theme import console, style_for_status, safe


REFRESH_INTERVAL = 0.3   # detik — detak redraw dashboard sekaligus timeout polling keypress

_STATUS_LABEL = {
    "waiting":     "menunggu",
    "held":        "ditahan",
    "failed":      "gagal",
    "done":        "selesai",
}


# =========================================
# ENTRY POINT
# =========================================

def show():
    """
    Tampilkan dashboard & tangani hotkey selama worker (termasuk
    post-process di akhir) masih jalan. Setelah selesai, tawarkan
    retry kalau ada episode gagal.
    """
    state = get_state()
    if not state["queue"]:
        return

    is_single = len([e for e in state["queue"] if e.get("status") != "removed"]) <= 1

    _enable_raw_mode()
    try:
        with Live(_render_dashboard(), console=console, refresh_per_second=4) as live:
            while scheduler.is_worker_alive():
                live.update(_render_dashboard())
                key = _read_key(REFRESH_INTERVAL)
                if not key:
                    continue
                key = key.lower()
                if key not in ("p", "r", "c"):
                    continue

                if is_single:
                    _handle_global_action(key)
                else:
                    _handle_targeted_action(key, live)

            live.update(_render_dashboard())
    finally:
        _disable_raw_mode()

    _offer_retry_if_needed()


# =========================================
# AKSI — GLOBAL (mode single-slot, tanpa prompt nomor)
# =========================================

def _handle_global_action(key: str):
    if key == "p":
        scheduler.pause_all()
    elif key == "r":
        scheduler.resume_all()
    elif key == "c":
        scheduler.cancel_all()


# =========================================
# AKSI — TARGET NOMOR (mode multi-slot)
# =========================================

def _handle_targeted_action(key: str, live: Live):
    action_label = {"p": "Pause", "r": "Resume", "c": "Cancel"}[key]
    raw = _prompt_target(live, action_label)

    if not raw:
        return   # Enter kosong = batal

    if raw == "a":
        if key == "p":
            scheduler.pause_all()
        elif key == "r":
            scheduler.resume_all()
        else:
            scheduler.cancel_all()
        return

    if not raw.isdigit():
        console.print("[error][x][/error] Input tidak valid.")
        return

    idx = int(raw) - 1
    if key == "p":
        ok = scheduler.hold_episode(idx)
    elif key == "r":
        ok = scheduler.resume_episode(idx)
    else:
        ok = scheduler.cancel_episode(idx)

    if not ok:
        console.print(f"[error][x][/error] Nomor {raw} tidak valid / aksi tidak bisa diterapkan.")


def _prompt_target(live: Live, action_label: str) -> str:
    """
    Minta input nomor target. Keluar sementara dari raw mode + pause
    Live biar console.input() bisa jalan normal (echo, backspace, dst),
    lalu balik lagi ke raw mode + lanjutkan Live setelah selesai.
    """
    live.stop()
    _disable_raw_mode()
    try:
        raw = console.input(
            f"\n{action_label} nomor berapa? (a=semua, Enter=batal): "
        ).strip().lower()
    finally:
        _enable_raw_mode()
        live.start()
    return raw


# =========================================
# RENDER DASHBOARD
# =========================================

def _render_dashboard() -> Table:
    state = get_state()
    queue = state["queue"]

    table = Table(border_style="border")
    table.add_column("No",     justify="right", style="muted", width=3)
    table.add_column("Nama")
    table.add_column("Status")

    for i, ep in enumerate(queue):
        status = ep.get("status", "waiting")
        if status == "removed":
            continue

        label = ep.get("ep") or (ep.get("link", "")[:40] or "-")
        style = style_for_status(status)

        if status == "downloading":
            prog = notifier.progress_for(i)
            pct  = prog.get("progress", ep.get("progress", 0)) or 0
            eta  = prog.get("eta", ep.get("eta", ""))
            seg  = prog.get("segment", ep.get("segment", 0))
            tot  = prog.get("total_segments", ep.get("total_segments", 0))

            detail = f"{pct:.0f}%"
            if tot:
                detail += f" {seg}/{tot}"
            if eta:
                detail += f" eta {eta}"
        else:
            detail = _STATUS_LABEL.get(status, status)

        table.add_row(str(i + 1), safe(label), f"[{style}]{detail}[/{style}]")

    table.caption = "[muted][p][/muted]ause  [muted][r][/muted]esume  [muted][c][/muted]ancel"
    return table


# =========================================
# RETRY GAGAL
# =========================================

def _offer_retry_if_needed():
    if not scheduler.has_failed_episodes():
        return
    answer = console.input("\nAda episode gagal, retry? (y/n): ").strip().lower()
    if answer == "y":
        scheduler.retry_failed()
        show()   # dashboard lagi buat sisa retry-nya


# =========================================
# RAW MODE (cbreak) — baca keypress tunggal tanpa Enter
# =========================================

_orig_termios = None


def _enable_raw_mode():
    """Aktifkan cbreak mode di stdin. No-op kalau bukan TTY (mis. testing non-interaktif)."""
    global _orig_termios
    if not sys.stdin.isatty():
        return
    try:
        _orig_termios = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
    except Exception:
        _orig_termios = None


def _disable_raw_mode():
    """Balikin terminal ke mode normal (canonical) — dipanggil sebelum console.input()."""
    global _orig_termios
    if _orig_termios is not None and sys.stdin.isatty():
        try:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _orig_termios)
        except Exception:
            pass


def _read_key(timeout: float):
    """Baca 1 karakter dari stdin kalau ada, non-blocking. Return None kalau tidak ada dalam timeout detik."""
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.read(1)
    except Exception:
        pass
    return None
  
