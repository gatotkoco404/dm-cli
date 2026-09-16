#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/post_process.py
Version: v0.02

Dijalankan otomatis setelah semua episode selesai download.
Tugas:
  1. Baca session.json untuk tahu episode, format target, durasi mode
  2. Remux file ke format yang dipilih (via ffmpeg, tanpa re-encode)
  3. Trim subtitle sesuai durasi mode (half_first / half_second / custom)
  4. Inject subtitle sebagai softsub ke file hasil remux
  5. Pindahkan hasil akhir dari folder tmp staging ke folder tujuan
     akhir, lalu bersihkan folder tmp episode itu (BARU — bag. 7 desain)
  6. Hapus file sementara (.srt tmp OS, bukan folder staging)
  7. Hapus session.json — KALAU SEMUA episode statusnya "done" (BERUBAH
     dari v0.01, lihat catatan BUG FIX di bawah)

Perubahan dari v0.01:
  - BUG FIX: delete_session() dulu dipanggil TANPA SYARAT di akhir run()
    — walau ada episode "failed"/"held" yang belum tuntas. Akibatnya
    session.json hilang padahal masih ada yang gagal, dan folder tmp-nya
    (berisi .part/.aria2 yang sebenarnya bisa di-resume) jadi gak
    kelihatan lagi dari sisi dm-cli status/bridge.py — kelihatan macam
    "progress ilang begitu aja" padahal sebenarnya cuma gagal. Sekarang
    session.json CUMA dihapus kalau semua episode "done" (lihat langkah
    6 di run()).

Perubahan dari versi lama (sebelum v0.01, riwayat pra-refactor):
  - Import load_session/delete_session pindah ke core.session.
  - Import needs_decrypt/decrypt_subtitle/get_sub_ext pindah ke
    handlers.sub_decrypt.
  - Langkah 5 (pindah + cleanup tmp staging) BARU — tidak ada di versi
    lama, karena versi lama download langsung ke folder tujuan.
  - BUG FIX: _inject_sub() dulu pakai variabel "output_path" yang tidak
    pernah didefinisikan (NameError kalau fungsi ini jalan). Sudah
    diperbaiki jadi "tmp_out" — lihat komentar FIX di dalam fungsi.

