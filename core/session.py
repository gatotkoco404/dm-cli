#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/session.py
Version: v0.01

Simpan/baca/hapus sesi download (session.json) — dipakai untuk fitur
"Lanjutkan download sebelumnya". Isi file di-encode Base64 (obfuscation
ringan, bukan enkripsi asli) supaya tidak gampang dibaca/diedit manual.

Port dari modules/helpers.py (fungsi save_session/load_session/delete_session),
dipisah jadi modul sendiri sesuai struktur folder baru.
"""

import os
import json
import base64

_SESSION_FILE = ""


# =========================================
# PATH
# =========================================

def set_path(path: str):
    """Set lokasi file session.json. Dipanggil sekali dari main.py saat startup."""
    global _SESSION_FILE
    _SESSION_FILE = path


# =========================================
# SAVE / LOAD / DELETE
# =========================================

def save_session(data: dict):
    """Simpan session ke file, di-encode Base64."""
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
    Load session dari file.
    Support file Base64 (format normal) maupun plain JSON (fallback,
    misal file sempat diedit manual). Return dict atau None kalau
    tidak ada / gagal baca.
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
            # fallback: coba baca sebagai plain JSON
            return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def delete_session():
    """Hapus file session jika ada."""
    if _SESSION_FILE and os.path.exists(_SESSION_FILE):
        try:
            os.remove(_SESSION_FILE)
        except Exception:
            pass


# =========================================
# HELPER — dipakai main.py buat cek menu "0. Lanjutkan"
# =========================================

def has_session() -> bool:
    """Cek cepat apakah ada session tersimpan, tanpa perlu load isinya."""
    return bool(_SESSION_FILE) and os.path.exists(_SESSION_FILE)
    