#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
handlers/sub_decrypt.py
Version: v0.01

Checker & decrypt subtitle KissKH.
Subtitle KissKH dienkripsi AES-CBC dengan key/IV yang beda-beda tergantung
ekstensi URL file sub-nya:
    .srt        → plain, tidak perlu decrypt
    .srt.txt    → V1
    .srt.txt1   → V2

Sumber key/IV: reverse engineering scripts.075605d76f7d5f68.js (lihat catatan RE).

Port murni dari modules/sub_decrypt.py — TIDAK ADA perubahan logic.
"""

import re
from base64 import b64decode
from Crypto.Cipher import AES


# =========================================
# KEYS
# =========================================

SUB_KEYS = {
    "txt":  (b"8056483646328763", b"6852612370185273"),   # V1
    "txt1": (b"AmSmZVcH93UQUezi", b"ReBKWW8cqdjPEnF6"),   # V2
}


# =========================================
# DETECTION
# =========================================

def get_sub_ext(src_url: str) -> str:
    """'...Ep2.id.srt.txt1?v=xxx' -> 'txt1'"""
    return src_url.split("?")[0].split(".")[-1]


def is_base64_cipher(line: str) -> bool:
    """Cek 1 baris teks: apakah pure base64 cipher (bukan nomor urut/timestamp/plain text)."""
    line = line.strip()
    if not line:
        return False
    if line.isdigit():
        return False           # nomor urut
    if "-->" in line:
        return False           # timestamp
    if " " in line:
        return False           # plain text biasanya ada spasi
    if len(line) < 8:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=]+", line))


def needs_decrypt(raw: str) -> bool:
    """
    Cek baris teks pertama (skip nomor urut & timestamp).
    Kalau base64 cipher -> perlu decrypt. Kalau plain text -> tidak.
    """
    for line in raw.splitlines():
        t = line.strip()
        if t and not t.isdigit() and "-->" not in t:
            return is_base64_cipher(t)
    return False


# =========================================
# DECRYPT
# =========================================

def _decrypt_line(b64_text: str, key: bytes, iv: bytes) -> str:
    cipher = AES.new(key, AES.MODE_CBC, iv)
    raw = cipher.decrypt(b64decode(b64_text))
    pad = raw[-1]                          # buang PKCS7 padding
    return raw[:-pad].decode("utf-8", errors="replace")


def decrypt_subtitle(raw_srt: str, src_url: str, log=None) -> str:
    """
    Entry point utama. Return subtitle sudah didekripsi (atau raw_srt
    apa adanya kalau memang tidak perlu didekripsi).

    PENTING: cek "perlu decrypt atau tidak" SELALU berdasarkan isi
    (needs_decrypt), bukan ekstensi link — link .srt pun kadang terenkripsi.
    Ekstensi cuma dipakai buat milih key V1/V2; kalau ekstensi tidak dikenal
    (mis. .srt) tapi ternyata terenkripsi, fallback ke key V1.

    log : callable opsional, dipanggil dengan 1 string pesan untuk tiap
          tahap penting (dipakai post_process.py untuk trace debug).
    """
    def _log(msg):
        if log:
            try:
                log(msg)
            except Exception:
                pass

    if not needs_decrypt(raw_srt):
        _log("needs_decrypt() -> False, subtitle dianggap PLAIN, decrypt DI-SKIP.")
        return raw_srt

    ext = get_sub_ext(src_url)
    key, iv = SUB_KEYS.get(ext, SUB_KEYS["txt"])   # fallback V1
    key_label = ext if ext in SUB_KEYS else f"{ext} (tidak dikenal, fallback ke 'txt'/V1)"
    _log(f"needs_decrypt() -> True, decrypt DIJALANKAN. ext={ext!r} -> key={key_label}")

    total_lines   = 0
    cipher_lines  = 0
    ok_lines      = 0
    fail_lines    = 0
    first_before  = None
    first_after   = None

    result = []
    for line in raw_srt.splitlines():
        total_lines += 1
        if is_base64_cipher(line):
            cipher_lines += 1
            if first_before is None:
                first_before = line.strip()
            try:
                decrypted_line = _decrypt_line(line.strip(), key, iv)
                result.append(decrypted_line)
                ok_lines += 1
                if first_after is None:
                    first_after = decrypted_line
            except Exception as e:
                result.append(line)   # gagal decrypt -> biarkan apa adanya
                fail_lines += 1
                _log(f"GAGAL decrypt 1 baris cipher ({e.__class__.__name__}: {e}). Baris asli dipertahankan.")
        else:
            result.append(line)

    _log(
        f"Selesai. total_baris={total_lines}, terdeteksi_cipher={cipher_lines}, "
        f"berhasil_decrypt={ok_lines}, gagal_decrypt={fail_lines}"
    )
    if first_before is not None:
        _log(f"Contoh baris cipher SEBELUM decrypt: {first_before[:80]!r}")
    if first_after is not None:
        _log(f"Contoh baris SESUDAH decrypt: {first_after[:80]!r}")

    return "\n".join(result)
    