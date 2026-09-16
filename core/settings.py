#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/settings.py
Version: v0.02

Pengaturan global yang disimpan permanen (settings.json).

Field yang dikelola:
  - download_path     : folder tujuan akhir hasil download
  - input_batch_path  : folder tempat file .txt/.json config batch
                         (dibaca oleh menu Multi m3u8/mp4 & Audio Multi)
  - conn               : jumlah koneksi paralel per file (aria2c -x/-s)
  - parallel            : jumlah episode yang didownload bersamaan (rolling)
  - format               : format output remux (mkv/mp4/ts/best) — dipakai
                            manual & multi. Mode Other selalu keluar mp4/mp3.
  - timeout               : timeout koneksi (detik)
  - rate_limit             : limit kecepatan download (mis. "2M")

Catatan migrasi dari versi lama:
  - Field "quality" (kualitas default HLS manual/multi) DIBUANG — field ini
    tidak pernah benar-benar dipakai di handler download (tidak ada
    -f selector di command yt-dlp-nya).
  - Field "input_batch_path" BARU — sebelumnya folder ini di-hardcode,
    sekarang jadi configurable sesuai keputusan desain.
"""

import os
import json


# =========================================
# DEFAULT & LABEL (urutan di sini = urutan tampil di menu Settings)
# =========================================

DEFAULT_SETTINGS = {
    "download_path":    "/storage/emulated/0/Download",
    "input_batch_path": "/storage/emulated/0/Download/input_batch",
    "conn":              "8",
    "parallel":           1,
    "format":              "mkv",
    "timeout":              "30",
    "rate_limit":            "",
    "turbovip_parallel_limit": 3,   # hardcode di file, TIDAK muncul di menu Settings CLI
                                     # (lihat FIELD_LABELS di bawah — sengaja tidak didaftarkan
                                     # di situ). Edit manual buka settings.json kalau perlu ubah.
}

# Label tampilan untuk menu Settings (CLI). Urutan dict ini yang dipakai
# saat menampilkan form bernomor — jangan diubah jadi set/dict tanpa urutan.
FIELD_LABELS = {
    "download_path":    "Download path",
    "input_batch_path": "Input batch path",
    "conn":              "Conn",
    "parallel":           "Parallel",
    "format":              "Format",
    "timeout":              "Timeout",
    "rate_limit":            "Rate limit",
}

_SETTINGS_FILE = ""


# =========================================
# PATH
# =========================================

def set_path(path: str):
    """Set lokasi file settings.json. Dipanggil sekali dari main.py saat startup."""
    global _SETTINGS_FILE
    _SETTINGS_FILE = path


# =========================================
# LOAD / SAVE
# =========================================

def load_settings() -> dict:
    """Load settings dari file, digabung dengan default (kalau ada field baru)."""
    merged = dict(DEFAULT_SETTINGS)
    if _SETTINGS_FILE and os.path.exists(_SETTINGS_FILE):
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged.update(data)
        except Exception:
            pass
    return merged


def save_settings(new_data: dict):
    """
    Simpan (merge) settings baru ke file.
    Return dict hasil merge, atau None kalau gagal.
    """
    if not _SETTINGS_FILE:
        return None
    merged = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in new_data:
            merged[key] = new_data[key]
    try:
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
        return merged
    except Exception:
        return None


# =========================================
# HELPER — dipakai oleh cli/views/settings_view.py
# =========================================

def field_order() -> list:
    """Return list key setting sesuai urutan tampil di menu (list, bukan dict_keys)."""
    return list(FIELD_LABELS.keys())


def label_for(key: str) -> str:
    """Ambil label tampilan untuk 1 field. Fallback ke key itu sendiri kalau tidak ada."""
    return FIELD_LABELS.get(key, key)
