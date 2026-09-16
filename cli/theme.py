#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/theme.py
Version: v0.03

Definisi tema visual — skema warna Cyberpunk Neon, gaya teks subtle
(cuma visual yang berubah, teks tetap bahasa Indonesia biasa — bag. 8
desain). Modul ini CUMA berisi definisi warna/style + 1 Console bersama,
tidak ada logic cetak/print — itu tanggung jawab cli/notifier.py.
"""

from rich.console import Console
from rich.theme import Theme
from rich.markup import escape as _escape_markup


# =========================================
# TEMA WARNA
# =========================================

CYBER_THEME = Theme({
    "border":  "cyan",
    "header":  "bold magenta",
    "success": "green",
    "running": "cyan",
    "warning": "magenta",
    "error":   "bold red",
    "text":    "white",
    "muted":   "grey62",
})

# Console tunggal dipakai di seluruh app — biar tema konsisten di mana pun
# console dipanggil, tidak perlu bikin Console() baru berkali-kali.
console = Console(theme=CYBER_THEME)


# =========================================
# MAPPING STATUS QUEUE -> STYLE
# =========================================
# Dipakai cli/views/control_view.py & tabel antrean lain, biar warna
# status konsisten di semua tempat tanpa hardcode ulang per file.

STATUS_STYLE = {
    "waiting":     "muted",
    "downloading": "running",
    "done":        "success",
    "failed":      "error",
    "paused":      "warning",
    "held":        "warning",
    "removed":     "muted",
}


def style_for_status(status: str) -> str:
    """Ambil nama style rich untuk 1 status queue. Fallback ke 'text' kalau tidak dikenal."""
    return STATUS_STYLE.get(status, "text")


# =========================================
# PREFIX LOG
# =========================================
# Dipakai cli/notifier.py buat prefix baris log: [+] sukses, [!] warning,
# [x] gagal. Format markup rich, warnanya ambil dari CYBER_THEME di atas.

PREFIX_OK   = "[success][+][/success]"
PREFIX_WARN = "[warning][![/warning]"
PREFIX_ERR  = "[error][x][/error]"
PREFIX_INFO = "[muted][*][/muted]"


# =========================================
# ESCAPE TEKS DINAMIS
# =========================================

def safe(text) -> str:
    """
    WAJIB dipakai untuk teks dinamis yang TIDAK kita kontrol sendiri —
    input user, judul video, path, pesan error dari yt-dlp/ffmpeg/
    exception Python — sebelum diselipkan ke string yang punya markup
    rich (mis. f"[error]{safe(msg)}[/error]").

    Kenapa perlu: rich pakai tanda kurung siku [...] sebagai syntax
    markup warna. Kalau teks dinamis itu KEBETULAN mengandung "[" atau
    "]" (judul video "Judul [1080p]", path yang diawali "/" langsung
    setelah "[", pesan error ffmpeg yang sering ada "[libx264 @ ...]"),
    rich bakal salah baca itu sebagai tag markup dan crash dengan
    rich.errors.MarkupError. safe() escape karakter itu jadi teks
    literal biasa, aman ditampilkan apa adanya.
    """
    return _escape_markup(str(text))
