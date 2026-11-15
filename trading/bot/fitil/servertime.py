"""Broker sunucu saatinin düzenini fiyat geçmişinden çıkarır.

Döviz piyasası her cuma New York saatiyle 17:00'de kapanır. Kapanışın sunucu saatinde nereye düştüğüne bakılır:
  - Yıl boyunca aynı saatteyse sunucu New York'a bağlıdır (çoğu broker: New York + 7, kışın UTC+2, yazın UTC+3).
  - New York yaz saatine geçince bir saat kayıyorsa sunucu sabit farklıdır (ör. hep UTC+2).
Bu yöntem piyasa kapalıyken de çalışır ve geçmişin her dönemini doğru çevirir.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

import pandas as pd

NY = "America/New_York"


def weekly_closes(server_index: pd.DatetimeIndex, bar_minutes: int) -> list[pd.Timestamp]:
    """Her hafta sonu boşluğundan önceki son çubuğun BİTİŞ zamanı (sunucu saati)."""
    idx = pd.DatetimeIndex(server_index).sort_values().unique()
    if len(idx) < 2:
        return []
    gaps = idx[1:] - idx[:-1]
    ends = idx[:-1][gaps > pd.Timedelta(hours=36)]
    return [t + pd.Timedelta(minutes=bar_minutes) for t in ends]


def detect_server_clock(server_index: pd.DatetimeIndex, bar_minutes: int, min_weeks: int = 3) -> dict | None:
    """Sunucu saat düzenini bulur. Dönüş:
        {"tz": "America/New_York", "plus_hours": p}   (New York'a bağlı, yaz saati dahil)
        {"offset_hours": x}                             (sabit fark)
        None                                            (veri yetersiz ya da düzen tanınmadı)
    """
    from zoneinfo import ZoneInfo
    ny = ZoneInfo(NY)
    groups: dict[bool, Counter] = {True: Counter(), False: Counter()}
    for end in weekly_closes(server_index, bar_minutes):
        # cuma 00:00 (sunucu) referans alınarak kapanışın saat değeri: cuma 23:59 ~ 24, cumartesi 00:00 = 24
        friday = (end - pd.Timedelta(minutes=1)).normalize()
        while friday.weekday() != 4:
            friday -= pd.Timedelta(days=1)
            if (end - friday) > pd.Timedelta(days=3):
                break
        if friday.weekday() != 4:
            continue
        hours = round((end - friday).total_seconds() / 3600.0)
        dst = bool(ny.dst(friday.to_pydatetime().replace(hour=12)) != timedelta(0))
        groups[dst][hours] += 1
    if sum(groups[True].values()) < min_weeks or sum(groups[False].values()) < min_weeks:
        return None
    summer = groups[True].most_common(1)[0][0]
    winter = groups[False].most_common(1)[0][0]
    if summer == winter:                               # kapanış sunucu saatinde sabit: New York'a bağlı
        plus = summer - 17
        return {"tz": NY, "plus_hours": plus} if -12 <= plus <= 14 else None
    if winter - summer == 1:                           # New York yaz saatinde bir saat erken: sabit fark
        offset = summer - 21                           # yaz: 17:00 New York = 21:00 UTC
        return {"offset_hours": offset} if -12 <= offset <= 14 else None
    return None


def describe(clock: dict) -> str:
    if "tz" in clock:
        return f"New York + {clock['plus_hours']:g} saat (kışın UTC+{clock['plus_hours'] - 5:g}, yazın UTC+{clock['plus_hours'] - 4:g})"
    return f"sabit UTC{clock['offset_hours']:+g}"
