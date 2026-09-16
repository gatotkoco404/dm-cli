#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/cleaner.py
Version: v0.01

Finishing stage untuk mode TurboVIP (TBV) — analog handlers/post_process.py
tapi khusus TBV. Tugas:
  1. strip_segment()   — hapus "topeng" PNG dari 1 file .ts (dipanggil
                          REAL-TIME oleh handlers/download_tbv.py setiap
                          1 segment selesai download, IN-PLACE — bukan
                          tulis ke file baru lalu hapus yang lama, karena
                          seluruh folder kerja toh dihapus di akhir kalau
                          semua sukses, jadi buang dua kali I/O percuma).
  2. remux_episode()   — setelah SEMUA segment bersih, gabung jadi 1 file
                          video via ffmpeg concat demuxer (stream copy,
                          tanpa re-encode).
  3. finalize_episode()— pindahkan hasil remux ke folder tujuan
                          (settings["download_path"]), lalu hapus
                          subfolder tmp episode.

Port dari decrypt_segment.py (v2) & remux_video.py (v3) milik user.
Logic deteksi PNG-hidden-TS & remux TIDAK diubah — cuma dibungkus ulang:
  - v2: mode folder-batch dibuang (tidak dipakai lagi, karena strip
    sekarang dipanggil per-file real-time dari download_tbv.py, bukan
    sekali jalan di akhir buat semua file).
  - v3: sys.exit()/print() diganti return value + push_event, biar bisa
    dipanggil dari worker thread tanpa menghentikan proses aplikasi.

Pemindahan ke folder tujuan & cleanup folder tmp pakai core/tbv_staging.py
(BUKAN core/staging.py) — lihat catatan pemisahan di tbv_staging.py.
"""

import os
import re
import shutil
import subprocess
import tempfile

from core import tbv_staging


# =========================================
# 1. STRIP PNG-HIDDEN-TS  — port persis dari decrypt_segment.py (v2)
# =========================================

PNG_MAGIC     = b"\x89PNG\r\n\x1a\n"
IEND_MARKER   = b"IEND"
TS_SYNC_BYTE  = 0x47


def strip_segment(path: str) -> str:
    """
    Baca 1 file .ts, kalau ternyata "disamarkan" jadi PNG palsu, strip
    header PNG-nya dan timpa file yang sama (in-place).

    Return status:
      "stripped"   -> tadinya PNG palsu, sudah di-strip jadi TS asli
      "already_ts" -> sudah TS asli (sync byte 0x47 di awal), tidak diubah
      "failed"     -> ke-detect PNG palsu tapi gagal nemu TS sync di dalamnya
      "unknown"    -> bukan PNG dan bukan TS, dibiarkan apa adanya
      "error"      -> gagal baca/tulis file (I/O error, dsb)
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        return "error"

    if data[:8] == PNG_MAGIC:
        extracted = _extract_hidden_ts(data)
        if extracted is None:
            return "failed"
        data_out, status = extracted, "stripped"
    elif data[:1] == bytes([TS_SYNC_BYTE]):
        return "already_ts"
    else:
        return "unknown"

    try:
        with open(path, "wb") as f:
            f.write(data_out)
    except Exception:
        return "error"

    return status


def _extract_hidden_ts(data: bytes):
    """Cari & kembalikan payload TS asli di dalam file PNG palsu."""
    iend_idx = data.find(IEND_MARKER)
    if iend_idx == -1:
        return None
    offset = iend_idx + 4 + 4

    while offset < len(data) and data[offset] == 0xFF:
        offset += 1

    scan_limit = min(4096, len(data) - offset)
    for extra in range(scan_limit):
        test_offset = offset + extra
        if _looks_like_ts_sync(data, test_offset):
            return data[test_offset:]
    return None


def _looks_like_ts_sync(data: bytes, offset: int, packet_size: int = 188, check_packets: int = 10) -> bool:
    if offset >= len(data):
        return False
    count_ok = 0
    for i in range(check_packets):
        pos = offset + i * packet_size
        if pos >= len(data):
            break
        if data[pos] == TS_SYNC_BYTE:
            count_ok += 1
        else:
            return False
    return count_ok >= 3


