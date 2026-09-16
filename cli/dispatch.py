#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/dispatch.py
Version: v0.01

Jembatan antara argumen CLI (cli/cli_args.py) & logic build-queue yang
SUDAH ADA di cli/views/*. TIDAK ADA logic download baru di sini — cuma
translate Namespace hasil argparse jadi overrides dict + queue, lalu
panggil reset_state()/start_worker() yang SAMA PERSIS dipakai menu
interaktif. Beda dari view interaktif: tidak ada console.input() sama
sekali, dan nunggu selesainya pakai cli/headless.py (bukan
control_view.py/tbv_control_view.py yang butuh raw-mode terminal).

STATUS: SEMUA subcommand (manual/multi/tbv/other/history/status) sudah
diimplementasi penuh, dibangun bertahap satu-persatu (lihat riwayat
diskusi desain Lapisan A) — tiap subcommand diuji dulu sebelum lanjut
ke berikutnya, bukan ditulis sekaligus.
"""

import os

from core.state import reset_state as reset_state_old, get_state as get_state_old
from core import scheduler
from core.config_parser import sanitize, parse_config_content, extract_referer
from core.tbv_state import reset_state as reset_state_tbv, get_state as get_state_tbv
from core import tbv_scheduler, tbv_parser
from cli import headless, notifier
from cli.theme import console, safe


def run(args):
    """Entry point dipanggil main.py setelah argparse berhasil parse."""
    handler = _HANDLERS.get(args.command)
    if handler is None:
        console.print(f"[error][x][/error] Perintah tidak dikenal: {args.command}")
        return
    handler(args)


# =========================================
# MANUAL — implementasi penuh
# =========================================

def _handle_manual(args):
    naming = _guess_name_from_link(args.link)

    ep = {
        "ep":             "01",
        "link":           args.link,
        "sub":            args.sub or "",
        "referer":        args.referer or "",
        "duration":       "off",
        "status":         "waiting",
        "progress":       0,
        "segment":        0,
        "total_segments": 0,
        "speed":          "",
        "eta":            "",
    }

    reset_state_old(overrides={
        "anime":            naming,
        "download_path":    args.download_path,
        "input_batch_path": "",
        "format":           args.format,
        "timeout":          args.timeout,
        "conn":             args.conn,
        "parallel":         1,   # manual selalu 1 episode, sama seperti mode interaktif
        "rate_limit":       args.rate_limit,
        "mode":             "manual",
        "queue":            [ep],
        "current_index":    0,
        "status":           "downloading",
    })

    scheduler.start_worker()
    _wait_and_report_old()


def _guess_name_from_link(link: str) -> str:
    """
    Mode interaktif minta user ketik nama file; headless gak ada prompt,
    jadi nama diambil otomatis dari bagian terakhir URL (tanpa ekstensi),
    di-sanitize. Fallback "download" kalau hasilnya kosong.
    """
    tail = link.rstrip("/").rsplit("/", 1)[-1]
    tail = tail.split("?", 1)[0]          # buang query string kalau ada
    tail = tail.rsplit(".", 1)[0] if "." in tail else tail
    return sanitize(tail) or "download"


# =========================================
# MULTI — implementasi penuh
# =========================================

def _handle_multi(args):
    full_path = args.file
    if not os.path.isfile(full_path):
        console.print(f"[error][x][/error] File tidak ditemukan: {safe(full_path)}")
        return

    filetype = "json" if full_path.lower().endswith(".json") else "txt"

    try:
        with open(full_path, "r", encoding="utf-8") as f:
            content = f.read()
        anime_title, episodes = parse_config_content(content, filetype)
        referer               = extract_referer(content, filetype)
    except Exception as e:
        console.print(f"[error][x][/error] Gagal parse file: {safe(e)}")
        return

    if not episodes:
        console.print("[warning][![/warning] Tidak ada episode terbaca dari file ini.")
        return

    if not anime_title.strip():
        # Headless -- tidak ada prompt kalau title kosong di file (beda
        # dari mode interaktif yang nanya lewat console.input()).
        # Fallback ke nama file itu sendiri (tanpa ekstensi).
        anime_title = os.path.splitext(os.path.basename(full_path))[0]

    # mode Referer -- sama seperti cli/views/multi_view.py: cuma aktif
    # kalau field "referer" di json terisi.
    if referer:
        for ep in episodes:
            ep["referer"] = referer
        console.print(f"[muted][*][/muted] Mode Referer aktif: {safe(referer)}")

    reset_state_old(overrides={
        "anime":            sanitize(anime_title),
        "download_path":    args.download_path,
        "input_batch_path": os.path.dirname(full_path),
        "config_file":      full_path,
        "format":           args.format,
        "timeout":          args.timeout,
        "conn":             args.conn,
        "parallel":         args.parallel,
        "rate_limit":       args.rate_limit,
        "mode":             "multi",
        "queue":            episodes,
        "current_index":    0,
        "status":           "downloading",
    })

    console.print(f"[success][+][/success] {len(episodes)} episode dimuat dari \"{safe(os.path.basename(full_path))}\".")
    scheduler.start_worker()
    _wait_and_report_old()


# =========================================
# TBV — implementasi penuh
# =========================================

def _handle_tbv(args):
    full_path = args.file
    if not os.path.isfile(full_path):
        console.print(f"[error][x][/error] File tidak ditemukan: {safe(full_path)}")
        return

    data, error = tbv_parser.load_batch_json(full_path)
    if error:
        console.print(f"[error][x][/error] {safe(error)}")
        return

    queue = tbv_parser.build_queue(data)
    if not queue:
        console.print("[warning][![/warning] Tidak ada episode valid (link m3u8 kosong?) di file ini.")
        return

    title = data.get("title", "") or "(tanpa judul)"

    reset_state_tbv(overrides={
        "title":            data.get("title", ""),
        "batch_file":       full_path,
        "download_path":    args.download_path,
        "input_batch_path": os.path.dirname(full_path),
        "format":           args.format,
        "timeout":          args.timeout,
        "rate_limit":       args.rate_limit,
        "parallel":         args.parallel,
        "queue":            queue,
        "current_index":    0,
        "status":           "downloading",
    })

    console.print(
        f"[success][+][/success] \"{safe(title)}\" — {len(queue)} episode "
        f"dimuat dari \"{safe(os.path.basename(full_path))}\"."
    )
    tbv_scheduler.start_worker()
    _wait_and_report_tbv()


# =========================================
# OTHER — implementasi penuh
# =========================================

def _handle_other(args):
    if not getattr(args, "other_mode", None):
        console.print("[error][x][/error] Pilih 'video' atau 'audio'. Lihat: dm-cli other --help")
        return

    if args.other_mode == "video":
        ep = {
            "ep":             "",
            "link":           args.link,
            "sub":            "",
            "mp3_only":       False,
            "quality":        args.quality,
            "sub_auto":       args.sub_auto,
            "playlist":       args.playlist,
            "status":         "waiting",
            "progress":       0,
            "segment":        0,
            "total_segments": 0,
            "speed":          "",
            "eta":            "",
        }
    else:  # audio
        ep = {
            "ep":             "",
            "link":           args.link,
            "sub":            "",
            "mp3_only":       True,
            "quality":        args.quality,
            "sub_auto":       False,
            "playlist":       False,
            "status":         "waiting",
            "progress":       0,
            "segment":        0,
            "total_segments": 0,
            "speed":          "",
            "eta":            "",
        }

    reset_state_old(overrides={
        "anime":            "Downloads",
        "download_path":    args.download_path,
        "input_batch_path": "",
        "config_file":      "",
        "timeout":          args.timeout,
        "conn":             args.conn,
        "parallel":         1,   # headless "other" selalu 1 link, sama seperti mode interaktif
        "rate_limit":       args.rate_limit,
        "mode":             "other",
        "queue":            [ep],
        "current_index":    0,
        "status":           "downloading",
    })

    scheduler.start_worker()
    _wait_and_report_old()


# =========================================
# HISTORY — implementasi penuh (baca-saja, resiko rendah)
# =========================================

def _handle_history(args):
    from core import history

    entries = history.load_recent(args.limit)
    if not entries:
        console.print("[muted][*][/muted] Belum ada riwayat download.")
        return

    for i, e in enumerate(entries, start=1):
        console.print(
            f"{i}. {safe(e.get('anime', '-'))} EP{e.get('ep', '-')} — "
            f"{safe(e.get('file', '-'))} ({e.get('time', '-')})"
        )


# =========================================
# STATUS — implementasi penuh (baca-saja, resiko rendah)
# =========================================

def _handle_status(args):
    import json as _json
    from core import session as session_mod
    from core import tbv_session as tbv_session_mod

    data     = session_mod.load_session()
    tbv_data = tbv_session_mod.load_session()

    if args.json:
        result = {
            "manual_multi_other": _summarize(data, title_key="anime") if data else None,
            "tbv":                _summarize(tbv_data, title_key="title") if tbv_data else None,
        }
        print(_json.dumps(result))
        return

    if not data and not tbv_data:
        console.print("[muted][*][/muted] Tidak ada sesi download yang sedang berjalan.")
        return

    if data:
        console.print(
            f"[header]Manual/Multi/Other[/header]: \"{safe(data.get('anime', '?'))}\" "
            f"— {len(data.get('queue', []))} episode"
        )
    if tbv_data:
        console.print(
            f"[header]TurboVIP[/header]: \"{safe(tbv_data.get('title', '?'))}\" "
            f"— {len(tbv_data.get('queue', []))} episode"
        )


def _summarize(data: dict, title_key: str) -> dict:
    queue = data.get("queue", [])
    return {
        "title":         data.get(title_key, ""),
        "total_episode": len(queue),
        "done":          sum(1 for ep in queue if ep.get("status") == "done"),
        "failed":        sum(1 for ep in queue if ep.get("status") == "failed"),
    }


# =========================================
# HELPER — tunggu worker pipeline lama (manual/multi/other)
# =========================================

def _wait_and_report_old():
    headless.wait_headless(
        is_worker_alive_fn   = scheduler.is_worker_alive,
        progress_snapshot_fn = notifier.progress_snapshot,
        queue_getter_fn      = lambda: get_state_old()["queue"],
    )
    state  = get_state_old()
    failed = [ep for ep in state["queue"] if ep.get("status") == "failed"]

    if failed:
        console.print(f"[warning][![/warning] {len(failed)} episode gagal. Jalankan ulang command kalau perlu.")
    else:
        console.print("[success][+][/success] Selesai.")


def _wait_and_report_tbv():
    headless.wait_headless(
        is_worker_alive_fn   = tbv_scheduler.is_worker_alive,
        progress_snapshot_fn = notifier.progress_snapshot,
        queue_getter_fn      = lambda: get_state_tbv()["queue"],
    )
    state  = get_state_tbv()
    failed = [ep for ep in state["queue"] if ep.get("status") == "failed"]

    if failed:
        console.print(f"[warning][![/warning] {len(failed)} episode gagal. Jalankan ulang command kalau perlu.")
    else:
        console.print("[success][+][/success] Selesai.")


_HANDLERS = {
    "manual":  _handle_manual,
    "multi":   _handle_multi,
    "tbv":     _handle_tbv,
    "other":   _handle_other,
    "history": _handle_history,
    "status":  _handle_status,
}
