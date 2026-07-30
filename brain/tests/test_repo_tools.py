"""Repo takip araçları (watch/unwatch/list/updates) — FakeDB + enjekte sahte client.

Araçlar hata FIRLATMAZ; Türkçe gözlem dict'i döner (get_speaker_status deseni).
Mutasyon pimi: get_repo_updates'in surfaced işaretlemesi silinirse 'ikinci
çağrı boş döner' testi kırılmalı.
"""
import pytest

from app import config, repo_watch, tools
from app.memory import Memory
from tests.fakes import FakeDB

REPO = "owner/repo"
NOW = "2026-07-30T10:00:00+00:00"


class FakeGitHubClient:
    """Varsayılan: repo var (200). Senaryo: path -> hata ya da (status, etag, json)."""

    def __init__(self, responses=None):
        self._responses = responses or {}

    def get(self, path, etag=None):
        resp = self._responses.get(path, (200, None, {"full_name": path}))
        if isinstance(resp, Exception):
            raise resp
        return resp


@pytest.fixture()
def db(monkeypatch):
    fake_db = FakeDB()
    tools.init(Memory(fake_db))
    monkeypatch.setattr(tools, "_github_client", FakeGitHubClient())
    return fake_db


def _watched(db, repo=REPO):
    return db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(repo)).get()


def _seed_event(db, repo=REPO, kind="release", title="v1.0.0", ts=NOW, surfaced=False):
    db.collection(repo_watch.EVENTS_COLLECTION).add({
        "repo": repo, "kind": kind, "title": title,
        "detail": "detay", "url": "https://github.com/x", "ts": ts,
        "surfaced": surfaced,
    })


def test_watch_repo_rejects_non_owner_repo_format(db):
    for bad in ("foo", "a/b/c", "/repo", "owner/", "owner/repo extra"):
        result = tools.watch_repo(bad)
        assert "hata" in result, bad
    assert not _watched(db).exists


def test_watch_repo_github_404_refuses_without_writing(db, monkeypatch):
    monkeypatch.setattr(tools, "_github_client", FakeGitHubClient({
        f"/repos/{REPO}": repo_watch.GitHubError("GitHub 404: /repos/owner/repo", status=404),
    }))

    result = tools.watch_repo(REPO)

    assert "hata" in result
    assert "bulunamadı" in result["hata"]
    assert not _watched(db).exists


def test_watch_repo_success_creates_doc_with_empty_baseline(db):
    result = tools.watch_repo(REPO, note="görsel RAG adayı")

    assert "izlemeye alındı" in result["sonuc"]
    doc = _watched(db).to_dict()
    assert doc["repo"] == REPO
    assert doc["note"] == "görsel RAG adayı"
    # Baseline alanları boş: ilk poll olay üretmez, mevcut durumu kaydeder.
    for field in ("last_check", "last_error", "release_etag", "commits_etag",
                  "last_release_tag", "last_commit_sha"):
        assert doc[field] is None, field
    assert doc["added_at"]


def test_watch_repo_duplicate_is_refused_without_overwriting(db):
    assert "sonuc" in tools.watch_repo(REPO, note="ilk not")

    result = tools.watch_repo(REPO, note="üzerine yazma girişimi")

    assert "zaten izleniyor" in result["hata"]
    assert _watched(db).to_dict()["note"] == "ilk not"


def test_unwatch_repo_deletes_existing(db):
    tools.watch_repo(REPO)

    result = tools.unwatch_repo(REPO)

    assert "çıkarıldı" in result["sonuc"]
    assert not _watched(db).exists


def test_unwatch_repo_reports_missing(db):
    result = tools.unwatch_repo("owner/unknown")

    assert "hata" in result


def test_list_watched_repos_projects_status_fields(db):
    tools.watch_repo(REPO, note="not1")
    db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(REPO)).set(
        {"last_check": NOW, "last_error": "GitHub 403: rate limit"}, merge=True
    )
    tools.watch_repo("owner/other")

    result = tools.list_watched_repos()

    assert result["sayi"] == 2
    by_repo = {r["repo"]: r for r in result["repolar"]}
    assert set(by_repo) == {REPO, "owner/other"}
    assert by_repo[REPO] == {
        "repo": REPO, "note": "not1",
        "last_check": NOW, "last_error": "GitHub 403: rate limit",
    }
    assert by_repo["owner/other"]["last_error"] is None


def test_get_repo_updates_returns_unsurfaced_and_marks_them(db):
    _seed_event(db, title="v1.0.0")
    _seed_event(db, kind="commits", title="3 yeni commit", ts="2026-07-30T11:00:00+00:00")
    _seed_event(db, title="eski", ts="2026-07-29T09:00:00+00:00", surfaced=True)

    result = tools.get_repo_updates()

    assert result["sayi"] == 2
    assert [e["title"] for e in result["olaylar"]] == ["v1.0.0", "3 yeni commit"]
    assert {e["repo"] for e in result["olaylar"]} == {REPO}

    # Mutasyon pimi: surfaced=True işaretlemesi silinirse bu çağrı boş DÖNMEZ.
    second = tools.get_repo_updates()
    assert second == {"olaylar": [], "sayi": 0}
    # Önceden surfaced olan olay hiç dönmedi.
    assert all(
        snap.to_dict()["surfaced"] is True
        for snap in db.collection(repo_watch.EVENTS_COLLECTION).stream()
    )


def test_repo_tools_zones():
    assert config.TOOL_ZONES["watch_repo"] == config.ZONE_YELLOW
    assert config.TOOL_ZONES["unwatch_repo"] == config.ZONE_YELLOW
    assert config.TOOL_ZONES["list_watched_repos"] == config.ZONE_GREEN
    assert config.TOOL_ZONES["get_repo_updates"] == config.ZONE_GREEN


def test_repo_tools_wired_into_all_tools():
    for fn in (tools.watch_repo, tools.unwatch_repo,
               tools.list_watched_repos, tools.get_repo_updates):
        assert fn in tools.ALL_TOOLS


def test_instruction_mentions_get_repo_updates():
    from app.agent import INSTRUCTION

    assert "get_repo_updates" in INSTRUCTION
