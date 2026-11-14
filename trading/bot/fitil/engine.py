"""Motor: yeni M15 çubuğunu yakalar, sinyali değerlendirir, riski boyutlandırır, pozisyonu yönetir.

step() tek bir tur atar; run() bunu canlıda scan_interval_sec aralıkla tekrarlar.
Koruma brokerdadır: her emir stop-loss ile gönderilir, bot dursa da stop yerinde kalır.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import events
from .broker import Broker, FatalError, Pos, round_lots_down
from .config import Config
from .strategy import Signal, atr, evaluate


class Engine:
    def __init__(self, broker: Broker, cfg: Config, state_path: str | Path | None = None,
                 journal_path: str | Path | None = None, dry_run: bool = False, log=print):
        self.broker, self.cfg, self.dry_run, self.log = broker, cfg, dry_run, log
        self.state_path = Path(state_path) if state_path else None
        self.journal_path = Path(journal_path) if journal_path else None
        self.seen: dict[str, datetime] = {}
        self.state = {"day": "", "day_start_equity": 0.0, "peak_equity": 0.0, "trades_today": 0, "tickets": {},
                      "flows_until": "", "withdraw_base": 0.0, "withdraw_week": "",
                      # adj: para akışlarından arındırma çarpanı. "Ayarlı özkaynak" = özkaynak * adj; yalnızca
                      # işlem kâr/zararıyla değişir. peak_equity ve day_start_equity ayarlı birimdedir.
                      "adj": 1.0, "peak_time": "", "day_start_time": "",
                      "peak_log": []}       # son zirve yükselişleri: [zaman, yükselişten önceki zirve]
        self._warned: set[str] = set()
        if self.state_path and self.state_path.exists():
            try:
                self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                bad = self.state_path.with_suffix(".bad")
                os.replace(self.state_path, bad)
                self.log(f"UYARI: durum dosyası bozuk, {bad.name} olarak ayrıldı. Zirve ve gün başı özkaynak sıfırdan başlıyor.")
        self._blackouts = []
        for x in cfg.blackouts:
            t = datetime.fromisoformat(x)
            self._blackouts.append(t.astimezone(timezone.utc).replace(tzinfo=None) if t.tzinfo else t)
        self.bar = timedelta(minutes=cfg.strategy.bar_minutes)
        self._flow_pending = False        # yeni akış görüldü; özkaynağın onu yansıttığından emin olmak için bir tur beklenir
        self._flow_error_at: datetime | None = None
        self._last_block: dict[str, str | None] = {}
        self._close_retry: dict[int, datetime] = {}

    # ------------------------------------------------------------------ durum
    def _save(self) -> None:
        if self.state_path:
            tmp = self.state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.state), encoding="utf-8")
            os.replace(tmp, self.state_path)             # yarım yazılmış dosya kalmaz

    def reset_peak(self, equity: float) -> None:
        """Düşüş frenini sıfırlar: zirve = şimdiki özkaynak. Yalnızca kullanıcı isteğiyle çağrılır."""
        self.state["peak_equity"] = equity * self.state["adj"]
        self._save()

    def _journal(self, now: datetime, event: str, symbol: str = "", **kw) -> None:
        try:
            self.log(f"{now:%Y-%m-%d %H:%M:%S} {event:10s} {symbol:10s} " + " ".join(f"{k}={v}" for k, v in kw.items()))
        except UnicodeEncodeError:               # konsol Türkçe karakteri yazamıyorsa günlük yine de tutulur
            pass
        if not self.journal_path:
            return
        new = not self.journal_path.exists()
        # utf-8-sig: Excel dosyayı Türkçe karakterleriyle doğru açar; BOM yalnızca dosya başına yazılır
        with self.journal_path.open("a", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc", "event", "symbol", "detail"])
            w.writerow([now.isoformat(), event, symbol, json.dumps(kw, ensure_ascii=False, default=str)])

    def _adj(self, equity: float) -> float:
        """Para yatırma/çekmeden arındırılmış özkaynak: yalnızca işlem kâr/zararıyla değişir."""
        return equity * self.state["adj"]

    def _roll_day(self, now: datetime, equity: float) -> None:
        day = now.strftime("%Y-%m-%d")
        adj_eq = self._adj(equity)
        changed = False
        if self.state["day"] != day:
            self.state.update(day=day, day_start_equity=adj_eq, day_start_time=now.isoformat(), trades_today=0)
            changed = True
        if adj_eq > self.state["peak_equity"]:
            log = self.state["peak_log"]
            log.append([now.isoformat(), self.state["peak_equity"]])
            cutoff = (now - timedelta(hours=24)).isoformat()
            self.state["peak_log"] = [x for x in log if x[0] >= cutoff][-200:]
            self.state.update(peak_equity=adj_eq, peak_time=now.isoformat())
            changed = True
        if self.state["withdraw_base"] <= 0:
            self.state["withdraw_base"] = equity
            changed = True
        if changed:
            self._save()

    def _apply_flows(self, now: datetime, equity: float) -> bool:
        """Para yatırma/çekmeyi kâr/zarardan ayırır. Dönüş: False ise bu tur özkaynak belirsizdir.

        Yöntem: akış f görüldüğünde çarpan adj *= (E - f) / E yapılır; böylece ayarlı özkaynak
        (E * adj) akıştan etkilenmez. Zamanlama sorunlarına karşı:
          - Akış ilk görüldüğü turda uygulanmaz, bir tur beklenir: özkaynak okuması akışı henüz
            yansıtmıyor olabilir.
          - Akış kaydı özkaynaktan GEÇ göründüyse, arada zirve ya da gün başı hayali sıçramayla
            kaydedilmiş olabilir; kayıt zamanı akıştan sonraysa aynı çarpanla düzeltilir.
          - Geçmiş okunamazsa tur iptal edilmez; yalnızca akış kontrolü sonraki tura kalır.
        """
        if not self.state["flows_until"]:
            self.state["flows_until"] = now.isoformat()
            self._save()
        since = datetime.fromisoformat(self.state["flows_until"])
        try:
            flows = [(t, a) for t, a in self.broker.flows_since(since) if t > since and a]
        except FatalError:
            raise
        except Exception as e:
            if self._flow_error_at is None or (now - self._flow_error_at).total_seconds() > 600:
                self._flow_error_at = now
                self._journal(now, "UYARI", "", neden="para akışı geçmişi okunamadı", hata=str(e))
            return True
        if not flows:
            self._flow_pending = False
            if now - since > timedelta(hours=6):
                self.state["flows_until"] = (now - timedelta(hours=1)).isoformat()
                self._save()
            return True
        if not self._flow_pending:
            self._flow_pending = True
            return False
        self._flow_pending = False
        flow = sum(a for _, a in flows)
        first = min(t for t, _ in flows)
        if equity > 0 and equity - flow > 0:
            k = (equity - flow) / equity
            self.state["adj"] *= k
            dt = self.state["day_start_time"]
            # Hayali sıçrama düzeltmesi (yalnızca para yatırmada, zirve yalnızca aşağı): akış kaydı geç
            # göründüyse arada zirve hayali özkaynakla yükselmiş olabilir. Akış anından (30 sn saat farkı
            # payıyla) sonraki yükselişler geri alınır: zirve, o yükselişlerden ÖNCEKİ değerin ve düzeltilmiş
            # değerin büyüğüne çekilir. Böylece akıştan önce var olan gerçek düşüş silinmez.
            if k < 1:
                edge = (first - timedelta(seconds=30)).isoformat()
                later = [x for x in self.state["peak_log"] if x[0] >= edge]
                if later:
                    self.state["peak_equity"] = max(later[0][1], self.state["peak_equity"] * k)
                    self.state["peak_time"] = now.isoformat()
                    self.state["peak_log"] = [x for x in self.state["peak_log"] if x[0] < edge]
            if dt and datetime.fromisoformat(dt) >= first:
                self.state["day_start_equity"] *= k
        if flow > 0:
            self.state["withdraw_base"] += flow
        self.state["flows_until"] = max(t for t, _ in flows).isoformat()
        self._journal(now, "AKIŞ", "", tutar=round(flow, 2), ozkaynak=round(equity, 2))
        self._save()
        return True

    def _withdraw_hint(self, now: datetime, equity: float) -> None:
        """Haftada bir: önceki zirveyi aşan kârın withdraw_frac kadarını çekim önerisi olarak yazar."""
        c = self.cfg
        if c.withdraw_frac <= 0 or now.weekday() != c.withdraw_weekday:
            return
        week = "{}-W{:02d}".format(*now.isocalendar()[:2])
        if self.state["withdraw_week"] == week:
            return
        self.state["withdraw_week"] = week
        gain = equity - self.state["withdraw_base"]
        amount = round(max(0.0, gain) * c.withdraw_frac, 2)
        if amount > 0:
            self.state["withdraw_base"] = equity - amount
            self._journal(now, "ÇEKİM", "", oneri=amount, ozkaynak=round(equity, 2),
                          not_="zirveyi aşan kârın oranı; çekmek sizin kararınız")
        self._save()

    # ------------------------------------------------------------------ kapılar
    def drawdown(self, equity: float) -> float:
        peak = self.state["peak_equity"]
        return 0.0 if peak <= 0 else max(0.0, 1.0 - self._adj(equity) / peak)

    def risk_factor(self, equity: float) -> float:
        """Zirveden düşüşe göre risk çarpanı: 1.0, 0.5 ya da 0 (dur)."""
        dd = self.drawdown(equity)
        if dd >= self.cfg.dd_halt:
            return 0.0
        return 0.5 if dd >= self.cfg.dd_half_risk else 1.0

    def entry_block(self, now: datetime, equity: float, n_positions: int, symbol: str | None = None,
                    since: datetime | None = None) -> str | None:
        """Yeni giriş yasaksa nedenini döndürür."""
        c = self.cfg
        if now.weekday() >= 5:
            return "hafta sonu"
        if not c.entry_hours[0] <= now.hour < c.entry_hours[1]:
            return "seans dışı"
        start = self.state["day_start_equity"]
        if start > 0 and (start - self._adj(equity)) / start >= c.daily_loss_limit:
            return "günlük kayıp sınırı"
        if self.risk_factor(equity) == 0.0:
            return "düşüş freni: sistem durdu"
        if self.state["trades_today"] >= c.max_trades_per_day:
            return "günlük işlem sınırı"
        if n_positions >= c.max_positions:
            return "azami pozisyon sayısı"
        group = c.symbols.get(symbol, {}).get("group") if symbol else None
        return events.blocked(now, group, self._blackouts, c.recurring_blackouts, c.blackout_minutes, since)

    def _open_risk(self, positions: list[Pos]) -> tuple[float, float]:
        total = usd = 0.0
        for p in positions:
            r = self.broker.loss_per_lot(p.symbol, p.direction, p.entry, p.sl) * p.lots
            total += r
            usd += r * p.direction * self.cfg.symbols.get(p.symbol, {}).get("usd_beta", 0)
        return total, usd

    # ------------------------------------------------------------------ tur
    def step(self) -> None:
        b, c = self.broker, self.cfg
        b.heartbeat()
        now = b.now()
        acct = b.account()
        settled = self._apply_flows(now, acct.equity)
        if settled or not self.state["day"]:
            self._roll_day(now, acct.equity)
        positions = b.positions()

        for p in [p for p in positions if p.sl <= 0]:        # güvenlik: stopsuz pozisyon tutulmaz
            retry = self._close_retry.get(p.ticket)
            if not (retry and now < retry):
                self._journal(now, "UYARI", p.symbol, ticket=p.ticket, neden="stopsuz pozisyon kapatılıyor")
            self._close(now, p, "stopsuz pozisyon")
        if any(p.sl <= 0 for p in positions) and not self.dry_run:
            positions = b.positions()

        live = {str(p.ticket) for p in positions}
        stale = [t for t in self.state["tickets"] if t not in live]
        if stale:
            for t in stale:
                del self.state["tickets"][t]
            self._save()

        if now.hour >= c.flat_hour or now.weekday() >= 5:
            for p in positions:
                self._close(now, p, "gün sonu")
            if now.hour >= c.flat_hour and now.weekday() < 5 and (self.dry_run or not b.positions()):
                self._withdraw_hint(now, b.account().equity)
            return

        # Gece taşınmış pozisyon (bot kapalıydı ya da kapanış reddedildi): ilk fırsatta kapat
        carried = [p for p in positions if p.open_time.date() < now.date()]
        for p in carried:
            self._close(now, p, "gece taşınmış")
        if carried and not self.dry_run:
            positions = b.positions()

        for sym in c.symbols:
            last = b.last_bar_time(sym)
            if last is None or self.seen.get(sym) == last:
                continue
            closed_at = last + self.bar
            if (now - closed_at).total_seconds() < c.settle_sec:
                continue
            self.seen[sym] = last
            df = b.bars(sym, c.history_bars)
            if len(df) < c.strategy.min_bars:
                if sym not in self._warned:
                    self._warned.add(sym)
                    self._journal(now, "UYARI", sym, neden="geçmiş yetersiz", cubuk=len(df), gereken=c.strategy.min_bars)
                if len(df) == 0:
                    continue
            mine = [p for p in positions if p.symbol == sym]
            if mine:
                for p in mine:
                    self._manage(now, p, df)
                continue
            if (now - closed_at).total_seconds() > c.max_signal_age_sec:
                continue                     # bot geç başladı: eski çubuğa girme
            if not settled:
                continue                     # para akışı işleniyor: özkaynak netleşene kadar giriş yok
            block = self.entry_block(now, acct.equity, len(positions), sym, since=last)
            if block != self._last_block.get(sym):
                self._last_block[sym] = block
                if block not in (None, "seans dışı", "hafta sonu"):
                    self._journal(now, "ENGEL", sym, neden=block, ozkaynak=round(acct.equity, 2),
                                  dusus_yuzde=round(100 * self.drawdown(acct.equity), 1))
            if block:
                continue
            tick = b.tick(sym)
            if tick is None:
                continue
            sig = evaluate(df, c.strategy, tick.ask - tick.bid)
            if sig is not None and self._enter(now, sym, sig, acct.equity, positions):
                positions = b.positions()

    # ------------------------------------------------------------------ giriş
    def _enter(self, now: datetime, sym: str, sig: Signal, equity: float, positions: list[Pos]) -> bool:
        b, c = self.broker, self.cfg
        spec, tick = b.spec(sym), b.tick(sym)
        if not spec.tradable or tick is None:
            return False
        entry = tick.ask if sig.direction > 0 else tick.bid
        sl = round(sig.stop, spec.digits)
        dist = (entry - sl) * sig.direction
        spread = tick.ask - tick.bid
        if dist <= max(spec.stops_level, c.strategy.min_stop_spread_mult * spread):
            self._journal(now, "atlandı", sym, neden="stop mesafesi yetersiz", mesafe=round(dist, 6))
            return False
        per_lot = b.loss_per_lot(sym, sig.direction, entry, sl)
        if per_lot <= 0:
            return False
        risk_pct = c.risk_tiers.get(sig.score, min(c.risk_tiers.values())) * self.risk_factor(equity)
        total, usd_net = self._open_risk(positions)
        budget = min(equity * risk_pct, max(0.0, equity * c.max_portfolio_risk - total))
        lots = round_lots_down(spec, budget / per_lot)
        if lots <= 0:
            self._journal(now, "atlandı", sym, neden="risk bütçesi minimum lota yetmiyor", butce=round(budget, 2))
            return False
        risk = lots * per_lot
        usd_dir = sig.direction * c.symbols[sym].get("usd_beta", 0)
        after = usd_net + usd_dir * risk
        if usd_dir != 0 and abs(after) > equity * c.max_usd_net_risk and abs(after) > abs(usd_net):
            self._journal(now, "atlandı", sym, neden="net USD risk sınırı")
            return False
        if not b.margin_ok(sym, sig.direction, lots):
            self._journal(now, "atlandı", sym, neden="teminat yetersiz")
            return False

        info = dict(yon="AL" if sig.direction > 0 else "SAT", lot=lots, giris=round(entry, spec.digits), sl=sl,
                    risk=round(risk, 2), risk_yuzde=round(100 * risk / equity, 3), kurulum=sig.setup,
                    puan=sig.score, neden=sig.reason)
        if self.dry_run:
            self._journal(now, "SİNYAL", sym, **info)
            return False
        res = b.market_order(sym, sig.direction, lots, sl, f"fitil-{sig.setup}")
        # Emrin sonucuna değil brokerdaki gerçeğe bak: "başarısız" dönen emir de pozisyon açmış olabilir.
        opened = [p for p in b.positions() if p.symbol == sym]
        if not opened:
            self._journal(now, "RED", sym, mesaj=res.message, **info)
            return False
        p = max(opened, key=lambda x: x.open_time)
        self.state["trades_today"] += 1
        self.state["tickets"][str(p.ticket)] = {"entry": p.entry, "init_dist": (p.entry - sl) * sig.direction}
        self._save()
        self._journal(now, "GİRİŞ", sym, ticket=p.ticket, dolum=p.entry, emir=res.message, **info)
        return True

    # ------------------------------------------------------------------ yönetim
    def _manage(self, now: datetime, p: Pos, df) -> None:
        c = self.cfg
        if len(df) < max(c.trail_bars, c.strategy.atr_period + 1):
            return
        spec = self.broker.spec(p.symbol)
        h, l, cl = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
        meta = self.state["tickets"].get(str(p.ticket))
        if not meta or meta["init_dist"] <= 0:
            # Durum dosyası kayıp (yeniden başlatma): başlangıç riskini mevcut stoptan, o da olmazsa ATR'den kur
            dist = (p.entry - p.sl) * p.direction
            meta = {"entry": p.entry, "init_dist": dist if dist > 0 else atr(h, l, cl, c.strategy.atr_period)}
            self.state["tickets"][str(p.ticket)] = meta
            self._save()
        moved_r = (cl[-1] - meta["entry"]) * p.direction / meta["init_dist"]
        new_sl = p.sl
        if moved_r >= c.breakeven_at_r:
            new_sl = max(new_sl, meta["entry"]) if p.direction > 0 else min(new_sl, meta["entry"])
        if moved_r >= c.trail_start_r:
            buf = c.strategy.stop_buffer_atr * atr(h, l, cl, c.strategy.atr_period)
            if p.direction > 0:
                new_sl = max(new_sl, l[-c.trail_bars:].min() - buf)
            else:
                new_sl = min(new_sl, h[-c.trail_bars:].max() + buf)
        new_sl = round(float(new_sl), spec.digits)
        if (new_sl - p.sl) * p.direction <= spec.point / 2:
            return                                    # yalnızca pozisyon lehine taşınır
        tick = self.broker.tick(p.symbol)
        if tick is None:
            return
        price = tick.bid if p.direction > 0 else tick.ask
        if (price - new_sl) * p.direction <= spec.stops_level:
            return                                    # fiyata fazla yakın
        if self.dry_run or self.broker.modify_sl(p, new_sl):
            self._journal(now, "STOP", p.symbol, ticket=p.ticket, eski=p.sl, yeni=new_sl, R=round(float(moved_r), 2))

    def _close(self, now: datetime, p: Pos, why: str) -> None:
        if self.dry_run:
            return
        retry = self._close_retry.get(p.ticket)
        if retry and now < retry:
            return                                    # reddedilen kapanışı dakikada bir yeniden dene
        res = self.broker.close(p)
        if res.ok:
            self._close_retry.pop(p.ticket, None)
        else:
            self._close_retry[p.ticket] = now + timedelta(seconds=60)
        self._journal(now, "ÇIKIŞ" if res.ok else "RED", p.symbol, ticket=p.ticket, neden=why,
                      fiyat=res.price, mesaj=res.message)

    # ------------------------------------------------------------------ canlı döngü
    def run(self) -> None:
        self.log("Bot çalışıyor. Durdurmak için Ctrl+C. Açık pozisyonların stopları brokerda kalır.")
        while True:
            try:
                self.step()
            except (KeyboardInterrupt, FatalError):
                raise
            except Exception as e:                    # tek bir hata botu düşürmesin; stoplar brokerda
                self.log(f"HATA: {type(e).__name__}: {e}")
            time.sleep(self.cfg.scan_interval_sec)
