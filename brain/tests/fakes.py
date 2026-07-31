"""Minimal in-memory stand-in for the Firestore client surface Memory uses."""
import itertools

from google.api_core.exceptions import AlreadyExists


class FakeSnap:
    """Snapshot object that mimics Firestore document snapshot."""
    def __init__(self, data, reference=None):
        self.exists = data is not None
        self._data = dict(data or {})
        # Real firestore_v1 DocumentSnapshot exposes `.reference` (the
        # DocumentReference it was read from) -- needed so query results can
        # be deleted individually (bulk delete-by-query has no dedicated
        # Firestore API; iterate + snap.reference.delete() is the standard
        # idiom). Only FakeQuery.stream() sets this; FakeCollection.stream()
        # doesn't need it yet (nothing deletes off a bare collection stream).
        self.reference = reference

    def to_dict(self):
        return self._data


class FakeDoc:
    def __init__(self, store, key):
        self.store, self.key = store, key

    @property
    def id(self):
        """Real DocumentReference exposes `.id` (the final path segment); tasks
        code reads task_id off it after collection.add()/query streams."""
        return self.key

    def get(self):
        data = self.store.get(self.key)
        return FakeSnap(data)

    def set(self, data, merge=False):
        if merge and self.key in self.store:
            self.store[self.key].update(data)
        else:
            self.store[self.key] = dict(data)

    def create(self, data):
        """Mirrors Firestore's DocumentReference.create(): a single atomic
        check-and-write that raises AlreadyExists if the doc is already
        present, instead of silently overwriting it like set() does."""
        if self.key in self.store:
            raise AlreadyExists(f"document already exists: {self.key}")
        self.store[self.key] = dict(data)

    def delete(self):
        self.store.pop(self.key, None)


class FakeQuery:
    """Minimal Firestore query surface: where(filter=FieldFilter)/order_by/limit/stream."""
    def __init__(self, store):
        self._store = store               # dict[doc_id, data] -- backing store, for .reference
        self._rows = list(store.items())  # list[(doc_id, data)]
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
        rows = [(doc_id, d) for doc_id, d in self._rows if self._match(d)]
        if self._order:
            field, direction = self._order
            # Sorts only by `field`; does not model Firestore's implicit `__name__` (doc id) secondary tiebreak.
            rows.sort(key=lambda kv: kv[1].get(field), reverse=(direction == "DESCENDING"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return [FakeSnap(d, reference=FakeDoc(self._store, doc_id)) for doc_id, d in rows]

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
        return FakeQuery(self.docs).where(filter=filter)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self.docs).order_by(field, direction)

    def limit(self, n):
        return FakeQuery(self.docs).limit(n)


class FakeEvent:
    """Minimal stand-in for ADK Event: has content with parts containing text."""
    def __init__(self, text):
        from types import SimpleNamespace
        self.content = SimpleNamespace(parts=[SimpleNamespace(text=text)])

    def is_final_response(self):
        return True


class FakeRunner:
    """Stands in for ADK Runner: yields one final event echoing the message."""
    def __init__(self, reply="cevap"):
        self._reply = reply

    async def run_async(self, *, user_id, session_id, new_message):
        yield FakeEvent(self._reply)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection())