# =========================================
# 2. REMUX  — port dari remux_video.py (v3)
# =========================================

def remux_episode(episode_dir: str, filename: str, fmt: str, push_event, ep_label: str = "?"):
    """
    Gabung semua segment .ts di dalam episode_dir jadi 1 file video
    `filename.fmt`, ditulis di dalam episode_dir juga (belum dipindah ke
    folder tujuan — itu tugas finalize_episode()).

    Return path file hasil remux, atau None kalau gagal (segment kosong,
    ffmpeg tidak ada, atau proses ffmpeg gagal).
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        _log(push_event, f"EP {ep_label}: ffmpeg tidak ditemukan, remux dibatalkan.", "err")
        return None

    segments = _collect_segments(episode_dir)
    if not segments:
        _log(push_event, f"EP {ep_label}: tidak ada segment .ts untuk diremux.", "err")
        return None

    output_path = os.path.join(episode_dir, f"{filename}.{fmt}")
    list_path   = _write_concat_list(segments)

    cmd = [
        ffmpeg_bin, "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-c", "copy",
    ]
    if fmt == "mp4":
        # AAC dalam TS pakai format ADTS, MP4 butuh raw AAC -> perlu bitstream filter ini
        cmd += ["-bsf:a", "aac_adtstoasc"]
    cmd.append(output_path)

    _log(push_event, f"EP {ep_label}: remux {len(segments)} segment -> {fmt}…")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        _log(push_event, f"EP {ep_label}: remux timeout.", "err")
        _safe_remove(list_path)
        return None
    except Exception as e:
        _log(push_event, f"EP {ep_label}: remux error — {e}", "err")
        _safe_remove(list_path)
        return None

    _safe_remove(list_path)

    if result.returncode != 0:
        err = result.stderr.splitlines()[-1] if result.stderr else "?"
        _log(push_event, f"EP {ep_label}: remux gagal — {err}", "err")
        return None

    _log(push_event, f"EP {ep_label}: remux OK -> {os.path.basename(output_path)}", "ok")
    return output_path


def _collect_segments(folder: str) -> list:
    files = [
        f for f in os.listdir(folder)
        if f.lower().endswith(".ts")
    ]
    files.sort(key=_natural_sort_key)
    return [os.path.join(os.path.abspath(folder), f) for f in files]


def _natural_sort_key(name: str):
    """Sort alami: segment2.ts sebelum segment10.ts, meski tanpa zero-padding."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def _write_concat_list(segment_paths: list) -> str:
    """Tulis file list sementara (OS tempfile) buat ffmpeg concat demuxer."""
    fd, list_path = tempfile.mkstemp(prefix="tbv_concat_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for path in segment_paths:
            escaped = path.replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    return list_path


# =========================================
# 3. FINALIZE  — pindah ke tujuan + cleanup tmp
# =========================================

def finalize_episode(episode_dir: str, remuxed_path: str, dest_folder: str, push_event, ep_label: str = "?") -> bool:
    """
    Pindahkan file hasil remux ke dest_folder (settings["download_path"]),
    lalu hapus subfolder tmp episode SELURUHNYA (segment mentah,
    aria2_input.txt, dsb — bukan cuma file remux-nya).

    Return True kalau file berhasil dipindah, False kalau gagal (folder
    tmp TIDAK dihapus kalau gagal, biar file tidak hilang).
    """
    if not remuxed_path or not os.path.isfile(remuxed_path):
        _log(push_event, f"EP {ep_label}: file hasil remux tidak ditemukan, batal pindah.", "err")
        return False

    fname = os.path.basename(remuxed_path)
    moved = tbv_staging.move_to_destination(episode_dir, dest_folder, filenames=[fname])

    if not moved:
        _log(push_event, f"EP {ep_label}: gagal pindah ke folder tujuan, file tetap di tmp.", "err")
        return False

    tbv_staging.cleanup_episode_dir(episode_dir)
    _log(push_event, f"EP {ep_label}: dipindah ke folder tujuan & tmp dibersihkan.", "ok")
    return True


# =========================================
# HELPERS
# =========================================

def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _log(push_event, msg, cls=""):
    try:
        push_event({"type": "log", "message": msg})
    except Exception:
        print(f"[cleaner] {msg}")
