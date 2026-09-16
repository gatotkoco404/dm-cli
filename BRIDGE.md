# Extension Browser ↔ dm-cli Bridge

Dokumen ini menjelaskan proyek tambahan di atas `dm-cli`: sebuah extension
browser yang men-scrape link video (m3u8/mp4) dari halaman web, mengirim
hasilnya ke `dm-cli` lewat server lokal (`bridge.py`), yang otomatis
memulai download tanpa perlu membuka menu `dm-cli` secara manual.

Status: **desain selesai, implementasi bertahap** (lihat bagian
"Status Implementasi" di bawah).

---

## 1. Gambaran Umum

```
┌──────────────────┐        ┌──────────────┐        ┌─────────────┐
│ Extension         │  HTTP  │  bridge.py    │ subproc│   dm-cli    │
│ Browser           │◄──────►│  (Flask,      │───────►│  (headless, │
│ (background/       │        │  localhost)   │        │  Lapisan A) │
│  service worker)   │        └──────┬────────┘        └──────┬──────┘
└──────────────────┘                │                        │
                                     │ baca file               │ tulis file
                                     ▼                        ▼
                          session.json / tbv_session.json
                          (ditulis dm-cli, dibaca bridge.py)
```

**Prinsip inti — fire and forget:**
`bridge.py` **tidak menunggu** download selesai. Alurnya:

1. Extension `POST /queue` — bridge tulis file `.json` ke
   `input_batch_path`, lalu **spawn proses `dm-cli` terpisah**
   (`start_new_session=True`, lepas dari lifecycle `bridge.py`), dan
   **langsung membalas** tanpa menunggu proses itu selesai.
2. Extension polling `GET /status` tiap beberapa detik — bridge
   **membaca ulang** `session.json`/`tbv_session.json` (file yang
   sama yang di-snapshot `dm-cli` tiap ~3 detik selama download
   berjalan), lalu meringkasnya jadi progress per-episode.

Tidak ada komunikasi langsung apa pun antara `bridge.py` dan proses
`dm-cli` yang sedang berjalan — semuanya lewat file. Ini kenapa
`bridge.py` bisa direstart kapan saja tanpa mengganggu download yang
sedang jalan, dan kenapa `/cancel` belum bisa diimplementasikan penuh
(lihat "Known Limitations").

---

## 2. Dua Pipeline yang Didukung

| Pipeline | Dipakai untuk | Handler dm-cli | Ciri khas |
|---|---|---|---|
| `multi`  | Situs dengan link m3u8/mp4 "normal" (kisskh, sebagian lk21, MovieBox+referer) | `handlers/download.py` (yt-dlp+aria2c) | Boleh ada `sub` per-episode, `referer` opsional |
| `tbv`    | Situs yang menyamarkan segment `.ts` jadi PNG palsu (TurboVIP, p2p tertentu) | `handlers/download_tbv.py` (aria2c raw + strip PNG realtime) | Tidak ada `sub`, episode ditulis apa adanya (tidak di-zero-pad) |

Field `"pipeline"` di body request `/queue` **wajib** salah satu dari
dua nilai ini — bridge memakainya untuk menentukan command apa yang
dijalankan (`dm-cli multi <file>` atau `dm-cli tbv <file>`), bukan untuk
ditulis ke file batch (dibuang oleh bridge sebelum file ditulis).

Field `"site"` **bebas** apa saja (`"lk21"`, `"kisskh"`, `"moviebox"`,
dst) — murni informasi, tidak memengaruhi logic apa pun, cuma
memudahkan kamu membaca log/riwayat nanti.

---

## 3. Format JSON yang Dikirim Extension ke Bridge

`POST /queue`, header `X-DM-Token: <token>`, body:

### Pipeline `multi`

```json
{
  "site": "kisskh",
  "pipeline": "multi",
  "title": "Nama Judul",
  "episode_count": 12,
  "referer": "https://provider.com/",
  "episodes": [
    { "episode": 1, "m3u8": "https://...m3u8-atau-mp4", "sub": "https://...srt" },
    { "episode": 2, "m3u8": "https://...", "sub": "" }
  ]
}
```

- `referer` — **opsional**, kosongkan/hapus kalau tidak butuh.
- `sub` — opsional per-episode, isi `""` kalau tidak ada.
- `episode` — **wajib angka** (int/float). Dipakai untuk zero-padding
  otomatis berdasarkan `episode_count` (mis. `episode_count: 12` →
  lebar 2 digit → `episode: 1` jadi `"...-01"`).

### Pipeline `tbv`

