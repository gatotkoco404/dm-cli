#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/notifier.py
Version: v0.02

Pengganti push_event/SSE dari versi Flask. Pola pub-sub — sengaja
dibikin mirip arsitektur lama: dulu push_event() di app.py cuma
broadcast ke sse_clients, lalu core.js (client) yang subscribe & render.
Sekarang: push_event() di sini cuma broadcast ke handler yang subscribe
lewat on(), lalu view CLI (mis. control_view.py) yang render.

KRUSIAL: push_event(data) di bawah adalah fungsi yang dioper LANGSUNG
sebagai parameter `push_event` ke handlers/download.py, handlers/other.py,
dan handlers/post_process.py — signature-nya (terima 1 dict, return None)
harus tetap sama persis seperti versi lama, supaya file-file itu tidak
perlu diubah cara manggilnya.

Event type yang dikenal (sama seperti versi lama):
  - "log"      : {"type": "log", "message": str}
  - "progress" : {"type": "progress", "index": int, "progress": float,
                   "segment"?: int, "total_segments"?: int,
                   "speed"?: str, "eta"?: str}
  - "status"   : {"type": "status", "status": str}
  - "queue"    : {"type": "queue", "queue": list, "current": int}
                  (dikirim oleh scheduler, bukan oleh handler download)

Tipe event lain di luar daftar ini tetap diterima & diteruskan ke
subscriber (router-nya generik), cuma tidak ada state internal khusus
yang di-update untuk tipe yang tidak dikenal.
"""

import re

from cli import theme


# =========================================
# STATE INTERNAL
# =========================================

_handlers        = {}   # event_type -> list of callable(dict)
_progress_state  = {}   # index -> {progress, speed, eta, segment, total_segments}
_status_state    = "idle"


# =========================================
# SUBSCRIBE
# =========================================

def on(event_type: str, handler):
    """
    Subscribe ke 1 tipe event ('log', 'progress', 'status', 'queue', dst),
    atau '*' untuk semua tipe. handler dipanggil dengan 1 argumen: dict
    event mentah (persis seperti data yang dikirim push_event).
    """
    _handlers.setdefault(event_type, []).append(handler)


def off(event_type: str, handler):
    """Lepas subscription. Aman dipanggil walau handler sudah tidak terdaftar."""
    lst = _handlers.get(event_type)
    if lst and handler in lst:
        lst.remove(handler)


# =========================================
# ENTRY POINT UTAMA — INI YANG DIOPER SEBAGAI push_event KE HANDLER LAMA
# =========================================

def push_event(data: dict):
    """
    Signature: (dict) -> None. Jangan diubah — dipanggil langsung oleh
    handlers/download.py, handlers/other.py, handlers/post_process.py
    persis seperti dulu mereka manggil push_event() versi Flask.
    """
    event_type = data.get("type", "")
    _update_state(data, event_type)
    _fire(event_type, data)
    _fire("*", data)


def _fire(event_type, data):
    for handler in _handlers.get(event_type, []):
        try:
            handler(data)
        except Exception:
            # 1 subscriber error tidak boleh menjatuhkan seluruh download
            pass


def _update_state(data, event_type):
    global _status_state

    if event_type == "progress":
        idx = data.get("index")
        if idx is not None:
            entry = _progress_state.setdefault(idx, {})
            for key in ("progress", "speed", "eta", "segment", "total_segments"):
                if key in data:
                    entry[key] = data[key]

    elif event_type == "status":
        _status_state = data.get("status", _status_state)


# =========================================
# BACA STATE — dipakai dashboard/view buat render
# =========================================

def progress_snapshot() -> dict:
    """Return copy state progress semua index saat ini. Aman diubah caller, tidak mutasi asli."""
    return {idx: dict(v) for idx, v in _progress_state.items()}


def progress_for(index: int) -> dict:
    """Ambil progress 1 index saja. Return dict kosong kalau belum ada data."""
    return dict(_progress_state.get(index, {}))


def current_status() -> str:
    return _status_state


def reset():
    """
    Bersihkan semua state internal (progress + status). Dipanggil
    scheduler tiap kali mulai sesi download baru, biar sisa data dari
    run sebelumnya tidak nyangkut.
    """
    global _status_state
    _progress_state.clear()
    _status_state = "idle"


# =========================================
# HANDLER DEFAULT — "log" (auto-print ke terminal)
# =========================================
# Heuristik sama persis seperti logic lama di core.js:
#   ada kata "gagal"/"error"  -> merah
#   ada kata "ok"/"selesai"    -> hijau
#   selain itu                   -> netral

_ERR_PATTERN = re.compile(r"gagal|error", re.IGNORECASE)
_OK_PATTERN  = re.compile(r"\bok\b|selesai", re.IGNORECASE)


def _default_log_handler(data: dict):
    msg = data.get("message", "")
    if not msg:
        return
    if _ERR_PATTERN.search(msg):
        prefix = theme.PREFIX_ERR
    elif _OK_PATTERN.search(msg):
        prefix = theme.PREFIX_OK
    else:
        prefix = theme.PREFIX_INFO
    theme.console.print(f"{prefix} {theme.safe(msg)}")
# =========================================
# Cuma untuk status yang butuh pengumuman satu-kali ke user. Status
# transisi rutin (paused/downloading dari aksi p/r/c user sendiri)
# sengaja TIDAK di-print di sini — itu sudah kelihatan visual dari
# dashboard, print tambahan cuma bikin berisik.

_STATUS_MESSAGES = {
    "no_connection": (theme.PREFIX_WARN, "Koneksi terputus, download dijeda otomatis."),
    "done":          (theme.PREFIX_OK,   "Semua download selesai! \U0001F389"),
    "cancelled":     (theme.PREFIX_WARN, "Download dibatalkan."),
}


def _default_status_handler(data: dict):
    status = data.get("status", "")
    info = _STATUS_MESSAGES.get(status)
    if not info:
        return
    prefix, msg = info
    theme.console.print(f"{prefix} {msg}")


# =========================================
# REGISTRASI HANDLER DEFAULT
# =========================================

on("log", _default_log_handler)
on("status", _default_status_handler)
