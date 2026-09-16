#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/tbv_staging.py
Version: v0.01

Kelola folder tmp staging khusus mode TurboVIP (TBV) —
<root_app>/tmp/tbv/<judul-episode>/. SENGAJA dipisah dari core/staging.py
(bukan reuse langsung) meski primitifnya nyaris identik, supaya alur TBV
tidak bercampur/coupling sama alur manual-multi-other yang sudah stabil.
Kalau nanti staging.py berubah demi kebutuhan pipeline lama, TBV tidak
ikut kena dampak, begitu juga sebaliknya.

Beda folder root dari core/staging.py (tmp/tbv/ vs tmp/) supaya isi
kedua pipeline tidak pernah tercampur/collide walau app root sama.

Sama seperti staging.py: modul ini cuma nyediain primitif (bikin folder,
pindah file, hapus folder) — keputusan bisnis "kapan pindah/cleanup"
tetap tanggung jawab pemanggil (handlers/cleaner.py, core/tbv_scheduler.py).
"""

import os
import shutil


_TMP_ROOT = ""


# =========================================
# SETUP
# =========================================

def set_root(app_root: str):
    """
    Set & siapkan folder tmp root khusus TBV, di dalam root aplikasi.
    Dipanggil sekali dari main.py saat startup (setelah core.staging.set_root()).
    """
    global _TMP_ROOT
    _TMP_ROOT = os.path.join(app_root, "tmp", "tbv")
    os.makedirs(_TMP_ROOT, exist_ok=True)


def get_root() -> str:
    """Return path tmp root TBV saat ini (buat keperluan debug/log)."""
    return _TMP_ROOT


# =========================================
# SUBFOLDER PER-EPISODE
# =========================================

def get_episode_dir(name: str) -> str:
    """
    Return path subfolder tmp untuk 1 episode TBV, dibuat kalau belum ada.
    `name` di-sanitize ulang di sini (defense in depth) meskipun caller
    (core/tbv_parser.py build_filename) biasanya sudah sanitize duluan.
    """
    from core.config_parser import sanitize
    safe_name = sanitize(name) or "unnamed"
    path = os.path.join(_TMP_ROOT, safe_name)
    os.makedirs(path, exist_ok=True)
    return path


# =========================================
# PINDAH KE TUJUAN AKHIR
# =========================================

def move_to_destination(episode_dir: str, dest_folder: str, filenames=None) -> list:
    """
    Pindahkan file hasil akhir dari episode_dir ke dest_folder.

    filenames : list nama file spesifik yang mau dipindah, atau None
                untuk pindahkan SEMUA file langsung di dalam episode_dir.

    Return: list path tujuan yang berhasil dipindah (bisa kosong kalau
            tidak ada yang berhasil — caller yang cek & putuskan langkah
            selanjutnya, fungsi ini tidak raise exception).
    """
    if not episode_dir or not os.path.isdir(episode_dir):
        return []

    os.makedirs(dest_folder, exist_ok=True)

    if filenames is None:
        entries = [
            f for f in os.listdir(episode_dir)
            if os.path.isfile(os.path.join(episode_dir, f))
        ]
    else:
        entries = filenames

    moved = []
    for fname in entries:
        src = os.path.join(episode_dir, fname)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dest_folder, fname)
        try:
            shutil.move(src, dst)
            moved.append(dst)
        except Exception:
            pass

    return moved


# =========================================
# CLEANUP
# =========================================

def cleanup_episode_dir(episode_dir: str):
    """
    Hapus 1 subfolder tmp episode TBV beserta seluruh isinya (segment
    mentah/bersih, aria2_input.txt, file remux sisa kalau gagal pindah).
    """
    if not episode_dir or not _is_inside_tmp_root(episode_dir):
        return
    try:
        if os.path.isdir(episode_dir):
            shutil.rmtree(episode_dir, ignore_errors=True)
    except Exception:
        pass


def cleanup_all():
    """
    Bersihkan SELURUH isi folder tmp/tbv root. Dipakai HANYA saat user
    eksplisit memilih "mulai baru, buang sesi TBV lama" — bukan otomatis
    di startup (konsisten dengan core/staging.py.cleanup_all()).
    """
    if not _TMP_ROOT or not os.path.isdir(_TMP_ROOT):
        return
    for entry in os.listdir(_TMP_ROOT):
        full = os.path.join(_TMP_ROOT, entry)
        try:
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                os.remove(full)
        except Exception:
            pass


# =========================================
# SAFETY
# =========================================

def _is_inside_tmp_root(path: str) -> bool:
    """Cek path yang mau di-rmtree beneran ada di dalam tmp/tbv root."""
    if not _TMP_ROOT:
        return False
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(_TMP_ROOT)
        return os.path.commonpath([abs_path, abs_root]) == abs_root
    except Exception:
        return False