```json
{
  "site": "lk21",
  "pipeline": "tbv",
  "title": "Nama Judul",
  "episode_count": 14,
  "episodes": [
    { "episode": 8.0, "m3u8": "https://...m3u8" },
    { "episode": 9.0, "m3u8": "https://..." }
  ]
}
```

- **Tidak ada** `sub` maupun `referer` — parser TBV tidak membacanya.
- `episode` — ditulis **apa adanya** ke nama file akhir (`8.0` tetap
  `8.0`, bukan `08`). Bisa string bebas juga (mis. `"08-09"` untuk
  gabungan episode), tidak ada validasi tipe.

---

## 4. Format File yang Ditulis Bridge ke `input_batch_path`

Bridge menulis **persis** body request di atas, **minus** field
`"site"` dan `"pipeline"` (keduanya metadata bridge, bukan bagian
skema yang dibaca `core/config_parser.py`/`core/tbv_parser.py`). Nama
file: `bridge_<pipeline>_<timestamp>.json`.

---

## 5. Spesifikasi API Bridge

Semua endpoint (kecuali `/ping`) butuh header:
```
X-DM-Token: <token yang sama dengan bridge_config.json>
```

| Method | Path | Body | Balasan |
|---|---|---|---|
| GET | `/ping` | – | `{"ok": true, "service": "dm-cli-bridge", "debug": true\|false}` — cek bridge nyala + status debug, tanpa token |
| POST | `/debug` | `{"enabled": true\|false}` | `{"ok": true, "debug": true\|false}` — toggle mode debug (lihat bagian 5b) |
| POST | `/queue` | JSON (lihat bagian 3) | `{"ok": true, "message": "queued", "pipeline": "...", "file": "...", "job_id": "..."}`, atau `409` kalau pipeline itu masih sibuk, atau `500` kalau proses crash instan (lihat bagian 5a) |
| GET | `/logs?job_id=X&since=N` | – | `{"ok": true, "debug": ..., "lines": [...], "cursor": N, "returncode": null\|int, "finished": bool}` |
| GET | `/status` | – | `{"ok": true, "sessions": {"multi": {...}\|null, "tbv": {...}\|null}}` |
| POST | `/cancel` | `{"pipeline": "multi"\|"tbv"}` | **`501` — belum diimplementasi**, lihat Known Limitations |

### 5a. Deteksi Crash Instan (selalu aktif)

Begitu proses `dm-cli` di-spawn, bridge tunggu `CRASH_CHECK_DELAY` (0.5 detik) lalu cek apakah proses sudah mati. Proses download normal makan waktu menit-menitan — kalau ternyata sudah exit dalam waktu sesingkat itu, itu hampir pasti error start (file batch korup, `episodes` kosong, dll), bukan proses yang sedang berjalan wajar. `/queue` akan balikin `500` + `log_tail` (isi cuplikan log kalau debug mode nyala, atau pesan "nyalakan debug mode" kalau tidak) — **bukan** `"queued"` palsu.

### 5b. Mode Debug

Diaktifkan lewat `POST /debug {"enabled": true}`. Saat ON, stdout+stderr proses `dm-cli` yang di-spawn **ditangkap** (bukan `DEVNULL`) ke buffer per-job (maks 500 baris, ring buffer), bisa dipoll incremental lewat `GET /logs?job_id=<id>&since=<cursor>` — kirim `since=0` pertama kali, lalu pakai nilai `cursor` dari respons sebelumnya untuk poll berikutnya (biar tidak dapat baris yang sama berulang). Saat OFF, stdout/stderr tetap `DEVNULL` (hemat resource) — deteksi crash instan (5a) tetap jalan, cuma tanpa isi log detail.

`job_id` didapat dari respons `/queue` (format `<pipeline>_<epoch_ms>`).

### 5c. Parameter Override per-Job

Body `/queue` boleh menambahkan key opsional berikut — **tidak** ditulis ke file batch (dm-cli parser tidak pernah melihatnya), melainkan diterjemahkan jadi flag CLI sekali-pakai yang ditempel ke command yang di-spawn:

| Key di body | Flag CLI yang dihasilkan |
|---|---|
| `download_path` | `--download-path` |
| `conn` | `--conn` |
| `parallel` | `--parallel` |
| `timeout` | `--timeout` |
| `rate_limit` | `--rate-limit` |
| `format` | `--format` |

Semua opsional — kalau tidak diisi/kosong, `dm-cli` pakai default dari `settings.json` seperti biasa (lihat Lapisan A).

### Contoh respons `/status`

```json
{
  "ok": true,
  "sessions": {
    "multi": null,
    "tbv": {
      "title": "Woori The Virgin",
      "summary": { "done": 3, "total": 14, "overall_percent": 24.5 },
      "episodes": [
        { "episode": 8.0, "status": "downloading", "progress": 45.2, "segment": 120, "total_segments": 265, "eta": "02:15" },
        { "episode": 9.0, "status": "waiting", "progress": 0, "segment": 0, "total_segments": 0, "eta": "" }
      ]
    }
  }
}
```

