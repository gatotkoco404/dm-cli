# Blueprint dm-cli

Dokumen ini menjelaskan **cara kerja** dan **kenapa** dm-cli dibangun
seperti sekarang — buat siapa pun yang mau baca kode, mengubahnya,
atau nambah fitur baru tanpa perlu nebak-nebak alasan di balik tiap
keputusan desain. Untuk peta file, lihat `STRUKTUR.md`. Untuk cara
pakai, lihat `README.md`.

---

## 1. Filosofi Desain

Beberapa prinsip yang dipegang konsisten di seluruh proyek ini —
kalau kamu paham prinsip ini, sebagian besar keputusan struktur kode
di bawah bakal terasa masuk akal tanpa perlu dihafal satu-satu:

1. **Pisahkan state/staging/session per fitur.** Setiap pipeline besar
   (pipeline lama, TurboVIP) punya state, folder tmp, dan file session
   sendiri-sendiri. Tidak ada state global yang dipakai bersama.
2. **Jangan ubah file yang sudah stabil demi fitur baru.** Kalau fitur
   baru butuh perilaku beda dari yang sudah ada, bikin file baru
   (bahkan kalau isinya mirip 90% dengan yang lama) — bukan menambah
   percabangan `if` di file lama.
3. **Duplikasi lebih aman daripada coupling yang salah.** Lihat bagian
   4 di bawah untuk alasan detail.
4. **Layer bawah tidak tahu apa-apa soal layer atas.** `core/*.py`
   tidak pernah import dari `handlers/*.py` atau `cli/*.py`. Alurnya
   selalu satu arah: `cli` → `core` (orkestrasi) → `handlers` (kerja
   nyata) → `core` (primitif: staging, session, dll).

---

## 2. Alur Besar Aplikasi

```
User
 │
 ▼
main.py ──(ada argumen CLI?)──► cli/dispatch.py ──► core/state atau core/tbv_state
 │                                                          │
 │ (tanpa argumen)                                          ▼
 ▼                                                   core/scheduler atau
cli/menu.py                                          core/tbv_scheduler
 │                                                          │
 ├─ manual_view / multi_view ──────────┐                    │
 ├─ other_audio_view / other_video_view┤                    │
 ├─ turbovip_view ──────────────────────┼──► core/state(+tbv)│
 ├─ history_view                        │    core/scheduler  │
 └─ settings_view                       │    (+ tbv)         │
                                         └────────────────────┘
                                                  │
                                    ┌─────────────┴─────────────┐
                                    ▼                           ▼
                          handlers/download.py         handlers/download_tbv.py
                          handlers/other.py                    │
                                    │                           ▼
                                    ▼                   handlers/cleaner.py
                          handlers/post_process.py      (strip+remux+finalize,
                          (remux, subtitle, pindah)      per-episode langsung)
                                    │                           │
                                    └─────────────┬─────────────┘
                                                  ▼
                                          folder tujuan akhir
                                          (settings["download_path"])
```

Dua jalur di kanan (pipeline lama vs TurboVIP) **tidak pernah saling
memanggil** — keduanya independen total dari `core/state.py` ke bawah.

---

## 3. Tiga "Dunia" Download yang Berbeda

### 3a. Pipeline Lama (Manual / Multi / Other)

Dipakai untuk link m3u8/mp4 normal dan platform umum (`yt-dlp`).
`yt-dlp` mengerjakan **dua hal sekaligus**: parsing manifest/metadata
DAN download — makanya handler-nya (`handlers/download.py`,
`handlers/other.py`) relatif sederhana, tinggal susun command yt-dlp
dan parse output live-nya buat progress.

Finishing (remux, subtitle) terjadi **di akhir, satu kali, untuk semua
episode sekaligus** — `handlers/post_process.py` di-loop setelah
`core/scheduler.py` selesai memproses seluruh queue. Ini karena versi
lama proyek ini (sebelum jadi CLI) memang begitu strukturnya, dan
tidak ada alasan kuat untuk mengubahnya.

