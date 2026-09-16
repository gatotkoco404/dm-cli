#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/config_parser.py
Version: v0.01

Isi modul:
  - sanitize()             : bersihkan nama file dari karakter ilegal
  - timestamp_to_sec()      : konversi timestamp HH:MM:SS/MM:SS ke detik
  - parse_config_content()   : parser format config terstruktur (.txt/.json)
                                untuk menu Multi m3u8/mp4 — port dari
                                modules/helpers.py, logic TIDAK diubah.
  - parse_link_list()          : parser BARU, format sederhana 1 baris
                                  1 link, khusus menu Other > Audio > Multi.

Catatan desain: parse_config_content() & parse_link_list() sengaja punya
bentuk return yang beda (dict episode lengkap vs list string link polos).
Konsumennya beda pola: episode manual/multi (download.py) butuh field
status/progress/segment dkk sejak awal, sedangkan link Other cukup link
mentah — ep dict lengkap untuk Other dibangun belakangan di view layer
(cli/views/other_audio_view.py), bukan di modul parser ini.
"""

import re
import json


# =========================================
# SANITIZE
# =========================================

def sanitize(name: str) -> str:
    """Hapus karakter yang tidak valid untuk nama file."""
    return re.sub(r'[\\/:*?"<>|]', '', name).strip()


# =========================================
# TIMESTAMP
# =========================================

def timestamp_to_sec(ts: str) -> float:
    """
    Konversi timestamp HH:MM:SS atau MM:SS ke detik.
    Contoh: '01:30:00' -> 5400.0
    """
    parts = [float(p) for p in ts.split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0]


# =========================================
# PARSER — CONFIG TERSTRUKTUR (.txt / .json) — menu Multi m3u8/mp4
# =========================================

# field metadata/settings lama yang harus diabaikan kalau masih ada di file
_IGNORED_KEYS = {
    "path", "download_path", "timeout", "conn", "format", "parallel",
    "rate", "rate_limit", "quality", "mp3", "duration", "duration_custom",
    "jumlah ep", "episode_count", "drama id", "drama_id", "lang",
}


def parse_config_content(content: str, filetype: str = "txt"):
    """
    Parse isi file config Multi — support 2 format: .txt & .json.

    Format .txt:
        Title    : Nama Anime/Drama     (opsional)
        Ep  : 01
        Link: https://...m3u8 (atau .mp4)
        Sub : https://...srt   (opsional)
        Ep  : 02
        ...

    Format .json:
        {
          "title": "Nama Anime/Drama",
          "episode_count": 12,
          "episodes": [
            {"episode": 1, "m3u8": "https://...", "sub": "https://..."},
            ...
          ]
        }

    Return (anime_title: str, episodes: list[dict]).
    """
    if filetype == "json":
        return _parse_config_json(content)
    return _parse_config_txt(content)


def _parse_config_txt(content: str):
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    anime_title = ""
    episodes    = []
    cur         = None

    for line in lines:
        if ":" not in line:
            continue
        key_raw, val_raw = line.split(":", 1)
        key = key_raw.strip().lower()
        val = val_raw.strip()

        if key in ("title", "name"):
            anime_title = val
            continue

        if key in _IGNORED_KEYS:
            continue

        if key in ("ep", "episode"):
            if cur:
                episodes.append(cur)
            cur = _new_episode(val)
            continue

        if cur is None:
            continue

        if key in ("link", "m3u8"):
            cur["link"] = val
        elif key in ("sub", "subtitle"):
            cur["sub"] = val

    if cur:
        episodes.append(cur)

    return anime_title, episodes


def _parse_config_json(content: str):
    data = json.loads(content)

    anime_title   = str(data.get("title", "") or "")
    episode_count = data.get("episode_count") or len(data.get("episodes", []))
    width         = max(2, len(str(int(episode_count or 1))))

    episodes = []
    for item in data.get("episodes", []):
        ep_num = item.get("episode", item.get("ep", 0))
        try:
            label = str(int(round(float(ep_num)))).zfill(width)
        except (TypeError, ValueError):
            label = str(ep_num)

        ep = _new_episode(label)
        ep["link"] = item.get("m3u8") or item.get("link") or ""
        ep["sub"]  = item.get("sub")  or item.get("subtitle") or ""
        episodes.append(ep)

    return anime_title, episodes


def extract_referer(content: str, filetype: str = "txt") -> str:
    """
    Ambil field "referer" dari file config Multi, KALAU ADA — BARU,
    dipakai cli/views/multi_view.py buat mode Referer (bag. diskusi
    desain menu Referer numpang di menu m3u8/mp4).

    SENGAJA dipisah dari parse_config_content()/_parse_config_json()
    di atas, bukan ditambahkan sebagai return value ketiga di sana —
    biar signature return function itu TIDAK berubah (masih 2-tuple
    anime_title, episodes persis seperti sebelumnya), jadi pemanggil
    lama yang belum butuh referer tidak perlu ikut disentuh.

    Return "" kalau field tidak ada / null / file .txt (format .txt
    tidak punya slot referer, cuma json). Tidak raise — kalau parsing
    json gagal di sini, biar parse_config_content() yang urus pesan
    errornya (dipanggil terpisah, duluan, oleh view).
    """
    if filetype != "json":
        return ""
    try:
        data = json.loads(content)
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("referer") or "").strip()


def _new_episode(ep_label: str) -> dict:
    """Buat dict episode baru dengan nilai default (dipakai internal parser)."""
    return {
        "ep":             ep_label,
        "link":           "",
        "sub":            "",
        "duration":       "off",
        "quality":        "best",
        "mp3_only":       False,
        "status":         "waiting",
        "progress":       0,
        "segment":        0,      # segment terakhir yang selesai
        "total_segments": 0,      # total segment (0 = belum diketahui)
        "speed":          "",
        "eta":            "",
        "retries":        0,
    }


# =========================================
# PARSER BARU — LIST LINK POLOS (.txt) — menu Other > Audio > Multi
# =========================================

def parse_link_list(content: str) -> list:
    """
    Parser sederhana untuk file .txt berisi 1 link per baris (format
    khusus Audio Multi — TIDAK support metadata/key:value seperti
    parse_config_content()).

    Baris kosong diabaikan. Baris yang tidak diawali http:// atau https://
    juga diabaikan (dianggap bukan link valid) — tidak di-raise error,
    biar file yang sedikit berantakan tetap bisa diproses sebisanya.
    Jumlah link yang berhasil kebaca ditampilkan ke user di view layer
    sebelum konfirmasi, jadi kalau ada baris yang kelewat, kelihatan.

    Return: list[str] — daftar link mentah, urutan sesuai file.
    """
    links = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if not (line.startswith("http://") or line.startswith("https://")):
            continue
        links.append(line)
    return links
    