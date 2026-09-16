#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/staging.py
Version: v0.01

Kelola folder tmp staging (<root_app>/tmp/) — tempat file ditulis SEBELUM
dipindah ke folder tujuan akhir. Tiap episode/link dapat subfolder sendiri
(tmp/<nama-file-sanitized>/), tempat download mentah, remux ffmpeg, dan
inject subtitle terjadi. Setelah sukses, hasil akhir dipindah ke folder
tujuan lalu subfolder tmp-nya dihapus.

Modul ini SENGAJA cuma nyediain primitif (bikin folder, pindah, hapus) —
tidak ada keputusan bisnis "kapan harus cleanup/move" di sini. Itu
tanggung jawab pemanggil (handlers/scheduler), supaya modul ini gampang
dites terpisah dan tidak nyampur tanggung jawab.

PENTING: cleanup_all() TIDAK dipanggil otomatis saat startup — kalau ada
sesi yang di-pause, file .part sisa aria2c ada di folder tmp episode
terkait dan itu yang dipakai untuk resume. cleanup_all() cuma dipanggil
eksplisit dari alur "user pilih mulai baru, buang sesi lama".
"""

import os
import shutil


_TMP_ROOT = ""


# =========================================
# SETUP
# =========================================

def set_root(app_root: str):
    """
    Set & siapkan folder tmp root, di dalam root aplikasi.
    Dipanggil sekali dari main.py saat startup.
    """
    global _TMP_ROOT
    _TMP_ROOT = os.path.join(app_root, "tmp")
    os.makedirs(_TMP_ROOT, exist_ok=True)


def get_root() -> str:
    """Return path tmp root saat ini (buat keperluan debug/log)."""
    return _TMP_ROOT


# =========================================
# SUBFOLDER PER-EPISODE
# =========================================

def get_episode_dir(name: str) -> str:
    """
    Return path subfolder tmp untuk 1 episode/link, dibuat kalau belum ada.
    `name` di-sanitize ulang di sini (defense in depth) meskipun caller
    biasanya sudah sanitize duluan — biar aman kalau ada yang lupa.
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
                untuk pindahkan SEMUA file langsung di dalam episode_dir
                (tidak masuk ke subfolder lagi kalau ada).

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
    Hapus 1 subfolder tmp episode beserta seluruh isinya.
    Dipakai untuk 3 skenario: sukses (setelah move berhasil),
    episode gagal, atau episode dicancel permanen.
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
    Bersihkan SELURUH isi folder tmp root.
    Dipakai HANYA saat user eksplisit memilih "mulai baru" dan membuang
    sesi lama (bag. 6 desain) — bukan dipanggil otomatis di startup.
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
    """
    Cek path yang mau di-rmtree beneran ada di dalam tmp root.
    Jaga-jaga biar cleanup_episode_dir() tidak bisa kepakai buat hapus
    folder di luar tmp secara tidak sengaja (misal kalau ada bug di
    caller yang ngirim path salah).
    """
    if not _TMP_ROOT:
        return False
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(_TMP_ROOT)
        return os.path.commonpath([abs_path, abs_root]) == abs_root
    except Exception:
        return False
        