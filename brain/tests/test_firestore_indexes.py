"""Her composite-index gerektiren sorgunun index'i BEYAN EDİLMİŞ olmalı.

Neden var: 3 Ağustos 2026'da Kadir telefonunda "Sohbetler yüklenemedi: HTTP 502"
gördü. Kök neden `conversations` sorgusunun composite index'inin üretimde hiç
oluşturulmamış VE `firestore.indexes.json`'da hiç beyan edilmemiş olmasıydı --
25 Temmuz'dan beri kırıktı ve kimse görmedi.

Testler görmedi çünkü `FakeDB` index kavramını bilmez: her sorgu orada çalışır.
Yani bu hata sınıfı, süit ne kadar büyürse büyüsün, ancak ÜRETİMDE ortaya çıkar.
Bu test o boşluğu kapatır: kaynakta `.where(...)` ile `.order_by(...)`'ı aynı
zincirde kullanan her modülün koleksiyonu, index dosyasında beyan edilmiş
olmalı.

Beyan, index'in Firestore'da GERÇEKTEN var olduğunu kanıtlamaz -- dosya hiçbir
şey tarafından otomatik uygulanmıyor (bkz. brain/README.md "Deploy"). Ama
"beyan edilmemiş" hâli, üretime bakmadan yakalanabilecek tek yarısıdır ve
bugünkü kesintinin sebebi tam olarak oydu.
"""
import importlib
import json
import pathlib
import re

INDEX_FILE = pathlib.Path(__file__).resolve().parents[1] / "firestore.indexes.json"
APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# .collection(X) ardından gelen zincirde hem where hem order_by varsa composite
# index gerekir (tek eşitlik + tek sıralama bile Firestore'da index ister).
_CHAIN = re.compile(r'\.collection\((?P<coll>[^)]*)\)(?P<chain>(?:\s*\.\w+\([^\n]*\))+)')


def _declared_collections() -> set[str]:
    data = json.loads(INDEX_FILE.read_text())
    return {i["collectionGroup"] for i in data["indexes"]}


def _modules_needing_an_index() -> dict[str, str]:
    """{modül adı: koleksiyon adı} -- composite index gerektiren her sorgu için."""
    found = {}
    for path in sorted(APP_DIR.glob("*.py")):
        source = path.read_text()
        for match in _CHAIN.finditer(source):
            chain = match.group("chain")
            if ".where(" in chain and ".order_by(" in chain:
                module = importlib.import_module(f"app.{path.stem}")
                # Sorgular koleksiyon adını modül sabitinden alır (COLLECTION /
                # <AD>_COLLECTION); sabiti modülden okuruz, string'i tahmin etmeyiz.
                name = match.group("coll").strip()
                collection = getattr(module, name, None) if name.isidentifier() else name.strip("\"'")
                if isinstance(collection, str):
                    found[path.stem] = collection
    return found


def test_every_query_that_needs_a_composite_index_declares_one():
    needed = _modules_needing_an_index()
    assert needed, "tarayıcı hiçbir sorgu bulamadı -- regex bozulmuş olabilir"
    declared = _declared_collections()
    missing = {mod: coll for mod, coll in needed.items() if coll not in declared}
    assert not missing, (
        "composite index gerektiren ama firestore.indexes.json'da beyan edilmeyen "
        f"sorgu(lar): {missing}. Üretimde HTTP 502'ye dönüşür ve hiçbir test bunu "
        "göremez (FakeDB index bilmez)."
    )


def test_the_scanner_actually_sees_the_two_known_queries():
    """Tarayıcının boş yeşile düşmediğinin pimi: bilinen iki sorguyu görmeli."""
    needed = _modules_needing_an_index()
    assert needed.get("conversations") == "conversations"
    assert needed.get("messages") == "messages"
