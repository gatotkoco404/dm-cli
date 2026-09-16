#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/tbv_state.py
Version: v0.01

State global sesi download TurboVIP (TBV) + lock-nya. Mirror
core/state.py, SENGAJA dipisah (bukan reuse dict yang sama) karena:
  - Field yang relevan beda (tidak ada "conn"/"mode" — TBV cuma 1 mode,
    concurrency segment diatur konstanta di handlers/download_tbv.py,
    bukan settings user).
  - Biar pipeline lama (manual/multi/other) & TBV benar-benar independen
    — kalau salah satu lagi jalan, tidak mungkin salah baca/tulis state
    milik yang lain lewat lock/dict yang salah.

Field tambahan di sini yang tidak ada di core/state.py:
  - "title"           : judul dari file .json batch (dipakai penamaan
                         file lewat core/tbv_parser.build_filename()).
  - "batch_file"       : path file .json yang dipilih user dari folder
                          settings["input_batch_path"] — analog
                          "config_file" di state.py, dipakai buat
                          ditampilkan ulang di UI (bukan dibaca ulang;
                          isi batch sudah masuk ke "queue" saat dimuat).

Field yang SENGAJA tidak ada dibanding core/state.py:
  - "conn"    : tidak dipakai, concurrency segment TBV pakai konstanta
                SEGMENT_PARALLEL/SEGMENT_CONN di handlers/download_tbv.py.
  - "mode"    : tidak perlu, TBV cuma 1 alur (tidak ada varian manual/
                multi/other seperti pipeline lama).
"""

import threading


_state_lock = threading.Lock()

_DEFAULT_STATE = {
    "title":            "",
    "batch_file":       "",
    "download_path":    "",
    "input_batch_path": "",
    "format":           "mkv",
    "timeout":          "30",
    "rate_limit":       "",
    "parallel":         1,

    "queue":            [],
    "current_index":    -1,

    "status":           "idle",     # idle | downloading | paused | done
    "paused":           False,
    "cancelled":        False,
}

_state = dict(_DEFAULT_STATE)


def get_state() -> dict:
    """
    Return referensi LANGSUNG ke state dict TBV (bukan copy) — sengaja,
    sama seperti core/state.py. Selalu akses/ubah isinya di dalam blok
    `with get_lock():`.
    """
    return _state


def get_lock() -> threading.Lock:
    """Lock khusus TBV — TIDAK sama dengan lock di core/state.py."""
    return _state_lock


def reset_state(overrides: dict = None):
    """
    Reset state TBV ke default, opsional dengan override awal (mis. isi
    dari settings.json + hasil load_batch_json() pas mau mulai sesi
    download TBV baru). Dipanggil dari view sebelum mulai queue baru,
    atau saat discard session TBV lama.
    """
    with _state_lock:
        _state.clear()
        _state.update(_DEFAULT_STATE)
        if overrides:
            _state.update(overrides)
