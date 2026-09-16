#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/other.py
Version: v0.01

Handler untuk mode Other — download dari platform apapun yang didukung
yt-dlp (YouTube, TikTok, Instagram, Facebook, Twitter/X, dll).

Port dari modules/other.py. Perubahan dari versi lama:
  - dl_path (folder tujuan akhir) TIDAK lagi dipakai langsung di sini,
    sama seperti handlers/download.py — file ditulis ke folder tmp
    staging, dipindah belakangan oleh handlers/post_process.py.
  - Karena desain menu Other di CLI TIDAK punya langkah input naming
    (Audio Single/Multi maupun Video semua auto dari judul yt-dlp),
    ep["ep"] praktis selalu kosong -> nama file belum diketahui sebelum
    download selesai. Folder tmp dikunci pakai index episode untuk
    kasus ini. Cabang "label diisi" tetap dipertahankan untuk jaga-jaga
    kalau nanti fitur naming ditambah ke menu Other.

Catatan (belum diputuskan, lihat dokumen desain bag. 10): _run_download()
di file ini nyaris identik dengan yang di handlers/download.py —
duplikasi ini SENGAJA dipertahankan dulu, konsolidasi belum diputuskan.
"""

import os
import time
import subprocess

from core.config_parser import sanitize
from core.dl_utils import (
    parse_percent,
    parse_segment,
    parse_speed,
    parse_eta,
    is_connected,
    build_aria2c_args,
    parse_aria2c_conn,
)
from core import staging

# =========================================
# QUALITY MAP
# =========================================

QUALITY_MAP = {
    "best":  "bestvideo+bestaudio/best",
    "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "720p":  "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "480p":  "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "360p":  "bestvideo[height<=360]+bestaudio/best[height<=360]",
    "240p":  "bestvideo[height<=240]+bestaudio/best[height<=240]",
}

# kualitas audio MP3 -> yt-dlp --audio-quality value (VBR)
MP3_QUALITY_MAP = {
    "320k": "0",   # VBR terbaik (~320k)
    "192k": "5",   # VBR ~192k
    "128k": "7",   # VBR ~128k
}


# =========================================
# PUBLIC — dipanggil dari core/scheduler.py
# =========================================

def download_other(ep, index, state, state_lock, push_event, get_proc, set_proc, **kwargs):
    """
    Handler mode Other.
    ep fields:
      link      : URL platform
      ep        : label/nama file (praktis selalu kosong di desain CLI
                  saat ini — kosong = pakai judul asli dari platform)
      quality   : best | 1080p | 720p | 480p | 360p | 320k | 192k | 128k
      mp3_only  : True / False
      sub_auto  : True / False
      playlist  : True / False
    Return True kalau sukses.
    """
    save_fn = kwargs.get("save_fn", None)
    rate    = state.get("rate_limit", "")
    conn    = state.get("conn", "8")
    timeout = state.get("timeout", "30")

    label = (ep.get("ep") or "").strip()

    if label:
        filename          = sanitize(label)
        episode_dir        = staging.get_episode_dir(filename)
        output_template     = os.path.join(episode_dir, filename + ".%(ext)s")
        ep["output_file"]    = os.path.join(episode_dir, filename + ".mp4")
    else:
        # label kosong -> nama file belum diketahui sampai yt-dlp selesai
        # (auto dari judul asli), jadi folder tmp dikunci ke index episode
        episode_dir = staging.get_episode_dir(f"other_{index}")
        output_template = os.path.join(episode_dir, "%(title)s.%(ext)s")

    ep["episode_dir"] = episode_dir

    mp3_only = ep.get("mp3_only", False)
    quality  = ep.get("quality",  "best")
    sub_auto = ep.get("sub_auto", False)
    playlist = ep.get("playlist", False)

    if mp3_only:
        cmd = _build_mp3_cmd(ep["link"], output_template, quality, rate, conn, timeout)
    else:
        cmd = _build_video_cmd(ep["link"], output_template, quality, sub_auto, playlist, rate, conn, timeout)

    return _run_download(
        cmd        = cmd,
        ep         = ep,
        index      = index,
        state      = state,
        state_lock = state_lock,
        push_event = push_event,
        set_proc   = set_proc,
        save_fn    = save_fn,
    )


# =========================================
# COMMAND BUILDER  — tidak berubah dari versi lama
# =========================================

def _build_video_cmd(url, output_template, quality, sub_auto, playlist, rate, conn, timeout):
    """Bangun command yt-dlp untuk download video."""
    fmt_sel = QUALITY_MAP.get(quality, QUALITY_MAP["best"])

    cmd = [
        "yt-dlp",
        "--no-colors",
        "--newline",
        "--no-warnings",
        "-f", fmt_sel,
        "--merge-output-format", "mp4",
        "--downloader", "aria2c",
        "--downloader-args", build_aria2c_args(conn, timeout),
        "--concurrent-fragments", str(conn),
        "--fragment-retries", "infinite",
        "-o", output_template,
    ]

    if sub_auto:
        cmd += [
            "--write-auto-sub",
            "--sub-lang", "id,en",
            "--convert-subs", "srt",
        ]

    if not playlist:
        cmd += ["--no-playlist"]

    if rate:
        cmd += ["--limit-rate", rate]

    cmd.append(url)
    return cmd


def _build_mp3_cmd(url, output_template, quality, rate, conn, timeout):
    """Bangun command yt-dlp untuk extract audio -> MP3."""
    audio_q = MP3_QUALITY_MAP.get(quality, "0")

    cmd = [
        "yt-dlp",
        "--no-colors",
        "--newline",
        "--no-warnings",
        "--extract-audio",
        "--audio-format",  "mp3",
        "--audio-quality", audio_q,
        "--embed-thumbnail",
        "--add-metadata",
        "--no-playlist",
        "--downloader", "aria2c",
        "--downloader-args", build_aria2c_args(conn, timeout),
        "--concurrent-fragments", str(conn),
        "--fragment-retries", "infinite",
        "-o", output_template,
    ]

    if rate:
        cmd += ["--limit-rate", rate]

    cmd.append(url)
    return cmd


# =========================================
# RUN DOWNLOAD  — tidak berubah dari versi lama (lihat catatan
# duplikasi di docstring atas file)
# =========================================

def _run_download(cmd, ep, index, state, state_lock, push_event, set_proc, save_fn=None):
    """Jalankan subprocess yt-dlp, parse output real-time."""
    MAX_FAIL_SEC  = 60
    SAVE_INTERVAL = 3
    fail_start    = None
    last_save     = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        set_proc(proc)

        conn_notified = False
        conn_max_seen = 0

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue

            seg_info = parse_segment(line)
            pct      = parse_percent(line)
            speed    = parse_speed(line)
            eta      = parse_eta(line)

            conn_dbg = parse_aria2c_conn(line)
            if conn_dbg is not None:
                _, dbg_cn, _ = conn_dbg
                if dbg_cn > conn_max_seen:
                    conn_max_seen = dbg_cn
                if not conn_notified and dbg_cn > 1:
                    push_event({
                        "type": "log",
                        "message": f"[conn] Multi-koneksi aktif ({dbg_cn}/{state.get('conn','?')})",
                    })
                    conn_notified = True

            if seg_info is not None:
                cur_seg, tot_seg = seg_info
                pct_from_seg     = round((cur_seg / tot_seg) * 100, 1) if tot_seg > 0 else 0
                fail_start       = None
                with state_lock:
                    ep["segment"]        = cur_seg
                    ep["total_segments"] = tot_seg
                    ep["progress"]       = pct_from_seg
                    if speed: ep["speed"] = speed
                    if eta:   ep["eta"]   = eta
                push_event({
                    "type": "progress", "index": index,
                    "progress": pct_from_seg, "segment": cur_seg,
                    "total_segments": tot_seg,
                    "speed": speed or ep.get("speed", ""),
                    "eta":   eta   or ep.get("eta",   ""),
                })

            elif pct is not None:
                fail_start = None
                with state_lock:
                    ep["progress"] = pct
                    if speed: ep["speed"] = speed
                    if eta:   ep["eta"]   = eta
                push_event({
                    "type": "progress", "index": index,
                    "progress": pct,
                    "speed": speed or ep.get("speed", ""),
                    "eta":   eta   or ep.get("eta",   ""),
                })

            now = time.time()
            if now - last_save >= SAVE_INTERVAL:
                if save_fn:
                    save_fn()
                last_save = now

            low = line.lower()
            if "error" in low or "retry" in low:
                if fail_start is None:
                    fail_start = time.time()
                elif time.time() - fail_start >= MAX_FAIL_SEC:
                    if not is_connected():
                        push_event({"type": "status", "status": "no_connection"})
                        with state_lock:
                            state["paused"] = True
                        while state["paused"] and not state["cancelled"]:
                            time.sleep(1)
                        if state["cancelled"]:
                            proc.terminate()
                            return False
                        fail_start = None
                    else:
                        proc.terminate()
                        push_event({"type": "log",
                            "message": f"{ep.get('ep','?')} gagal 1 menit, skip."})
                        return False

            with state_lock:
                paused    = state["paused"]
                cancelled = state["cancelled"]

            if cancelled:
                proc.terminate()
                return False

            if paused:
                proc.terminate()
                push_event({"type": "status", "status": "paused"})
                while state["paused"] and not state["cancelled"]:
                    time.sleep(0.3)
                if state["cancelled"]:
                    return False
                fail_start = None
                return _run_download(cmd, ep, index, state, state_lock, push_event, set_proc, save_fn)

        proc.wait()
        set_proc(None)

        if conn_max_seen == 1 and not conn_notified:
            push_event({
                "type": "log",
                "message": "[conn] Server sepertinya tidak mendukung multi-koneksi (cuma 1 aktif)",
            })

        return proc.returncode == 0

    except Exception as e:
        push_event({"type": "log", "message": f"Other download error: {e}"})
        return False
