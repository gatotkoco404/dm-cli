#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/menu.py
Version: v0.03

Menu utama CLI. Perubahan dari v0.02: tambah menu "Download TurboVIP"
(poin 3) + opsi lanjutkan sesi TBV ("00").

CATATAN PENTING soal sesi ganda: pipeline lama (manual/multi/other) dan
TBV punya session/state SENDIRI-SENDIRI (session.json vs tbv_session.json,
core.state vs core.tbv_state) — jadi sengaja TIDAK saling meng-warning.
User boleh punya sesi manual yang belum selesai DAN sesi TBV yang belum
selesai secara bersamaan; masing-masing cuma minta konfirmasi discard
kalau mau mulai baru DI JALUR YANG SAMA (mis. sesi TBV lama cuma
diperingatkan kalau user mau mulai TBV baru lagi, bukan kalau user mau
buka menu m3u8/mp4).
"""

from core import session as session_mod
from core import tbv_session as tbv_session_mod
from core import scheduler
from core import tbv_scheduler
from cli.theme import console, safe

from cli.views import (
    manual_view,
    multi_view,
    other_audio_view,
    other_video_view,
    turbovip_view,
    settings_view,
    history_view,
    control_view,
    tbv_control_view,
)


def run():
    """Entry point utama — dipanggil dari main.py."""
    while True:
        has_session     = session_mod.has_session()
        has_tbv_session = tbv_session_mod.has_session()

        console.print("\n[header]=== Menu Utama ===[/header]")
        if has_session:
            console.print("0.  Lanjutkan download sebelumnya")
        if has_tbv_session:
            console.print("00. Lanjutkan download TurboVIP sebelumnya")
        console.print("1. Download m3u8/mp4")
        console.print("2. Download Other")
        console.print("3. Download TurboVIP")
        console.print("4. History")
        console.print("5. Settings")
        console.print("6. Exit")

        choice = console.input("\nPilih menu: ").strip()

        if choice == "00" and has_tbv_session:
            _resume_session_tbv()

        elif choice == "0" and has_session:
            _resume_session()

        elif choice == "1":
            if has_session and not _confirm_discard_if_needed():
                continue
            _menu_m3u8_mp4()

        elif choice == "2":
            if has_session and not _confirm_discard_if_needed():
                continue
            _menu_other()

        elif choice == "3":
            if has_tbv_session and not _confirm_discard_if_needed_tbv():
                continue
            turbovip_view.show()

        elif choice == "4":
            history_view.show()

        elif choice == "5":
            settings_view.show()

        elif choice == "6":
            console.print("\n[muted][*][/muted] Sampai jumpa!")
            break

        else:
            console.print("[error][x][/error] Pilihan tidak valid.")


# =========================================
# SESSION — PIPELINE LAMA (manual/multi/other)
# =========================================

def _resume_session():
    data = session_mod.load_session()
    if not data:
        console.print("[error][x][/error] Gagal membaca session.")
        return
    console.print(
        f"[success][+][/success] Melanjutkan sesi: \"{safe(data.get('anime', '?'))}\" "
        f"({len(data.get('queue', []))} episode)."
    )
    scheduler.resume_from_session(data)
    control_view.show()


def _confirm_discard_if_needed() -> bool:
    """
    Kalau ada session pipeline lama & user mau mulai download baru
    (bukan lewat opsi 'Lanjutkan'), kasih warning dulu. Return True
    kalau boleh lanjut (session lama sudah dibuang), False kalau
    dibatalkan.
    """
    answer = console.input(
        "\n[warning][![/warning] Ada sesi belum selesai, mulai baru akan "
        "menghapusnya. Lanjut? (y/n): "
    ).strip().lower()
    if answer != "y":
        return False
    scheduler.discard_session()
    return True


# =========================================
# SESSION — TURBOVIP (terpisah total dari pipeline lama)
# =========================================

def _resume_session_tbv():
    data = tbv_session_mod.load_session()
    if not data:
        console.print("[error][x][/error] Gagal membaca session TurboVIP.")
        return
    console.print(
        f"[success][+][/success] Melanjutkan sesi TurboVIP: \"{safe(data.get('title', '?'))}\" "
        f"({len(data.get('queue', []))} episode)."
    )
    tbv_scheduler.resume_from_session(data)
    tbv_control_view.show()


def _confirm_discard_if_needed_tbv() -> bool:
    """Versi TBV dari _confirm_discard_if_needed() — sesi yang dibuang cuma sesi TBV."""
    answer = console.input(
        "\n[warning][![/warning] Ada sesi TurboVIP belum selesai, mulai baru "
        "akan menghapusnya. Lanjut? (y/n): "
    ).strip().lower()
    if answer != "y":
        return False
    tbv_scheduler.discard_session()
    return True


# =========================================
# SUBMENU
# =========================================

def _menu_m3u8_mp4():
    console.print("\n1. Manual (1 link)")
    console.print("2. Multi (baca folder input_batch)")
    choice = console.input("\nPilih nomor: ").strip()
    if choice == "1":
        manual_view.show()
    elif choice == "2":
        multi_view.show()
    else:
        console.print("[error][x][/error] Pilihan tidak valid.")


def _menu_other():
    console.print("\n1. Audio (Only Audio)")
    console.print("2. Video (dengan audio)")
    choice = console.input("\nPilih nomor: ").strip()
    if choice == "1":
        other_audio_view.show()
    elif choice == "2":
        other_video_view.show()
    else:
        console.print("[error][x][/error] Pilihan tidak valid.")
