#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/tbv_parser.py
Version: v0.01

Parser khusus mode TurboVIP (TBV). TIDAK pakai yt-dlp — provider TBV
menyamarkan segment .ts jadi file PNG palsu (lihat handlers/cleaner.py),
yt-dlp tidak tahu cara handle itu, jadi kita parse manifest m3u8 manual.

Port dari m3u8_downloader.py (v1) milik user. Perubahan dari versi asli:
  - fetch_m3u8()/extract_segment_urls()/write_aria2_input() TIDAK berubah
    logic-nya sama sekali — cuma dibungkus jadi fungsi yang me-raise
    exception (bukan print+sys.exit), supaya pemanggil (nanti
    core/tbv_scheduler.py) yang putuskan cara melaporkan error
    (push_event), bukan file ini yang keluar paksa dari program.
  - Ditambah load_batch_json() & build_queue() — BARU, tidak ada di versi
    asli. Ini pengganti input() manual: baca file .json yang formatnya
    {title, episode_count, episodes:[{episode, m3u8}, ...]} dari folder
    yang diset di settings "input_batch_path".

PENTING soal urutan segment (jangan diubah tanpa alasan kuat):
extract_segment_urls() HARUS mempertahankan urutan baris asli manifest
m3u8 (dari atas ke bawah), dan write_aria2_input() HARUS menuliskannya
ke file .txt dengan urutan & zero-padding yang sama. Kalau urutan ini
meleset, hasil remux (handlers/cleaner.py, concat demuxer ffmpeg, sort
berdasarkan nama file) akan rusak/acak tanpa ada error yang kelihatan.
"""

import json
import os
from urllib.parse import urljoin

import requests

from core.config_parser import sanitize

SEGMENT_PREFIX = "segment"
SEGMENT_EXT    = ".ts"
ARIA2_INPUT_NAME = "aria2_input.txt"


# =========================================
# FETCH & EXTRACT MANIFEST  — tidak berubah dari versi asli (v1)
# =========================================

def fetch_m3u8(url: str, timeout: int = 30) -> str:
    """Download isi file m3u8 sebagai teks. Raise kalau gagal."""
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def extract_segment_urls(m3u8_text: str, base_url: str) -> list:
    """
    Ambil semua url segment dari isi m3u8, URUT sesuai urutan baris asli.
    Baris yang diabaikan: baris kosong dan baris yang diawali '#' (tag).
    Url relatif otomatis di-resolve terhadap base_url m3u8.

    Raise ValueError kalau tidak ada segment ditemukan (kemungkinan URL
    yang diberikan adalah master playlist, bukan media playlist).
    """
    urls = []
    for raw_line in m3u8_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(urljoin(base_url, line))

    if not urls:
        raise ValueError(
            "Tidak ada url segment ditemukan — pastikan link adalah media "
            "playlist (.m3u8) berisi segment, bukan master playlist."
        )
    return urls


def write_aria2_input(urls: list, txt_path: str) -> list:
    """
    Tulis file .txt format input aria2c ke `txt_path`, urut dari 1 s.d.
    selesai. Nama file di-zero-pad (mis. segment0001.ts) supaya urutan
    tetap benar walau di-sort alfabetis oleh sistem/pemutar video.

    Return: list nama file output sesuai urutan (dipakai handlers/download_tbv.py
    untuk tracking progress & handlers/cleaner.py untuk collect segment).
    """
    total     = len(urls)
    pad_width = len(str(total))
    out_names = []

    with open(txt_path, "w", encoding="utf-8") as f:
        for idx, url in enumerate(urls, start=1):
            out_name = f"{SEGMENT_PREFIX}{str(idx).zfill(pad_width)}{SEGMENT_EXT}"
            out_names.append(out_name)
            f.write(f"{url}\n")
            f.write(f"    out={out_name}\n")

    return out_names


def prepare_segment_list(m3u8_url: str, episode_dir: str):
    """
    Wrapper end-to-end: fetch manifest -> extract urls -> tulis aria2
    input txt di dalam episode_dir. Dipakai scheduler, TIDAK raise —
    return (out_names, aria2_input_path, error) supaya scheduler tinggal
    push_event kalau error tanpa perlu try/except sendiri.

    Sukses -> (out_names, aria2_input_path, None)
    Gagal  -> (None, None, "pesan error")
    """
    try:
        m3u8_text = fetch_m3u8(m3u8_url)
        urls      = extract_segment_urls(m3u8_text, m3u8_url)

        aria2_input_path = os.path.join(episode_dir, ARIA2_INPUT_NAME)
        out_names         = write_aria2_input(urls, aria2_input_path)

        return out_names, aria2_input_path, None

    except requests.exceptions.RequestException as e:
        return None, None, f"Gagal fetch manifest: {e}"
    except ValueError as e:
        return None, None, str(e)
    except Exception as e:
        return None, None, f"Parser error: {e}"


# =========================================
# BATCH JSON  — BARU, gantinya input manual
# =========================================

def load_batch_json(path: str):
    """
    Baca file .json batch TBV dari `path` (biasanya hasil pilihan user
    dari folder settings["input_batch_path"]).

    Format yang diharapkan:
        {
          "title": "Nama Judul",
          "episode_count": 14,
          "episodes": [
            {"episode": 8.0, "m3u8": "https://..."},
            ...
          ]
        }

    Return (data: dict, error: str|None). Field yang hilang diisi default
    aman (title "" , episodes []) — bukan raise, biar UI bisa tampilkan
    pesan yang jelas ke user alih-alih traceback.
    """
    if not path or not os.path.isfile(path):
        return None, f"File tidak ditemukan: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        return None, f"File JSON tidak valid: {e}"
    except Exception as e:
        return None, f"Gagal baca file: {e}"

    if not isinstance(raw, dict):
        return None, "Struktur JSON tidak sesuai (harus object di root)."

    episodes_raw = raw.get("episodes", [])
    if not isinstance(episodes_raw, list) or not episodes_raw:
        return None, "Tidak ada 'episodes' di dalam file JSON."

    data = {
        "title":         str(raw.get("title", "") or "").strip(),
        "episode_count": raw.get("episode_count", len(episodes_raw)),
        "episodes":      episodes_raw,
    }
    return data, None


def build_queue(batch_data: dict) -> list:
    """
    Ubah hasil load_batch_json() jadi list episode siap-pakai buat
    core/tbv_state.py (queue). Field episode & judul ditulis APA ADANYA
    dari json — tidak ada normalisasi angka/padding, sesuai keputusan
    desain (biar user yang tentukan penamaan, script terima bersih saja).

    Tiap entri queue punya field yang dipakai sepanjang alur:
      title, episode, m3u8, status, progress, segment, total_segments,
      eta, output_file, episode_dir
    """
    title = batch_data.get("title", "")
    queue = []

    for item in batch_data.get("episodes", []):
        m3u8 = item.get("m3u8") or item.get("link") or ""
        if not m3u8:
            continue

        episode = item.get("episode", item.get("ep", ""))

        queue.append({
            "title":           title,
            "episode":         episode,
            "m3u8":            m3u8,
            "status":          "waiting",
            "progress":        0,
            "segment":         0,
            "total_segments":  0,
            "eta":             "",
            "output_file":     "",
            "episode_dir":     "",
        })

    return queue


def build_filename(title: str, episode) -> str:
    """
    Bangun nama file dasar (tanpa ekstensi) dari title + episode, format
    "{title} - {episode}" apa adanya, lalu di-sanitize sekali di sini
    (defense in depth — konsumen tidak perlu sanitize ulang).
    """
    raw = f"{title} - {episode}".strip(" -")
    return sanitize(raw) or "unnamed"
