"""One-time migration of the old `brain` service memory into the new vault.

For installations upgrading from the earlier JARVIS `brain` service, whose
Firestore held `profile/main`, `facts` and `lessons` without user scope or
versions. Each record is copied with its collection and timestamp as
provenance and marked as not re-verified, into the owner's Harness `Memory`
scope:

- `MEMORY.md`: standing preferences from the profile and every fact (injected
  into each turn);
- `profil.md`: the remaining profile fields (read on demand);
- `dersler.md`: lessons.

Existing vault files are never overwritten: the write uses
`expected_version=None`, so a second run fails instead of duplicating. The
old collections are only read. Credentials come from the current `gcloud`
user session.

Usage: python migrations/legacy_brain_memory.py PROJECT_ID OWNER_EMAIL
"""

import asyncio
import subprocess
import sys
from datetime import datetime, timezone

import google.oauth2.credentials
import httpx
from google.cloud.firestore import AsyncClient

from jarvis_core.assistant import memory_scope
from jarvis_core.firestore_stores import FirestoreMemoryStore

# Profile fields that are standing guidance for every turn; the rest go to profil.md.
_EVERY_TURN_FIELDS = ('communication_style', 'language_rules', 'rules')
_PROFILE_LABELS = {
    'full_name': 'Ad soyad', 'location': 'Konum', 'career': 'Kariyer', 'hardware': 'Donanım',
    'os_and_env': 'İşletim sistemi ve ortam', 'tech_stack': 'Teknoloji yığını', 'remote_tools': 'Uzak erişim araçları',
    'active_projects': 'Aktif projeler', 'contact_handles': 'İletişim hesapları', 'girlfriend': 'Kız arkadaşı',
    'coffee_shop': 'Kahveci', 'coffee_preference': 'Kahve tercihi',
    'communication_style': 'İletişim tarzı', 'language_rules': 'Dil kuralları', 'rules': 'Kurallar',
}


def _gcloud_token() -> str:
    return subprocess.run(['gcloud', 'auth', 'print-access-token'], check=True, capture_output=True, text=True).stdout.strip()


def _owner_uid(project_id: str, email: str, token: str) -> str:
    response = httpx.post(
        f'https://identitytoolkit.googleapis.com/v1/projects/{project_id}/accounts:lookup',
        headers={'Authorization': f'Bearer {token}', 'x-goog-user-project': project_id},
        json={'email': [email]},
        timeout=30,
    )
    response.raise_for_status()
    users = response.json().get('users', [])
    if len(users) != 1:
        raise SystemExit(f'{email} has not signed in to the PWA yet; sign in once, then re-run')
    return users[0]['localId']


def _render(value: object) -> str:
    if isinstance(value, list):
        return '\n'.join(f'  - {_render(item)}' for item in value)
    if isinstance(value, dict):
        return '\n'.join(f'  - {key}: {_render(item)}' for key, item in value.items())
    return str(value).strip()


def _provenance(collection: str, ts: str | None) -> str:
    when = f', kayıt {ts}' if ts else ''
    return f'(kaynak: eski JARVIS `{collection}`{when}; güncelliği doğrulanmadı)'


async def main(project_id: str, owner_email: str) -> None:
    token = _gcloud_token()
    uid = _owner_uid(project_id, owner_email, token)
    client = AsyncClient(project=project_id, credentials=google.oauth2.credentials.Credentials(token))

    profile_doc = await client.collection('profile').document('main').get()
    profile = (profile_doc.to_dict() or {}) if profile_doc.exists else {}
    facts = [doc.to_dict() async for doc in client.collection('facts').stream()]
    lessons = [doc.to_dict() async for doc in client.collection('lessons').stream()]
    migrated = datetime.now(timezone.utc).date().isoformat()

    main_lines = ['# Kalıcı notlar', '', f'Eski JARVIS hafızasından {migrated} tarihinde taşındı.', '']
    for key in _EVERY_TURN_FIELDS:
        if profile.get(key):
            main_lines += [f'## {_PROFILE_LABELS[key]}', _render(profile[key]), _provenance('profile', None), '']
    if facts:
        main_lines.append('## Bilinenler')
        for fact in sorted(facts, key=lambda f: f.get('ts', '')):
            main_lines.append(f"- {fact['text'].strip()} {_provenance('facts', fact.get('ts'))}")
        main_lines.append('')

    profile_lines = ['# Profil', '', _provenance('profile', None), '']
    for key, value in profile.items():
        if key in _EVERY_TURN_FIELDS or not value:
            continue
        profile_lines += [f'## {_PROFILE_LABELS.get(key, key)}', _render(value), '']

    lesson_lines = ['# Dersler', '']
    for lesson in sorted(lessons, key=lambda item: item.get('ts', '')):
        lesson_lines += [
            f"## {lesson.get('context', '').strip()}",
            f"- Denenen: {lesson.get('tried', '').strip()}",
            f"- Yanlış giden: {lesson.get('went_wrong', '').strip()}",
            f"- Doğrusu: {lesson.get('correct', '').strip()}",
            _provenance('lessons', lesson.get('ts')),
            '',
        ]

    store = FirestoreMemoryStore(client)
    scope = memory_scope(uid)
    files = (('MEMORY.md', main_lines), ('profil.md', profile_lines), ('dersler.md', lesson_lines))
    existing = [name for name, _ in files if await store.read(f'{scope}/{name}', max_chars=1) is not None]
    if existing:
        raise SystemExit(f'vault already has {", ".join(existing)}; nothing migrated')
    for name, lines in files:
        result = await store.write(f'{scope}/{name}', '\n'.join(lines).rstrip() + '\n', expected_version=None)
        print(f'{name}: written (version {result.version})')
    print(f'migrated {len(facts)} facts, {len(lessons)} lessons, {len(profile)} profile fields')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
