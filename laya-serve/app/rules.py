"""Local mode: deterministic rules-based typed-decision engine.

A keyword baseline over Persian and English ticket text — honest by design:
it is not a transformer, confidences stay moderate, and ambiguous input
falls below the worker's abstention threshold instead of guessing loudly.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Fixed order, used only to break score ties deterministically.
CATEGORY_ORDER = ("billing", "technical", "shipping", "account", "other")

# Curated keyword sets for well-known category labels; an option list with
# unknown labels still works (each label matches its own words).
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "billing": (
        "invoice", "payment", "paid", "refund", "billing", "charged",
        "overcharged", "subscription", "receipt", "price", "renew", "chargeback",
        "فاکتور", "صورتحساب", "پرداخت", "بازپرداخت", "هزینه", "قیمت",
        "اشتراک", "کیف پول", "بانکی",
    ),
    "technical": (
        "error", "bug", "crash", "broken", "fails", "failure", "exception",
        "not working", "doesnt work", "wont start", "timeout", "install", "api",
        "خطا", "باگ", "اشکال", "خراب", "کرش", "نصب", "کند", "قطع",
        "کار نمیکند", "نمیشود", "نشان داده نمیشود",
    ),
    "shipping": (
        "delivery", "delivered", "shipping", "shipment", "parcel", "package",
        "tracking", "courier", "arrived", "address", "dispatch",
        "تحویل", "ارسال", "مرسوله", "رهگیری", "آدرس", "پیک", "پست", "بسته",
    ),
    "account": (
        "account", "login", "log in", "signin", "password", "reset", "profile",
        "verify", "verification", "blocked", "suspended", "sign up", "register",
        "حساب", "ورود", "گذرواژه", "رمز عبور", "پروفایل", "ایمیل", "مسدود",
        "عضویت", "تایید هویت",
    ),
}

# Priority: 1 = high ... max = low (mirrors tickets.priority).
HIGH_KEYWORDS = (
    "urgent", "asap", "immediately", "emergency", "right away", "deadline",
    "critical", "unacceptable", "terrible", "worst", "today",
    "فوری", "فورا", "اضطراری", "همین الان", "مهلت", "امروز", "فاجعه",
)
LOW_KEYWORDS = (
    "suggestion", "feature request", "wondering", "curious", "just asking",
    "minor", "پیشنهاد", "کنجکاو", "فقط سوال",
)

# Needs-human-review signals: legal, escalation, anger, repetition.
REVIEW_KEYWORDS = (
    "lawsuit", "legal action", "attorney", "lawyer", "court", "sue",
    "escalate", "manager", "supervisor", "unacceptable", "terrible", "worst",
    "angry", "furious", "disappointed", "again and again", "third time",
    "fed up", "chargeback",
    "وکیل", "دادگاه", "شکایت", "ارجاع", "سرپرست", "عصبانی", "ناراضی",
    "فاجعه", "دوباره", "بار سوم",
)


def normalize(text: str) -> str:
    """Lowercase, fold Arabic to Persian letters, drop ZWNJ and punctuation."""
    text = text.lower()
    text = text.replace("\u200c", "").replace("\u200d", "")
    text = text.replace("\u064a", "\u06cc").replace("\u0643", "\u06a9")
    text = text.replace("'", "").replace("\u2018", "").replace("\u2019", "")
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=None)
def _pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)")


def _count(normalized: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for kw in keywords if _pattern(kw).search(normalized))


def _answer_choice(options: list[str], normalized: str) -> tuple[str, float]:
    scored: list[tuple[str, str, int]] = []
    for opt in options:
        label = opt.strip().lower()
        keywords = CATEGORY_KEYWORDS.get(label, (label,) if label else ())
        scored.append((opt, label, _count(normalized, keywords)))

    best = max(s for _, _, s in scored)
    if best == 0:
        # No signal at all: say so with a confidence below any sane threshold.
        fallback = next((o for o, k, _ in scored if k in ("other", "unknown")), options[0])
        return fallback, 0.3

    top = [row for row in scored if row[2] == best]
    if len(top) > 1:
        # Tie: deterministic pick, low confidence so the worker abstains.
        top.sort(key=lambda r: (CATEGORY_ORDER.index(r[1]) if r[1] in CATEGORY_ORDER else len(CATEGORY_ORDER), r[1]))
        return top[0][0], 0.45

    return top[0][0], round(min(0.9, 0.55 + 0.12 * (best - 1)), 2)


def _answer_score(minimum: int, maximum: int, normalized: str) -> tuple[int, float]:
    high = _count(normalized, HIGH_KEYWORDS)
    low = _count(normalized, LOW_KEYWORDS)
    if high > low:
        return minimum, round(min(0.9, 0.6 + 0.1 * (high - 1)), 2)
    if low > high:
        return maximum, round(min(0.9, 0.6 + 0.1 * (low - 1)), 2)
    return minimum + (maximum - minimum) // 2, 0.55


def _answer_review(normalized: str, weak_choice: bool) -> tuple[bool, float]:
    hits = _count(normalized, REVIEW_KEYWORDS)
    if hits:
        return True, round(min(0.92, 0.75 + 0.07 * (hits - 1)), 2)
    if weak_choice:
        # The category answer itself was uncertain -> a human should look.
        return True, 0.6
    return False, 0.65


def decide(text: str, questions: list[dict], min_confidence: float) -> dict[str, dict]:
    """Answer every typed question deterministically.

    Two passes: noul (needs review) can incorporate the confidence of the
    choice answers, so question order in the payload never changes a result.
    """
    normalized = normalize(text)
    answers: dict[str, dict] = {}
    weak_choice = False

    for q in questions:
        if q["type"] == "choice":
            value, confidence = _answer_choice(q.get("options") or [], normalized)
            weak_choice = weak_choice or confidence < min_confidence
        elif q["type"] == "score":
            value, confidence = _answer_score(q["min"], q["max"], normalized)
        else:
            continue  # noul handled in the second pass
        answers[q["id"]] = {"value": value, "confidence": confidence}

    for q in questions:
        if q["type"] == "noul":
            value, confidence = _answer_review(normalized, weak_choice)
            answers[q["id"]] = {"value": value, "confidence": confidence}

    return {q["id"]: answers[q["id"]] for q in questions}
