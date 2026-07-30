"""repo_watch.poll_once: sahte GitHub istemcisi + FakeDB ile saf mantık testleri.

Senaryo kapsamı spec §7'deki liste: baseline (olay ÜRETMEZ), yeni release,
yeni commit'ler, ikisi birden, 304 (değişiklik yok), releases-404 (hata değil),
commits-404/ağ hatası (last_error + tur devam eder).
"""
from app import repo_watch
from tests.fakes import FakeDB

NOW = "2026-07-30T10:00:00+00:00"
REPO = "owner/repo"
RELEASES = f"/repos/{REPO}/releases/latest"
COMMITS = f"/repos/{REPO}/commits?per_page={repo_watch.MAX_COMMITS}"


def now_fn():
    return NOW


class FakeGitHubClient:
    """Senaryolu sahte client: path -> (status, etag, json) ya da fırlatılacak
    hata. Çağrıları (path, etag) olarak kaydeder — If-None-Match pin'i için."""

    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def get(self, path, etag=None):
        self.calls.append((path, etag))
        resp = self._responses[path]
        if isinstance(resp, Exception):
            raise resp
        return resp


def _seed_repo(db, repo=REPO, **overrides):
    doc = {
        "repo": repo,
        "note": "",
        "added_at": NOW,
        "last_check": None,
        "last_error": None,
        "release_etag": None,
        "commits_etag": None,
        "last_release_tag": None,
        "last_commit_sha": None,
    }
    doc.update(overrides)
    db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(repo)).create(doc)


def _events(db):
    return [snap.to_dict() for snap in db.collection(repo_watch.EVENTS_COLLECTION).stream()]


def _doc(db, repo=REPO):
    return db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(repo)).get().to_dict()


def _commit(sha, message):
    return {"sha": sha, "commit": {"message": message}}


