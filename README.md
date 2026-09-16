# dm-cli

CLI downloader anime/video untuk Termux (Android) — support HLS
(m3u8), mp4, platform umum lewat `yt-dlp` (YouTube, TikTok, dll), dan
provider yang menyamarkan segment video sebagai file PNG palsu
(TurboVIP).

Bisa dipakai lewat menu interaktif, lewat command line langsung
(headless), atau dikontrol dari extension browser lewat server lokal
(`bridge.py`).

Dokumen lain di repo ini:
- `STRUKTUR.md` — peta lengkap file/folder & tanggung jawabnya.
- `BLUEPRINT.md` — cara kerja tiap komponen, buat yang mau kontribusi/
  ubah kode.
- `BRIDGE.md` — kontrak API `bridge.py` ↔ extension browser.

---

## 1. Requirement

- Python 3.9+
- [`aria2c`](https://aria2.github.io/) — downloader inti
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) — extractor m3u8/mp4/
  platform umum
- [`ffmpeg`](https://ffmpeg.org/) — remux & subtitle inject
- Paket Python: `rich`, `requests` (dan `flask`, kalau mau pakai
  `bridge.py`)

Di Termux:
```bash
pkg install python aria2 ffmpeg
pip install yt-dlp rich requests --break-system-packages
```

---

## 2. Instalasi

```bash
git clone <url-repo-kamu> dm-cli
cd dm-cli
pip install -e . --break-system-packages
```

`pip install -e .` (baca `pyproject.toml`) mendaftarkan command
`dm-cli` ke `$PREFIX/bin/`, yang sudah ada di `PATH` Termux secara
default — jadi begitu ini selesai, `dm-cli` bisa dipanggil **dari
direktori mana pun**, tidak perlu `cd` ke folder ini lagi:

```bash
cd ~
dm-cli --help
```

**Kalau muncul `ModuleNotFoundError: No module named 'core'`** setelah
install — itu tanda `pyproject.toml` belum lengkap mendaftarkan
package `core`/`cli`/`handlers`. Pastikan isinya mengandung:
```toml
[tool.setuptools.packages.find]
include = ["core*", "cli*", "handlers*"]
namespaces = true
```
`namespaces = true` penting supaya folder tanpa `__init__.py` (kalau
ada) tetap dikenali sebagai package. Setelah diperbaiki, install ulang:
```bash
pip uninstall dm-cli -y --break-system-packages
pip install -e . --break-system-packages
```

---

## 3. Mode Interaktif (menu)

```bash
dm-cli
```
Menampilkan menu utama — pilih nomor, ikuti prompt. Ini mode default,
behavior tidak berubah dari sebelum ada mode headless.

Menu yang tersedia:
1. Download m3u8/mp4 (Manual — 1 link, atau Multi — baca file batch)
2. Download Other (yt-dlp — video atau audio-only)
3. Download TurboVIP (baca file batch `.json` khusus)
4. History
5. Settings
6. Exit

Kalau ada sesi yang belum selesai (baik pipeline biasa maupun
TurboVIP), opsi "Lanjutkan" akan muncul otomatis di menu.

**Kontrol saat download jalan:** tekan `p` (pause), `r` (resume), `c`
(cancel) — kalau lagi download banyak episode sekaligus, akan diminta
nomor episode target (atau `a` untuk semua).

---

## 4. Mode Headless (command line langsung)

Cocok dipakai lewat script lain, cron job, atau kalau kamu cuma mau
download 1 hal cepat tanpa buka menu.

```bash
dm-cli manual "https://...m3u8"
dm-cli manual "https://...mp4" --referer "https://provider.com/" --sub "https://...srt"

dm-cli multi /storage/emulated/0/kdrama/batch.json
dm-cli multi ./batch.txt --parallel 3 --conn 16

dm-cli tbv /storage/emulated/0/kdrama/drama.json
dm-cli tbv ./drama.json --parallel 2 --format mkv

dm-cli other video "https://youtu.be/xxxx" --quality 1080p
dm-cli other audio "https://youtu.be/xxxx" --quality 192k

dm-cli history --limit 20
dm-cli status
dm-cli status --json
```

Jalankan `dm-cli <command> --help` untuk daftar flag lengkap tiap
subcommand.

### Flag override (berlaku sekali pakai, tidak disimpan ke settings.json)

| Flag | Berlaku di |
|---|---|
| `--conn N` | manual, multi, other |
| `--parallel N` | multi, tbv |
| `--timeout N` | manual, multi, tbv, other |
| `--rate-limit R` | manual, multi, tbv, other |
| `--format FMT` | manual, multi, tbv |
| `--download-path PATH` | semua |
| `--referer URL` | manual saja (mp4; untuk m3u8, referer ikut terpasang di fetch manifest & segment sekaligus — lihat batasan yt-dlp) |
| `--sub URL` | manual saja |

Untuk mode `multi`, `referer` **bukan** flag CLI — dibaca dari field
`"referer"` di dalam file batch `.json` (lihat bagian 5).

### Progress di terminal

Kalau dipanggil langsung dari terminal interaktif (bukan lewat script
lain), progress ditampilkan live per baris. Kalau dipanggil dari
subprocess/script (mis. `bridge.py`), progress tidak diprint ke
stdout — baca `session.json`/`tbv_session.json` (atau pakai
`dm-cli status --json`) untuk progress-nya.

---

## 5. Format File Batch

### Mode Multi (`.txt` atau `.json`)

```json
{
  "title": "Nama Judul",
  "episode_count": 12,
  "referer": "https://provider.com/",
  "episodes": [
    { "episode": 1, "m3u8": "https://...", "sub": "https://...srt" },
    { "episode": 2, "m3u8": "https://...", "sub": "" }
  ]
}
```
- `referer` — opsional, kosongkan/hapus kalau tidak perlu.
- `sub` — opsional per-episode.
- `episode` — **harus angka**, otomatis di-zero-pad sesuai
  `episode_count` (mis. `1` → `"01"` kalau `episode_count: 12`).

Format `.txt` alternatif (lebih sederhana, tanpa referer/sub):
```
Title: Nama Judul
Ep  : 01
Link: https://...m3u8
Sub : https://...srt
Ep  : 02
Link: https://...
```

### Mode TurboVIP (`.json` saja)

```json
{
  "title": "Nama Judul",
  "episode_count": 14,
  "episodes": [
    { "episode": 8.0, "m3u8": "https://...m3u8" },
    { "episode": 9.0, "m3u8": "https://..." }
  ]
}
```
- **Tidak ada** `sub`/`referer` — parser TBV tidak membacanya.
- `episode` ditulis **apa adanya** ke nama file akhir (`8.0` tetap
  `8.0`, bukan `"08"`), bebas berupa angka atau string.

Field `"link"` diterima sebagai alias `"m3u8"` di kedua format.

---

## 6. Settings

Diatur lewat menu Settings (interaktif) atau edit `settings.json`
langsung:

| Field | Keterangan |
|---|---|
| `download_path` | Folder tujuan akhir hasil download |
| `input_batch_path` | Folder tempat file `.txt`/`.json` batch dicari (menu Multi & TurboVIP) |
| `conn` | Jumlah koneksi paralel per file (aria2c `-x`/`-s`) — tidak dipakai TurboVIP |
| `parallel` | Jumlah episode didownload bersamaan |
| `format` | Format output remux (`mkv`/`mp4`/`ts`/dll) — menu Other selalu keluar mp4/mp3 |
| `timeout` | Timeout koneksi (detik) |
| `rate_limit` | Limit kecepatan, mis. `"2M"` (kosong = tanpa limit) |

---

## 7. Bridge (opsional — kontrol dari extension browser)

```bash
pip install flask --break-system-packages
```
Buat `bridge_config.json` (sejajar `main.py`):
```json
{ "token": "isi-bebas", "port": 15200 }
```
Jalankan:
```bash
python bridge.py
```
Detail lengkap kontrak API, format request/response, dan known
limitations ada di `BRIDGE.md`.

---

## 8. Known Limitations

- Cancel lintas-proses (lewat `bridge.py`) belum didukung — lihat
  `BRIDGE.md`.
- Retry episode gagal di mode headless berarti menjalankan ulang
  command yang sama — TIDAK ada auto-retry/prompt interaktif (biar
  tidak nge-hang kalau dipanggil dari script tanpa stdin).
- Progress via `bridge.py` bersifat polling (baca file), bukan push
  realtime — ada jeda sesuai interval simpan snapshot (~3 detik).
  