### 3b. TurboVIP (TBV)

Provider TBV menyamarkan tiap segment `.ts` sebagai file PNG palsu.
`yt-dlp` tidak paham ini, jadi **parsing dan download dipisah total**
dari `yt-dlp`:
- `core/tbv_parser.py` fetch manifest m3u8 manual, extract url
  segment (urutan dijaga ketat), tulis jadi file input `aria2c`.
- `handlers/download_tbv.py` jalankan `aria2c` langsung (bukan lewat
  `yt-dlp`), banyak segment kecil diunduh **paralel** (`-j16`, bukan
  banyak koneksi per file — segmennya kecil, 500KB–3MB, jadi
  percuma pakai multi-koneksi per file).
- Begitu 1 segment selesai, **langsung** di-strip PNG-nya
  (`handlers/cleaner.strip_segment()`) — realtime, bukan nunggu semua
  segment kelar dulu baru dibersihkan sekaligus.
- Progress (persen/ETA) **dihitung manual** (sliding window
  segment-selesai per detik) karena stdout `aria2c` di-`DEVNULL`-kan,
  beda dari pipeline lama yang tinggal parse teks output `yt-dlp`.

**Beda arsitektur penting:** finishing (remux+pindah) TBV terjadi
**per-episode, di dalam handler itu sendiri** (`download_tbv_episode()`
memanggil `cleaner.remux_episode()` dan `cleaner.finalize_episode()`
langsung), BUKAN di tahap terpisah setelah semua episode selesai
seperti pipeline lama. Makanya `core/tbv_scheduler.py` tidak punya
tahap "post-process" — begitu status episode `"done"`, file itu sudah
benar-benar utuh di folder tujuan.

### 3c. Kenapa Tidak Reuse `yt-dlp` untuk TBV?

`yt-dlp` tidak punya cara untuk "unduh file, lalu jalankan transformasi
byte-level custom sebelum dianggap selesai" — dia dirancang untuk
alur unduh-lalu-mux standar. Strip PNG itu operasi khusus di luar apa
pun yang didukung `yt-dlp` secara native, jadi parsing manual +
`aria2c` langsung adalah satu-satunya opsi realistis.

---

## 4. Kenapa Duplikasi, Bukan Reuse (`X.py` vs `tbv_X.py`)

Pasangan file berikut **sengaja** dibuat terpisah, bukan digabung jadi
1 modul generik dengan parameter:

| Pipeline Lama | TurboVIP | Kenapa Tidak Digabung |
|---|---|---|
| `core/staging.py` | `core/tbv_staging.py` | Beda root folder tmp (`tmp/` vs `tmp/tbv/`); kalau digabung, perubahan behavior salah satu pipeline beresiko ikut mempengaruhi yang lain |
| `core/state.py` | `core/tbv_state.py` | Field beda (TBV tidak punya `conn`/`mode`); state global sengaja tidak dibagi biar 2 pipeline bisa (secara teori) berjalan tanpa rebutan lock yang sama |
| `core/session.py` | `core/tbv_session.py` | File beda (`session.json` vs `tbv_session.json`) — sesi yang belum selesai di 1 pipeline tidak boleh terpengaruh aktivitas pipeline lain |
| `core/scheduler.py` | `core/tbv_scheduler.py` | Alur beda total (lihat 3a vs 3b — ada/tidaknya tahap post-process terpisah) |
| `cli/views/control_view.py` | `cli/views/tbv_control_view.py` | `control_view.py` sendiri ditandai di docstring-nya sebagai **file paling berisiko** di proyek ini (gabungan `rich.Live` + raw-mode keypress) — duplikasi dianggap lebih aman daripada memparameterisasi file yang rawan itu |