def test_new_repo_first_poll_records_baseline_without_events():
    """Yeni eklenen repo ilk turda olay ÜRETMEZ: mevcut durum baseline olur
    (yoksa 10 yıllık tarih 'yeni' diye raporlanır). Mutasyon pimi: baseline
    kontrolü silinirse bu test kırılmalı."""
    db = FakeDB()
    _seed_repo(db)
    client = FakeGitHubClient({
        RELEASES: (200, "rel-etag", {"tag_name": "v1.0.0", "body": "notes", "html_url": "u"}),
        COMMITS: (200, "com-etag", [_commit("abc", "init")]),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 0, "hata": 0}
    assert _events(db) == []
    doc = _doc(db)
    assert doc["last_release_tag"] == "v1.0.0"
    assert doc["last_commit_sha"] == "abc"
    assert doc["release_etag"] == "rel-etag"
    assert doc["commits_etag"] == "com-etag"
    assert doc["last_check"] == NOW
    assert doc["last_error"] is None
    # Koşullu istek pimi: saklanan etag bir sonraki turda If-None-Match olur.
    client2 = FakeGitHubClient({RELEASES: (304, "rel-etag", None), COMMITS: (304, "com-etag", None)})
    repo_watch.poll_once(db, client2, now_fn)
    assert client2.calls == [(RELEASES, "rel-etag"), (COMMITS, "com-etag")]


def test_new_release_tag_produces_one_release_event():
    db = FakeDB()
    _seed_repo(db, last_release_tag="v1.0.0", last_commit_sha="abc")
    body = "uzun not " * 100  # 900 char > RELEASE_DETAIL_CHARS
    client = FakeGitHubClient({
        RELEASES: (200, None, {
            "tag_name": "v1.1.0", "body": body,
            "html_url": "https://github.com/owner/repo/releases/tag/v1.1.0",
        }),
        COMMITS: (304, None, None),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 1, "hata": 0}
    (event,) = _events(db)
    assert event["repo"] == REPO
    assert event["kind"] == "release"
    assert event["title"] == "v1.1.0"
    assert len(event["detail"]) <= repo_watch.RELEASE_DETAIL_CHARS
    assert event["url"] == "https://github.com/owner/repo/releases/tag/v1.1.0"
    assert event["surfaced"] is False
    assert event["ts"] == NOW
    assert _doc(db)["last_release_tag"] == "v1.1.0"


def test_new_commits_produce_one_commits_event_with_titles():
    db = FakeDB()
    _seed_repo(db, last_release_tag="v1.0.0", last_commit_sha="aaa")
    client = FakeGitHubClient({
        RELEASES: (304, None, None),
        COMMITS: (200, None, [
            _commit("ccc", "third commit\nuzun gövde"),
            _commit("bbb", "second commit"),
            _commit("aaa", "first commit"),  # baseline — buradan eskisi dahil değil
        ]),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 1, "hata": 0}
    (event,) = _events(db)
    assert event["kind"] == "commits"
    assert event["title"] == "2 yeni commit"
    assert "third commit" in event["detail"]
    assert "second commit" in event["detail"]
    assert "first commit" not in event["detail"]
    assert event["surfaced"] is False
    assert _doc(db)["last_commit_sha"] == "ccc"


def test_release_and_commits_together_produce_two_events():
    db = FakeDB()
    _seed_repo(db, last_release_tag="v1.0.0", last_commit_sha="aaa")
    client = FakeGitHubClient({
        RELEASES: (200, None, {"tag_name": "v2.0.0", "body": "", "html_url": "u"}),
        COMMITS: (200, None, [_commit("bbb", "new work"), _commit("aaa", "old")]),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 2, "hata": 0}
    assert sorted(e["kind"] for e in _events(db)) == ["commits", "release"]


def test_304_means_no_events_but_last_check_updates():
    db = FakeDB()
    _seed_repo(db, last_release_tag="v1.0.0", last_commit_sha="abc",
               release_etag="rel-etag", commits_etag="com-etag")
    client = FakeGitHubClient({RELEASES: (304, "rel-etag", None), COMMITS: (304, "com-etag", None)})

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 0, "hata": 0}
    assert _events(db) == []
    doc = _doc(db)
    assert doc["last_check"] == NOW
    assert doc["last_error"] is None


def test_releases_404_means_repo_has_no_releases_not_an_error():
    db = FakeDB()
    _seed_repo(db, last_release_tag=None, last_commit_sha="abc")
    client = FakeGitHubClient({
        RELEASES: repo_watch.GitHubError("GitHub 404: /repos/owner/repo/releases/latest", status=404),
        COMMITS: (304, None, None),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 0, "hata": 0}
    assert _doc(db)["last_error"] is None
    # "Kontrol edildi, release yok" damgası: None değil "" — bir sonraki
    # testin ön koşulu.
    assert _doc(db)["last_release_tag"] == ""


def test_first_release_after_no_release_baseline_produces_event():
    """Release'siz repo izlenmeye başlandıktan SONRA ilk release'ini çıkarırsa
    bu bir olaydır — sessizce baseline olmamalı (None yerine "" damgasının
    varlık sebebi)."""
    db = FakeDB()
    _seed_repo(db, last_release_tag="", last_commit_sha="abc")
    client = FakeGitHubClient({
        RELEASES: (200, "rel-etag", {
            "tag_name": "v0.1.0", "body": "ilk release",
            "html_url": "https://github.com/owner/repo/releases/tag/v0.1.0",
        }),
        COMMITS: (304, None, None),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 1, "hata": 0}
    events = _events(db)
    assert len(events) == 1
    assert events[0]["kind"] == "release"
    assert events[0]["title"] == "v0.1.0"
    assert _doc(db)["last_release_tag"] == "v0.1.0"


def test_commits_error_writes_last_error_and_round_continues():
    """Bir repo'nun commits hatası (404 ya da ağ) tüm turu öldürmez: hata
    last_error'a yazılır, diğer repo normal işlenir, özet 'hata' sayar."""
    db = FakeDB()
    broken = "owner/broken"
    _seed_repo(db, last_release_tag="v1", last_commit_sha="abc")
    _seed_repo(db, repo=broken, last_release_tag="v1", last_commit_sha="abc")
    client = FakeGitHubClient({
        RELEASES: (304, None, None),
        COMMITS: (200, None, [_commit("zzz", "fresh"), _commit("abc", "base")]),
        f"/repos/{broken}/releases/latest": (304, None, None),
        f"/repos/{broken}/commits?per_page={repo_watch.MAX_COMMITS}":
            repo_watch.GitHubError("GitHub 404: commits", status=404),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary["kontrol"] == 2
    assert summary["hata"] == 1
    assert summary["olay"] == 1  # sağlam repo'nun commit olayı yine üretildi
    assert "404" in _doc(db, broken)["last_error"]
    assert _doc(db, broken)["last_check"] == NOW
    assert _doc(db)["last_error"] is None


def test_network_failure_is_also_isolated_per_repo():
    db = FakeDB()
    _seed_repo(db, last_release_tag="v1", last_commit_sha="abc")
    client = FakeGitHubClient({
        RELEASES: repo_watch.GitHubError("GitHub erişim hatası: timeout"),
        COMMITS: (304, None, None),
    })

    summary = repo_watch.poll_once(db, client, now_fn)

    assert summary == {"kontrol": 1, "olay": 0, "hata": 1}
    assert "erişim" in _doc(db)["last_error"]


def test_doc_id_encodes_slash_for_real_firestore():
    """Firestore doc id '/' içeremez; 'owner/repo' ham id yapılırsa GERÇEK
    istemci ValueError fırlatır (FakeDB zorlamaz — canlı tohum kaydında
    yakalandı). Pin: doc_id kodlaması geri alınırsa bu test kırılmalı."""
    assert repo_watch.doc_id("owner/repo") == "owner%2Frepo"
    assert "/" not in repo_watch.doc_id("owner/repo")
    # Çakışma yok: farklı repo'lar farklı id üretir.
    assert repo_watch.doc_id("a/b-c") != repo_watch.doc_id("a-b/c")
