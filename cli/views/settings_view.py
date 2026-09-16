#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli/views/settings_view.py
Version: v0.02

Menu Settings — form edit bernomor. Port dari settings.js lama (form web)
jadi interaksi CLI: tampilkan field bernomor -> user pilih nomor -> input
nilai baru -> simpan.

Field & label diambil dari core/settings.py (field_order()/label_for())
biar satu sumber kebenaran, tidak hardcode ulang di sini.
"""

from core import settings
from cli.theme import console, safe


def show():
    """Entry point menu Settings. Loop sampai user pilih 'Simpan & keluar'."""
    while True:
        cfg = settings.load_settings()
        keys = settings.field_order()

        console.print("\n[header]Pengaturan[/header]")
        for i, key in enumerate(keys, start=1):
            label = settings.label_for(key)
            value = cfg.get(key, "")
            value_display = safe(value) if value != "" else "(kosong)"
            console.print(f"[muted]{i}.[/muted] {label:<18} : {value_display}")

        exit_num = len(keys) + 1
        console.print(f"[muted]{exit_num}.[/muted] Simpan & keluar")

        choice = console.input("\nPilih nomor: ").strip()

        if not choice.isdigit():
            console.print("[error][x][/error] Input tidak valid, masukkan nomor.")
            continue

        choice_num = int(choice)

        if choice_num == exit_num:
            console.print("[success][+][/success] Pengaturan disimpan.")
            return

        if not (1 <= choice_num <= len(keys)):
            console.print("[error][x][/error] Nomor di luar jangkauan.")
            continue

        key   = keys[choice_num - 1]
        label = settings.label_for(key)
        current = cfg.get(key, "")

        new_value = console.input(
            f"{label} baru [{safe(current)}]: ",
            markup=False
        ).strip()
        
        if new_value == "":
            # Enter kosong = biarkan nilai lama, tidak diubah
            continue

        _apply_field(cfg, key, new_value)
        saved = settings.save_settings(cfg)
        if saved is None:
            console.print("[error][x][/error] Gagal menyimpan pengaturan.")
        else:
            console.print(f"[success][+][/success] {label} diperbarui.")


def _apply_field(cfg: dict, key: str, raw_value: str):
    """
    Terapkan nilai baru ke cfg dict, dengan konversi tipe seperlunya.
    "parallel" harus int, field lain tetap string.
    """
    if key == "parallel":
        try:
            cfg[key] = max(1, int(raw_value))
        except ValueError:
            console.print("[warning][![/warning] Parallel harus angka, nilai lama dipertahankan.")
    else:
        cfg[key] = raw_value