DEPENDENSI PENTING: fungsi run() di sini baca ep["episode_dir"] dari
data session (session.json), BUKAN dari dict live di memori. Artinya
core/scheduler.py WAJIB menyertakan "episode_dir" di whitelist field
yang disimpan ke session.json — kalau kelewat, post_process tidak akan
tahu folder tmp mana yang harus dipindah/dibersihkan.
"""

import os
import re
import time
import subprocess
import tempfile

from core.session import load_session, delete_session
from core import staging
from handlers.sub_decrypt import needs_decrypt, decrypt_subtitle


# =========================================
# PUBLIC — dipanggil dari core/scheduler.py
# =========================================

def run(push_event, session_data=None):
    """
    Entry point post-processing.
    push_event  : callable untuk kirim log
    session_data: dict session (kalau None, load dari file)
    """
    data = session_data or load_session()
    if not data:
        _log(push_event, "post_process: tidak ada session, skip.", "warn")
        return

    queue       = data.get("queue", [])
    fmt         = data.get("format", "mkv").lower()
    dest_folder = data.get("download_path", "")
    has_ffmpeg  = _check_ffmpeg(push_event)

    tmp_subs = []   # path file .srt sementara (OS tempfile) yang perlu dihapus

    for ep in queue:
        if ep.get("status") != "done":
            continue

        output_file = ep.get("output_file", "")
        if not output_file or not os.path.exists(output_file):
            output_file = _find_actual_file(output_file)
            if not output_file:
                _log(push_event, f"EP {ep.get('ep','?')}: file tidak ditemukan, skip.", "warn")
                continue

        ep_label    = ep.get("ep", "?")
        sub_url     = ep.get("sub", "")
        dur_mode    = ep.get("duration", "off")
        dur_custom  = ep.get("duration_custom", "")
        episode_dir = ep.get("episode_dir", "")

        _log(push_event, f"Post-process EP {ep_label}…")

        # ── 1. Remux format ──
        final_file = output_file
        if has_ffmpeg:
            final_file = _remux(output_file, fmt, push_event, ep_label)
        else:
            _log(push_event, f"EP {ep_label}: ffmpeg tidak ada, skip remux.", "warn")

        # ── 2. Download & trim subtitle ──
        srt_path = None
        if sub_url and final_file:
            srt_path = _download_sub(sub_url, push_event, ep_label)
            if srt_path:
                tmp_subs.append(srt_path)
                srt_path = _check_decrypt_sub(srt_path, sub_url, push_event, ep_label)
                trimmed = _trim_sub(srt_path, dur_mode, dur_custom, push_event, ep_label)
                if trimmed and trimmed != srt_path:
                    tmp_subs.append(trimmed)
                    srt_path = trimmed

        # ── 3. Inject subtitle ──
        if srt_path and final_file and has_ffmpeg:
            _inject_sub(final_file, srt_path, push_event, ep_label)

        # ── 4. Pindah hasil akhir tmp -> folder tujuan, lalu cleanup tmp ──
        if episode_dir and dest_folder:
            moved = staging.move_to_destination(episode_dir, dest_folder)
            if moved:
                staging.cleanup_episode_dir(episode_dir)
                _log(push_event, f"EP {ep_label}: dipindah ke folder tujuan.", "ok")
            else:
                _log(push_event,
                     f"EP {ep_label}: gagal pindah ke folder tujuan, file tetap di tmp.", "err")
        else:
            _log(push_event,
                 f"EP {ep_label}: episode_dir/download_path tidak diketahui, lewati pemindahan.",
                 "warn")

        _log(push_event, f"EP {ep_label}: selesai.", "ok")

    # ── 5. Hapus file sementara subtitle (OS tempfile) ──
    for path in tmp_subs:
        _safe_remove(path)

    # ── 6. Hapus session — KECUALI masih ada episode belum tuntas ──
    # "belum tuntas" = bukan "done" dan bukan "removed" (dibatalkan
    # permanen user). Ini termasuk "failed" (gagal download/koneksi
    # putus/dsb) MAUPUN "held" (di-pause per-episode tapi gak pernah
    # di-resume sebelum semua slot worker selesai).
    #
    # KENAPA INI PENTING: kalau session.json dihapus tanpa syarat,
    # episode yang gagal jadi "hilang jejak" -- padahal file segmennya
    # (.part/.aria2) MASIH ADA di folder tmp episode itu (loop di atas
    # cuma proses status "done", yang "failed" di-skip total, folder
    # tmp-nya TIDAK dibersihkan). Kalau nanti link yang sama didownload
    # ulang, aria2c/yt-dlp otomatis LANJUT dari situ (--continue=true),
    # bukan dari nol. Tapi itu semua percuma kalau session.json keburu
    # dihapus -- dm-cli status/bridge.py gak akan pernah tau ada yang
    # perlu di-retry, karena satu-satunya sumber info mereka (session
    # file) sudah tidak ada.
    unfinished = [ep for ep in queue if ep.get("status") not in ("done", "removed")]

    if unfinished:
        _log(push_event,
             f"{len(unfinished)} episode belum tuntas (gagal/ditahan) — "
             f"session.json TIDAK dihapus, bisa di-retry.", "warn")
    else:
        delete_session()
        _log(push_event, "Post-process selesai.", "ok")


# =========================================
# FFMPEG CHECK  — tidak berubah
# =========================================

def _check_ffmpeg(push_event):
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        _log(push_event,
             "ffmpeg tidak ditemukan — remux & inject sub dilewati. "
             "Install dengan: pkg install ffmpeg", "warn")
        return False


# =========================================
# REMUX FORMAT  — tidak berubah
# =========================================

def _remux(input_path, target_fmt, push_event, ep_label):
    """
    Remux file ke format target tanpa re-encode.
    Return path file hasil remux, atau input_path kalau tidak perlu/gagal.
    """
    ext_actual = os.path.splitext(input_path)[1].lstrip(".").lower()

    if ext_actual == target_fmt:
        _log(push_event, f"EP {ep_label}: format sudah {target_fmt}, skip remux.")
        return input_path

    output_path = os.path.splitext(input_path)[0] + f".{target_fmt}"

    _log(push_event, f"EP {ep_label}: remux {ext_actual} -> {target_fmt}…")

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c", "copy",          # tidak re-encode
        "-map", "0",           # ambil semua stream
        output_path
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            _log(push_event,
                 f"EP {ep_label}: remux gagal — {result.stderr.splitlines()[-1] if result.stderr else '?'}",
                 "err")
            return input_path

        _safe_remove(input_path)
        _log(push_event, f"EP {ep_label}: remux OK -> {os.path.basename(output_path)}", "ok")
        return output_path

    except subprocess.TimeoutExpired:
        _log(push_event, f"EP {ep_label}: remux timeout.", "err")
        return input_path
    except Exception as e:
        _log(push_event, f"EP {ep_label}: remux error — {e}", "err")
        return input_path


# =========================================
# DOWNLOAD SUBTITLE  — tidak berubah
# =========================================

def _download_sub(url, push_event, ep_label):
    """Download subtitle ke file tmp OS. Return path atau None kalau gagal."""
    try:
        import urllib.request
        tmp = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".srt", delete=False,
            dir=tempfile.gettempdir()
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
            tmp.write(raw_bytes)
        tmp.close()
        _log(push_event, f"EP {ep_label}: subtitle didownload ({len(raw_bytes)} bytes).", "")
        return tmp.name
    except Exception as e:
        _log(push_event, f"EP {ep_label}: gagal download sub — {e}", "err")
        return None


# =========================================
# CEK & DECRYPT SUBTITLE  — tidak berubah (selain import get_sub_ext)
# =========================================

def _check_decrypt_sub(srt_path, sub_url, push_event, ep_label):
    """
    Cek apakah subtitle butuh decrypt (AES, kissKH) berdasarkan isi & URL-nya.
    Kalau perlu, decrypt dan tulis ulang ke file yang sama.
    Return srt_path (path tidak berubah — isi file yang diganti).
    """
    try:
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
    except Exception as e:
        _log(push_event, f"EP {ep_label}: gagal baca sub untuk cek decrypt — {e}", "err")
        return srt_path

    if not needs_decrypt(raw):
        _log(push_event, f"EP {ep_label}: sub terdeteksi plain, skip decrypt.", "")
        return srt_path

    from handlers.sub_decrypt import get_sub_ext
    ext = get_sub_ext(sub_url)

    def _trace(msg):
        _log(push_event, f"EP {ep_label}: [decrypt] {msg}", "")

    decrypted = decrypt_subtitle(raw, sub_url, log=_trace)

    try:
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(decrypted)
        _log(push_event, f"EP {ep_label}: subtitle didekripsi.", "ok")
    except Exception as e:
        _log(push_event, f"EP {ep_label}: gagal tulis sub hasil decrypt — {e}", "err")

    return srt_path


# =========================================
# TRIM SUBTITLE  — tidak berubah
# =========================================

def _trim_sub(srt_path, dur_mode, dur_custom, push_event, ep_label):
    """
    Trim subtitle sesuai mode durasi.

    dur_mode:
      off         -> tidak perlu trim, return srt_path asli
      half_first  -> buang sub setelah titik tengah, tidak perlu shift
      half_second -> buang sub sebelum titik tengah, shift mundur
      custom      -> buang sub di luar range, shift jika perlu

    Return path file .srt hasil trim (file baru), atau srt_path kalau tidak perlu trim.
    """
    d = (dur_mode or "off").strip().lower()
    if d == "off":
        return srt_path

    entries = _parse_srt(srt_path)
    if not entries:
        return srt_path

    total_dur = entries[-1]["end"]
    mid       = total_dur / 2

    if d == "half_first":
        result = [e for e in entries if e["start"] < mid]
        for e in result:
            if e["end"] > mid:
                e["end"] = mid
        shift = 0.0

    elif d == "half_second":
        result = [e for e in entries if e["end"] > mid]
        shift  = mid

    elif d == "custom":
        start_sec, end_sec = _parse_custom_range(dur_custom, total_dur)
        result = []
        for e in entries:
            if e["end"] <= start_sec:
                continue
            if end_sec and e["start"] >= end_sec:
                continue
            e2 = dict(e)
            if e2["start"] < start_sec:
                e2["start"] = start_sec
            if end_sec and e2["end"] > end_sec:
                e2["end"] = end_sec
            result.append(e2)
        shift = start_sec

    else:
        return srt_path

    if not result:
        _log(push_event, f"EP {ep_label}: tidak ada sub setelah trim.", "warn")
        return srt_path

    if shift > 0:
        for e in result:
            e["start"] = max(0.0, e["start"] - shift)
            e["end"]   = max(0.0, e["end"]   - shift)

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".srt", delete=False,
        encoding="utf-8", dir=tempfile.gettempdir()
    )
    for idx, e in enumerate(result, 1):
        tmp.write(f"{idx}\n")
        tmp.write(f"{_sec_to_ts(e['start'])} --> {_sec_to_ts(e['end'])}\n")
        tmp.write(e["text"] + "\n\n")
    tmp.close()

    _log(push_event,
         f"EP {ep_label}: sub ditrim ({len(result)} entri, mode={d}).", "ok")
    return tmp.name


def _parse_custom_range(dur_custom, total_dur):
    """
    Parse string custom range.
    Format: "HH:MM:SS-HH:MM:SS" atau "HH:MM:SS-end"
    Return (start_sec, end_sec) — end_sec None berarti sampai akhir.
    """
    if not dur_custom:
        return 0.0, None
    parts = dur_custom.split("-", 1)
    start_sec = _ts_to_sec(parts[0].strip()) if parts[0].strip() else 0.0
    if len(parts) < 2 or parts[1].strip().lower() in ("end", "inf", ""):
        end_sec = None
    else:
        end_sec = _ts_to_sec(parts[1].strip())
    return start_sec, end_sec


# =========================================
# INJECT SUBTITLE (softsub)
# =========================================

def _inject_sub(video_path, srt_path, push_event, ep_label):
    """
    Inject .srt ke video sebagai subtitle track (softsub).
    File video diganti di tempat (via file tmp).
    """
    ext     = os.path.splitext(video_path)[1]
    tmp_out = video_path + ".tmp" + ext

    _log(push_event, f"EP {ep_label}: inject subtitle…")

    cmd = [
        'ffmpeg', '-y',
        '-i', video_path,      # Input 0 (Video asli)
        '-i', srt_path,        # Input 1 (Subtitle baru)
        '-map', '0:v',         # Ambil video dari input 0
        '-map', '0:a',         # Ambil semua audio dari input 0
        '-map', '1:s',         # Ambil subtitle dari file SRT
        '-c', 'copy',
        tmp_out                 # FIX: dulu pakai variabel "output_path" yang
                                 # tidak pernah didefinisikan di fungsi ini
                                 # (NameError). Harusnya tmp_out — file remux
                                 # sementara yang di-rename balik ke nama asli
                                 # kalau sukses (lihat bawah).
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            err = result.stderr.splitlines()[-1] if result.stderr else "?"
            _log(push_event, f"EP {ep_label}: inject sub gagal — {err}", "err")
            _safe_remove(tmp_out)
            return

        _safe_remove(video_path)
        os.rename(tmp_out, video_path)
        _log(push_event, f"EP {ep_label}: subtitle diinjek.", "ok")

    except subprocess.TimeoutExpired:
        _log(push_event, f"EP {ep_label}: inject sub timeout.", "err")
        _safe_remove(tmp_out)
    except Exception as e:
        _log(push_event, f"EP {ep_label}: inject sub error — {e}", "err")
        _safe_remove(tmp_out)


# =========================================
# SRT PARSER / WRITER UTILS  — tidak berubah
# =========================================

def _parse_srt(path):
    """Parse file .srt -> list of {start, end, text}."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        blocks  = re.split(r'\n\s*\n', content.strip())
        entries = []

        for block in blocks:
            lines = block.strip().splitlines()
            if len(lines) < 2:
                continue
            ts_line = -1
            for i, l in enumerate(lines):
                if "-->" in l:
                    ts_line = i
                    break
            if ts_line < 0:
                continue
            m = re.match(
                r'(\d+:\d+:\d+,\d+)\s*-->\s*(\d+:\d+:\d+,\d+)',
                lines[ts_line]
            )
            if not m:
                continue
            text = "\n".join(lines[ts_line + 1:]).strip()
            text = re.sub(r'<[^>]+>', '', text)
            if not text:
                continue
            entries.append({
                "start": _ts_to_sec(m.group(1)),
                "end":   _ts_to_sec(m.group(2)),
                "text":  text,
            })
        return entries
    except Exception:
        return []


