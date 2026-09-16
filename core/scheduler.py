#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/scheduler.py
Version: v0.01

Worker rolling-parallel + API kontrol pause/resume/cancel (single-target
maupun semua sekaligus) + kontrol session (resume/discard). Port dari
download_worker()/_claim_next_index()/_get_handler()/_do_save_session()/
_start_worker() di app.py lama, plus logic dari route /hold-ep,
/resume-ep, /delete-ep, /pause, /resume, /cancel, /retry-failed,
/session-restore — semua ditulis ulang sebagai fungsi biasa (bukan
Flask route) yang dipanggil dari cli/views/control_view.py.

ARSITEKTUR KONTROL (penting dipahami sebelum baca kode):
  - Pause/resume/cancel SATU episode spesifik (dashboard multi-slot minta
    nomor) pakai mekanisme PERSIS SAMA seperti "hold-ep" di versi lama:
    set ep["_held_by_user"]=True lalu terminate subprocess-nya langsung
    (lewat _active_procs). handlers/download.py & handlers/other.py
    TIDAK PERLU DIUBAH sama sekali — _run_download() di sana sudah
    otomatis menangani subprocess yang di-terminate paksa (return False),
    lalu _worker_slot() di sini yang menandai status "held" karena ada
    flag _held_by_user.
  - Pause/resume/cancel SEMUA sekaligus (opsi "a" di dashboard, atau
    hotkey langsung di mode single-slot) pakai flag global
    state["paused"]/state["cancelled"] — mekanisme asli yang sudah ada
    dari awal di _run_download(), otomatis berlaku ke semua slot yang
    lagi jalan tanpa kode tambahan.

