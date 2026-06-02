"""
Обновление бота с GitHub — скачивает ТОЛЬКО изменённые файлы.

Как это работает (без git на машине):
1. Берём дерево файлов нужной ветки через GitHub API (1 запрос).
2. Для каждого файла из репозитория считаем git-blob-SHA локальной копии
   и сравниваем с SHA на GitHub. Совпало — файл не трогаем.
3. Изменённые/новые файлы скачиваем по raw-ссылке (raw.githubusercontent.com
   не тратит лимит GitHub API) и пишем атомарно (tmp + os.replace).
4. Опционально: если в репозитории есть delete.json (список путей) — удаляем
   перечисленные файлы (как в FunPay Cardinal).

Пользовательские данные не трогаются никогда — см. PROTECTED_PREFIXES.
Если задан прокси (config.funpay.proxy) — все запросы идут через него,
чтобы обновление работало там, где GitHub режется.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from config.settings import config_manager
from utils.logger import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Эти пути НИКОГДА не перезаписываются и не удаляются обновлением —
# здесь лежат настройки, ключи автовыдачи, плагины пользователя, логи и venv.
PROTECTED_PREFIXES = (
    "data/",
    "plugins_user/",
    "venv/",
    ".venv/",
    ".git/",
    "__pycache__/",
)
# Точечные файлы, которые тоже не трогаем.
PROTECTED_FILES = {
    "config.json",
    ".gitignore",
}

_REQUEST_TIMEOUT = 30


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательное
# ──────────────────────────────────────────────────────────────────────────────

def _proxies() -> Optional[Dict[str, str]]:
    proxy = config_manager.settings.funpay.proxy
    if not proxy:
        return None
    if "@" not in proxy and proxy.count(":") == 3:
        login, password, ip, port = proxy.split(":")
        proxy = f"{login}:{password}@{ip}:{port}"
    url = f"http://{proxy}"
    return {"http": url, "https": url}


def _is_protected(rel_path: str) -> bool:
    rel = rel_path.replace("\\", "/")
    if rel in PROTECTED_FILES:
        return True
    return any(rel.startswith(p) for p in PROTECTED_PREFIXES)


def _git_blob_sha(data: bytes) -> str:
    """SHA1 в формате git blob: sha1("blob <size>\\0<data>")."""
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode() + b"\x00")
    h.update(data)
    return h.hexdigest()


def _local_blob_sha(rel_path: str) -> Optional[str]:
    path = PROJECT_ROOT / rel_path
    if not path.is_file():
        return None
    try:
        return _git_blob_sha(path.read_bytes())
    except Exception:
        return None


def _atomic_write_bytes(rel_path: str, data: bytes) -> None:
    path = PROJECT_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".upd_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


# ──────────────────────────────────────────────────────────────────────────────
# GitHub
# ──────────────────────────────────────────────────────────────────────────────

def _repo() -> str:
    return config_manager.settings.update.repo.strip().strip("/")


def _branch() -> str:
    return config_manager.settings.update.branch.strip() or "main"


def fetch_remote_tree() -> Tuple[Optional[List[dict]], Optional[str]]:
    """
    Возвращает (список blob-ов вида {path, sha}, ошибка).
    blob-ы — только файлы (не папки).
    """
    repo, branch = _repo(), _branch()
    url = f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
    try:
        r = requests.get(
            url,
            headers={"Accept": "application/vnd.github+json"},
            proxies=_proxies(),
            timeout=_REQUEST_TIMEOUT,
        )
    except Exception as e:
        return None, f"Нет связи с GitHub: {e}"

    if r.status_code == 404:
        return None, f"Репозиторий или ветка не найдены: {repo}@{branch}"
    if r.status_code == 403:
        return None, "GitHub отклонил запрос (лимит API). Попробуйте позже."
    if r.status_code != 200:
        return None, f"GitHub вернул код {r.status_code}"

    try:
        data = r.json()
    except Exception as e:
        return None, f"Не удалось разобрать ответ GitHub: {e}"

    if data.get("truncated"):
        logger.warning("updater: дерево GitHub усечено (репозиторий очень большой)")

    blobs = [
        {"path": el["path"], "sha": el["sha"]}
        for el in data.get("tree", [])
        if el.get("type") == "blob"
    ]
    if not blobs:
        return None, "GitHub вернул пустой список файлов"
    return blobs, None


def compute_changes() -> Tuple[Optional[List[dict]], Optional[str]]:
    """
    Список файлов, которые отличаются от GitHub (новые/изменённые).
    Защищённые пути исключены. Возвращает (changed, ошибка).
    """
    blobs, err = fetch_remote_tree()
    if err:
        return None, err

    changed = []
    for b in blobs:
        rel = b["path"]
        if _is_protected(rel):
            continue
        if _local_blob_sha(rel) != b["sha"]:
            changed.append(b)
    return changed, None


def download_file(rel_path: str) -> Optional[bytes]:
    repo, branch = _repo(), _branch()
    # Кодируем путь по сегментам, оставляя слэши.
    safe = "/".join(requests.utils.quote(seg) for seg in rel_path.split("/"))
    url = f"https://raw.githubusercontent.com/{repo}/{branch}/{safe}"
    try:
        r = requests.get(url, proxies=_proxies(), timeout=_REQUEST_TIMEOUT)
        if r.status_code != 200:
            logger.warning(f"updater: {rel_path} → код {r.status_code}")
            return None
        return r.content
    except Exception as e:
        logger.warning(f"updater: не скачал {rel_path}: {e}")
        return None


def _process_deletions() -> List[str]:
    """
    Если в скачанном репозитории есть delete.json (список путей) — удаляет их.
    Защищённые пути не трогает. Возвращает список удалённых.
    """
    deleted: List[str] = []
    data = download_file("delete.json")
    if not data:
        return deleted
    try:
        import json
        paths = json.loads(data.decode("utf-8"))
    except Exception:
        return deleted
    for rel in paths:
        if not isinstance(rel, str) or _is_protected(rel):
            continue
        target = PROJECT_ROOT / rel
        try:
            if target.is_file():
                target.unlink()
                deleted.append(rel)
            elif target.is_dir():
                import shutil
                shutil.rmtree(target, ignore_errors=True)
                deleted.append(rel)
        except Exception as e:
            logger.debug(f"updater: не удалил {rel}: {e}")
    return deleted


# ──────────────────────────────────────────────────────────────────────────────
# Публичные функции
# ──────────────────────────────────────────────────────────────────────────────

def check_only() -> Tuple[Optional[List[str]], Optional[str]]:
    """Только проверка: список путей, которые обновятся (без скачивания файлов)."""
    changed, err = compute_changes()
    if err:
        return None, err
    return [b["path"] for b in changed], None


def perform_update() -> dict:
    """
    Скачивает и применяет только изменённые файлы.

    Возвращает dict:
        {"ok": bool, "updated": [...], "failed": [...], "deleted": [...], "error": str|None}
    """
    if not config_manager.settings.update.enabled:
        return {"ok": False, "updated": [], "failed": [], "deleted": [], "error": "Обновления отключены в настройках."}

    changed, err = compute_changes()
    if err:
        return {"ok": False, "updated": [], "failed": [], "deleted": [], "error": err}

    updated: List[str] = []
    failed: List[str] = []

    for b in changed:
        rel = b["path"]
        data = download_file(rel)
        if data is None:
            failed.append(rel)
            continue
        # Подтверждаем, что скачали именно ту версию (защита от подмены/обрыва).
        if _git_blob_sha(data) != b["sha"]:
            logger.warning(f"updater: SHA не совпал для {rel} — пропуск")
            failed.append(rel)
            continue
        try:
            _atomic_write_bytes(rel, data)
            updated.append(rel)
        except Exception as e:
            logger.warning(f"updater: не записал {rel}: {e}")
            failed.append(rel)

    deleted = _process_deletions()

    if updated or deleted:
        logger.info(f"updater: обновлено {len(updated)}, удалено {len(deleted)}, ошибок {len(failed)}")

    return {
        "ok": not failed,
        "updated": updated,
        "failed": failed,
        "deleted": deleted,
        "error": None,
    }
