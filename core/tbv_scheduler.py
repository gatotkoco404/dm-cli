#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/tbv_scheduler.py
Version: v0.01

Mandor untuk mode TurboVIP (TBV) — TERPISAH dari core/scheduler.py tapi
polanya SENGAJA dibuat mirip (rolling-parallel worker, kontrol
pause/resume/cancel per-episode maupun semua sekaligus, session
snapshot) supaya:
  a. Konsisten cara pakainya dari sisi cli/views/*.
  b. Kalau nanti menu Referer jadi digarap, tinggal reuse file ini
     (lewat set_handler(), lihat di bawah) alih-alih bikin scheduler
     ketiga dari nol — beda menu cuma beda handler + cara build queue.

BEDA UTAMA dari core/scheduler.py (kenapa lebih pendek):
  Pipeline lama: semua episode didownload dulu ke tmp, BARU di akhir ada
  1 tahap post_process terpisah yang loop ulang semua episode buat
  remux+subtitle+pindah (handlers/post_process.py).
  TBV: handlers/download_tbv.download_tbv_episode() SUDAH mengerjakan
  semuanya SENDIRI per-episode (download -> strip -> remux -> pindah ke
  tujuan -> hapus tmp) dalam SATU pemanggilan. Jadi begitu semua worker
  slot selesai, tidak ada tahap post-process tambahan yang perlu
  dipanggil scheduler — file yang sudah "done" sudah benar-benar utuh
  di folder tujuan saat itu juga.

CATATAN kalau nanti dipakai ulang untuk Referer (baca sebelum extend):
  _save_session_snapshot() di bawah cuma whitelist field yang dipakai
  TBV (title/episode/m3u8/...). Kalau Referer punya field tambahan yang
  perlu selamat dari crash (mis. "sub", "referer", "link_type"),
  whitelist itu WAJIB ditambah dulu — kalau lupa, field itu bakal hilang
  pas resume-from-session walau ada di queue live saat sesi masih jalan.
"""

import threading
import time

from core.tbv_state import get_state, get_lock, reset_state
from core.tbv_session import save_session, delete_session
from core.history import save_history
from core import tbv_staging
from cli.notifier import push_event

from handlers.download_tbv import download_tbv_episode


# =========================================
# STATE MODUL — proses aktif per index + worker thread + handler aktif
# =========================================

_active_procs      = {}
_active_procs_lock = threading.Lock()
_worker_thread      = None

_handler = download_tbv_episode   # default TBV — lihat set_handler()


def set_handler(handler_fn):
    """
    Ganti handler yang dipakai worker. Dipakai menu Referer nanti biar
    bisa reuse scheduler ini apa adanya — cukup panggil
    set_handler(handlers.download_referer.download_referer_episode)
    sebelum start_worker(), TANPA perlu duplikasi rolling-worker logic
    di bawah. Signature handler harus SAMA persis dengan
    download_tbv_episode() (ep, index, state, state_lock, push_event,
    get_proc, set_proc, **kwargs) -> bool.
    """
    global _handler
    _handler = handler_fn


# =========================================
# WORKER UTAMA — model ROLLING (sama persis pola core/scheduler.py)
# =========================================

def _download_worker():
    """
    Worker paralel model ROLLING: begitu 1 slot kosong (episode selesai
    TOTAL — sudah termasuk remux & pindah ke tujuan), langsung ambil
    episode berikutnya. Jumlah slot bersamaan = state["parallel"].
    """
    state      = get_state()
    state_lock = get_lock()

    with state_lock:
        state["status"]    = "downloading"
        state["paused"]    = False
        state["cancelled"] = False

    queue    = state["queue"]
    total    = len(queue)
    parallel = max(1, int(state.get("parallel", 1)))

    claim_lock = threading.Lock()

    def _claim_next_index():
        with claim_lock:
            for idx in range(total):
                if queue[idx]["status"] == "waiting":
                    queue[idx]["status"] = "downloading"
                    return idx
            return None

    def _worker_slot():
        while True:
            with state_lock:
                if state["cancelled"]:
                    return

            idx = _claim_next_index()
            if idx is None:
                return

            ep = queue[idx]

            with state_lock:
                if state["cancelled"]:
                    return
                if state["paused"]:
                    push_event({"type": "status", "status": "paused"})
            while True:
                with state_lock:
                    if not state["paused"] or state["cancelled"]:
                        break
                time.sleep(0.3)
            with state_lock:
                if state["cancelled"]:
                    return
                state["status"]        = "downloading"
                state["current_index"] = idx
                queue[idx]["status"]   = "downloading"
                queue[idx]["progress"] = 0
                queue[idx]["eta"]      = ""
                queue[idx]["segment"]  = 0

            push_event({"type": "queue", "queue": queue, "current": idx})
            _save_session_snapshot()

            success = _handler(
                ep         = ep,
                index      = idx,
                state      = state,
                state_lock = state_lock,
                push_event = push_event,
                get_proc   = lambda i=idx: _active_procs.get(i),
                set_proc   = lambda p, i=idx: _set_proc(i, p),
                save_fn    = _save_session_snapshot,
            )

            with state_lock:
                if queue[idx]["status"] == "removed":
                    pass
                elif success:
                    queue[idx]["status"]   = "done"
                    queue[idx]["progress"] = 100
                    queue[idx]["eta"]      = ""
                    save_history({
                        "anime": state.get("title", ""),
                        "ep":    ep.get("episode", ""),
                        "file":  ep.get("output_file", ""),
                        "path":  state.get("download_path", ""),
                        "time":  time.strftime("%Y-%m-%d %H:%M"),
                    })
                elif ep.pop("_held_by_user", False):
                    queue[idx]["status"] = "held"
                else:
                    queue[idx]["status"] = "failed"

            push_event({"type": "queue", "queue": queue, "current": idx})
            _save_session_snapshot()

    slots = [threading.Thread(target=_worker_slot, daemon=True) for _ in range(parallel)]
    for t in slots:
        t.start()
    for t in slots:
        t.join()

    with state_lock:
        if state["cancelled"]:
            state["status"] = "idle"
            push_event({"type": "status", "status": "cancelled"})
            return
        state["status"]        = "done"
        state["current_index"] = -1
        unfinished = [ep for ep in queue if ep.get("status") not in ("done", "removed")]

    # TIDAK ada tahap post-process terpisah — tiap episode sudah utuh
    # di folder tujuan begitu status-nya "done" (lihat docstring atas).
    push_event({"type": "status", "status": "done"})

    # BUG FIX (v0.02): dulu delete_session() dipanggil TANPA SYARAT di
    # sini, walau ada episode "failed"/"held" yang belum tuntas —
    # akibatnya tbv_session.json hilang padahal folder tmp episode yang
    # gagal masih ada (berisi segment .ts yang belum lengkap, bisa
    # di-resume aria2c --continue kalau di-retry), dan dm-cli status/
    # bridge.py jadi gak tau ada yang perlu diretry. Sekarang cuma
    # dihapus kalau SEMUA episode "done".
    if unfinished:
        push_event({
            "type": "log",
            "message": f"{len(unfinished)} episode belum tuntas (gagal/ditahan) — "
                       f"tbv_session.json TIDAK dihapus, bisa di-retry.",
        })
    else:
        delete_session()


def _set_proc(idx, proc):
    with _active_procs_lock:
        if proc is None:
            _active_procs.pop(idx, None)
        else:
            _active_procs[idx] = proc


# =========================================
# SESSION SNAPSHOT
# =========================================

def _save_session_snapshot():
    """
    Simpan snapshot state TBV ke tbv_session.json. "episode_dir" tetap
    disimpan (dipakai hold_episode/cancel_episode/cancel_all buat tau
    folder tmp mana yang mau di-terminate/dibersihkan) meski beda dari
    pipeline lama, di TBV folder ini sudah kepakai penuh lifecycle-nya
    di dalam handler itu sendiri.
    """
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        queue_snapshot = []
        for ep in state["queue"]:
            queue_snapshot.append({
                "title":           ep.get("title", ""),
                "episode":         ep.get("episode", ""),
                "m3u8":            ep.get("m3u8", ""),
                "status":          ep.get("status", "waiting"),
                "progress":        ep.get("progress", 0),
                "segment":         ep.get("segment", 0),
                "total_segments":  ep.get("total_segments", 0),
                "eta":             ep.get("eta", ""),
                "output_file":     ep.get("output_file", ""),
                "episode_dir":     ep.get("episode_dir", ""),
            })

        save_session({
            "title":            state.get("title", ""),
            "batch_file":       state.get("batch_file", ""),
            "download_path":    state.get("download_path", ""),
            "input_batch_path": state.get("input_batch_path", ""),
            "format":           state.get("format", "mkv"),
            "timeout":          state.get("timeout", "30"),
            "rate_limit":       state.get("rate_limit", ""),
            "parallel":         state.get("parallel", 1),
            "queue":            queue_snapshot,
            "current_index":    state.get("current_index", 0),
        })


# =========================================
# START / STATUS WORKER
# =========================================

def start_worker():
    """Jalankan _download_worker di thread baru. Aman dipanggil berkali-kali."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_download_worker, daemon=True)
    _worker_thread.start()


def is_worker_alive() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


# =========================================
# KONTROL — SATU EPISODE SPESIFIK
# =========================================

def hold_episode(idx: int) -> bool:
    """Tahan (pause) 1 episode spesifik. Beda dari pause_all() yang nahan SEMUA slot."""
    state      = get_state()
    state_lock = get_lock()
    proc = None
    with state_lock:
        if not (0 <= idx < len(state["queue"])):
            return False
        ep = state["queue"][idx]
        if ep["status"] == "downloading":
            ep["_held_by_user"] = True
            with _active_procs_lock:
                proc = _active_procs.get(idx)
        elif ep["status"] in ("waiting", "failed"):
            ep["status"] = "held"
        else:
            return False

    if proc:
        try:
            proc.terminate()
        except Exception:
            pass

    push_event({"type": "queue", "queue": state["queue"], "current": idx})
    _save_session_snapshot()
    return True


def resume_episode(idx: int) -> bool:
    """Lanjutkan 1 episode yang sedang ditahan/gagal."""
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        if not (0 <= idx < len(state["queue"])):
            return False
        ep = state["queue"][idx]
        if ep["status"] not in ("held", "failed"):
            return False
        ep["status"] = "waiting"
        ep.pop("_held_by_user", None)

    push_event({"type": "queue", "queue": state["queue"], "current": idx})
    _save_session_snapshot()
    start_worker()
    return True


def cancel_episode(idx: int) -> bool:
    """
    Batalkan 1 episode PERMANEN — hentikan aria2c (kalau lagi jalan) +
    bersihkan folder tmp-nya. Tidak bisa di-resume lagi.
    """
    state      = get_state()
    state_lock = get_lock()
    proc = None
    episode_dir = ""
    with state_lock:
        if not (0 <= idx < len(state["queue"])):
            return False
        ep = state["queue"][idx]
        was_downloading = (ep["status"] == "downloading")
        ep["_held_by_user"] = False
        if was_downloading:
            with _active_procs_lock:
                proc = _active_procs.get(idx)
        episode_dir  = ep.get("episode_dir", "")
        ep["status"] = "removed"

    if proc:
        try:
            proc.terminate()
        except Exception:
            pass

    if episode_dir:
        tbv_staging.cleanup_episode_dir(episode_dir)

    push_event({"type": "queue", "queue": state["queue"], "current": idx})
    _save_session_snapshot()
    return True


# =========================================
# KONTROL — SEMUA SEKALIGUS
# =========================================

def pause_all():
    """Pause seluruh slot yang lagi jalan lewat flag global."""
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        if state["status"] == "downloading":
            state["paused"] = True
            state["status"] = "paused"
    push_event({"type": "status", "status": "paused"})


def resume_all():
    """Lanjutkan seluruh slot yang ditahan lewat pause_all()."""
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        if state["status"] == "paused":
            state["paused"]    = False
            state["cancelled"] = False
            state["status"]    = "downloading"
    push_event({"type": "status", "status": "downloading"})
    start_worker()


def cancel_all():
    """
    Batalkan SEMUA — hentikan seluruh aria2c aktif, bersihkan folder tmp
    tiap episode yang belum "done"/"removed", hapus session TBV.
    """
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        state["cancelled"] = True
        state["paused"]    = False
        state["status"]    = "idle"
        queue_snapshot = list(state["queue"])

    with _active_procs_lock:
        procs = list(_active_procs.values())
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass

    for ep in queue_snapshot:
        if ep.get("status") not in ("done", "removed"):
            episode_dir = ep.get("episode_dir", "")
            if episode_dir:
                tbv_staging.cleanup_episode_dir(episode_dir)

    delete_session()
    push_event({"type": "status", "status": "cancelled"})


# =========================================
# RETRY GAGAL
# =========================================

def has_failed_episodes() -> bool:
    state = get_state()
    return any(ep.get("status") == "failed" for ep in state["queue"])


def retry_failed() -> bool:
    """Set ulang semua episode 'failed' jadi 'waiting', lalu jalankan worker lagi."""
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        any_failed = False
        for ep in state["queue"]:
            if ep["status"] == "failed":
                ep["status"]   = "waiting"
                ep["progress"] = 0
                ep["eta"]      = ""
                ep["segment"]  = 0
                any_failed = True
        if not any_failed:
            return False
        state["paused"]    = False
        state["cancelled"] = False
        state["status"]    = "downloading"

    _save_session_snapshot()
    start_worker()
    return True


# =========================================
# SESSION — LANJUTKAN / BUANG SESI LAMA
# =========================================

def resume_from_session(session_data: dict):
    """
    Restore queue & pengaturan dari session TBV yang di-load, lalu
    jalankan worker (dipanggil setelah user pilih "lanjutkan download
    TBV sebelumnya" di menu). Episode yang statusnya masih "downloading"
    saat crash otomatis dilanjut oleh worker (bukan resume manual) —
    aria2c --continue=true di handlers/download_tbv.py yang urus
    melanjutkan segment yang belum lengkap, prepare_segment_list()
    dipanggil ulang jadi tidak masalah walau aria2_input.txt lama masih
    ada.
    """
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        state.update({
            "title":            session_data.get("title", ""),
            "batch_file":       session_data.get("batch_file", ""),
            "download_path":    session_data.get("download_path", ""),
            "input_batch_path": session_data.get("input_batch_path", ""),
            "format":           session_data.get("format", "mkv"),
            "timeout":          session_data.get("timeout", "30"),
            "rate_limit":       session_data.get("rate_limit", ""),
            "parallel":         session_data.get("parallel", 1),
            "queue":            session_data.get("queue", []),
            "current_index":    session_data.get("current_index", 0),
            "status":           "idle",
            "paused":           False,
            "cancelled":        False,
        })

    # episode yang tadinya "downloading" saat crash -> balikin ke
    # "waiting" biar diklaim ulang worker, folder tmp-nya biarkan apa
    # adanya (aria2c --continue akan lanjut dari situ, bukan dari nol).
    with state_lock:
        for ep in state["queue"]:
            if ep.get("status") == "downloading":
                ep["status"] = "waiting"

    start_worker()


def discard_session():
    """
    Buang sesi TBV lama sepenuhnya: hapus tbv_session.json, bersihkan
    SELURUH folder tmp/tbv, reset state TBV ke kosong.
    """
    delete_session()
    tbv_staging.cleanup_all()
    reset_state()
