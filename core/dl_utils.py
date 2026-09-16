#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/dl_utils.py
Version: v0.01

Utility murni buat parsing output live subprocess yt-dlp/aria2c (progress,
speed, ETA, jumlah koneksi aktif) + builder argumen aria2c + cek koneksi
internet. Dipakai bareng oleh handlers/download.py & handlers/other.py.

Port dari modules/helpers.py — logic TIDAK diubah. Dipisah jadi modul
sendiri karena beda concern dari core/config_parser.py (yang itu soal
parsing FILE config, ini soal parsing OUTPUT PROSES live).
"""

import re
import urllib.request

# NOTE: file ini read-only (project knowledge). format_eta() di bawah
# BARU — dipakai handlers/download_tbv.py. Tempel manual ke dl_utils.py
# asli di project kamu (taruh di bagian mana saja, tidak bergantung
# urutan sama fungsi lain di file ini).


# =========================================
# PARSE PROGRESS OUTPUT YT-DLP
# =========================================

def parse_percent(line: str):
    """
    Extract persentase dari output yt-dlp.
    Contoh: '[download]  65.3% of ...'
    Return float atau None.
    """
    m = re.search(r'(\d+\.?\d*)%', line)
    return float(m.group(1)) if m else None


def parse_segment(line: str):
    """
    Extract info fragment dari output yt-dlp.

    Format 1 (aria2c/fragmented):
      [download] Downloading fragment 45 of 120  -> (45, 120)

    Format 2 (hlsnative — total di awal):
      [hlsnative] Total fragments: 695           -> (0, 695)

    Format 3 (hlsnative — progress per fragment):
      [download] 2.3% of ~1.20GiB ... (frag 14/695)  -> (14, 695)

    Return None kalau tidak match.
    """
    m = re.search(r'fragment\s+(\d+)\s+of\s+(\d+)', line, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = re.search(r'Total fragments:\s*(\d+)', line, re.IGNORECASE)
    if m:
        return (0, int(m.group(1)))

    m = re.search(r'\(frag\s+(\d+)/(\d+)\)', line, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))

    return None


def parse_speed(line: str):
    """
    Extract kecepatan dari output yt-dlp.
    Contoh: '2.45MiB/s'
    Return string atau None.
    """
    m = re.search(r'(\d+\.?\d+\s*[KMG]iB/s)', line)
    return m.group(1) if m else None


def parse_eta(line: str):
    """
    Extract ETA dari output yt-dlp.
    Contoh: 'ETA 00:42'
    Return string atau None.
    """
    m = re.search(r'ETA\s+(\d{1,2}:\d{2}(?::\d{2})?)', line)
    return m.group(1) if m else None


# =========================================
# NETWORK
# =========================================

def is_connected() -> bool:
    """Cek koneksi internet dengan ping ke 1.1.1.1."""
    try:
        urllib.request.urlopen("http://1.1.1.1", timeout=3)
        return True
    except Exception:
        return False


# =========================================
# ARIA2C DOWNLOADER-ARGS BUILDER
# =========================================

def build_aria2c_args(conn, timeout, size_threshold_mb: int = 300, debug_summary: bool = True) -> str:
    """
    Bangun string --downloader-args buat yt-dlp+aria2c.

    Deteksi ukuran file & keputusan split diserahkan sepenuhnya ke aria2c
    sendiri lewat -k/--min-split-size:
      - aria2c gak akan split file yang ukurannya < 2x(-k).
      - Dengan -k = size_threshold_mb / conn, file kecil (di bawah
        threshold) otomatis cuma pakai 1 koneksi, conn penuh baru
        tercapai sekitar file berukuran >= size_threshold_mb.
      - Kalau server gak support Range request, aria2c otomatis fallback
        ke 1 koneksi sendiri.

    debug_summary=True -> aria2c print ringkasan tiap 1 detik (termasuk
    "CN:<n>" = jumlah koneksi aktif saat itu).
    """
    conn = int(conn)
    min_split_mb = max(1, size_threshold_mb // max(conn, 1))

    args = (
        f"aria2c:"
        f"-x{conn} "
        f"-s{conn} "
        f"-k{min_split_mb}M "
        f"--file-allocation=none "
        f"--max-tries=5 "
        f"--retry-wait=3 "
        f"--timeout={timeout}"
    )

    if debug_summary:
        args += " --summary-interval=1 --console-log-level=info"

    return args


# =========================================
# ARIA2C CONN DEBUG PARSER
# =========================================

_ARIA2_CONN_RE = re.compile(r'\((\d+)%\)\s*CN:(\d+)\s*DL:(\S+)')


def parse_aria2c_conn(line: str):
    """
    Baca baris ringkasan aria2c buat lihat berapa koneksi yang beneran
    aktif saat itu.
    Contoh baris aria2c: '[#2089b0 465MiB/1.1GiB(41%) CN:8 DL:4.9MiB ETA:2m22s]'
    Return (persen, jumlah_conn_aktif, speed_str) atau None kalau baris ini
    bukan baris ringkasan aria2c.
    """
    m = _ARIA2_CONN_RE.search(line)
    if not m:
        return None
    pct, cn, speed = m.groups()
    return int(pct), int(cn), speed


# =========================================
# ETA FORMATTING  — BARU, dipakai handlers/download_tbv.py
# =========================================
# Beda dari parse_eta() di atas (yang EXTRACT string ETA dari output
# yt-dlp/aria2c verbose), fungsi ini BUAT string ETA dari angka detik
# hasil hitungan sendiri (sliding-window rate segment/detik) — dipakai
# TBV karena stdout aria2c di-DEVNULL-kan (tidak ada teks buat di-parse).

def format_eta(seconds) -> str:
    """
    Format angka detik (int/float) jadi string "MM:SS", atau "H:MM:SS"
    kalau >= 1 jam. Return "--:--" kalau input None/negatif/tidak valid.
    """
    if seconds is None:
        return "--:--"
    try:
        seconds = int(round(seconds))
    except (TypeError, ValueError):
        return "--:--"
    if seconds < 0:
        return "--:--"

    h, rem = divmod(seconds, 3600)
    m, s   = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02}:{s:02}"
    return f"{m:02}:{s:02}"