DEPENDENSI: "episode_dir" WAJIB ada di whitelist snapshot session
(_save_session_snapshot di bawah) — dipakai handlers/post_process.py
buat tau folder tmp mana yang harus dipindah/dibersihkan setelah semua
episode selesai.
"""

import threading
import time

from core.state import get_state, get_lock, reset_state
from core.session import save_session, delete_session
from core.history import save_history
from core import staging
from cli.notifier import push_event

from handlers.download import download_episode, cleanup_tmp_manifests
from handlers.other import download_other
from handlers.post_process import run as post_process_run


# =========================================
# STATE MODUL — proses aktif per index + worker thread
# =========================================

_active_procs      = {}
_active_procs_lock = threading.Lock()
_worker_thread      = None


# =========================================
# HANDLER ROUTING
# =========================================

_HANDLER_MAP = {
    "manual": download_episode,
    "multi":  download_episode,
    "other":  download_other,
}


def _get_handler(mode: str):
    """Return fungsi handler sesuai mode. Tambah entri baru di sini kalau ada mode baru."""
    return _HANDLER_MAP.get(mode, download_episode)


# =========================================
# WORKER UTAMA — model ROLLING (persis logic lama)
# =========================================

def _download_worker():
    """
    Worker paralel model ROLLING: begitu 1 slot kosong (episode selesai),
    langsung ambil episode berikutnya dari antrean — tidak menunggu
    episode lain dalam "grup" yang sama selesai.

    Jumlah slot bersamaan = state["parallel"]. parallel=1 -> serial.
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
                queue[idx]["speed"]    = ""

            push_event({"type": "queue", "queue": queue, "current": idx})
            _save_session_snapshot()

            mode    = state.get("mode", "manual")
            handler = _get_handler(mode)
            success = handler(
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
                        "anime": state["anime"],
                        "ep":    ep.get("ep", ""),
                        "file":  ep.get("output_file", ""),
                        "path":  state["download_path"],
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

    push_event({"type": "status", "status": "done"})

    try:
        cleanup_tmp_manifests()
    except Exception:
        pass

    try:
        push_event({"type": "log", "message": "Memulai post-process..."})
        post_process_run(push_event=push_event)
    except Exception as e:
        push_event({"type": "log", "message": f"Post-process error: {e}"})
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
    Simpan snapshot state ke session.json — dipanggil dari worker &
    handler download. "episode_dir" WAJIB ada di sini (lihat catatan
    dependensi di docstring atas file).
    """
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        queue_snapshot = []
        for ep in state["queue"]:
            queue_snapshot.append({
                "ep":             ep.get("ep", ""),
                "link":           ep.get("link", ""),
                "sub":            ep.get("sub", ""),
                "duration":       ep.get("duration", "off"),
                "quality":        ep.get("quality", "best"),
                "mp3_only":       ep.get("mp3_only", False),
                "status":         ep.get("status", "waiting"),
                "progress":       ep.get("progress", 0),
                "segment":        ep.get("segment", 0),
                "total_segments": ep.get("total_segments", 0),
                "speed":          ep.get("speed", ""),
                "eta":            ep.get("eta", ""),
                "output_file":    ep.get("output_file", ""),
                "episode_dir":    ep.get("episode_dir", ""),
            })

        save_session({
            "anime":            state["anime"],
            "download_path":    state["download_path"],
            "input_batch_path": state.get("input_batch_path", ""),
            "config_file":      state["config_file"],
            "format":           state["format"],
            "timeout":          state["timeout"],
            "conn":             state["conn"],
            "parallel":         state["parallel"],
            "rate_limit":       state["rate_limit"],
            "mode":             state["mode"],
            "queue":            queue_snapshot,
            "current_index":    state["current_index"],
        })


# =========================================
# START / STATUS WORKER
# =========================================

def start_worker():
    """
    Jalankan _download_worker di thread baru. Aman dipanggil berkali-kali
    — tidak akan spawn worker kedua kalau yang lama masih hidup.
    """
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_download_worker, daemon=True)
    _worker_thread.start()


def is_worker_alive() -> bool:
    return bool(_worker_thread and _worker_thread.is_alive())


# =========================================
# KONTROL — SATU EPISODE SPESIFIK (dashboard multi-slot, pilih nomor)
# =========================================

def hold_episode(idx: int) -> bool:
    """
    Tahan (pause) 1 episode spesifik. Beda dari pause_all() yang nahan
    SEMUA slot — ini cuma target 1 index. Return True kalau berhasil.
    """
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
    start_worker()   # jaga-jaga kalau worker sebelumnya sudah berhenti total
    return True


def cancel_episode(idx: int) -> bool:
    """
    Batalkan 1 episode secara PERMANEN — hentikan proses (kalau lagi
    jalan) + bersihkan folder tmp staging-nya. Beda dari hold: episode
    ini tidak bisa di-resume lagi, harus dimasukkan ulang ke antrean
    kalau mau download lagi.
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
        ep["_held_by_user"] = False   # biar finalize block tidak nandain 'held'
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
        staging.cleanup_episode_dir(episode_dir)

    push_event({"type": "queue", "queue": state["queue"], "current": idx})
    _save_session_snapshot()
    return True


# =========================================
# KONTROL — SEMUA SEKALIGUS (opsi "a", atau mode single-slot langsung)
# =========================================

def pause_all():
    """Pause seluruh slot yang lagi jalan lewat flag global — otomatis
    kepakai di semua thread _run_download() yang aktif."""
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
    Batalkan SEMUA — hentikan seluruh proses aktif, bersihkan folder tmp
    tiap episode yang belum "done", hapus session.
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
                staging.cleanup_episode_dir(episode_dir)

    delete_session()
    try:
        cleanup_tmp_manifests()
    except Exception:
        pass
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
                ep["speed"]    = ""
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
    Restore queue & pengaturan dari session yang di-load, lalu jalankan
    worker (dipanggil setelah user pilih "0. Lanjutkan download
    sebelumnya" di menu utama).
    """
    state      = get_state()
    state_lock = get_lock()
    with state_lock:
        state.update({
            "anime":            session_data.get("anime", ""),
            "download_path":    session_data.get("download_path", ""),
            "input_batch_path": session_data.get("input_batch_path", ""),
            "config_file":      session_data.get("config_file", ""),
            "format":           session_data.get("format", "mkv"),
            "timeout":          session_data.get("timeout", "30"),
            "conn":             session_data.get("conn", "8"),
            "parallel":         session_data.get("parallel", 1),
            "rate_limit":       session_data.get("rate_limit", ""),
            "mode":             session_data.get("mode", "manual"),
            "queue":            session_data.get("queue", []),
            "current_index":    session_data.get("current_index", 0),
            "status":           "idle",
            "paused":           False,
            "cancelled":        False,
        })
    start_worker()


def discard_session():
    """
    Buang sesi lama sepenuhnya: hapus session.json, bersihkan SELURUH
    folder tmp staging, reset state ke kosong. Dipanggil dari view
    setelah user konfirmasi warning "mulai baru akan menghapus sesi lama"
    (bag. 6 desain).
    """
    delete_session()
    staging.cleanup_all()
    reset_state()