def _ts_to_sec(ts):
    """00:40:05,200 -> float detik."""
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


def _sec_to_ts(sec):
    """float detik -> 00:00:05,200."""
    sec = max(0.0, sec)
    h   = int(sec // 3600)
    m   = int((sec % 3600) // 60)
    s   = int(sec % 60)
    ms  = round((sec - int(sec)) * 1000)
    if ms >= 1000:
        ms = 999
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# =========================================
# FILE HELPERS  — tidak berubah
# =========================================

def _find_actual_file(expected_path):
    """
    Cari file dengan nama yang sama tapi ext berbeda.
    Misal expected .mp4 tapi actual .mkv atau .webm.
    """
    if not expected_path:
        return None
    base    = os.path.splitext(expected_path)[0]
    folder  = os.path.dirname(expected_path)
    if not os.path.isdir(folder):
        return None
    for ext in (".mp4", ".mkv", ".webm", ".ts", ".m4v", ".avi"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return None


def _safe_remove(path):
    """Hapus file tanpa raise exception."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# =========================================
# LOG HELPER  — tidak berubah
# =========================================

def _log(push_event, msg, cls=""):
    """Kirim log ke notifier."""
    try:
        push_event({"type": "log", "message": msg})
    except Exception:
        print(f"[post_process] {msg}")
