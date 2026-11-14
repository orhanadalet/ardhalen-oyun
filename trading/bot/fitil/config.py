"""Yapılandırma. Tüm oranlar özkaynağın kesridir; sabit dolar tutarı yoktur."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class StrategyParams:
    atr_period: int = 14
    min_bars: int = 600               # değerlendirme için gereken en az M15 çubuğu
    h1_ema: int = 50                  # yön: H1 kapanışlarının EMA'sı
    h4_ema: int = 20                  # yön: H4 kapanışlarının EMA'sı
    asia_end_hour: int = 7            # UTC; Asya aralığı 00:00 - bu saat
    min_day_bars: int = 40            # "önceki gün" sayılması için gereken en az M15 çubuğu (10 saat)
    sweep_fresh_bars: int = 4         # süpürmeden önce bu kadar çubuk seviyenin doğru tarafında kapanmış olmalı
    sweep_close_frac: float = 0.6     # süpürme çubuğu aralığının bu kadarını geri almış olmalı
    sweep_min_range_atr: float = 0.8
    strong_range_atr: float = 1.5     # bu kadar geniş çubuk +1 kanaat puanı
    pullback_ema_fast: int = 20
    pullback_ema_slow: int = 50
    pullback_touch_bars: int = 3
    pullback_swing_bars: int = 5
    stop_buffer_atr: float = 0.25     # stop, yapının bu kadar ötesine konur
    max_stop_atr: float = 3.0         # stop bundan uzaksa işlem alınmaz
    min_stop_spread_mult: float = 10.0  # stop mesafesi spread'in en az bu katı olmalı


@dataclass
class Config:
    # sembol adı (brokerdaki TAM ad) -> {"usd_beta": -1 | 0 | +1, "group": "oil" (isteğe bağlı)}
    #   usd_beta: sembolde LONG olmak USD'de short ise -1 (EURUSD, XAUUSD), long ise +1 (USDJPY), petrol 0
    symbols: dict = field(default_factory=dict)
    magic: int = 20261115
    history_bars: int = 1500
    scan_interval_sec: float = 5.0    # yeni çubuk kontrolü ve pozisyon takibi
    settle_sec: float = 3.0           # çubuk kapanışından sonra bekleme
    max_signal_age_sec: float = 90.0  # bundan eski sinyale girilmez (bot geç başladıysa)
    server_utc_offset_hours: float | None = None   # sabit fark; None: aşağıdaki ayar ya da otomatik
    # Sunucu saati bir saat dilimine bağlıysa (çoğu broker: New York + 7 saat) burada tanımlayın;
    # yaz saati geçişleri doğru hesaplanır:  {"tz": "America/New_York", "plus_hours": 7}
    server_time: dict | None = None
    deviation_points: int = 30

    # Zaman (UTC)
    entry_hours: tuple = (7, 17)      # [başlangıç, bitiş) saatleri arasında yeni giriş
    flat_hour: int = 20               # bu saatte tüm pozisyonlar kapatılır (rollover öncesi)
    blackouts: list = field(default_factory=list)   # ["2026-11-18T19:00", ...] UTC, yüksek etkili veri
    blackout_minutes: int = 15        # veri saatinin bu kadar öncesi/sonrası giriş yok
    recurring_blackouts: list = field(default_factory=lambda: [
        {"name": "NFP", "weekday": 4, "week_of_month": 1, "time": "08:30", "tz": "America/New_York"},
        {"name": "EIA", "weekday": 2, "time": "10:30", "tz": "America/New_York", "groups": ["oil"]},
    ])

    # Risk (özkaynak kesri)
    risk_tiers: dict = field(default_factory=lambda: {1: 0.0025, 2: 0.005, 3: 0.01})
    max_portfolio_risk: float = 0.03
    max_usd_net_risk: float = 0.02
    daily_loss_limit: float = 0.03    # gün başı özkaynağa göre; aşılınca o gün yeni giriş yok
    max_trades_per_day: int = 20
    max_positions: int = 5
    dd_half_risk: float = 0.10        # zirveden bu kadar düşüşte risk yarıya iner
    dd_halt: float = 0.20             # bu kadar düşüşte yeni giriş durur

    # Pozisyon yönetimi
    breakeven_at_r: float = 1.0       # bu kadar R lehe gidince stop girişe
    trail_start_r: float = 1.5        # bu kadar R'den sonra stop yapıyı izler
    trail_bars: int = 8

    # Çekim önerisi: haftada bir, önceki zirveyi aşan kârın bu kadarı çekilmek üzere önerilir.
    # Bot para çekemez; yalnızca tutarı günlüğe yazar. 0 = kapalı.
    withdraw_frac: float = 0.5
    withdraw_weekday: int = 4         # 4 = Cuma (gün sonu kapanışında)

    strategy: StrategyParams = field(default_factory=StrategyParams)

    @staticmethod
    def load(path: str | Path) -> "Config":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"Bilinmeyen ayar(lar): {sorted(unknown)}")
        strat = StrategyParams(**raw.pop("strategy", {}))
        cfg = Config(strategy=strat, **raw)
        cfg.risk_tiers = {int(k): float(v) for k, v in cfg.risk_tiers.items()}
        cfg.entry_hours = tuple(cfg.entry_hours)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if not self.symbols:
            raise ValueError("symbols boş: en az bir sembol tanımlayın")
        for name, s in self.symbols.items():
            if s.get("usd_beta") not in (-1, 0, 1):
                raise ValueError(f"{name}: usd_beta -1, 0 ya da 1 olmalı")
        if max(self.risk_tiers.values()) > 0.02:
            raise ValueError("risk_tiers: işlem başına risk %2'yi aşamaz")
        if not 0 < self.max_portfolio_risk <= 0.10:
            raise ValueError("max_portfolio_risk (0, 0.10] aralığında olmalı")
        if not 0 <= self.entry_hours[0] < self.entry_hours[1] <= self.flat_hour <= 23:
            raise ValueError("entry_hours ve flat_hour tutarsız")
        if not 0 < self.dd_half_risk < self.dd_halt < 1:
            raise ValueError("dd_half_risk < dd_halt olmalı")
        if not 0 <= self.withdraw_frac <= 1:
            raise ValueError("withdraw_frac 0-1 aralığında olmalı")
        if not 0 < self.daily_loss_limit <= 0.10:
            raise ValueError("daily_loss_limit (0, 0.10] aralığında olmalı")
        from datetime import datetime
        from .events import validate_rules
        for x in self.blackouts:
            datetime.fromisoformat(x)
        validate_rules(self.recurring_blackouts)
        if self.server_time is not None:
            from .events import _zone
            _zone(self.server_time["tz"])
            float(self.server_time.get("plus_hours", 0))
