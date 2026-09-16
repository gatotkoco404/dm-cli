#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/download_tbv.py
Version: v0.01

Handler utama mode TurboVIP (TBV). Beda dari handlers/download.py &
handlers/other.py yang keduanya panggil yt-dlp, di sini yang jalan
adalah aria2c LANGSUNG membaca file input (core/tbv_parser.py sudah
tulis daftar url segment + nama outputnya duluan), karena provider TBV
menyamarkan tiap segment .ts jadi file PNG palsu — yt-dlp tidak paham
itu, jadi urusan ambil playlist & urusan download dipisah total dari
jalur yt-dlp yang sudah ada.

Tugas fungsi utama download_tbv_episode() (dipanggil dari
core/tbv_scheduler.py, signature SAMA dengan handlers/download.py &
handlers/other.py biar polanya konsisten):
  1. Siapkan folder kerja (core/tbv_staging.py) + daftar segment
     (core/tbv_parser.py).
  2. Jalankan aria2c (banyak file .ts kecil sekaligus, BUKAN banyak
     koneksi per file — lihat SEGMENT_PARALLEL/SEGMENT_CONN di bawah).
  3. Poll progress tiap 1 detik; begitu 1 segment terdeteksi kelar
     (file ada, kontrol .aria2 hilang), LANGSUNG panggil
     handlers/cleaner.strip_segment() saat itu juga (real-time, bukan
     nunggu semua kelar dulu baru dibersihkan sekaligus).
  4. Hitung persentase + ETA sendiri (sliding window) — beda dari
     handler yt-dlp yang tinggal parse teks output, di sini stdout
     aria2c di-DEVNULL-kan sepenuhnya, progress SEPENUHNYA dari hasil
     hitung sendiri berdasar jumlah file yang sudah lengkap+bersih.
  5. Pause/resume/cancel pola SAMA seperti handlers/download.py:
     pause = terminate proc lalu tunggu, resume = jalankan ulang
     command yang sama (aria2c --continue=true otomatis lanjut dari
     segment yang belum selesai, tidak mulai dari nol).
  6. Setelah semua segment bersih -> handlers/cleaner.remux_episode()
     -> handlers/cleaner.finalize_episode() (pindah + cleanup tmp).