**Prinsip umum:** kalau menggabungkan 2 hal mirip butuh menambahkan
parameter/`if` yang mengubah perilaku file yang sudah stabil, opsi
yang dipilih di proyek ini adalah **duplikasi**, bukan generalisasi
prematur. Ini juga berlaku di `handlers/other.py` — `_run_download()`
di situ sengaja nyaris identik dengan yang di `handlers/download.py`,
dicatat eksplisit di komentar sebagai duplikasi yang disengaja.

---

## 5. Pola yang Berulang di Kedua Pipeline

Karena strukturnya paralel, pola berikut muncul 2x (sekali untuk
pipeline lama, sekali untuk TBV) — paham satu berarti paham dua-duanya:

### State + Lock
```python
_state = { ... }          # dict module-level
_state_lock = threading.Lock()

def get_state() -> dict: return _state
def get_lock() -> threading.Lock: return _state_lock
def reset_state(overrides=None): ...
```
Semua akses/ubah `state` **harus** di dalam `with get_lock():` — state
diakses dari banyak thread sekaligus (worker slot + thread pembaca
keypress di dashboard).

### Scheduler "Rolling Worker"
```
N thread worker (N = state["parallel"]) jalan bersamaan.
Tiap thread: claim episode "waiting" berikutnya -> proses -> ulangi
sampai tidak ada lagi "waiting" -> keluar.
```
"Rolling" artinya begitu 1 slot kosong, dia langsung ambil episode
berikutnya dari antrean — bukan menunggu "batch" episode yang sama
selesai bersamaan.

### Session Snapshot
Tiap kali status episode berubah, scheduler panggil
`_save_session_snapshot()` — dump `state["queue"]` (field yang
di-whitelist, lihat komentar di kode) ke `session.json`/
`tbv_session.json`, di-encode base64 (obfuscation ringan, bukan
enkripsi). File ini yang dibaca `dm-cli status` dan `bridge.py` untuk
progress — **bukan** komunikasi langsung ke proses yang sedang jalan.

### Kontrol Pause/Resume/Cancel — 2 Level
- **1 episode spesifik:** `hold_episode()`/`resume_episode()`/
  `cancel_episode()` — set flag `_held_by_user` lalu terminate
  subprocess-nya langsung.
- **Semua sekaligus:** `pause_all()`/`resume_all()`/`cancel_all()` —
  set flag global `state["paused"]`/`state["cancelled"]`, otomatis
  berlaku ke semua slot yang sedang jalan tanpa kode tambahan (handler
  download sudah cek flag ini di loopnya).

---

## 6. Lapisan Tambahan (Headless, Packaging, Bridge)

Tiga lapisan ini dibangun berurutan, satu di atas yang lain:

### Lapisan A — Headless CLI (`cli/cli_args.py` + `cli/dispatch.py`)
`main.py` cek `len(sys.argv)`: kalau ada argumen, parsing lewat
`argparse` (`cli_args.build_parser()`) lalu `dispatch.run(args)` —
**tidak lewat `cli/menu.py` sama sekali**. `dispatch.py` membangun
`ep`/queue langsung dari argumen (bukan `console.input()`), lalu
memanggil `reset_state()`/`start_worker()` yang **sama persis** dengan
yang dipanggil dari menu interaktif. Progress ditampilkan lewat
`cli/headless.py` (bukan `control_view.py`) — cek `sys.stdout.isatty()`
untuk mutuskan print progress atau diam (biar bersih kalau dipanggil
dari script lain).

**Kenapa tidak ada flag pause/resume di headless:** command headless
adalah 1 proses yang jalan sampai selesai lalu keluar. Command kedua
buat "pause" akan jadi proses Python terpisah tanpa akses ke memori
proses pertama — butuh mekanisme IPC baru yang belum dibutuhkan
sekarang. Cancel cukup lewat `Ctrl+C` (`KeyboardInterrupt` sudah
ditangkap di `main.py`).

