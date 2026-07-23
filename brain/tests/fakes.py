"""Minimal in-memory stand-in for the Firestore client surface Memory uses."""
import itertools


class FakeSnap:
    """Snapshot object that mimics Firestore document snapshot."""
    def __init__(self, data):
        self.exists = data is not None
        self._data = dict(data or {})

    def to_dict(self):
        return self._data


class FakeDoc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    def get(self):
        data = self.store.get(self.key)
        return FakeSnap(data)

    def set(self, data, merge=False):
        if merge and self.key in self.store:
            self.store[self.key].update(data)
        else:
            self.store[self.key] = dict(data)


class FakeCollection:
    _ids = itertools.count()

    def __init__(self):
        self.docs = {}

    def document(self, doc_id=None):
        doc_id = doc_id or f"auto{next(self._ids)}"
        return FakeDoc(self.docs, doc_id)

    def add(self, data):
        doc_id = f"auto{next(self._ids)}"
        self.docs[doc_id] = dict(data)
        return None, FakeDoc(self.docs, doc_id)

    def stream(self):
        """Yield FakeSnap objects for each document, mimicking Firestore's stream()."""
        for data in self.docs.values():
            yield FakeSnap(data)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())
