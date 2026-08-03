"""Weekly retrospective module (North Star §8.4, Phase Y1.4).

Scans system activity over the past 7 days across audit logs, events, tasks,
facts, and lessons to extract performance metrics and recent lessons, generating
a structured Turkish retro report for Kadir in the "retro" chat session.

Design rules:
- All queries in `collect_week` stream collections and filter/sort in Python to
  stay compatible with FakeDB in tests and avoid requiring composite Firestore
  indexes.
- If the LLM generation fails (timeout/429/connection error) or returns empty
  text, NO report is produced and `{"ok": False, "reason": ...}` is returned
  (Principle 4: no fake or partial retro reports).
- Event obsolete record measurement (§8.4): count of `events` older than 90 days
  is reported for measurement only — NO deletion is performed in this phase
  (deletion requiring confirmation flow deferred to Phase Y3).
- Reports are delivered to session `REPORT_SESSION_ID = "retro"` via `make_reporter`.
"""
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Callable

from . import config, messages, tasks

REPORT_SESSION_ID = "retro"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(ts_val: Any) -> datetime | None:
    if not ts_val or not isinstance(ts_val, str):
        return None
    try:
        dt = datetime.fromisoformat(ts_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def make_reporter(store, user_id: str):
    """Factory for reporting function bound to the "retro" session.

    Appends the retro report as a model message to session REPORT_SESSION_ID.
    """
    def report(text: str) -> None:
        store.append(user_id, REPORT_SESSION_ID, "model", text)
    return report


FACTORY_ACTOR_PREFIX = "factory:"


def _factory_line(factory: dict) -> str:
    """Fabrika envanterinin tek satırlık raporu (§8.5 değişmez 6).

    Hiç koşu yoksa bunu AÇIKÇA söyler: "üretilmiş ajan yok" bir gözlemdir,
    satırın sessizce kaybolması ise envanterin taranıp taranmadığını
    belirsiz bırakırdı."""
    if not factory["instances_7d"]:
        return "- Fabrika (Kademe 1): bu hafta üretilmiş ajan yok."
    return (
        f"- Fabrika (Kademe 1): {factory['instances_7d']} örnek, "
        f"{factory['tool_calls_7d']} araç çağrısı. "
        f"Şablon dağılımı: {json.dumps(factory['by_template'], ensure_ascii=False)}"
    )


def collect_week(db, now_fn=_now) -> dict:
    """Collect system metrics and recent lessons from the past 7 days.

    Streams `audit_log`, `events`, `tasks`, `facts`, and `lessons` collections
    and filters/sorts in Python to maintain FakeDB compatibility. Also measures
    the count of `events` older than 90 days without deleting any documents.
    """
    now_str = now_fn()
    now_dt = _parse_ts(now_str) or datetime.now(timezone.utc)
    cutoff_7d = now_dt - timedelta(days=7)
    cutoff_90d = now_dt - timedelta(days=90)

    # 1. audit_log
    audit_total_7d = 0
    audit_decisions = {"allow": 0, "block": 0, "confirm": 0, "dry_run": 0}
    blocked_tools_counter: dict[str, int] = {}
    # Fabrika envanteri (North Star §8.5, değişmez 6: "haftalık retro üretilmiş
    # ajan envanterini de tarar — ajan sürünmesine karşı temizlik"). Ayrı bir
    # koleksiyon YOK: Kademe 1 örnekleri geçicidir ve arkalarında yalnızca audit
    # izlerini bırakır (actor="factory:<şablon>#<örnek>"), ki §8.5 değişmez 5'in
    # istediği iz zaten budur. Şablon başına koşu sayısı ve ayrı örnek sayısı,
    # "hangi kalıp gerçekten kullanılıyor" sorusunun cevabıdır.
    factory_runs: dict[str, int] = {}
    factory_instances: set[str] = set()

    for snap in db.collection("audit_log").stream():
        entry = snap.to_dict()
        ts = _parse_ts(entry.get("ts"))
        if ts and ts >= cutoff_7d:
            audit_total_7d += 1
            dec = entry.get("decision")
            if dec in audit_decisions:
                audit_decisions[dec] += 1
            elif dec:
                audit_decisions[dec] = 1

            if dec == "block":
                tool = entry.get("tool") or "unknown"
                blocked_tools_counter[tool] = blocked_tools_counter.get(tool, 0) + 1

            actor = entry.get("actor") or ""
            if actor.startswith(FACTORY_ACTOR_PREFIX):
                # "factory:<şablon>#<örnek>" -- şablon adı ile örnek kimliğini ayır.
                label = actor[len(FACTORY_ACTOR_PREFIX):]
                template = label.split("#", 1)[0] or "bilinmeyen"
                factory_runs[template] = factory_runs.get(template, 0) + 1
                factory_instances.add(label)

    sorted_blocked = sorted(blocked_tools_counter.items(), key=lambda x: x[1], reverse=True)
    top_blocked_tools = dict(sorted_blocked[:5])

    # 2. events
    events_total_7d = 0
    events_kinds: dict[str, int] = {}
    events_notify_count = 0
    obsolete_90d_count = 0

    for snap in db.collection("events").stream():
        entry = snap.to_dict()
        ts = _parse_ts(entry.get("ts"))
        if not ts:
            continue

        if ts < cutoff_90d:
            obsolete_90d_count += 1

        if ts >= cutoff_7d:
            events_total_7d += 1
            kind = entry.get("kind") or "unknown"
            events_kinds[kind] = events_kinds.get(kind, 0) + 1

            res = entry.get("result") or {}
            is_notify = False
            if kind == "health_check":
                is_notify = any(not c.get("ok") for c in res.get("servisler", []))
            elif kind == "task_tick":
                is_notify = (res.get("done", 0) + res.get("exhausted", 0)) > 0
            elif kind == "weekly_retro":
                is_notify = bool(res.get("ok"))
            elif "notify" in res:
                is_notify = bool(res.get("notify"))

            if is_notify:
                events_notify_count += 1

    # 3. tasks
    tasks_total_7d = 0
    task_statuses = {"done": 0, "budget_exhausted": 0, "active": 0}

    for snap in db.collection("tasks").stream():
        entry = snap.to_dict()
        created_ts = _parse_ts(entry.get("created_at")) or _parse_ts(entry.get("updated_at"))
        if created_ts and created_ts >= cutoff_7d:
            tasks_total_7d += 1
            st = entry.get("status") or "unknown"
            task_statuses[st] = task_statuses.get(st, 0) + 1

    # 4. facts
    facts_added_7d = 0
    for snap in db.collection("facts").stream():
        entry = snap.to_dict()
        ts = _parse_ts(entry.get("ts"))
        if ts and ts >= cutoff_7d:
            facts_added_7d += 1

    # 5. lessons
    lessons_added_7d = 0
    all_lessons = []
    for snap in db.collection("lessons").stream():
        entry = snap.to_dict()
        ts_dt = _parse_ts(entry.get("ts"))
        if ts_dt and ts_dt >= cutoff_7d:
            lessons_added_7d += 1
        all_lessons.append((ts_dt or datetime.min.replace(tzinfo=timezone.utc), entry))

    all_lessons.sort(key=lambda pair: pair[0], reverse=True)
    recent_lessons = [entry for _, entry in all_lessons[:5]]

    return {
        "factory": {
            "tool_calls_7d": sum(factory_runs.values()),
            "instances_7d": len(factory_instances),
            "by_template": dict(sorted(factory_runs.items(), key=lambda x: x[1], reverse=True)),
        },
        "audit_log": {
            "total_7d": audit_total_7d,
            "decisions": audit_decisions,
            "top_blocked_tools": top_blocked_tools,
        },
        "events": {
            "total_7d": events_total_7d,
            "kinds": events_kinds,
            "notify_count": events_notify_count,
            "obsolete_90d_count": obsolete_90d_count,
        },
        "tasks": {
            "total_7d": tasks_total_7d,
            "statuses": task_statuses,
        },
        "facts": {
            "added_7d_count": facts_added_7d,
        },
        "lessons": {
            "added_7d_count": lessons_added_7d,
            "recent_lessons": recent_lessons,
        },
    }


def _generate_retro_content(model: str, prompt: str) -> str:
    """Issue a generateContent HTTP POST call to the LLM backend (consult.py pattern)."""
    base_url = (config.LLM_BASE_URL or "https://generativelanguage.googleapis.com").rstrip("/")
    system_instruction = (
        "Sen Kadir'in JARVIS sisteminin haftalık retro ve gelişim asistanısın. "
        "Sağlanan haftalık metrikleri ve son dersleri analiz ederek kısa, net, "
        "ve eyleme dönüştürülebilir Türkçe bir haftalık retro raporu oluştur."
    )
    body = json.dumps({
        "system_instruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1beta/models/{model}:generateContent",
        data=body,
        headers={
            "x-goog-api-key": os.environ.get("GOOGLE_API_KEY", ""),
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read())

    texts = [
        part["text"]
        for candidate in data.get("candidates", [])
        for part in candidate.get("content", {}).get("parts", [])
        if part.get("text")
    ]
    return "\n".join(texts)


def _build_prompt(data: dict) -> str:
    audit_info = data["audit_log"]
    events_info = data["events"]
    tasks_info = data["tasks"]
    facts_info = data["facts"]
    lessons_info = data["lessons"]

    lines = [
        "Haftalık Performans ve Gözlem Verileri:",
        f"- Denetim Kayıtları (son 7 gün): Toplam {audit_info['total_7d']} karar.",
        f"  Karar dağılımı: {json.dumps(audit_info['decisions'], ensure_ascii=False)}",
        f"  En çok engellenen araçlar: {json.dumps(audit_info['top_blocked_tools'], ensure_ascii=False)}",
        _factory_line(data["factory"]),
        f"- Olaylar (son 7 gün): Toplam {events_info['total_7d']}, Bildirim üretilen: {events_info['notify_count']}.",
        f"  Olay türleri: {json.dumps(events_info['kinds'], ensure_ascii=False)}",
        f"  90 günden eski olay sayısı (temizlik ölçümü): {events_info['obsolete_90d_count']}",
        f"- Görevler (son 7 gün): Toplam {tasks_info['total_7d']}.",
        f"  Durum dağılımı: {json.dumps(tasks_info['statuses'], ensure_ascii=False)}",
        f"- Bellek: Eklenen gerçek sayısı: {facts_info['added_7d_count']}, Eklenen ders sayısı: {lessons_info['added_7d_count']}.",
        "",
        "Son 5 Ders:",
    ]
    if lessons_info["recent_lessons"]:
        for i, l in enumerate(lessons_info["recent_lessons"], 1):
            lines.append(
                f"{i}. Bağlam: {l.get('context', '')} | Denenen: {l.get('tried', '')} | "
                f"Hata: {l.get('went_wrong', '')} | Doğru Yol: {l.get('correct', '')}"
            )
    else:
        lines.append("Henüz ders kaydı yok.")

    lines.extend([
        "",
        "Lütfen Kadir'e kısa ve yapıcı bir haftalık rapor yaz (North Star §8.4):",
        "1. Bu hafta ne öğrendim / ne yapıldı?",
        "2. Hangi hatalar yapıldı ve bir daha tekrarlanmayacak?",
        "3. Sistem sağlığı ve gelecek hafta için öneriler.",
    ])
    return "\n".join(lines)


def run(
    db,
    *,
    llm_fn: Callable[[str], str] | None = None,
    report_fn: Callable[[str], None] | None = None,
    owner: str | None = None,
    now_fn=_now,
) -> dict:
    """Execute weekly retrospective: collect metrics, generate LLM report, deliver report.

    If LLM invocation fails or returns empty response, NO report is delivered and
    `{"ok": False, "reason": ...}` is returned (Principle 4: no fake/partial retro reports).
    """
    data = collect_week(db, now_fn=now_fn)
    prompt = _build_prompt(data)

    if llm_fn is None:
        def _default_llm(p: str) -> str:
            model = config.resolve_text_model()
            return _generate_retro_content(model, p)
        call_llm = _default_llm
    else:
        call_llm = llm_fn

    try:
        report_text = call_llm(prompt)
    except Exception as exc:
        logging.warning("retro: LLM haftalık özet üretirken hata aldı: %s", exc)
        return {
            "ok": False,
            "reason": f"LLM hatası: {exc}",
            "summary": f"Haftalık retro çalıştırılamadı (LLM hatası: {exc})",
            "data": data,
        }

    if not isinstance(report_text, str) or not report_text.strip():
        logging.warning("retro: LLM boş yanıt döndürdü")
        return {
            "ok": False,
            "reason": "LLM boş yanıt döndürdü",
            "summary": "Haftalık retro çalıştırılamadı (LLM boş yanıt)",
            "data": data,
        }

    if report_fn is None:
        target_owner = owner or tasks.default_owner()
        if target_owner:
            report_fn = make_reporter(messages.MessageStore(db), target_owner)

    if report_fn:
        try:
            report_fn(report_text)
        except Exception:
            logging.exception("retro: rapor mesajı yazılamadı")

    summary_line = "Haftalık retro raporu oluşturuldu ve retro oturumuna gönderildi."
    return {
        "ok": True,
        "summary": summary_line,
        "report": report_text,
        "data": data,
    }
