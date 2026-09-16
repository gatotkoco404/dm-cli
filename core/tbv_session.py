#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/tbv_session.py
Version: v0.01

Simpan/baca/hapus sesi download TurboVIP (TBV) — tbv_session.json.
Mirror persis core/session.py, SENGAJA pakai file terpisah dari
session.json pipeline lama supaya sesi manual/multi/other & sesi TBV
tidak pernah tabrakan kalau kebetulan sama-sama ada yang belum selesai
(mis. app ditutup paksa saat TBV jalan, tapi ternyata ada juga sesi
manual yang di-pause sebelumnya — dua-duanya harus tetap bisa di-resume
independen).

Isi file di-encode Base64 sama seperti session.py (obfuscation ringan,
bukan enkripsi asli).
"""

import os
import json
import base64

_SESSION_FILE = ""


# =========================================
# PATH
# =========================================

def set_path(path: str):
    """Set lokasi file tbv_session.json. Dipanggil sekali dari main.py saat startup."""
    global _SESSION_FILE
    _SESSION_FILE = path


# =========================================
# SAVE / LOAD / DELETE
# =========================================

def save_session(data: dict):
    """Simpan session TBV ke file, di-encode Base64."""
    if not _SESSION_FILE:
        return
    try:
        raw     = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        encoded = base64.b64encode(raw)
        with open(_SESSION_FILE, "wb") as f:
            f.write(encoded)
    except Exception:
        pass


def load_session():
    """
    Load session TBV dari file. Support Base64 (normal) & plain JSON
    (fallback). Return dict atau None kalau tidak ada/gagal baca.
    """
    if not _SESSION_FILE or not os.path.exists(_SESSION_FILE):
        return None
    try:
        with open(_SESSION_FILE, "rb") as f:
            raw = f.read()
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
            return json.loads(decoded)
        except Exception:
            return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def delete_session():
    """Hapus file session TBV jika ada."""
    if _SESSION_FILE and os.path.exists(_SESSION_FILE):
        try:
            os.remove(_SESSION_FILE)
        except Exception:
            pass


# =========================================
# HELPER — dipakai cli/menu.py buat cek menu "lanjutkan TBV sebelumnya"
# =========================================

def has_session() -> bool:
    """Cek cepat apakah ada session TBV tersimpan, tanpa perlu load isinya."""
    return bool(_SESSION_FILE) and os.path.exists(_SESSION_FILE)
