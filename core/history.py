#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/history.py
Version: v0.01

Simpan/baca/hapus riwayat download (history.json).
Entry terbaru selalu di posisi paling atas (index 0).

Port dari modules/helpers.py (fungsi save_history/load_history/delete_history),
dipisah jadi modul sendiri sesuai struktur folder baru.
"""

import os
import json

_HISTORY_FILE = ""
_MAX_ENTRIES  = 100


# =========================================
# PATH
# =========================================

def set_path(path: str):
    """Set lokasi file history.json. Dipanggil sekali dari main.py saat startup."""
    global _HISTORY_FILE
    _HISTORY_FILE = path


# =========================================
# SAVE / LOAD / DELETE
# =========================================

def save_history(entry: dict):
    """
    Tambahkan satu entry ke history.json.
    Maksimal _MAX_ENTRIES entry, yang terbaru di atas.
    """
    if not _HISTORY_FILE:
        return
    history = load_history()
    history.insert(0, entry)
    history = history[:_MAX_ENTRIES]
    try:
        with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def load_history() -> list:
    """Load seluruh history dari file. Return list (kosong jika tidak ada/gagal)."""
    if not _HISTORY_FILE or not os.path.exists(_HISTORY_FILE):
        return []
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def delete_history():
    """Hapus file history."""
    if _HISTORY_FILE and os.path.exists(_HISTORY_FILE):
        try:
            os.remove(_HISTORY_FILE)
        except Exception:
            pass


# =========================================
# HELPER — dipakai cli/views/history_view.py
# =========================================

def load_recent(n: int = 10) -> list:
    """Ambil n entry terbaru saja (default 10, sesuai desain menu History)."""
    return load_history()[:n]
    