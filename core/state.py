#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/state.py
Version: v0.01

State global aplikasi + lock-nya. Dipakai bareng oleh core/scheduler.py
dan cli/views/control_view.py (dashboard + hotkey pause/resume/cancel)
untuk sinkronisasi antar thread — worker download jalan di thread
terpisah, sementara main thread baca keypress & render dashboard.

Port dari state dict di app.py lama. Struktur dict + threading.Lock-nya
dipertahankan karena masih relevan buat CLI — scheduler tetap jalan di
background thread, terlepas dari Flask atau tidak.

Perubahan dari versi lama:
  - Field "quality" dibuang (sesuai keputusan penghapusan settings.quality)
  - Field "input_batch_path" ditambah (sesuai field baru di settings.py)
  - Dibungkus jadi get_state()/get_lock()/reset_state() alih-alih dict
    modul-level yang diakses langsung — biar reset antar sesi jelas
    tanpa bikin instance Lock baru (lock harus sama terus selama app hidup)
"""

import threading


_state_lock = threading.Lock()

_DEFAULT_STATE = {
    "anime":            "",
    "download_path":    "",
    "input_batch_path": "",
    "config_file":      "",
    "format":           "mkv",
    "timeout":          "30",
    "conn":             "8",
    "parallel":         1,
    "rate_limit":       "",

    "mode":             "manual",   # manual | multi | other

    "queue":            [],
    "current_index":    -1,

    "status":           "idle",     # idle | downloading | paused | done
    "paused":           False,
    "cancelled":        False,
}

_state = dict(_DEFAULT_STATE)


def get_state() -> dict:
    """
    Return referensi LANGSUNG ke state dict (bukan copy) — sengaja,
    karena scheduler & view perlu mutasi in-place dengan lock yang sama.
    Selalu akses/ubah isinya di dalam blok `with get_lock():`.
    """
    return _state


def get_lock() -> threading.Lock:
    """Lock bersama buat sinkronisasi akses ke state antar thread."""
    return _state_lock


def reset_state(overrides: dict = None):
    """
    Reset state ke default, opsional dengan override awal (misal isi
    dari settings.json pas mau mulai sesi download baru). Dipanggil
    dari view sebelum mulai queue baru, atau saat discard session lama.
    """
    with _state_lock:
        _state.clear()
        _state.update(_DEFAULT_STATE)
        if overrides:
            _state.update(overrides)