`summary.overall_percent` = rata-rata `progress` semua episode dalam batch (bukan cuma yang sedang `downloading`) — dipakai buat progress bar keseluruhan tanpa extension perlu menghitung sendiri dari array `episodes`.

`sessions.multi`/`sessions.tbv` bernilai `null` kalau tidak ada sesi
sedang berjalan di pipeline itu (sudah `done` — session file otomatis
dihapus scheduler — atau memang belum pernah dimulai).

---

## 6. Setup

### `bridge_config.json` (taruh di root, sejajar `main.py`)

```json
{
  "token": "isi-bebas-tapi-wajib-sama-persis-dengan-extension",
  "port": 15200
}
```

Token **custom**, bebas apa saja — yang penting nilai di extension dan
di file ini identik. Bridge menolak (`401`) kalau header
`X-DM-Token` tidak cocok atau tidak dikirim sama sekali.

### Menjalankan

```bash
pip install flask --break-system-packages   # sekali saja
python bridge.py
```

Bridge bind ke `127.0.0.1` **saja** — tidak bisa diakses dari perangkat
lain di jaringan yang sama, hanya dari device yang sama (tempat
extension browser & Termux berjalan).

### Sisi Extension

`fetch()` ke bridge **wajib** dilakukan dari **background/service
worker** extension, **bukan** dari content script — content script
tunduk pada aturan CORS milik halaman yang sedang dibuka dan bisa
diblokir saat mencoba akses `localhost`; background/service worker
punya izin sendiri dan tidak kena batasan itu.

---

## 7. Known Limitations

- **1 batch aktif per pipeline** (Opsi A yang disepakati) — kalau
  pipeline `tbv` sedang jalan, `/queue` dengan `pipeline: "tbv"` lain
  akan ditolak (`409`) sampai batch yang jalan selesai. Tidak ada
  antrean otomatis di sisi bridge. Kalau nanti dibutuhkan, ini bisa
  ditambah sebagai fitur terpisah (folder `pending/` + bridge yang
  memproses satu-satu).
- **`/cancel` belum berfungsi.** `cancel_all()` di
  `core/scheduler.py`/`core/tbv_scheduler.py` beroperasi di state
  dalam memori proses `dm-cli` yang sedang jalan — `bridge.py` adalah
  proses Python **terpisah**, jadi tidak bisa memanggil fungsi itu
  secara berarti (state yang diaksesnya bukan state proses yang mau
  dibatalkan). Solusi butuh mekanisme signal-file baru (mis. file
  `<pipeline>.cancel` yang di-*poll* worker di sela loop-nya) —
  ini perubahan ke `core/scheduler.py`/`core/tbv_scheduler.py` yang
  **belum didesain**, menyusul kalau dibutuhkan.
- **Progress bukan push, tapi polling.** Tidak ada websocket/SSE;
  extension harus polling `/status` sendiri (disarankan tiap 1–2
  detik). `dm-cli` menulis snapshot progress tiap ~3 detik
  (`SAVE_INTERVAL` di `handlers/download.py`/`handlers/download_tbv.py`)
  — bisa diturunkan ke 1 detik kalau progress terasa kurang responsif.
- **Deteksi "sibuk" berbasis keberadaan file session**, bukan status
  sebenarnya — sesi yang di-pause tapi belum di-cancel tetap dianggap
  "sibuk" (session file-nya masih ada), ini sesuai perilaku yang
  memang diinginkan (jangan timpa sesi yang masih bisa dilanjutkan).

---

## 8. Status Implementasi

| Bagian | Status |
|---|---|
| Lapisan A — `dm-cli manual` | ✅ selesai |
| Lapisan A — `dm-cli multi` | ⏳ berikutnya |
| Lapisan A — `dm-cli tbv` | ⏳ setelah `multi` |
| Lapisan A — `dm-cli other` | ⏳ setelah `tbv` |
| Lapisan B — packaging (`pyproject.toml`) | ⏳ belum |
| Lapisan C — `bridge.py` | ✅ v0.02 — `/queue`, `/status`, `/debug`, `/logs` fungsional + deteksi crash instan + parameter override; `/cancel` masih placeholder |
| Extension browser | 🔧 dikerjakan terpisah oleh pemilik proyek |

`bridge.py` sudah bisa dites langsung terhadap `dm-cli multi`/`dm-cli tbv`
begitu dua command itu selesai diimplementasikan di `cli/dispatch.py`.

