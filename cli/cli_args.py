#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/cli_args.py
Version: v0.01

Parser argumen buat mode headless dm-cli (Lapisan A). "dm-cli" TANPA
argumen tetap masuk menu interaktif — main.py yang cek len(sys.argv)==1
duluan SEBELUM modul ini dipanggil sama sekali, lihat main.py.

Semua flag override (--conn/--parallel/--timeout/--rate-limit/--format/
--download-path) BERLAKU SEKALI PAKAI SAJA — TIDAK ditulis balik ke
settings.json. Default tiap flag diambil dari settings.json SAAT
parser dibangun (bukan hardcode), lihat _add_common_overrides().

STATUS IMPLEMENTASI (lihat cli/dispatch.py buat detail):
  manual  -> SUDAH jalan penuh
  multi, tbv, other -> parser SUDAH lengkap (--help sudah benar), tapi
                        handler-nya masih stub "coming next" — dikerjakan
                        bertahap sesuai kesepakatan desain Lapisan A.
"""

import argparse

from core import settings


def build_parser() -> argparse.ArgumentParser:
    cfg = settings.load_settings()

    parser = argparse.ArgumentParser(
        prog="dm-cli",
        description="Anime/video downloader CLI — Termux Edition. "
                     "Tanpa argumen -> menu interaktif.",
    )
    sub = parser.add_subparsers(dest="command")

    # ---- manual ----
    p_manual = sub.add_parser(
        "manual",
        help="Download 1 link m3u8/mp4 langsung.",
        description="Download 1 link m3u8/mp4 tanpa lewat menu.",
    )
    p_manual.add_argument("link", help="URL m3u8 atau mp4.")
    p_manual.add_argument("--sub", default="", help="URL subtitle .srt (opsional).")
    p_manual.add_argument(
        "--referer", default="",
        help="Header Referer buat request (opsional — dipakai kalau situs butuh referer, mis. MovieBox).",
    )
    _add_common_overrides(p_manual, cfg)

    # ---- multi ----
    p_multi = sub.add_parser(
        "multi",
        help="Download banyak episode dari file .txt/.json.",
        description="Download batch m3u8/mp4 dari file config (format Multi).",
    )
    p_multi.add_argument("file", help="Path file .txt atau .json (format Multi).")
    _add_common_overrides(p_multi, cfg)

    # ---- tbv ----
    p_tbv = sub.add_parser(
        "tbv",
        help="Download batch TurboVIP dari file .json.",
        description="Download batch TurboVIP (segment PNG-masked, strip realtime).",
    )
    p_tbv.add_argument("file", help="Path file .json (format TurboVIP).")
    _add_common_overrides(p_tbv, cfg)

    # ---- other ----
    p_other = sub.add_parser(
        "other",
        help="Download dari platform lain (yt-dlp) — video/audio.",
    )
    other_sub = p_other.add_subparsers(dest="other_mode")

    p_other_video = other_sub.add_parser("video", help="Download video+audio.")
    p_other_video.add_argument("link", help="URL video (YouTube, TikTok, dll).")
    p_other_video.add_argument(
        "--quality", default="best",
        choices=["best", "1080p", "720p", "480p", "360p", "240p"],
        help="Kualitas video (default: best).",
    )
    p_other_video.add_argument("--playlist", action="store_true", help="Download seluruh playlist.")
    p_other_video.add_argument("--sub-auto", action="store_true", help="Sertakan auto-subtitle.")
    _add_common_overrides(p_other_video, cfg, include_format=False)

    p_other_audio = other_sub.add_parser("audio", help="Download audio only (mp3).")
    p_other_audio.add_argument("link", help="URL video/audio.")
    p_other_audio.add_argument(
        "--quality", default="320k",
        choices=["320k", "192k", "128k"],
        help="Bitrate MP3 (default: 320k).",
    )
    _add_common_overrides(p_other_audio, cfg, include_format=False)

    # ---- history ----
    p_history = sub.add_parser("history", help="Tampilkan riwayat download.")
    p_history.add_argument("--limit", type=int, default=10, help="Jumlah entri ditampilkan (default: 10).")

    # ---- status ----
    p_status = sub.add_parser("status", help="Cek status sesi download yang sedang berjalan.")
    p_status.add_argument("--json", action="store_true", help="Output machine-readable (dipakai script bridge).")

    return parser


def _add_common_overrides(p: argparse.ArgumentParser, cfg: dict, include_format: bool = True):
    """Flag override 1x-pakai; default diambil dari settings.json saat ini."""
    p.add_argument("--conn", default=cfg.get("conn"),
                    help=f"Koneksi paralel per file (default: {cfg.get('conn')}).")
    p.add_argument("--parallel", type=int, default=cfg.get("parallel"),
                    help=f"Episode paralel (default: {cfg.get('parallel')}).")
    p.add_argument("--timeout", default=cfg.get("timeout"),
                    help=f"Timeout koneksi detik (default: {cfg.get('timeout')}).")
    p.add_argument("--rate-limit", dest="rate_limit", default=cfg.get("rate_limit"),
                    help="Limit kecepatan, mis. 2M (default: dari settings).")
    p.add_argument("--download-path", dest="download_path", default=cfg.get("download_path"),
                    help="Folder tujuan (default: dari settings).")
    if include_format:
        p.add_argument("--format", default=cfg.get("format"),
                        help=f"Format output remux (default: {cfg.get('format')}).")