### Lapisan B — Packaging (`pyproject.toml`)
`pip install -e .` bikin `dm-cli` executable dari mana saja. Editable
install menambahkan root proyek ke `sys.path` secara global — efeknya
sama seperti menjalankan `python main.py` dari dalam folder proyek
(yang membuat `core`/`cli`/`handlers` otomatis kebaca sebagai
namespace package Python 3), tapi sekarang berlaku dari direktori
manapun.

### Lapisan C — Bridge (`bridge.py`)
Server Flask lokal (`127.0.0.1` saja), terpisah total dari `dm-cli`.
Prinsip **fire-and-forget**: `POST /queue` tulis file batch + `Popen`
proses `dm-cli` lepas (`start_new_session=True`), langsung balas tanpa
menunggu selesai. `GET /status` membaca ulang
`session.json`/`tbv_session.json` — **tidak ada komunikasi langsung**
ke proses `dm-cli` yang sedang berjalan, semuanya lewat file. Detail
lengkap kontrak API ada di `BRIDGE.md`.

**Kenapa fire-and-forget:** batch bisa berjalan puluhan menit. Kalau
`bridge.py` menunggu sampai selesai sebelum membalas, dia tidak bisa
melayani request lain selama itu.

---

## 7. Titik Integrasi untuk Fitur Baru

Prinsip dari bagian 1: **jangan ubah file yang stabil kalau tidak
perlu**. Panduan kasar mana yang perlu disentuh tergantung jenis
perubahan:

| Kalau kamu mau... | Sentuh file... |
|---|---|
| Menambah menu baru yang pakai alur download yang sudah ada | `cli/menu.py` + 1 view baru di `cli/views/` |
| Menambah provider yang butuh cara download berbeda total | Pipeline baru (contoh: TurboVIP) — bikin `core/<nama>_state.py`, `core/<nama>_scheduler.py`, `handlers/download_<nama>.py`, dst., mengikuti pola bagian 5. **Jangan** modifikasi scheduler/state pipeline lain. |
| Menambah field baru ke format file batch | `core/config_parser.py` (mode Multi) atau `core/tbv_parser.py` (mode TBV) — tambah fungsi baru kalau bisa (contoh: `extract_referer()`), jangan ubah signature fungsi yang sudah ada kalau ada pemanggil lain yang bergantung pada bentuk return-nya. |
| Menambah subcommand headless baru | `cli/cli_args.py` (tambah subparser) + `cli/dispatch.py` (tambah handler) — reuse `reset_state()`/`start_worker()` yang sudah ada, jangan tulis ulang logic build-queue. |
| Menambah endpoint di `bridge.py` | `bridge.py` saja — dia baca `core/*` lewat fungsi publik yang sudah ada (`load_session()`, dst.), tidak boleh menambah logic scheduling baru di situ. |

---

## 8. Known Gaps (dicatat sengaja, bukan lupa)

- **Cancel lintas-proses lewat bridge belum ada.** `cancel_all()`
  beroperasi di state dalam memori proses `dm-cli` yang sedang jalan
  — `bridge.py` proses terpisah, tidak bisa memanggilnya secara
  berarti. Solusi butuh mekanisme signal-file yang di-*poll* worker,
  belum didesain.
- **Session yang gagal-tapi-disimpan tidak punya cara "dibuang manual"
  dari headless/bridge.** Mode interaktif punya
  `_confirm_discard_if_needed()` di `menu.py`; `dm-cli multi`/`dm-cli
  tbv` langsung menimpa state in-memory tanpa cek session lama sama
  sekali (aman, tidak nge-block, tapi bisa menimpa jejak episode gagal
  sebelumnya kalau keburu mulai batch baru).
- **Retry batch gagal di headless = jalankan ulang command yang sama**,
  bukan "retry cuma yang gagal" — kalau file batch berisi campuran
  episode sukses+gagal, re-run akan mencoba **semua** lagi (episode
  yang sudah sukses & sudah dipindah tidak akan didownload ulang dari
  awal karena sumbernya sudah tidak ada di tmp, tapi tetap boros kalau
  banyak yang sudah sukses).
  
