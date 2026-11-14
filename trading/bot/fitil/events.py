"""Yinelenen veri saatleri (NFP, EIA gibi) ve tek seferlik yasak pencereleri.

Kural örneği:
  {"name": "NFP", "weekday": 4, "week_of_month": 1, "time": "08:30", "tz": "America/New_York"}
  {"name": "EIA", "weekday": 2, "time": "10:30", "tz": "America/New_York", "groups": ["oil"]}
weekday: 0=Pazartesi ... 6=Pazar. groups yoksa kural tüm sembollere uygulanır.

Sınır: takvim resmi tatillere göre kayabilir (ör. NFP ikinci cumaya, EIA perşembeye).
Kesin tarihleri config'teki "blackouts" listesine ayrıca yazın.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception as e:                      # Windows'ta tzdata paketi gerekir
        raise RuntimeError(f"Saat dilimi yüklenemedi: {name}. 'pip install tzdata' çalıştırın.") from e


def validate_rules(rules: list[dict]) -> None:
    for r in rules:
        if not 0 <= int(r.get("weekday", -1)) <= 6:
            raise ValueError(f"recurring_blackouts: weekday 0-6 olmalı: {r}")
        time.fromisoformat(r["time"])
        _zone(r.get("tz", "UTC"))


def rule_time_utc(rule: dict, day: date) -> datetime | None:
    """Kural o gün geçerliyse olayın UTC zamanını (tz'siz) döndürür."""
    if day.weekday() != int(rule["weekday"]):
        return None
    wom = rule.get("week_of_month")
    if wom is not None and (day.day - 1) // 7 + 1 != int(wom):
        return None
    local = datetime.combine(day, time.fromisoformat(rule["time"]), tzinfo=_zone(rule.get("tz", "UTC")))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def blocked(now: datetime, group: str | None, fixed: list[datetime], rules: list[dict], minutes: int,
            since: datetime | None = None) -> str | None:
    """[since, now] aralığı (UTC) bir olayın +/- minutes penceresine değiyorsa olayın adını döndürür.

    since, değerlendirilen çubuğun açılış zamanıdır: veri o çubuğun içinde açıklandıysa
    çubuk kapanışındaki sinyal de yasaktır (haber fitili süpürme sinyaline benzer).
    """
    w = timedelta(minutes=minutes)
    lo, hi = (since or now) - w, now + w

    def hit(t: datetime) -> bool:
        return lo <= t <= hi
    for t in fixed:
        if hit(t):
            return "veri saati"
    for r in rules:
        groups = r.get("groups")
        if groups and group not in groups:
            continue
        for d in (now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)):
            t = rule_time_utc(r, d)
            if t is not None and hit(t):
                return f"veri saati: {r.get('name', '?')}"
    return None
