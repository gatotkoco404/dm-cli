# Struktur Proyek dm-cli

Peta lengkap file & folder, dengan 1 baris keterangan tanggung jawab
tiap file. Untuk penjelasan **cara kerja** dan **alur data** antar
file, lihat `BLUEPRINT.md`. Untuk cara **pakai**, lihat `README.md`.

```
dm-cli/
├── main.py                    Entry point. Setup path, routing
│                               interaktif vs headless (Lapisan A).
├── pyproject.toml              Config packaging (Lapisan B) — biar
│                               `dm-cli` bisa dipanggil dari mana saja.
├── bridge.py                    Server HTTP lokal — jembatan extension
│                               browser & dm-cli headless (Lapisan C).
│
├── settings.json                *runtime* — konfigurasi (dibuat otomatis
│                               saat pertama kali disimpan lewat menu
│                               Settings, ATAU muncul dengan default
│                               begitu load_settings() pertama dipanggil).
├── session.json                 *runtime* — snapshot sesi manual/multi/
│                               other yang sedang/pernah jalan.
├── tbv_session.json             *runtime* — sama seperti di atas, khusus
│                               TurboVIP.
├── history.json                 *runtime* — riwayat download (dibagi
│                               semua mode).
├── bridge_config.json            *runtime, buat sendiri* — token + port
│                               bridge.py (lihat BRIDGE.md).
├── tmp/                          *runtime* — folder kerja sementara.
│   └── tbv/                      Subfolder terpisah khusus TurboVIP.
│
├── cli/
│   ├── menu.py                  Router menu interaktif utama.
│   ├── theme.py                  Definisi warna/style Rich + helper
│   │                            escape teks dinamis (safe()).
│   ├── notifier.py                Pub-sub event (push_event) — jembatan
│   │                            handler/scheduler ke tampilan (baik
│   │                            dashboard interaktif maupun headless).
│   ├── cli_args.py                 Builder argparse — semua subcommand
│   │                            `dm-cli <command> ...` (Lapisan A).
│   ├── dispatch.py                  Terjemahkan Namespace argparse jadi
│   │                            panggilan reset_state()/start_worker()
│   │                            yang sama dipakai menu interaktif.
│   ├── headless.py                   Progress reporter mode headless
│   │                            (bukan dashboard raw-mode, cuma print
│   │                            baris, isatty-aware).
│   └── views/
│       ├── manual_view.py            UI menu Manual (1 link).
│       ├── multi_view.py              UI menu Multi (baca batch file).
│       ├── other_audio_view.py         UI menu Other > Audio.
│       ├── other_video_view.py          UI menu Other > Video.
│       ├── turbovip_view.py              UI menu TurboVIP.
│       ├── settings_view.py               UI menu Settings.
│       ├── history_view.py                 UI menu History.
│       ├── control_view.py                  Dashboard progress pipeline
│       │                                  lama (manual/multi/other) —
│       │                                  raw-mode terminal + hotkey.
│       └── tbv_control_view.py               Dashboard progress TurboVIP
│                                            (duplikasi control_view.py
│                                            yang diarahkan ke tbv_state/
│                                            tbv_scheduler — LIHAT
│                                            BLUEPRINT.md kenapa duplikasi
│                                            disengaja, bukan reuse).
│
├── core/
│   ├── settings.py                Backend settings.json.
│   ├── session.py                  Backend session.json (base64).
│   ├── history.py                   Backend history.json.
│   ├── staging.py                    Primitif folder tmp/ (pipeline
│   │                              lama: manual/multi/other).
│   ├── state.py                       State + lock global pipeline lama.
│   ├── scheduler.py                    "Mandor" pipeline lama — rolling
│   │                              worker + kontrol pause/resume/cancel.
│   ├── config_parser.py                 Parser batch .txt/.json (mode
│   │                              Multi) + extract_referer().
│   ├── dl_utils.py                       Parser output live yt-dlp/
│   │                              aria2c + format_eta().
│   ├── metadata.py                        Ambil metadata via yt-dlp
│   │                              --dump-json (dipakai menu Other).
│   │
│   ├── tbv_parser.py                       Parser m3u8 manual (TANPA
│   │                              yt-dlp) + loader batch json TBV.
│   ├── tbv_staging.py                       Primitif folder tmp/tbv/
│   │                              (terpisah dari staging.py).
│   ├── tbv_state.py                          State + lock khusus TBV
│   │                              (terpisah dari state.py).
│   ├── tbv_session.py                         Backend tbv_session.json
│   │                              (terpisah dari session.py).
│   └── tbv_scheduler.py                        "Mandor" TBV — mirip
│                                    scheduler.py, tapi tanpa tahap
│                                    post-process terpisah (lihat
│                                    BLUEPRINT.md).
│
└── handlers/
    ├── download.py                  Worker manual & multi (yt-dlp +
    │                              aria2c) — HLS trim, subtitle, referer.
    ├── other.py                      Worker menu Other (yt-dlp murni,
    │                              video/audio, platform apapun).
    ├── post_process.py                Finishing pipeline lama — remux,
    │                              subtitle inject, pindah ke tujuan.
    ├── sub_decrypt.py                  Decrypt subtitle terenkripsi
    │                              (dipanggil post_process.py).
    ├── cleaner.py                       Finishing TBV — strip PNG,
    │                              remux, finalize (analog post_process.py
    │                              tapi per-episode, bukan batch di akhir).
    └── download_tbv.py                   Worker TBV — aria2c raw +
                                     strip realtime + progress/ETA
                                     manual (sliding window).
```

---

## Kategori File

### File kode (masuk git)
Semua yang di atas KECUALI bagian "runtime" — ini yang di-commit ke
repo.

### File runtime (JANGAN commit — masuk `.gitignore`)
```
settings.json
session.json
tbv_session.json
history.json
bridge_config.json
tmp/
```
Semua ini **dibuat otomatis** (atau dibuat manual sekali oleh user,
untuk `bridge_config.json`) saat aplikasi jalan — isinya spesifik per
device/user (path folder download, token pribadi, dst), tidak relevan
buat orang lain yang clone repo ini.

Contoh `.gitignore` yang disarankan:
```gitignore
settings.json
session.json
tbv_session.json
history.json
bridge_config.json
tmp/
__pycache__/
*.pyc
*.egg-info/
```

### Proyek terpisah (bukan bagian repo dm-cli ini)
Extension browser (JavaScript, `manifest.json`, dst) adalah **proyek
terpisah** yang berkomunikasi dengan `dm-cli` lewat `bridge.py` —
tidak hidup di dalam struktur folder di atas. Lihat `BRIDGE.md` untuk
kontrak API antara keduanya.

---

## Kenapa Ada 2 Set File yang Mirip (`X.py` vs `tbv_X.py`)

Kalau kamu perhatikan, banyak file punya pasangan: `staging.py` ↔
`tbv_staging.py`, `state.py` ↔ `tbv_state.py`, `session.py` ↔
`tbv_session.py`, `scheduler.py` ↔ `tbv_scheduler.py`. Ini **bukan
duplikasi karena lupa refactor** — ini keputusan desain yang
disengaja. Alasan lengkapnya ada di `BLUEPRINT.md` bagian
"Kenapa Duplikasi, Bukan Reuse".

