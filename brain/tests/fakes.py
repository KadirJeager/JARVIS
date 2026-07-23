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


class FakeQuery:
    """Minimal Firestore query surface: where(filter=FieldFilter)/order_by/limit/stream."""
    def __init__(self, rows):
        self._rows = list(rows)          # list[dict]
        self._filters = []               # list[(field_path, op_string, value)]
        self._order = None               # (field, direction)
        self._limit = None

    def where(self, filter=None):
        self._filters.append((filter.field_path, filter.op_string, filter.value))
        return self

    def order_by(self, field, direction="ASCENDING"):
        self._order = (field, direction)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def stream(self):
        rows = [d for d in self._rows if self._match(d)]
        if self._order:
            field, direction = self._order
            rows.sort(key=lambda d: d.get(field), reverse=(direction == "DESCENDING"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnap(d) for d in rows]

    def _match(self, d):
        for field_path, op_string, value in self._filters:
            if op_string != "==":
                raise NotImplementedError(f"FakeQuery op {op_string}")
            if d.get(field_path) != value:
                return False
        return True


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

    def where(self, filter=None):
        return FakeQuery(self.docs.values()).where(filter=filter)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self.docs.values()).order_by(field, direction)

    def limit(self, n):
        return FakeQuery(self.docs.values()).limit(n)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())