TUNING — sesuaikan di sini kalau provider mulai reset koneksi / rate
limit di angka tinggi. File .ts TBV kecil (500KB-3MB), makanya strategi
di sini "banyak file sekaligus" (-j), BUKAN "banyak koneksi per file"
(-x/-s) seperti mode manual/multi yang pakai file besar per episode.
"""

import os
import shutil
import subprocess
import time

from core import tbv_parser, tbv_staging
from core.dl_utils import is_connected, format_eta
from handlers.cleaner import strip_segment, remux_episode, finalize_episode

# =========================================
# TUNING — sesuaikan kalau kena limit/reset connection dari server
# =========================================
SEGMENT_PARALLEL = 16   # -j aria2c, jumlah file .ts diunduh bersamaan
SEGMENT_CONN     = 1    # -x/-s per file — gak perlu, file kecil

POLL_INTERVAL    = 1.0  # detik, jeda tiap siklus cek progress
ETA_WINDOW_SEC   = 10   # lebar sliding-window buat hitung rate/ETA
MAX_FAIL_SEC     = 60   # detik tanpa progress sama sekali -> anggap macet
SAVE_INTERVAL    = 3    # detik, jeda antar simpan session snapshot


# =========================================
# PUBLIC — dipanggil dari core/tbv_scheduler.py
# =========================================

def download_tbv_episode(ep, index, state, state_lock, push_event, get_proc, set_proc, **kwargs):
    """
    Handler utama mode TBV. Return True kalau sukses (sudah dipindah ke
    folder tujuan), False kalau gagal/dibatalkan.
    """
    save_fn = kwargs.get("save_fn", None)

    title    = ep.get("title", "")
    episode  = ep.get("episode", "")
    m3u8_url = ep.get("m3u8", "")
    ep_label = f"{title} - {episode}".strip(" -") or "?"

    filename    = tbv_parser.build_filename(title, episode)
    episode_dir = tbv_staging.get_episode_dir(filename)
    ep["episode_dir"] = episode_dir

    # ── 1. siapkan daftar segment ──
    out_names, aria2_input_path, error = tbv_parser.prepare_segment_list(m3u8_url, episode_dir)
    if error:
        push_event({"type": "log", "message": f"EP {ep_label}: {error}"})
        return False

    total = len(out_names)
    with state_lock:
        ep["total_segments"] = total

    fmt     = state.get("format", "mkv")
    timeout = state.get("timeout", "30")
    rate    = state.get("rate_limit", "")

    cmd = _build_aria2c_cmd(aria2_input_path, episode_dir, timeout, rate)

    # ── 2-5. jalankan + poll + strip realtime + pause/resume/cancel ──
    success = _run_aria2c(
        cmd         = cmd,
        episode_dir = episode_dir,
        out_names   = out_names,
        ep          = ep,
        ep_label    = ep_label,
        index       = index,
        state       = state,
        state_lock  = state_lock,
        push_event  = push_event,
        set_proc    = set_proc,
        save_fn     = save_fn,
    )

    if not success:
        return False

    # ── 6. remux + finalize ──
    remuxed_path = remux_episode(episode_dir, filename, fmt, push_event, ep_label)
    if not remuxed_path:
        return False

    dest_folder = state.get("download_path", "")
    ep["output_file"] = os.path.join(dest_folder, f"{filename}.{fmt}")

    return finalize_episode(episode_dir, remuxed_path, dest_folder, push_event, ep_label)


# =========================================
# ARIA2C COMMAND BUILDER
# =========================================

def _build_aria2c_cmd(aria2_input_path, episode_dir, timeout, rate):
    aria2c_bin = shutil.which("aria2c") or "aria2c"

    cmd = [
        aria2c_bin,
        f"--input-file={aria2_input_path}",
        f"--dir={episode_dir}",
        f"-j{SEGMENT_PARALLEL}",
        f"-x{SEGMENT_CONN}",
        f"-s{SEGMENT_CONN}",
        "--auto-file-renaming=false",
        "--continue=true",
        "--allow-overwrite=true",
        f"--timeout={timeout}",
        "--max-tries=5",
        "--retry-wait=3",
    ]

    if rate:
        # limit TOTAL bandwidth episode ini (bukan per-file) — karena
        # yang jalan bersamaan banyak file kecil, bukan 1 file lewat
        # banyak koneksi seperti mode manual/multi.
        cmd.append(f"--max-overall-download-limit={rate}")

    return cmd


# =========================================
# PROGRESS TRACKING
# =========================================

def _count_newly_done(episode_dir, out_names, processed):
    """
    Cek segment yang file-nya sudah ada & kontrol .aria2 sudah hilang
    (berarti transfer selesai), TAPI belum ada di `processed`.
    Return list nama file yang baru saja selesai kali ini.
    """
    newly_done = []
    for name in out_names:
        if name in processed:
            continue
        target  = os.path.join(episode_dir, name)
        control = target + ".aria2"
        if os.path.exists(target) and not os.path.exists(control):
            newly_done.append(name)
    return newly_done


def _calc_eta(window, total, done):
    """
    ETA dari sliding-window (rate = Δdone/Δwaktu dalam ETA_WINDOW_SEC
    terakhir). Return "" kalau sudah selesai, "--:--" kalau belum bisa
    dihitung (window kosong/rate 0), atau string "MM:SS"/"H:MM:SS".
    """
    if done >= total:
        return ""
    if len(window) < 2:
        return "--:--"

    t_old, d_old = window[0]
    t_new, d_new = window[-1]
    delta_t = t_new - t_old
    delta_d = d_new - d_old

    if delta_t <= 0 or delta_d <= 0:
        return "--:--"

    rate      = delta_d / delta_t
    remaining = total - done
    return format_eta(remaining / rate)


# =========================================
# RUN + POLL LOOP (core)
# =========================================

def _run_aria2c(cmd, episode_dir, out_names, ep, ep_label, index, state, state_lock, push_event, set_proc, save_fn=None):
    """
    Jalankan aria2c, poll tiap POLL_INTERVAL detik, strip segment
    realtime, laporkan progress/eta, dan tangani pause/resume/cancel.
    Return True kalau SEMUA segment berhasil didownload & di-strip.
    """
    total      = len(out_names)
    processed  = set()
    window     = []     # list (timestamp, done_count) buat _calc_eta
    fail_start = None
    last_save  = time.time()

    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        set_proc(proc)

        while True:
            time.sleep(POLL_INTERVAL)

            newly_done = _count_newly_done(episode_dir, out_names, processed)
            for name in newly_done:
                path   = os.path.join(episode_dir, name)
                status = strip_segment(path)
                if status in ("failed", "error"):
                    push_event({
                        "type": "log",
                        "message": f"EP {ep_label}: gagal proses {name} ({status}).",
                    })
                processed.add(name)

            done = len(processed)
            now  = time.time()
            window.append((now, done))
            window[:] = [(t, d) for t, d in window if now - t <= ETA_WINDOW_SEC]

            percent = round((done / total) * 100, 1) if total else 0.0
            eta_str = _calc_eta(window, total, done)

            with state_lock:
                ep["progress"] = percent
                ep["segment"]  = done
                ep["eta"]      = eta_str

            push_event({
                "type":           "progress",
                "index":          index,
                "progress":       percent,
                "segment":        done,
                "total_segments": total,
                "eta":            eta_str,
            })

            if now - last_save >= SAVE_INTERVAL:
                if save_fn:
                    save_fn()
                last_save = now

            proc_exited = proc.poll() is not None

            if proc_exited:
                # tangkap sisa segment yang mungkin baru kelar pas proc exit
                for name in _count_newly_done(episode_dir, out_names, processed):
                    strip_segment(os.path.join(episode_dir, name))
                    processed.add(name)
                break

            with state_lock:
                paused    = state["paused"]
                cancelled = state["cancelled"]

            if cancelled:
                proc.terminate()
                set_proc(None)
                return False

            if paused:
                proc.terminate()
                push_event({"type": "status", "status": "paused"})

                while state["paused"] and not state["cancelled"]:
                    time.sleep(0.3)

                if state["cancelled"]:
                    set_proc(None)
                    return False

                # resume: rerun command yang sama — aria2c --continue=true
                # otomatis lanjut dari segment yang belum selesai.
                proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                set_proc(proc)
                fail_start = None
                continue

            # deteksi macet total (tidak ada segment baru sama sekali)
            if not newly_done:
                if fail_start is None:
                    fail_start = now
                elif now - fail_start >= MAX_FAIL_SEC:
                    if not is_connected():
                        push_event({"type": "status", "status": "no_connection"})
                        proc.terminate()
                        with state_lock:
                            state["paused"] = True
                        while state["paused"] and not state["cancelled"]:
                            time.sleep(1)
                        if state["cancelled"]:
                            set_proc(None)
                            return False
                        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        set_proc(proc)
                        fail_start = None
                    else:
                        proc.terminate()
                        set_proc(None)
                        push_event({
                            "type": "log",
                            "message": f"EP {ep_label}: macet {MAX_FAIL_SEC} detik tanpa progress, skip.",
                        })
                        return False
            else:
                fail_start = None

        set_proc(None)

        if len(processed) < total:
            push_event({
                "type": "log",
                "message": f"EP {ep_label}: berhenti dengan {len(processed)}/{total} segment saja, dianggap gagal.",
            })
            return False

        return True

    except Exception as e:
        push_event({"type": "log", "message": f"EP {ep_label}: download error — {e}"})
        set_proc(None)
        return False
