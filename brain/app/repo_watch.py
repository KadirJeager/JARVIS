"""GitHub repo takibi: izlenen repo'ların yeni release/commit'lerini olaya çevirir.

Saf mantık + ince HTTP katmanı (stdlib urllib — live_model.py deseni, yeni
bağımlılık yok). Kota dostu: her uç ETag ile koşullu istek atar, değişmeyen
repo 304 döner ve GitHub tarafında 0 kota harcar. Scheduler log'u için özet
dict Türkçe anahtarlı: {"kontrol", "olay", "hata"}.

Hata modeli (İlke 4, hata = gözlem): repo başına hata izole — bir repo'nun
404'ü/ağ hatası last_error'a yazılır ve tur diğer repo'larla devam eder.
Yeni eklenen repo ilk turda olay ÜRETMEZ; mevcut durumu baseline olur
(spec §3).
"""
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

WATCH_COLLECTION = "repo_watch"
EVENTS_COLLECTION = "repo_watch_events"
MAX_COMMITS = 10
RELEASE_DETAIL_CHARS = 500

_API_BASE = "https://api.github.com"
_TIMEOUT_SECONDS = 15
_USER_AGENT = "jarvis-brain-repo-watch"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitHubError(Exception):
    """GitHub API/ulaşım hatası — metni gözlem olarak last_error'a yazılır.

    `status` HTTP kodudur (ağ hatasında None): releases ucundaki 404 'release
    yok' demektir ve hata sayılmaz; diğer her durum hatadır."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class GitHubClient:
    """Minimal GitHub REST istemcisi: koşullu GET (ETag/If-None-Match).

    Token opsiyonel (GITHUB_TOKEN env): yoksa anonim kota (60 istek/saat/IP —
    ~10 repo x 2 uç x saatlik = 20 istek, yeterli; spec §3)."""

    def __init__(self, token: str = ""):
        self._token = token

    def get(self, path: str, etag: str | None = None) -> tuple[int, str | None, object]:
        """GET <api-base><path> -> (status, etag, json|None).

        304 -> (304, gönderilen etag, None). 2xx -> parse edilmiş JSON.
        Diğer her durum (4xx/5xx, ağ, bozuk JSON) GitHubError fırlatır."""
        request = urllib.request.Request(
            f"{_API_BASE}{path}",
            headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/vnd.github+json",
            },
        )
        if etag:
            request.add_header("If-None-Match", etag)
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read()
                return response.status, response.headers.get("ETag"), json.loads(body)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                return 304, etag, None
            raise GitHubError(f"GitHub {exc.code}: {path}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise GitHubError(f"GitHub erişim hatası ({path}): {exc}") from exc


def make_client() -> GitHubClient:
    """Prod istemci: GITHUB_TOKEN varsa Authorization header'lı."""
    return GitHubClient(token=os.environ.get("GITHUB_TOKEN", ""))


def poll_once(db, client: GitHubClient, now_fn=_now) -> dict:
    """İzlenen tüm repo'ları bir tur kontrol eder; özet dict döner.

    Repo başına hata izolasyonu: GitHubError -> last_error yazılır, o repo
    atlanır, tur devam eder. Başarılı turda last_error None'a çekilir."""
    summary = {"kontrol": 0, "olay": 0, "hata": 0}
    for snap in db.collection(WATCH_COLLECTION).stream():
        doc = snap.to_dict()
        repo = doc["repo"]
        summary["kontrol"] += 1
        now = now_fn()
        try:
            events, updates = _poll_repo(client, repo, doc, now)
        except GitHubError as exc:
            logging.warning("repo_watch: %s kontrolü hata verdi: %s", repo, exc)
            summary["hata"] += 1
            db.collection(WATCH_COLLECTION).document(repo).set(
                {"last_check": now, "last_error": str(exc)}, merge=True
            )
            continue
        for event in events:
            db.collection(EVENTS_COLLECTION).add(event)
        summary["olay"] += len(events)
        db.collection(WATCH_COLLECTION).document(repo).set(updates, merge=True)
    return summary


def _poll_repo(client: GitHubClient, repo: str, doc: dict, now: str) -> tuple[list, dict]:
    """Tek repo: release + commits soruları. (olaylar, durum güncellemesi) döner."""
    events = []
    updates = {"last_check": now, "last_error": None}

    try:
        status, etag, data = client.get(f"/repos/{repo}/releases/latest", doc.get("release_etag"))
    except GitHubError as exc:
        if exc.status != 404:
            raise
        # releases ucunda 404 = repo'nun release'i yok; hata değil (spec §3).
        status, etag, data = 404, None, None
    if etag:
        updates["release_etag"] = etag
    if status == 200 and data:
        tag = data.get("tag_name", "")
        if doc.get("last_release_tag") is None:
            updates["last_release_tag"] = tag  # baseline: ilk turda olay üretme
        elif tag != doc["last_release_tag"]:
            events.append({
                "repo": repo,
                "kind": "release",
                "title": tag,
                "detail": (data.get("body") or "")[:RELEASE_DETAIL_CHARS],
                "url": data.get("html_url") or f"https://github.com/{repo}/releases",
                "ts": now,
                "surfaced": False,
            })
            updates["last_release_tag"] = tag

    status, etag, data = client.get(
        f"/repos/{repo}/commits?per_page={MAX_COMMITS}", doc.get("commits_etag")
    )
    if etag:
        updates["commits_etag"] = etag
    if status == 200 and data:
        commits = [c for c in data if isinstance(c, dict) and c.get("sha")]
        if doc.get("last_commit_sha") is None:
            if commits:
                updates["last_commit_sha"] = commits[0]["sha"]  # baseline
        else:
            new_commits = []
            for commit in commits:
                if commit["sha"] == doc["last_commit_sha"]:
                    break  # GitHub newest-first döner: baseline'a kadar yenidir
                new_commits.append(commit)
            if new_commits:
                titles = [
                    (c.get("commit", {}).get("message") or "").splitlines()[0]
                    for c in new_commits[:MAX_COMMITS]
                ]
                events.append({
                    "repo": repo,
                    "kind": "commits",
                    "title": f"{len(new_commits)} yeni commit",
                    "detail": "\n".join(titles),
                    "url": f"https://github.com/{repo}/commits",
                    "ts": now,
                    "surfaced": False,
                })
                updates["last_commit_sha"] = new_commits[0]["sha"]

    return events, updates
