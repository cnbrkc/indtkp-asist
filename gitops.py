"""Depoya yazma: atomik JSON, git/GitHub API ile config.json kaydı ve sonraki çalıştırmayı tetikleme."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import tempfile
import threading
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from commandtext import ConfigStore, SAVE_STATUS_TEXT

log = logging.getLogger("telegram-filter")


async def dispatch_next_run(pat: str) -> tuple[bool, str]:
    """Yeni bir Actions çalışması başlatır. GH_PAT yoksa manuel moda düşer."""
    if not pat:
        return False, "GH_PAT tanımlı değil; Actions sayfasından 'Run workflow' ile başlat."
    repository = os.getenv("GITHUB_REPOSITORY", "")
    workflow = os.getenv("GITHUB_WORKFLOW_FILE", "telegram-monitor.yml")
    ref = os.getenv("GITHUB_REF_NAME", "main")
    if not repository:
        return False, "GITHUB_REPOSITORY bulunamadı (Actions dışında mı çalışıyor?)."
    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    request = urllib.request.Request(
        url,
        data=json.dumps({"ref": ref}).encode(),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {pat}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with await asyncio.to_thread(urllib.request.urlopen, request, timeout=20) as response:
            response.read()
        log.info("Sonraki GitHub Actions çalışması başlatıldı (%s/%s @ %s).", repository, workflow, ref)
        return True, f"Yeni çalışma başlatıldı ({repository}@{ref})."
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        log.error("Actions başlatılamadı: HTTP %s %s", exc.code, body)
        return False, f"GitHub isteği başarısız: HTTP {exc.code}. GH_PAT'in 'Actions: Read and write' yetkisi var mı?"
    except Exception as exc:  # noqa: BLE001 - ağ hatası bot'u öldürmemeli
        log.exception("Actions başlatılamadı.")
        return False, f"GitHub isteği başarısız: {type(exc).__name__}."


def atomic_write_json(path: Path, data: dict) -> None:
    """JSON'u yarıda kalmayacak şekilde yaz: geçici dosya → fsync → os.replace.

    Yarım yazılmış bir config.json bot'u açılışta çökertir; bu yüzden doğrudan
    hedef dosyaya yazmıyoruz.
    """
    path = Path(path)
    persisted_data = dict(data)
    persisted_data.pop("notify_bot_token", None)
    payload = json.dumps(persisted_data, ensure_ascii=False, indent=2) + "\n"
    directory = str(path.parent) if str(path.parent) else "."
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    )
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    try:  # dizin girdisinin de diske inmesini garantile
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _run_git(args: Sequence[str], cwd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "git komutu bulunamadı"
    except subprocess.TimeoutExpired:
        return 124, f"git {args[0]} zaman aşımına uğradı"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def git_repo_root(path: str | os.PathLike[str]) -> Path | None:
    """Verilen yolun bağlı olduğu git deposunun kökünü bul; yoksa None."""
    code, out = _run_git(["rev-parse", "--show-toplevel"], cwd=str(path))
    if code == 0 and out:
        return Path(out.splitlines()[0].strip())
    return None


def push_token() -> str:
    """Depoya yazmak için kullanılabilecek token (ilk dolu olan)."""
    for name in ("CONFIG_PUSH_TOKEN", "GITHUB_TOKEN", "GH_PAT"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def current_branch(repo: Path) -> str:
    code, out = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo))
    branch = out.splitlines()[0].strip() if code == 0 and out else ""
    if not branch or branch == "HEAD":  # detached HEAD
        branch = os.getenv("GITHUB_REF_NAME", "").strip()
    return branch


GIT_WRITE_LOCK = threading.RLock()


def commit_and_push(path: Path, message: str) -> tuple[str, str]:
    with GIT_WRITE_LOCK:
        return _commit_and_push_locked(path, message)


def _commit_and_push_locked(path: Path, message: str) -> tuple[str, str]:
    """config.json'ı commit'leyip origin'e gönder: (durum, detay).

    durum: pushed | clean | local | no-repo | error
    Hiçbir durumda istisna fırlatmaz; çağıran yalnızca raporlar.
    """
    path = Path(path)
    repo = git_repo_root(path.parent if path.parent != Path("") else ".")
    commit_message = f"config: {message}" if message else "config: güncelleme"
    if repo is None:
        return _push_via_api(path, commit_message)
    try:
        relative = str(path.resolve().relative_to(repo.resolve()))
    except (ValueError, OSError):
        return "no-repo", "config.json depo dışında"

    token = push_token()
    code, out = _run_git(["status", "--porcelain", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git status başarısız: {out[:160]}"
    if not out.strip():
        return "clean", "depoda commit edilecek değişiklik yok"

    code, out = _run_git(["add", "--", relative], cwd=str(repo))
    if code != 0:
        return "error", f"git add başarısız: {out[:160]}"
    code, out = _run_git(
        ["-c", "user.name=telegram-indirim-takipci", "-c",
         "user.email=actions@github.invalid", "commit", "-m", commit_message, "--", relative],
        cwd=str(repo),
    )
    if code != 0:
        return "error", f"git commit başarısız: {out[:160]}"

    # Sıra önemli: Actions'ın GITHUB_REF_NAME'i pull_request olayında dal adı
    # değil "4/merge" gibi bir birleştirme referansıdır. Onu doğrudan refspec
    # olarak kullanmak depoda gereksiz bir dal açar; yerel dal daha güvenilir.
    branch = (os.getenv("CONFIG_PUSH_BRANCH", "").strip()
              or current_branch(repo)
              or os.getenv("GITHUB_REF_NAME", "").strip())
    if not branch:
        return "local", "dal adı bulunamadı; commit yerelde kaldı"

    code, out = push_branch(repo, branch, token)
    if code != 0 and ("non-fast-forward" in out or "fetch first" in out or "rejected" in out):
        _run_git(["pull", "--rebase", "--no-edit", "origin", branch], cwd=str(repo), timeout=60)
        code, out = push_branch(repo, branch, token)
    if code != 0:
        if not token:
            return "local", (f"depoya gönderilemedi ve push token'ı yok; commit yerelde kaldı "
                             f"(git push origin {branch}). Hata: {out[:160]}")
        return "error", f"git push başarısız: {out[:200]}"
    return "pushed", f"origin/{branch}"


def push_branch(repo: Path, branch: str, token: str = "") -> tuple[int, str]:
    """Dalı ``origin``e gönder.

    Önce ortamda hazır kimlik bilgisi varmış gibi düz ``git push`` dener;
    GitHub Actions'ta ``actions/checkout`` kimliği ``.git/config``e yazar ve
    job'a ``contents: write`` verildiği için bu tek başına yeterlidir.
    Başarısız olursa token'ı ``http.extraheader`` ile enjekte ederek tekrar
    dener (VM'de çalıştırma senaryosu).

    Sıra bilinçli: Actions'ta üstüne bir de ``http.extraheader`` eklemek
    çift ``Authorization`` başlığı üretip push'u bozabilir.
    """
    plain = ["push", "origin", f"HEAD:{branch}"]
    code, out = _run_git(plain, cwd=str(repo), timeout=60)
    if code == 0 or not token:
        return code, out
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return _run_git(
        ["-c", f"http.extraheader=AUTHORIZATION: basic {auth}", *plain],
        cwd=str(repo), timeout=60,
    )


def _github_api(method: str, url: str, token: str, payload: dict | None = None) -> tuple[bool, Any, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "telegram-indirim-takipci",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8", "replace")
        return True, (json.loads(body) if body else {}), ""
    except urllib.error.HTTPError as exc:
        return False, None, f"HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:160]}"
    except Exception as exc:  # noqa: BLE001 - ağ hatası ayar komutunu öldürmemeli
        return False, None, f"{type(exc).__name__}: {exc}"


def _push_via_api(path: Path, message: str) -> tuple[str, str]:
    """git yoksa/config depo dışındaysa GitHub Contents API ile güncelle."""
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    token = push_token()
    if not repository or not token:
        return "no-repo", "GITHUB_REPOSITORY veya push token'ı yok"
    branch = (os.getenv("CONFIG_PUSH_BRANCH") or os.getenv("GITHUB_REF_NAME") or "main").strip()
    url = f"https://api.github.com/repos/{repository}/contents/{urllib.parse.quote(path.name)}"
    ok, current, _ = _github_api("GET", f"{url}?ref={branch}", token)
    payload = {
        "message": message,
        "content": base64.b64encode(path.read_bytes()).decode(),
        "branch": branch,
    }
    if ok and isinstance(current, dict) and current.get("sha"):
        payload["sha"] = current["sha"]
    ok, _, detail = _github_api("PUT", url, token, payload)
    if not ok:
        return "error", detail or "Contents API isteği başarısız"
    return "pushed", f"{repository}@{branch} (API)"


async def save_config(store: ConfigStore, note: str = "") -> tuple[bool, str]:
    """config.json'ı atomik yaz ve mümkse repo'ya işle: (yazıldı_mı, rapor)."""
    try:
        await asyncio.to_thread(atomic_write_json, store.path, store.config)
    except OSError as exc:
        return False, (f"⚠️ Ayar çalışan botta aktif ama config.json yazılamadı "
                       f"({type(exc).__name__}: {exc}) — yeniden başlatınca kaybolur.")
    status, detail = await asyncio.to_thread(commit_and_push, store.path, note)
    return True, SAVE_STATUS_TEXT.get(status, SAVE_STATUS_TEXT["error"]).format(detail=detail)
