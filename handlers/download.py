#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/download.py
Version: v0.02

Handler untuk mode manual & multi (mp4/m3u8/HLS).
Berisi: download_episode, manifest trim HLS, subtitle downloader.

Perubahan dari v0.01: tambah dukungan field "referer" opsional per
episode (mode Referer, numpang menu m3u8/mp4 — lihat diskusi desain,
disepakati TIDAK perlu menu/parser terpisah karena semua kasus referer
yang ditemui sejauh ini link-nya .mp4, yt-dlp sudah native support lewat
--referer). Kalau field kosong/tidak ada, behavior persis seperti
sebelumnya (flag tidak ditambahkan sama sekali).

Port dari modules/download.py. Perubahan dari versi lama:
  - dl_path (folder tujuan akhir) TIDAK lagi dipakai langsung di sini.
    File ditulis ke folder tmp staging per-episode (core/staging.py),
    baru dipindah ke folder tujuan akhir belakangan oleh
    handlers/post_process.py setelah remux & subtitle inject selesai.
  - Import helper parsing-progress pindah ke core/dl_utils.py.
  - Import sanitize/timestamp_to_sec pindah ke core/config_parser.py.

Catatan: fungsi cleanup_tmp_manifests() di file ini urusannya BEDA dari
core/staging.py — ini soal file .m3u8 sementara hasil trim durasi (pakai
tempfile sistem), bukan folder tmp staging tempat hasil download ditaruh.
Jangan tertukar walau sama-sama nama "tmp".
"""

import os
import json
import time
import tempfile
import subprocess

import requests

from core.config_parser import sanitize, timestamp_to_sec
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

# file manifest temp (trim durasi) yang perlu dibersihkan setelah selesai
_tmp_manifests = []


# =========================================
# PUBLIC — dipanggil dari core/scheduler.py
# =========================================

def download_episode(ep, index, state, state_lock, push_event, get_proc, set_proc, **kwargs):
    """
    Handler utama untuk mode manual & multi.
    Return True kalau sukses, False kalau gagal/skip.

    File ditulis ke folder tmp staging (core/staging.py), BUKAN langsung
    ke folder tujuan akhir — pemindahan ke folder tujuan terjadi belakangan
    di handlers/post_process.py setelah remux & subtitle inject selesai.
    """
    save_fn = kwargs.get("save_fn", None)
    anime   = sanitize(state["anime"])
    conn    = state["conn"]
    timeout = state["timeout"]
    fmt     = state["format"]
    rate    = state.get("rate_limit", "")
    referer = ep.get("referer", "")

    filename    = f"{anime}-{ep['ep']}"
    episode_dir = staging.get_episode_dir(filename)

    output_template = os.path.join(episode_dir, filename + ".%(ext)s")

    # catat expected output path & folder tmp-nya ke ep — dipakai session
    # & post_process nanti buat tau di mana hasil akhir & dari mana mau
    # dipindah ke folder tujuan
    ep["output_file"] = os.path.join(episode_dir, filename + ".mp4")
    ep["episode_dir"] = episode_dir

    # ── trim manifest HLS kalau perlu ──
    duration_mode = ep.get("duration", "off")
    custom_range  = ep.get("duration_custom", "")
    tmp_manifest  = None

    if duration_mode not in ("off", ""):
        tmp_manifest = _build_trimmed_manifest(
            m3u8_url      = ep["link"],
            duration_mode = duration_mode,
            custom_range  = custom_range if duration_mode == "custom" else None,
            push_event    = push_event,
        )

    dl_url      = f"file://{tmp_manifest}" if tmp_manifest else ep["link"]
    extra_flags = ["--enable-file-urls"] if tmp_manifest else []

    # ── bangun command yt-dlp ──
    cmd = [
        "yt-dlp",
        "--no-colors",
        "--newline",
        "--no-warnings",
        "--downloader", "aria2c",
        "--downloader-args", build_aria2c_args(conn, timeout),
        "--concurrent-fragments", conn,
        "--fragment-retries", "infinite",
        "--merge-output-format", fmt,
        "-o", output_template,
    ] + extra_flags

    if rate:
        cmd += ["--limit-rate", rate]

    if referer:
        # BARU — mode Referer (numpang menu m3u8/mp4, bukan menu terpisah).
        # yt-dlp nerapin --referer SERAGAM ke semua request (manifest
        # maupun tiap segment) — tidak ada cara pisahin keduanya lewat
        # yt-dlp. Cukup untuk kasus mp4 (1 request); kasus m3u8 yang
        # butuh referer beda antara fetch-manifest vs download-segment
        # BELUM didukung di sini, perlu parser custom terpisah kalau
        # nanti dibutuhkan (lihat diskusi desain menu Referer).
        cmd += ["--referer", referer]

    cmd.append(dl_url)

    # ── jalankan download ──
    success = _run_download(
        cmd        = cmd,
        ep         = ep,
        index      = index,
        state      = state,
        state_lock = state_lock,
        push_event = push_event,
        set_proc   = set_proc,
        save_fn    = save_fn,
    )

    # ── download subtitle ──
    if success and ep.get("sub"):
        _download_subtitle(ep["sub"], episode_dir, filename, push_event)

    return success


def cleanup_tmp_manifests():
    """
    Hapus semua file manifest .m3u8 sementara hasil trim durasi.
    BEDA dari core/staging.py — ini bukan folder tmp hasil download,
    cuma file manifest kecil buat keperluan trim. Dipanggil dari
    scheduler saat semua slot selesai/cancel.
    """
    global _tmp_manifests
    for path in _tmp_manifests:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    _tmp_manifests = []


# =========================================
# MANIFEST TRIM HLS  (tidak berubah dari versi lama)
# =========================================

def _build_trimmed_manifest(m3u8_url, duration_mode, custom_range, push_event):
    """
    Buat manifest HLS yang dipotong sesuai mode.
    Return path file tmp (.m3u8) atau None kalau gagal / mode off.

    duration_mode:
        off         -> None (download penuh)
        half_first  -> 50% pertama
        half_second -> 50% kedua
        custom      -> pakai custom_range "HH:MM:SS-HH:MM:SS"
    """
    d = (duration_mode or "off").strip().lower()
    if d == "off":
        return None

    try:
        manifest_text, _, _ = _fetch_manifest(m3u8_url, push_event)
        if not manifest_text:
            push_event({"type": "log", "message": "Gagal fetch manifest, download full."})
            return None

        header_lines, segments = _parse_segments(manifest_text)
        if not segments:
            return None

        total_dur = sum(s["duration"] for s in segments)
        mid       = total_dur / 2

        if d == "half_first":
            acc = 0
            cut = len(segments)
            for idx, s in enumerate(segments):
                acc += s["duration"]
                if acc >= mid:
                    cut = idx + 1
                    break
            selected  = segments[:cut]
            seq_start = 0

        elif d == "half_second":
            acc = 0
            cut = 0
            for idx, s in enumerate(segments):
                acc += s["duration"]
                if acc >= mid:
                    cut = idx
                    break
            selected  = segments[cut:]
            seq_start = cut

        elif d == "custom" and custom_range:
            parts     = custom_range.split("-")
            start_sec = timestamp_to_sec(parts[0].strip()) if len(parts) > 0 else 0
            end_sec   = timestamp_to_sec(parts[1].strip()) if len(parts) > 1 else total_dur

            acc       = 0
            start_idx = 0
            end_idx   = len(segments)
            for idx, s in enumerate(segments):
                prev = acc
                acc += s["duration"]
                if prev <= start_sec < acc:
                    start_idx = idx
                if prev <= end_sec < acc:
                    end_idx = idx + 1
                    break

            selected  = segments[start_idx:end_idx]
            seq_start = start_idx

        else:
            return None

        if not selected:
            return None

        new_manifest = _build_manifest(header_lines, selected, seq_start)

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".m3u8", delete=False,
            dir=tempfile.gettempdir()
        )
        tmp.write(new_manifest)
        tmp.close()

        global _tmp_manifests
        _tmp_manifests.append(tmp.name)

        sel_dur = sum(s["duration"] for s in selected)
        push_event({"type": "log", "message":
            f"Trim: {len(selected)} segment (~{sel_dur/60:.1f} menit)"})

        return tmp.name

    except Exception as e:
        push_event({"type": "log", "message": f"Trim error: {e}, download full."})
        return None


def _fetch_manifest(m3u8_url, push_event):
    """Fetch manifest m3u8 via yt-dlp (untuk dapat header yang benar)."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--no-warnings", "--quiet", m3u8_url],
            capture_output=True, text=True, timeout=30
        )
        if not result.stdout.strip():
            return None, None, None

        data         = json.loads(result.stdout)
        headers      = data.get("http_headers", {})
        manifest_url = data["formats"][0]["url"]

        r = requests.get(manifest_url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None, None, None

        return r.text, headers, manifest_url

    except Exception as e:
        push_event({"type": "log", "message": f"Fetch manifest error: {e}"})
        return None, None, None


def _parse_segments(manifest_text):
    """Parse manifest m3u8 jadi header_lines dan list segment."""
    lines        = manifest_text.splitlines()
    header_lines = []
    segments     = []
    i            = 0

    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXTINF:"):
            duration  = float(line.split(":")[1].rstrip(","))
            byterange = ""
            tsurl     = ""
            if i + 1 < len(lines) and lines[i + 1].startswith("#EXT-X-BYTERANGE:"):
                byterange = lines[i + 1]
                tsurl     = lines[i + 2] if i + 2 < len(lines) else ""
                i += 3
            else:
                tsurl = lines[i + 1] if i + 1 < len(lines) else ""
                i += 2
            segments.append({
                "extinf":    line,
                "byterange": byterange,
                "url":       tsurl,
                "duration":  duration,
            })
        else:
            if not segments:
                header_lines.append(line)
            i += 1

    return header_lines, segments


def _build_manifest(header_lines, segments, seq_start):
    """Rakit manifest baru dari header + segments yang sudah dipotong."""
    out = ""
    for line in header_lines:
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            out += f"#EXT-X-MEDIA-SEQUENCE:{seq_start}\n"
        else:
            out += line + "\n"

    for s in segments:
        out += s["extinf"] + "\n"
        if s["byterange"]:
            out += s["byterange"] + "\n"
        out += s["url"] + "\n"

    out += "#EXT-X-ENDLIST\n"
    return out


# =========================================
# RUN DOWNLOAD (core)  — tidak berubah dari versi lama
# =========================================

def _run_download(cmd, ep, index, state, state_lock, push_event, set_proc, save_fn=None):
    """
    Jalankan subprocess yt-dlp, parse output real-time.
    Handle pause, cancel, retry, no-connection.
    Return True kalau sukses.
    """
    MAX_FAIL_SEC = 60
    fail_start   = None

    SAVE_INTERVAL = 3
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
                fail_start       = None

                with state_lock:
                    ep["total_segments"] = tot_seg
                    if cur_seg > 0:
                        pct_from_seg     = round((cur_seg / tot_seg) * 100, 1) if tot_seg > 0 else 0
                        ep["segment"]    = cur_seg
                        ep["progress"]   = pct_from_seg
                        if speed: ep["speed"] = speed
                        if eta:   ep["eta"]   = eta
                        push_event({
                            "type":           "progress",
                            "index":          index,
                            "progress":       pct_from_seg,
                            "segment":        cur_seg,
                            "total_segments": tot_seg,
                            "speed":          speed or ep.get("speed", ""),
                            "eta":            eta   or ep.get("eta",   ""),
                        })
                    else:
                        push_event({
                            "type":           "progress",
                            "index":          index,
                            "progress":       ep.get("progress", 0),
                            "segment":        ep.get("segment", 0),
                            "total_segments": tot_seg,
                            "speed":          ep.get("speed", ""),
                            "eta":            ep.get("eta",   ""),
                        })

            elif pct is not None:
                fail_start = None
                with state_lock:
                    ep["progress"] = pct
                    if speed: ep["speed"] = speed
                    if eta:   ep["eta"]   = eta

                push_event({
                    "type":     "progress",
                    "index":    index,
                    "progress": pct,
                    "speed":    speed or ep.get("speed", ""),
                    "eta":      eta   or ep.get("eta",   ""),
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
                            "message": f"EP {ep['ep']} gagal 1 menit, skip."})
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

        proc.wait()
        set_proc(None)

        if conn_max_seen == 1 and not conn_notified:
            push_event({
                "type": "log",
                "message": "[conn] Server sepertinya tidak mendukung multi-koneksi (cuma 1 aktif)",
            })

        return proc.returncode == 0

    except Exception as e:
        push_event({"type": "log", "message": f"Download error: {e}"})
        return False


# =========================================
# SUBTITLE  — parameter dl_path diganti jadi episode_dir (folder tmp),
# bukan folder tujuan akhir. Logic download tidak berubah.
# =========================================

def _download_subtitle(url, episode_dir, filename, push_event):
    """Download file subtitle .srt ke folder tmp episode. Retry 5x dengan jeda 3 detik."""
    subtitle_path = os.path.join(episode_dir, filename + ".srt")
    for attempt in range(1, 6):
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            with open(subtitle_path, "w", encoding="utf-8") as f:
                f.write(r.text)
            push_event({"type": "log", "message": "Subtitle OK"})
            return
        except Exception as e:
            if attempt == 5:
                push_event({"type": "log", "message": f"Subtitle gagal: {e}"})
            else:
                time.sleep(3)
