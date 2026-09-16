#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/metadata.py
Version: v0.01

Ambil metadata video/audio dari URL via yt-dlp --dump-json — judul,
durasi, daftar kualitas video (dengan estimasi ukuran), estimasi ukuran
MP3 di 3 tingkat bitrate tetap.

Port dari route POST /info (get_info()) & _fmt_size() di app.py lama.
Dulu dibungkus jsonify(), sekarang return dict Python biasa — konsumen
(cli/views/other_audio_view.py, other_video_view.py) baca dict-nya
langsung tanpa perantara HTTP.
"""

import json
import subprocess


def fetch_video_info(url: str) -> dict:
    """
    Ambil metadata dari URL via yt-dlp --dump-json.

    Return dict:
      sukses -> {"ok": True, "title": str, "qualities": [...],
                 "duration": int, "mp3_sizes": {...}, "ext": str}
      gagal  -> {"ok": False, "msg": str}
    """
    url = (url or "").strip()
    if not url:
        return {"ok": False, "msg": "URL kosong"}

    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-download", "--no-warnings", "--quiet", url],
            capture_output=True, text=True, timeout=30,
        )

        if not result.stdout.strip():
            err = result.stderr.strip().splitlines()
            msg = err[-1] if err else "yt-dlp tidak menghasilkan output"
            return {"ok": False, "msg": msg}

        data     = json.loads(result.stdout)
        title    = data.get("title") or data.get("fulltitle") or ""
        duration = data.get("duration") or 0
        formats  = data.get("formats", [])

        # ── kumpulkan ukuran terbaik per height ──
        height_map = {}
        for f in formats:
            h = f.get("height")
            if not h or not isinstance(h, int) or h <= 0:
                continue
            size = f.get("filesize") or f.get("filesize_approx") or 0
            if size > height_map.get(h, 0):
                height_map[h] = size

        heights_sorted = sorted(height_map.keys(), reverse=True)

        qualities = []
        best_size = max(height_map.values()) if height_map else 0
        qualities.append({
            "val":   "best",
            "label": "Best",
            "size":  _fmt_size(best_size),
        })
        for h in heights_sorted:
            qualities.append({
                "val":   f"{h}p",
                "label": f"{h}p",
                "size":  _fmt_size(height_map[h]),
            })

        # ── estimasi ukuran MP3 dari durasi, 3 tingkat bitrate tetap ──
        mp3_sizes = {}
        if duration:
            for label, kbps in [("320k", 320), ("192k", 192), ("128k", 128)]:
                mp3_sizes[label] = _fmt_size(int(duration * kbps * 1000 / 8))
        else:
            mp3_sizes = {"320k": "?", "192k": "?", "128k": "?"}

        return {
            "ok":        True,
            "title":     title,
            "qualities": qualities,
            "duration":  duration,
            "mp3_sizes": mp3_sizes,
            "ext":       data.get("ext", "mp4"),
        }

    except subprocess.TimeoutExpired:
        return {"ok": False, "msg": "Timeout — link terlalu lama diproses"}
    except json.JSONDecodeError:
        return {"ok": False, "msg": "Gagal parse output yt-dlp"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


def _fmt_size(b) -> str:
    """Format bytes ke string yang mudah dibaca (KB/MB/GB)."""
    if not b or b <= 0:
        return "?"
    if b < 1024 ** 2:
        return f"{b/1024:.0f}KB"
    if b < 1024 ** 3:
        return f"{b/1024**2:.1f}MB"
    return f"{b/1024**3:.2f}GB"
