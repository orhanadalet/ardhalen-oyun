"""Çalıştırma:  cd trading && python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fitil.broker import FatalError, PaperBroker, Pos, Spec, round_lots_down   # noqa: E402
from fitil.config import Config, StrategyParams                    # noqa: E402
from fitil.engine import Engine                                    # noqa: E402
from fitil.mt5_broker import MT5Broker                             # noqa: E402
from fitil.strategy import evaluate, htf_bias, liquidity_levels    # noqa: E402
from fitil import backtest as bt                                   # noqa: E402
from fitil import events                                           # noqa: E402

SPREAD = 0.00002


def SP(**kw):
    """Testler iki kurulumu da kapsar (varsayılan yapılandırmada süpürme kapalıdır)."""
    kw.setdefault("setups", ("sweep", "pullback"))
    return StrategyParams(**kw)

SPEC = Spec("EURUSD", 5, 0.00001, 0.01, 0.01, 100.0)


def m15(n=1600, end="2024-03-13 09:00", drift=2e-6, wig=0.0004):
    """Hafif yükselen, dalgalı M15 serisi; son çubuğun açılış zamanı `end` (UTC)."""
    idx = pd.date_range(end=end, periods=n, freq="15min")
    i = np.arange(n)
    close = 1.10 + drift * i + wig * np.sin(i / 7.0)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + 0.0001,
                         "low": np.minimum(open_, close) - 0.0001, "close": close}, index=idx)


def with_sweep_low(df):
    """Son çubuğu önceki günün dibini süpürüp geri içeride kapanan bir çubuğa çevirir."""
    df = df.copy()
    p = SP()
    pdl = liquidity_levels(df.iloc[:-1], p)["pdl"]
    for k in range(2, 7):                                   # önceki çubuklar seviyenin hemen üstünde
        df.iloc[-k] = [pdl + 0.0003, pdl + 0.0004, pdl + 0.0002, pdl + 0.0003]
    df.iloc[-1] = [pdl + 0.0001, pdl + 0.00045, pdl - 0.0001, pdl + 0.0004]
    return df, pdl


def paper(df, equity=10_000.0, start=None, **kw):
    return PaperBroker({"EURUSD": df}, {"EURUSD": SPEC}, {"EURUSD": 100_000.0}, {"EURUSD": "quote"},
                       {"EURUSD": SPREAD}, equity=equity, commission_per_lot=0.0, start=start, **kw)


def cfg(**kw):
    kw.setdefault("settle_sec", 0.0)        # simülasyonda saat tam çubuk kapanışındadır
    kw.setdefault("strategy", SP())
    c = Config(symbols={"EURUSD": {"usd_beta": -1}}, **kw)
    c.validate()
    return c


def at_end(df):
    """Saati son çubuğun kapanış anına getirilmiş PaperBroker (son çubuk = az önce kapandı)."""
    extra = df.iloc[[-1]].copy()
    extra.index = extra.index + pd.Timedelta(minutes=15)
    full = pd.concat([df, extra])
    return paper(full, start=full.index[-1])


class Strategy(unittest.TestCase):
    def test_sweep_of_previous_day_low_gives_long_with_stop_below_wick(self):
        df, pdl = with_sweep_low(m15())
        sig = evaluate(df, SP(), SPREAD)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.direction, sig.setup), (1, "sweep"))
        self.assertLess(sig.stop, df.low.iloc[-1])
        self.assertIn(sig.score, (1, 2, 3))

    def test_no_sweep_when_bar_closes_below_level(self):
        df, pdl = with_sweep_low(m15())
        df.iloc[-1] = [pdl + 0.0001, pdl + 0.00015, pdl - 0.0004, pdl - 0.0003]   # gerçek kırılım
        sig = evaluate(df, SP(), SPREAD)
        self.assertTrue(sig is None or sig.setup != "sweep" or sig.direction != 1)

    def test_wide_spread_rejects_trade(self):
        df, _ = with_sweep_low(m15())
        self.assertIsNone(evaluate(df, SP(), spread=0.0005))    # stop < 10 x spread

    def test_mirror_gives_short_on_high_sweep(self):
        df, _ = with_sweep_low(m15())
        mirrored = df.copy()
        for col, src in (("open", "open"), ("close", "close"), ("high", "low"), ("low", "high")):
            mirrored[col] = 2.2 - df[src]
        sig = evaluate(mirrored, SP(), SPREAD)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.direction, sig.setup), (-1, "sweep"))
        self.assertGreater(sig.stop, mirrored.high.iloc[-1])

    def test_signal_uses_only_closed_bars_no_lookahead(self):
        df, _ = with_sweep_low(m15())
        future = pd.concat([df, m15(50, end="2024-03-13 21:30") * 3.0])
        self.assertEqual(evaluate(df, SP(), SPREAD), evaluate(future.iloc[:len(df)], SP(), SPREAD))

    def test_bias_follows_trend(self):
        self.assertEqual(htf_bias(m15(drift=2e-5, wig=0.0001), SP()), 1)
        self.assertEqual(htf_bias(m15(drift=-2e-5, wig=0.0001), SP()), -1)

    def test_too_little_history_returns_none(self):
        self.assertIsNone(evaluate(m15(200), SP(), SPREAD))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.df, self.pdl = with_sweep_low(m15())
        self.logs = []

    def engine(self, broker, **kw):
        conf = kw.pop("conf", cfg())
        return Engine(broker, conf, log=self.logs.append, **kw)

    def test_entry_has_stop_and_risk_within_tier(self):
        b = at_end(self.df)
        e = self.engine(b)
        e.step()
        pos = b.positions()
        self.assertEqual(len(pos), 1)
        p = pos[0]
        self.assertEqual(p.direction, 1)
        self.assertLess(p.sl, p.entry)
        risk = (p.entry - p.sl) * p.lots * 100_000
        self.assertLessEqual(risk, 10_000 * 0.01 + 1e-6)             # en yüksek kademe %1
        self.assertGreater(risk, 10_000 * 0.0025 * 0.9)              # en düşük kademenin altında değil
        self.assertEqual(e.state["trades_today"], 1)

    def test_same_bar_is_not_traded_twice(self):
        b = at_end(self.df)
        e = self.engine(b)
        e.step(); e.step(); e.step()
        self.assertEqual(len(b.positions()), 1)

    def test_dry_run_sends_nothing(self):
        b = at_end(self.df)
        self.engine(b, dry_run=True).step()
        self.assertEqual(b.positions(), [])
        self.assertTrue(any("SİNYAL" in x for x in self.logs))

    def test_tiny_account_skips_instead_of_oversizing(self):
        df = self.df
        extra = df.iloc[[-1]].copy(); extra.index = extra.index + pd.Timedelta(minutes=15)
        full = pd.concat([df, extra])
        b = paper(full, equity=100.0, start=full.index[-1])           # 100 USD: 0.01 lot bile bütçeyi aşar
        self.engine(b).step()
        self.assertEqual(b.positions(), [])
        self.assertTrue(any("minimum lota yetmiyor" in x for x in self.logs))

    def test_daily_loss_limit_blocks_entries(self):
        b = at_end(self.df)
        e = self.engine(b)
        e.state.update(day=b.now().strftime("%Y-%m-%d"), day_start_equity=10_400.0, peak_equity=10_400.0)
        e.step()                                                      # 10.000 / 10.400 = -%3,8
        self.assertEqual(b.positions(), [])

    def test_drawdown_brake_halves_then_halts(self):
        e = self.engine(at_end(self.df))
        e.state["peak_equity"] = 10_000.0
        self.assertEqual(e.risk_factor(9_500.0), 1.0)
        self.assertEqual(e.risk_factor(8_900.0), 0.5)
        self.assertEqual(e.risk_factor(7_900.0), 0.0)

    def test_gates(self):
        e = self.engine(at_end(self.df), conf=cfg(blackouts=["2024-03-13T12:30"], max_trades_per_day=2))
        day = datetime(2024, 3, 13)
        self.assertIsNone(e.entry_block(day.replace(hour=9), 10_000, 0))
        self.assertEqual(e.entry_block(day.replace(hour=3), 10_000, 0), "seans dışı")
        self.assertEqual(e.entry_block(day.replace(hour=18), 10_000, 0), "seans dışı")
        self.assertEqual(e.entry_block(day.replace(hour=12, minute=40), 10_000, 0), "veri saati")
        self.assertEqual(e.entry_block(day.replace(hour=9), 10_000, 5), "azami pozisyon sayısı")
        self.assertEqual(e.entry_block(datetime(2024, 3, 16, 9), 10_000, 0), "hafta sonu")
        e.state["trades_today"] = 2
        self.assertEqual(e.entry_block(day.replace(hour=9), 10_000, 0), "günlük işlem sınırı")

    def test_stale_signal_is_ignored_when_bot_starts_late(self):
        df = self.df
        late = df.iloc[[-1]].copy(); late.index = late.index + pd.Timedelta(minutes=15)
        b = paper(pd.concat([df, late]), start=late.index[0])
        b.now = lambda: late.index[0].to_pydatetime() + timedelta(minutes=5)     # kapanıştan 5 dk sonra
        self.engine(b).step()
        self.assertEqual(b.positions(), [])

    def _run_forward(self, tail_closes):
        """Sinyal çubuğundan sonra verilen kapanışlarla devam eden seriyi baştan sona çalıştırır."""
        df = self.df
        idx = pd.date_range(df.index[-1] + pd.Timedelta(minutes=15), periods=len(tail_closes), freq="15min")
        closes = np.array(tail_closes, dtype=float)
        opens = np.concatenate([[df.close.iloc[-1]], closes[:-1]])
        tail = pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) + 0.00002,
                             "low": np.minimum(opens, closes) - 0.00002, "close": closes}, index=idx)
        b = paper(pd.concat([df, tail]), start=idx[0])
        e = self.engine(b)
        e.step()
        entry_pos = b.positions()[0]
        first_sl, entry = entry_pos.sl, entry_pos.entry
        sls = [first_sl]
        while b.advance():
            e.step()
            if b.positions():
                sls.append(b.positions()[0].sl)
        return b, entry, first_sl, sls

    def test_stop_loss_closes_position_at_stop(self):
        c0 = self.df.close.iloc[-1]
        b, entry, sl, _ = self._run_forward([c0 - 0.0002, c0 - 0.0006, c0 - 0.0012, c0 - 0.0012])
        self.assertEqual(b.positions(), [])
        t = b.closed[0]
        self.assertEqual(t["reason"], "stop")
        self.assertAlmostEqual(t["exit"], sl, places=5)
        self.assertLess(t["pnl"], 0)
        self.assertGreaterEqual(t["pnl"], -10_000 * 0.01 * 1.05)       # kayıp, kademenin izin verdiği riski aşmaz

    def test_stop_only_moves_in_favor_and_reaches_breakeven(self):
        c0 = self.df.close.iloc[-1]
        up = [c0 + 0.0003 * k for k in range(1, 13)]
        b, entry, sl0, sls = self._run_forward(up + [up[-1] - 0.0002, up[-1] - 0.0004])
        self.assertTrue(all(b2 >= a - 1e-9 for a, b2 in zip(sls, sls[1:])))    # asla geri gitmez
        self.assertGreaterEqual(max(sls), entry - 1e-9)                         # başabaşa ulaştı
        self.assertGreater(max(sls), sl0)

    def test_flat_hour_closes_everything(self):
        c0 = self.df.close.iloc[-1]
        n = 4 * 12                                                    # 09:15 -> 21:00 UTC
        b, entry, sl, _ = self._run_forward([c0 + 0.00001 * (k % 3) for k in range(n)])
        self.assertEqual(b.positions(), [])
        t = b.closed[0]
        self.assertEqual(t["reason"], "kapat")
        self.assertEqual(pd.Timestamp(t["close_time"]).hour, 20)

    def test_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            b = at_end(self.df)
            e = self.engine(b, state_path=Path(d) / "s.json", journal_path=Path(d) / "j.csv")
            e.step()
            e2 = self.engine(b, state_path=Path(d) / "s.json")
            self.assertEqual(e2.state["trades_today"], 1)
            self.assertIn(str(b.positions()[0].ticket), e2.state["tickets"])
            self.assertIn("GİRİŞ", (Path(d) / "j.csv").read_text(encoding="utf-8"))


class Sizing(unittest.TestCase):
    def test_round_down(self):
        self.assertEqual(round_lots_down(SPEC, 0.239), 0.23)
        self.assertEqual(round_lots_down(SPEC, 0.0099), 0.0)
        self.assertEqual(round_lots_down(SPEC, float("inf")), 0.0)

    def test_config_rejects_dangerous_values(self):
        with self.assertRaises(ValueError):
            cfg(risk_tiers={1: 0.05})
        with self.assertRaises(ValueError):
            cfg(max_portfolio_risk=0.5)
        with self.assertRaises(ValueError):
            Config(symbols={}).validate()


def fake_mt5(demo=True, server_offset_h=2):
    """MetaTrader5 modülünün istek yapısını yakalayan sahtesi."""
    m = types.SimpleNamespace(
        ACCOUNT_TRADE_MODE_DEMO=0, SYMBOL_TRADE_MODE_FULL=4, TIMEFRAME_M15=15, POSITION_TYPE_BUY=0,
        ORDER_TYPE_BUY=0, ORDER_TYPE_SELL=1, TRADE_ACTION_DEAL=1, TRADE_ACTION_SLTP=6, ORDER_TIME_GTC=0,
        ORDER_FILLING_FOK=0, ORDER_FILLING_IOC=1, ORDER_FILLING_RETURN=2, TRADE_RETCODE_DONE=10009,
        TRADE_RETCODE_DONE_PARTIAL=10010, TRADE_RETCODE_PLACED=10008,
        sent=[], pos=[], retcode=10009, login=111, demo=demo, tick_age=0.0, calls=0, pos_error=False)
    import time as _t
    t0 = _t.time()
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "ok")
    m.account_info = lambda: types.SimpleNamespace(trade_mode=0 if m.demo else 2, equity=5000.0, balance=5000.0,
                                                   margin_free=4000.0, currency="USD", login=m.login)
    m.symbol_info = lambda s: types.SimpleNamespace(digits=5, point=0.00001, volume_min=0.01, volume_step=0.01,
                                                    volume_max=50.0, trade_stops_level=10, trade_mode=4, filling_mode=2)
    m.symbol_select = lambda s, v: True
    def symbol_info_tick(s):
        m.calls += 1                              # canlı piyasa: her çağrıda biraz daha yeni tick
        if m.tick_age > 0:                        # piyasa kapalı: son tick sabit, akmıyor
            return types.SimpleNamespace(bid=1.1000, ask=1.1001, time=t0 + server_offset_h * 3600 - m.tick_age)
        return types.SimpleNamespace(bid=1.1000, ask=1.1001, time=_t.time() + server_offset_h * 3600 + m.calls * 1e-3)
    m.symbol_info_tick = symbol_info_tick
    m.order_calc_profit = lambda typ, s, vol, a, b: (b - a) * 100_000 * vol * (1 if typ == 0 else -1)
    m.order_calc_margin = lambda typ, s, vol, price: vol * 1000.0
    m.positions_get = lambda: None if m.pos_error else tuple(m.pos)
    m.DEAL_TYPE_BALANCE = 2
    m.connected, m.inits, m.deals = True, 0, []
    m.terminal_info = lambda: types.SimpleNamespace(connected=m.connected, trade_allowed=True)
    m.history_deals_get = lambda lo, hi: tuple(d for d in m.deals if lo <= d.time <= hi)

    def initialize():
        m.inits += 1
        m.connected = True
        return True
    m.initialize = initialize

    def copy_rates_from_pos(sym, tf, pos, count):
        df = m15(count + 1, end=pd.Timestamp(_t.time(), unit="s").floor("15min"))
        t = (df.index.astype("int64") // 10**9 + server_offset_h * 3600).to_numpy()
        arr = np.zeros(count, dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")])
        for k in ("open", "high", "low", "close"):
            arr[k] = df[k].to_numpy()[:count]
        arr["time"] = t[:count]
        return arr
    m.copy_rates_from_pos = copy_rates_from_pos

    def order_send(req):
        m.sent.append(req)
        if req["action"] == 1 and "position" not in req:
            m.pos.append(types.SimpleNamespace(ticket=777, symbol=req["symbol"], type=req["type"], volume=req["volume"],
                                               price_open=req["price"], sl=req["sl"], magic=req["magic"],
                                               time=_t.time() + server_offset_h * 3600))
        return types.SimpleNamespace(retcode=m.retcode, order=555, price=req.get("price", 0.0), comment="done")
    m.order_send = order_send
    return m


class MT5Adapter(unittest.TestCase):
    def test_refuses_real_account_by_default(self):
        with self.assertRaises(RuntimeError):
            MT5Broker(cfg(), sleep=lambda x: None, mt5=fake_mt5(demo=False))
        MT5Broker(cfg(), allow_real=True, sleep=lambda x: None, mt5=fake_mt5(demo=False))

    def test_server_offset_detected_and_times_are_utc(self):
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=fake_mt5(server_offset_h=3))
        self.assertEqual(b._offset, timedelta(hours=3))
        self.assertLess(abs((b.tick("EURUSD").time - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()), 5)

    def test_order_request_carries_stop_magic_and_filling(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        res = b.market_order("EURUSD", 1, 0.10, 1.0950, "fitil-sweep")
        self.assertTrue(res.ok)
        self.assertEqual(res.ticket, 777)
        req = m.sent[0]
        self.assertEqual((req["type"], req["price"], req["sl"], req["volume"]), (0, 1.1001, 1.0950, 0.10))
        self.assertEqual(req["magic"], cfg().magic)
        self.assertEqual(req["type_filling"], m.ORDER_FILLING_IOC)
        self.assertFalse(b.market_order("EURUSD", 1, 0.10, 0.0, "x").ok)       # stopsuz emir yok

    def test_loss_per_lot_and_close_request(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        self.assertAlmostEqual(b.loss_per_lot("EURUSD", 1, 1.1000, 1.0950), 500.0)
        self.assertEqual(b.loss_per_lot("EURUSD", 1, 1.1000, 1.1050), 0.0)
        self.assertEqual(b.loss_per_lot("EURUSD", 1, 1.1000, 0.0), float("inf"))
        b.market_order("EURUSD", 1, 0.10, 1.0950, "t")
        pos = b.positions()[0]
        b.close(pos)
        req = m.sent[-1]
        self.assertEqual((req["type"], req["position"], req["price"]), (1, 777, 1.1000))
        self.assertTrue(b.modify_sl(pos, 1.0990))
        self.assertEqual((m.sent[-1]["action"], m.sent[-1]["sl"]), (m.TRADE_ACTION_SLTP, 1.0990))


    def test_heartbeat_reconnects(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        n = m.inits
        b.heartbeat()
        self.assertEqual(m.inits, n)                 # bağlıyken yeniden başlatmaz
        m.connected = False
        b.heartbeat()
        self.assertEqual(m.inits, n + 1)

    def test_bars_are_closed_utc_bars(self):
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=fake_mt5(server_offset_h=3))
        df = b.bars("EURUSD", 50)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertEqual(len(df), 50)
        self.assertLessEqual(df.index[-1].to_pydatetime(), now)             # sunucu saati UTC'ye çevrildi
        self.assertLess((now - b.last_bar_time("EURUSD")).total_seconds(), 31 * 60)

    def test_net_flows_counts_only_balance_deals_after_since(self):
        import time as _t
        m = fake_mt5(server_offset_h=2)
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        srv = _t.time() + 2 * 3600
        m.deals = [types.SimpleNamespace(type=2, profit=-300.0, time=srv - 600),      # çekim, 10 dk önce
                   types.SimpleNamespace(type=2, profit=1000.0, time=srv - 7200),     # yatırma, 2 saat önce
                   types.SimpleNamespace(type=0, profit=55.0, time=srv - 300)]        # işlem kârı: akış değil
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertEqual(sum(a for _, a in b.flows_since(now - timedelta(hours=1))), -300.0)
        self.assertEqual(sum(a for _, a in b.flows_since(now - timedelta(hours=3))), 700.0)
        m.history_deals_get = lambda lo, hi: None
        m.last_error = lambda: (-10004, "No IPC connection")
        with self.assertRaises(RuntimeError):                # hata "akış yok" sayılmaz
            b.flows_since(now - timedelta(hours=1))

    def test_engine_runs_against_mt5_adapter(self):
        m = fake_mt5()
        logs = []
        e = Engine(MT5Broker(cfg(), sleep=lambda x: None, mt5=m), cfg(settle_sec=0.0), log=logs.append)
        e.step()
        e.step()
        self.assertFalse([x for x in logs if "HATA" in x])


class Hardening(unittest.TestCase):
    def setUp(self):
        self.df, _ = with_sweep_low(m15())
        self.logs = []

    def _settle_flows(self, e, b):
        """Akış ilk görüldüğü turda bekletilir, ikinci turda uygulanır."""
        self.assertFalse(e._apply_flows(b.now(), b.account().equity))
        self.assertTrue(e._apply_flows(b.now(), b.account().equity))

    def test_withdrawal_does_not_trigger_drawdown_brake_or_daily_limit(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.state.update(day=b.now().strftime("%Y-%m-%d"), day_start_equity=10_000.0, peak_equity=10_000.0,
                       flows_until=(b.now() - timedelta(hours=1)).isoformat(), withdraw_base=10_000.0)
        b.deposit(-5_000.0)                                # hesabın yarısı çekildi
        self._settle_flows(e, b)
        self.assertEqual(e.drawdown(b.account().equity), 0.0)
        self.assertEqual(e.risk_factor(b.account().equity), 1.0)
        e.step()
        self.assertEqual(len(b.positions()), 1)            # işlem hâlâ açılabiliyor
        p = b.positions()[0]
        self.assertLessEqual((p.entry - p.sl) * p.lots * 100_000, 5_000 * 0.01 + 1e-6)   # lot YENİ özkaynağa göre
        self.assertTrue(any("AKIŞ" in x for x in self.logs))

    def test_real_loss_still_triggers_brake_after_withdrawal(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.state.update(day=b.now().strftime("%Y-%m-%d"), day_start_equity=12_500.0, peak_equity=12_500.0,
                       flows_until=(b.now() - timedelta(hours=1)).isoformat(), withdraw_base=12_500.0)
        b.balance = 12_000.0                               # önce 12.500 -> 12.000 gerçek kayıp (-%4)
        b.deposit(-2_000.0)                                # sonra 2.000 çekim
        self._settle_flows(e, b)
        self.assertAlmostEqual(e.drawdown(b.account().equity), 0.04, delta=0.001)
        e.step()
        self.assertEqual(b.positions(), [])                # günlük kayıp sınırı (%3) hâlâ geçerli

    def test_signal_during_pending_flow_is_not_traded(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.state.update(flows_until=(b.now() - timedelta(hours=1)).isoformat())
        b.deposit(3_000.0)
        e.step()                                           # akış bu turda ilk kez görüldü
        self.assertEqual(b.positions(), [])

    def test_position_without_stop_is_closed(self):
        b = at_end(self.df)
        b.market_order("EURUSD", 1, 0.10, 1.0, "elle")
        b.positions()[0].sl = 0.0
        e = Engine(b, cfg(), log=self.logs.append)
        e.step()
        self.assertTrue(all(p.sl > 0 for p in b.positions()))
        self.assertEqual(b.closed[0]["reason"], "kapat")
        self.assertTrue(any("stopsuz" in x for x in self.logs))

    def test_trailing_recovers_after_state_loss(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.step()
        p = b.positions()[0]
        e2 = Engine(b, cfg(), log=self.logs.append)        # durum dosyası olmadan yeniden başlatma
        e2._manage(b.now(), p, b.bars("EURUSD", 1500))
        meta = e2.state["tickets"][str(p.ticket)]
        self.assertAlmostEqual(meta["init_dist"], p.entry - p.sl)

    def test_recurring_blackouts(self):
        rules = Config(symbols={"X": {"usd_beta": 0}}).recurring_blackouts
        f = lambda t, g=None: events.blocked(datetime.fromisoformat(t), g, [], rules, 15)
        self.assertEqual(f("2024-03-01T13:35"), "veri saati: NFP")      # kış: 08:30 ET = 13:30 UTC
        self.assertEqual(f("2024-07-05T12:25"), "veri saati: NFP")      # yaz: 08:30 ET = 12:30 UTC
        self.assertIsNone(f("2024-07-05T13:30"))
        self.assertIsNone(f("2024-03-08T13:30"))                        # ikinci cuma
        self.assertEqual(f("2024-03-06T15:30", "oil"), "veri saati: EIA")
        self.assertIsNone(f("2024-03-06T15:30", None))                  # EIA yalnızca petrol grubuna
        e = Engine(at_end(self.df), Config(symbols={"EURUSD": {"usd_beta": -1}, "XTIUSD": {"usd_beta": 0, "group": "oil"}}),
                   log=self.logs.append)
        t = datetime(2024, 3, 6, 15, 30)
        self.assertIsNone(e.entry_block(t, 10_000, 0, "EURUSD"))
        self.assertEqual(e.entry_block(t, 10_000, 0, "XTIUSD"), "veri saati: EIA")

    def test_withdraw_hint_once_per_week_from_profit_above_base(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.state["withdraw_base"] = 9_000.0
        friday = datetime(2024, 3, 15, 20, 0)
        e._withdraw_hint(friday, 10_000.0)
        self.assertEqual(sum("ÇEKİM" in x for x in self.logs), 1)
        self.assertIn("oneri=500.0", [x for x in self.logs if "ÇEKİM" in x][0])
        self.assertEqual(e.state["withdraw_base"], 9_500.0)
        e._withdraw_hint(friday + timedelta(minutes=5), 10_000.0)        # aynı hafta: tekrar yok
        self.assertEqual(sum("ÇEKİM" in x for x in self.logs), 1)
        e._withdraw_hint(friday + timedelta(days=7), 9_400.0)            # zirvenin altında: öneri yok
        self.assertEqual(sum("ÇEKİM" in x for x in self.logs), 1)
        e._withdraw_hint(datetime(2024, 3, 14, 20, 0), 20_000.0)         # perşembe: öneri yok
        self.assertEqual(sum("ÇEKİM" in x for x in self.logs), 1)


class Backtest(unittest.TestCase):
    def test_loaders_mt5_and_histdata_m1(self):
        df = m15(40, end="2024-03-13 09:00")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "EURUSD.csv"
            with p.open("w") as f:
                f.write("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n")
                for t, r in df.iterrows():
                    f.write(f"{t:%Y.%m.%d}\t{t:%H:%M:%S}\t{r.open:.5f}\t{r.high:.5f}\t{r.low:.5f}\t{r.close:.5f}\t1\t0\t1\n")
            got = bt.load_m15(p)
            self.assertEqual(len(got), 40)
            self.assertEqual(got.index[-1], df.index[-1])
            self.assertEqual(bt.load_m15(p, tz_shift_hours=2).index[-1], df.index[-1] - pd.Timedelta(hours=2))
            q = Path(d) / "m1.csv"                          # HistData M1: 30 dakika -> 2 adet M15
            with q.open("w") as f:
                for i in range(30):
                    t = pd.Timestamp("2024-03-13 09:00") + pd.Timedelta(minutes=i)
                    px = 1.10 + i * 0.0001
                    f.write(f"{t:%Y%m%d %H%M%S};{px:.5f};{px + 0.00005:.5f};{px - 0.00005:.5f};{px + 0.00002:.5f};0\n")
            m = bt.load_m15(q)
            self.assertEqual(len(m), 2)
            self.assertAlmostEqual(m.open.iloc[0], 1.10)
            self.assertAlmostEqual(m.close.iloc[0], 1.10 + 14 * 0.0001 + 0.00002)
            self.assertAlmostEqual(m.high.iloc[1], 1.10 + 29 * 0.0001 + 0.00005)

    def test_run_and_report_end_to_end(self):
        rng = np.random.default_rng(4)
        idx = pd.date_range("2024-01-01", periods=4 * 24 * 30, freq="15min")
        idx = idx[idx.weekday < 5]
        c = 1.10 * np.exp(np.cumsum(rng.standard_t(4, len(idx)) * 0.0004))
        o = np.concatenate([[1.10], c[:-1]])
        w = np.abs(rng.normal(0, 0.0003, (2, len(idx))))
        df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + w[0], "low": np.minimum(o, c) - w[1], "close": c}, index=idx)
        trades, equity, engine = bt.run({"EURUSD": df}, cfg(), equity=10_000.0)
        self.assertGreater(len(trades), 5)
        self.assertAlmostEqual(equity.iloc[-1], 10_000.0 + trades.pnl.sum(), places=4)   # muhasebe tutarlı
        self.assertTrue((pd.to_datetime(trades.close_time).dt.normalize() == pd.to_datetime(trades.open_time).dt.normalize()).all())
        self.assertTrue(pd.to_datetime(trades.open_time).dt.hour.between(7, 16).all())
        self.assertGreaterEqual(trades.r.min(), -1.6)       # kayıp, boşluk/kayma payıyla yaklaşık 1R'yi aşmaz
        self.assertLessEqual((trades.risk / 10_000.0).max(), 0.0105)
        text = bt.report(trades, equity)
        for key in ("Kazanma oranı", "Kuruluma göre", "Sembole göre", "Aylık getiri"):
            self.assertIn(key, text)

    def test_symbol_kinds(self):
        self.assertEqual([bt.kind_of(s) for s in ("EURUSD", "USDJPY", "USDCAD", "XAUUSD.m", "USOIL", "XBRUSD", "GOLD")],
                         ["fx", "jpy", "fx_usd", "gold", "oil", "oil", "gold"])


class ReviewFixes(unittest.TestCase):
    """Bağımsız incelemede bulunan hataların geri gelmesini önleyen testler."""

    def setUp(self):
        self.df, self.pdl = with_sweep_low(m15())
        self.logs = []

    def _clocked(self, **kw):
        b = at_end(self.df)
        clock = [b.now()]
        b.now = lambda: clock[0]
        e = Engine(b, cfg(), log=self.logs.append, dry_run=True, **kw)
        e.step()

        def scans(n, engine=None):
            for _ in range(n):
                clock[0] += timedelta(seconds=5)
                (engine or e).step()
        return b, e, clock, scans

    def assert_clean(self, e, b, msg=""):
        eq = b.account().equity
        self.assertAlmostEqual(e.drawdown(eq), 0.0, places=6, msg=msg)
        self.assertEqual(e.risk_factor(eq), 1.0, msg)
        self.assertIsNone(e.entry_block(b.now().replace(hour=9), eq, 0), msg)

    def test_deposit_with_fast_scans_is_not_double_counted(self):
        b, e, clock, scans = self._clocked()
        clock[0] += timedelta(seconds=2)
        b.deposit(10_000.0)                                  # 10.000 yatırıldı, kayıt hemen görünür
        scans(30)
        self.assert_clean(e, b)
        self.assertEqual(sum("AKIŞ" in x for x in self.logs), 1)
        self.assertAlmostEqual(e.state["adj"], 0.5)

    def test_flow_record_visible_before_equity_reflects_it(self):
        b, e, clock, scans = self._clocked()
        b._flows.append((clock[0] + timedelta(seconds=1), 5_000.0))   # kayıt göründü, özkaynak henüz 10.000
        scans(1)
        b.balance += 5_000.0                                           # özkaynak bir sonraki turda güncellendi
        scans(10)
        self.assert_clean(e, b)

    def test_flow_record_visible_long_after_equity(self):
        for lag_scans, amount in ((4, 10_000.0), (40, 3_000.0), (40, -4_000.0)):   # 20 sn ve 200 sn gecikme
            self.logs.clear()
            b, e, clock, scans = self._clocked()
            t = clock[0] + timedelta(seconds=1)
            b.balance += amount                              # özkaynak değişti, kayıt henüz yok
            scans(lag_scans)
            b._flows.append((t, amount))
            scans(5)
            self.assert_clean(e, b, f"gecikme={lag_scans} tutar={amount}")

    def test_restart_while_flow_record_is_lagging(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "s.json"
            b, e, clock, scans = self._clocked(state_path=sp)
            t = clock[0] + timedelta(seconds=1)
            b.balance += 3_000.0
            scans(3)
            e2 = Engine(b, cfg(), log=self.logs.append, dry_run=True, state_path=sp)   # yeniden başlatma
            scans(3, e2)
            b._flows.append((t, 3_000.0))
            scans(5, e2)
            self.assert_clean(e2, b)

    def test_lagging_flow_across_utc_midnight_does_not_block_the_day(self):
        b, e, clock, scans = self._clocked()
        clock[0] = clock[0].replace(hour=23, minute=59, second=50)
        scans(1)                                             # 23:59:55: gün başı zaten kayıtlı
        t = clock[0] + timedelta(seconds=1)
        b.balance += 600.0                                   # 23:59:56 para yatırıldı
        scans(3)                                             # gece yarısı geçildi, gün başı hayali özkaynakla kaydedildi
        b._flows.append((t, 600.0))
        scans(4)
        self.assertAlmostEqual(e.state["day_start_equity"], e._adj(b.account().equity), delta=0.01)
        self.assert_clean(e, b)

    def test_real_gain_and_loss_are_not_mistaken_for_flows(self):
        b, e, clock, scans = self._clocked()
        b.balance += 400.0                                   # gerçek kâr: akış kaydı yok
        scans(3)
        self.assertAlmostEqual(e.state["peak_equity"], 10_400.0, delta=0.01)
        clock[0] += timedelta(minutes=10)
        b.balance -= 1_300.0                                 # gerçek kayıp: zirveden %12,5
        scans(3)
        self.assertAlmostEqual(e.drawdown(b.account().equity), 0.125, delta=0.001)
        self.assertEqual(e.risk_factor(b.account().equity), 0.5)
        clock[0] += timedelta(minutes=10)
        b.deposit(5_000.0)                                   # para yatırmak düşüşü silmez
        scans(4)
        self.assertAlmostEqual(e.drawdown(b.account().equity), 0.125, delta=0.001)
        self.assertEqual(sum("AKIŞ" in x for x in self.logs), 1)
        clock[0] += timedelta(minutes=10)
        b.deposit(-6_000.0)                                  # para çekmek de düşüşü değiştirmez
        scans(4)
        self.assertAlmostEqual(e.drawdown(b.account().equity), 0.125, delta=0.001)

    def test_random_flows_and_pnl_match_ground_truth_drawdown(self):
        # Rastgele kâr/zarar ve para akışları; akış kaydı özkaynaktan önce, aynı anda ya da sonra görünebilir.
        # Motorun düşüşü, yalnızca işlem sonuçlarından hesaplanan gerçek düşüşle aynı olmalı.
        for seed in range(25):
            rng = np.random.default_rng(seed)
            self.logs.clear()
            b, e, clock, scans = self._clocked()
            perf, peak = 1.0, 1.0
            for _ in range(12):
                clock[0] += timedelta(minutes=30)
                if rng.random() < 0.5:                       # işlem sonucu
                    r = float(rng.uniform(-0.04, 0.05))
                    b.balance *= 1.0 + r
                    perf *= 1.0 + r
                    scans(3)
                    peak = max(peak, perf)
                else:                                        # para akışı
                    amount = float(rng.uniform(-0.5, 1.5)) * b.balance
                    lag = int(rng.integers(-1, 30))          # -1: kayıt önce görünür; >0: geç görünür
                    t = clock[0] + timedelta(seconds=1)
                    if lag < 0:
                        b._flows.append((t, amount)); scans(1); b.balance += amount
                    else:
                        b.balance += amount; scans(lag); b._flows.append((t, amount))
                    scans(4)
                truth = 1.0 - perf / peak
                self.assertAlmostEqual(e.drawdown(b.account().equity), truth, places=6, msg=f"tohum={seed}")
            self.assertAlmostEqual(e._adj(b.account().equity) / 10_000.0, perf, places=6, msg=f"tohum={seed}")

    def test_flow_history_failure_does_not_stop_day_end_close(self):
        b = at_end(self.df)
        b.market_order("EURUSD", 1, 0.1, 1.0, "t")
        clock = [b.now().replace(hour=20, minute=0)]
        b.now = lambda: clock[0]

        def boom(since):
            raise RuntimeError("geçmiş okunamadı")
        b.flows_since = boom
        e = Engine(b, cfg(), log=self.logs.append)
        e.step()
        self.assertEqual(b.positions(), [])                  # pozisyon yine de kapatıldı
        e.step()
        self.assertEqual(sum("para akışı geçmişi okunamadı" in x for x in self.logs), 1)   # uyarı tekrarlanmaz

    def test_failed_order_result_but_open_position_is_still_tracked(self):
        b = at_end(self.df)
        real = b.market_order

        def lying(*a, **k):
            r = real(*a, **k)
            return type(r)(False, 0, 0.0, "10008 placed")   # emir "başarısız" döndü ama pozisyon açıldı
        b.market_order = lying
        e = Engine(b, cfg(), log=self.logs.append)
        e.step()
        self.assertEqual(len(b.positions()), 1)
        self.assertEqual(e.state["trades_today"], 1)
        self.assertIn(str(b.positions()[0].ticket), e.state["tickets"])

    def test_position_read_error_is_not_treated_as_flat(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        m.pos_error = True
        with self.assertRaises(RuntimeError):
            b.positions()
        e = Engine(b, cfg(), log=self.logs.append)
        e.state["tickets"]["777"] = {"entry": 1.1, "init_dist": 0.001}
        with self.assertRaises(RuntimeError):
            e.step()
        self.assertIn("777", e.state["tickets"])            # durum silinmedi

    def test_partial_and_placed_retcodes_count_as_success(self):
        for code in (10010, 10008):
            m = fake_mt5()
            m.retcode = code
            self.assertTrue(MT5Broker(cfg(), sleep=lambda x: None, mt5=m).market_order("EURUSD", 1, 0.1, 1.09, "t").ok)
        m = fake_mt5()
        m.retcode = 10006                                   # reddedildi
        self.assertFalse(MT5Broker(cfg(), sleep=lambda x: None, mt5=m)._send({"action": 6}).ok)

    def test_monday_uses_last_full_day_not_sunday_evening(self):
        idx = pd.date_range("2024-03-07 00:00", "2024-03-11 09:00", freq="15min")   # Per..Pzt
        idx = idx[(idx.weekday < 5) | ((idx.weekday == 6) & (idx.hour >= 22))]      # pazar yalnızca 22:00+
        df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
        fri = df.index.normalize() == pd.Timestamp("2024-03-08")
        sun = df.index.normalize() == pd.Timestamp("2024-03-10")
        df.loc[fri, "high"], df.loc[fri, "low"] = 1.1050, 1.0950
        df.loc[sun, "high"], df.loc[sun, "low"] = 1.1010, 1.0990
        lv = liquidity_levels(df, SP())
        self.assertEqual((lv["pdh"], lv["pdl"]), (1.1050, 1.0950))

    def test_news_release_bar_itself_is_blocked(self):
        rules = Config(symbols={"X": {"usd_beta": 0}}).recurring_blackouts
        now = datetime(2024, 7, 5, 12, 45, 3)               # NFP 12:30 UTC; çubuk 12:30-12:45
        self.assertIsNone(events.blocked(now, None, [], rules, 15))                 # eski davranış: kaçırırdı
        self.assertEqual(events.blocked(now, None, [], rules, 15, since=datetime(2024, 7, 5, 12, 30)), "veri saati: NFP")
        self.assertIsNone(events.blocked(datetime(2024, 7, 5, 13, 15, 3), None, [], rules, 15, since=datetime(2024, 7, 5, 13, 0)))

    def test_position_carried_overnight_is_closed_next_morning(self):
        b = at_end(self.df)
        e = Engine(b, cfg(), log=self.logs.append)
        e.step()
        p = b.positions()[0]
        p.open_time = p.open_time - timedelta(days=1)        # dün açılmış gibi
        e.step()
        self.assertEqual([x for x in b.positions() if x.ticket == p.ticket], [])
        self.assertTrue(any("gece taşınmış" in x for x in self.logs))

    def test_rejected_close_is_retried_once_a_minute_not_every_scan(self):
        b = at_end(self.df)
        clock = [b.now().replace(hour=20, minute=0)]
        b.now = lambda: clock[0]
        b.market_order("EURUSD", 1, 0.1, 1.0, "t")
        calls = []
        b.close = lambda p: calls.append(1) or type(b.market_order("EURUSD", 1, 0.0, 9.9, "x"))(False, message="red")
        e = Engine(b, cfg(), log=self.logs.append)
        for _ in range(13):                                  # 65 saniye
            e.step()
            clock[0] += timedelta(seconds=5)
        self.assertEqual(len(calls), 2)

    def test_block_reason_is_journaled_once(self):
        df = self.df
        idx = pd.date_range(df.index[-1] + pd.Timedelta(minutes=15), periods=6, freq="15min")
        tail = pd.DataFrame({"open": df.close.iloc[-1], "high": df.close.iloc[-1] + 0.0001,
                             "low": df.close.iloc[-1] - 0.0001, "close": df.close.iloc[-1]}, index=idx)
        b = paper(pd.concat([df, tail]), start=idx[0])
        e = Engine(b, cfg(), log=self.logs.append)
        e.state.update(day=b.now().strftime("%Y-%m-%d"), day_start_equity=10_000.0, peak_equity=13_000.0,
                       flows_until=b.now().isoformat(), withdraw_base=13_000.0)
        for _ in range(5):                                   # beş ayrı çubuk değerlendirilir
            e.step()
            b.advance()
        self.assertEqual(len(e.seen), 1)
        self.assertGreater(b.clock, idx[3])
        hits = [x for x in self.logs if "ENGEL" in x]
        self.assertEqual(len(hits), 1)                       # aynı neden yalnızca bir kez yazılır
        self.assertIn("düşüş freni", hits[0])

    def test_state_file_is_atomic_and_corruption_is_survivable(self):
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "s.json"
            sp.write_text("{bozuk", encoding="utf-8")
            e = Engine(at_end(self.df), cfg(), state_path=sp, log=self.logs.append)
            self.assertTrue((Path(d) / "s.bad").exists())
            self.assertTrue(any("bozuk" in x for x in self.logs))
            e.step()
            self.assertEqual(json.loads(sp.read_text(encoding="utf-8"))["trades_today"], 1)
            self.assertFalse((Path(d) / "s.tmp").exists())

    def test_timezone_aware_blackout_is_normalised(self):
        e = Engine(at_end(self.df), cfg(blackouts=["2024-03-13T08:30:00-04:00"]), log=self.logs.append)
        self.assertEqual(e.entry_block(datetime(2024, 3, 13, 12, 35), 10_000, 0), "veri saati")

    def test_server_time_zone_conversion_handles_dst(self):
        c = cfg(server_time={"tz": "America/New_York", "plus_hours": 7})
        b = MT5Broker(c, sleep=lambda x: None, mt5=fake_mt5())
        ep = lambda s: (pd.Timestamp(s) - pd.Timestamp("1970-01-01")).total_seconds()
        self.assertEqual(b._utc(ep("2024-01-15 10:00")), datetime(2024, 1, 15, 8, 0))    # kış: sunucu = UTC+2
        self.assertEqual(b._utc(ep("2024-07-15 10:00")), datetime(2024, 7, 15, 7, 0))    # yaz: sunucu = UTC+3
        idx = b._utc_index(pd.Series([ep("2024-03-08 10:00"), ep("2024-03-11 10:00")]))  # ABD yaz saati geçişi
        self.assertEqual(list(idx), [pd.Timestamp("2024-03-08 08:00"), pd.Timestamp("2024-03-11 07:00")])

    def test_stale_ticks_do_not_produce_an_offset(self):
        m = fake_mt5()
        m.tick_age = 1700.0                                  # piyasa kapalı: son tick 28 dk önce, akmıyor
        with self.assertRaises(RuntimeError):
            MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        MT5Broker(cfg(server_utc_offset_hours=2), sleep=lambda x: None, mt5=m)      # elle ayarla başlar

    def test_offset_not_refreshed_from_old_tick_after_suspend(self):
        m = fake_mt5(server_offset_h=3)
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        self.assertEqual(b._offset, timedelta(hours=3))
        b._offset_at -= 7200                                 # saatlik yenileme zamanı gelmiş
        b._hb_at -= 7200                                     # ama önceki tur 2 saat önceydi (uyku/kesinti)
        m.tick_age = 7190.0                                  # elde yalnızca 2 saatlik eski tick var
        b._newest_tick -= 9000
        b.heartbeat()
        self.assertEqual(b._offset, timedelta(hours=3))      # eski tick'ten yeni fark türetilmedi

    def test_switching_to_real_account_stops_the_bot(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), sleep=lambda x: None, mt5=m)
        b.heartbeat()
        m.demo = False
        with self.assertRaises(FatalError):
            b.heartbeat()
        m.demo, m.login = True, 222
        with self.assertRaises(FatalError):
            b.heartbeat()

    def test_paper_close_applies_slippage(self):
        df = self.df
        extra = df.iloc[[-1]].copy(); extra.index = extra.index + pd.Timedelta(minutes=15)
        full = pd.concat([df, extra])
        b = paper(full, start=full.index[-1], slippage={"EURUSD": 0.0001})
        b.market_order("EURUSD", 1, 0.1, 1.0, "t")
        p = b.positions()[0]
        self.assertAlmostEqual(b.close(p).price, b.tick("EURUSD").bid - 0.0001)


class Timeframes(unittest.TestCase):
    def h1(self, n=1400, seed=9):
        rng = np.random.default_rng(seed)
        idx = pd.date_range("2024-01-01", periods=n, freq="1h")
        idx = idx[idx.weekday < 5]
        c = 1.10 * np.exp(np.cumsum(rng.standard_t(4, len(idx)) * 0.0008))
        o = np.concatenate([[1.10], c[:-1]])
        w = np.abs(rng.normal(0, 0.0006, (2, len(idx))))
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + w[0], "low": np.minimum(o, c) - w[1], "close": c}, index=idx)

    def conf(self, **sp):
        c = Config(symbols={"EURUSD": {"usd_beta": -1}}, strategy=StrategyParams(bar_minutes=60, min_bars=300, **sp))
        c.validate()
        return c

    def test_hourly_bars_run_end_to_end_and_respect_day_rules(self):
        trades, equity, _ = bt.run({"EURUSD": self.h1()}, self.conf(), 10_000.0)
        self.assertGreater(len(trades), 3)
        self.assertAlmostEqual(equity.iloc[-1], 10_000.0 + trades.pnl.sum(), places=4)
        opened, closed = pd.to_datetime(trades.open_time), pd.to_datetime(trades.close_time)
        self.assertTrue((opened.dt.normalize() == closed.dt.normalize()).all())
        self.assertTrue(opened.dt.hour.between(7, 16).all())
        self.assertTrue((opened.dt.minute == 0).all())            # saatlik çubuk kapanışlarında giriş

    def test_previous_day_and_asia_levels_scale_with_timeframe(self):
        df = self.h1(200)
        lv = liquidity_levels(df.loc[:"2024-01-04 09:00"], StrategyParams(bar_minutes=60))
        day = df.loc["2024-01-03"]
        asia = df.loc["2024-01-04 00:00":"2024-01-04 06:59"]
        self.assertEqual((lv["pdh"], lv["pdl"]), (day.high.max(), day.low.min()))
        self.assertEqual((lv["asia_h"], lv["asia_l"]), (asia.high.max(), asia.low.min()))

    def test_sweep_setup_is_off_by_default(self):
        df, _ = with_sweep_low(m15())
        self.assertEqual(StrategyParams().setups, ("pullback",))
        default = evaluate(df, StrategyParams(), SPREAD)
        self.assertTrue(default is None or default.setup == "pullback")
        self.assertEqual(evaluate(df, SP(), SPREAD).setup, "sweep")

    def test_setups_can_be_switched_off(self):
        df, _ = with_sweep_low(m15())
        self.assertEqual(evaluate(df, SP(), SPREAD).setup, "sweep")
        only_pb = evaluate(df, StrategyParams(setups=("pullback",)), SPREAD)
        self.assertTrue(only_pb is None or only_pb.setup == "pullback")
        with self.assertRaises(ValueError):
            cfg(strategy=StrategyParams(setups=("martingale",)))
        with self.assertRaises(ValueError):
            cfg(strategy=StrategyParams(bar_minutes=7))

    def test_mt5_timeframe_constant_follows_config(self):
        m = fake_mt5()
        m.TIMEFRAME_H1, m.TIMEFRAME_M5, seen = 16385, 5, []
        real = m.copy_rates_from_pos
        m.copy_rates_from_pos = lambda sym, tf, pos, count: seen.append(tf) or real(sym, tf, pos, count)
        MT5Broker(self.conf(), sleep=lambda x: None, mt5=m).bars("EURUSD", 10)
        MT5Broker(cfg(), sleep=lambda x: None, mt5=m).bars("EURUSD", 10)
        self.assertEqual(seen, [16385, 15])

    def test_loader_reads_pandas_style_csv_and_resamples_to_target(self):
        df = m15(96, end="2024-03-13 23:45")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            df.rename(columns=str.capitalize).to_csv(p)
            self.assertEqual(len(bt.load_m15(p)), 96)
            h = bt.load_m15(p, minutes=60)
            self.assertEqual(len(h), 24)
            self.assertAlmostEqual(h.high.iloc[0], df.high.iloc[:4].max())
            self.assertAlmostEqual(h.close.iloc[0], df.close.iloc[3])
            hp = Path(d) / "h.csv"
            h.to_csv(hp)
            with self.assertRaises(ValueError):                    # saatlik veri M15 olarak kullanılamaz
                bt.load_m15(hp, minutes=15)

    def test_loader_reads_dukascopy_day_first_dates(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "duka.csv"
            p.write_text("Gmt time,Open,High,Low,Close,Volume\n"
                         "01.02.2017 22:00:00.000,1.05236,1.05253,1.05221,1.05227,143.5\n"
                         "01.02.2017 23:00:00.000,1.05227,1.05426,1.05226,1.05282,253.1\n"
                         "02.02.2017 00:00:00.000,1.05282,1.05300,1.05200,1.05250,100.0\n")
            df = bt.load_m15(p, minutes=60)
            self.assertEqual(df.index[0], pd.Timestamp("2017-02-01 22:00"))      # 1 Şubat, 2 Ocak değil
            self.assertEqual(df.index[-1], pd.Timestamp("2017-02-02 00:00"))
            self.assertAlmostEqual(df.close.iloc[1], 1.05282)

    def test_loader_dukascopy_local_time_offsets_and_closed_market_fill(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "duka_local.csv"
            p.write_text("Local time,Open,High,Low,Close,Volume\n"
                         "08.03.2019 16:58:00.000 GMT-0500,1.1235,1.1236,1.1234,1.1235,10\n"      # kış: UTC 21:58
                         "08.03.2019 16:59:00.000 GMT-0500,1.1235,1.1237,1.1235,1.1236,12\n"
                         "08.03.2019 17:00:00.000 GMT-0500,1.1236,1.1236,1.1236,1.1236,0\n"       # kapalı piyasa dolgusu
                         "09.03.2019 12:00:00.000 GMT-0500,1.1236,1.1236,1.1236,1.1236,0\n"
                         "11.03.2019 03:00:00.000 GMT-0400,1.1240,1.1242,1.1239,1.1241,20\n"      # yaz: UTC 07:00
                         "11.03.2019 03:01:00.000 GMT-0400,1.1241,1.1243,1.1240,1.1242,20\n"
                         "11.03.2019 03:02:00.000 GMT-0400,1.1242,1.1244,1.1241,1.1243,20\n")
            df = bt.load_m15(p, minutes=1)
            self.assertEqual(list(df.index), [pd.Timestamp("2019-03-08 21:58"), pd.Timestamp("2019-03-08 21:59"),
                                              pd.Timestamp("2019-03-11 07:00"), pd.Timestamp("2019-03-11 07:01"),
                                              pd.Timestamp("2019-03-11 07:02")])
            m = bt.load_m15(p, minutes=15)
            self.assertEqual(list(m.index), [pd.Timestamp("2019-03-08 21:45"), pd.Timestamp("2019-03-11 07:00")])
            self.assertAlmostEqual(m.high.iloc[1], 1.1244)

    def test_walk_forward_tests_only_on_unseen_period(self):
        folds, oos = bt.walk_forward({"EURUSD": self.h1(2600, seed=3)}, self.conf(),
                                     {"max_stop_atr": [2.0, 3.0]}, train_days=40, test_days=20, min_trades=3)
        self.assertGreaterEqual(len(folds), 2)
        for f in folds.itertuples():
            tr_end = pd.Timestamp(f.egitim.split("..")[1])
            te_start, te_end = (pd.Timestamp(x) for x in f.test.split(".."))
            self.assertEqual(te_start, tr_end)
            self.assertGreater(te_end, te_start)
        if len(oos):
            first_test = pd.Timestamp(folds.test.iloc[0].split("..")[0])
            self.assertGreaterEqual(pd.to_datetime(oos.open_time).min(), first_test)

    def test_single_time_conversion_path_in_zone_mode(self):
        b = MT5Broker(cfg(server_time={"tz": "America/New_York", "plus_hours": 7}), sleep=lambda x: None, mt5=fake_mt5())
        ep = lambda t: (pd.Timestamp(t) - pd.Timestamp("1970-01-01")).total_seconds()
        for t in ("2024-03-10 09:30", "2024-03-08 12:00", "2024-11-03 08:30", "2024-07-01 00:15"):   # yaz saati geçişleri dahil
            self.assertEqual(pd.Timestamp(b._utc(ep(t))), b._utc_index([ep(t)])[0], t)


class LivePathAgainstSimulatedTerminal(unittest.TestCase):
    """Canlı kod yolu (MT5Broker + Engine), MT5 arayüzünü taklit eden simülatöre karşı baştan sona çalıştırılır
    ve aynı verideki doğrudan simülasyonla karşılaştırılır. Sunucu saati UTC'den farklıdır ve dönem bir
    yaz saati geçişini içerir; zaman dönüşümünde bir saatlik hata işlemleri değiştirir."""

    def data(self, seed=21, start="2024-02-26", days=26, p0=1.10, vol=0.0004):
        rng = np.random.default_rng(seed)
        idx = pd.date_range(start, periods=96 * days, freq="15min")
        idx = idx[(idx.weekday < 5) | ((idx.weekday == 6) & (idx.hour >= 22))]
        c = p0 * np.exp(np.cumsum(rng.standard_t(4, len(idx)) * vol))
        o = np.concatenate([[p0], c[:-1]])
        w = np.abs(rng.normal(0, vol * 0.7, (2, len(idx)))) * p0
        return pd.DataFrame({"open": o, "high": np.maximum(o, c) + w[0], "low": np.minimum(o, c) - w[1], "close": c}, index=idx)

    def brokers(self):
        from mt5_sim import SimMT5
        data = {"EURUSD": self.data(21), "USDJPY": self.data(22, p0=150.0)}
        specs = {"EURUSD": SPEC, "USDJPY": Spec("USDJPY", 3, 0.001, 0.01, 0.01, 100.0)}

        def make():
            return PaperBroker(data, specs, {"EURUSD": 1e5, "USDJPY": 1e5}, {"EURUSD": "quote", "USDJPY": "base"},
                               {"EURUSD": 0.00008, "USDJPY": 0.010}, equity=10_000.0, commission_per_lot=7.0,
                               start=data["EURUSD"].index[700])
        return make, SimMT5

    def conf(self, **kw):
        c = Config(symbols={"EURUSD": {"usd_beta": -1}, "USDJPY": {"usd_beta": 1}}, settle_sec=0.0,
                   strategy=SP(), **kw)
        c.validate()
        return c

    def drive(self, engine, paper, hook=None):
        n = 0
        while True:
            engine.step()
            n += 1
            if hook:
                hook(n)
            if not paper.advance():
                break
        return pd.DataFrame(paper.closed)

    def test_live_path_reproduces_direct_simulation_trade_for_trade(self):
        make, SimMT5 = self.brokers()
        direct = make()
        ref = self.drive(Engine(direct, self.conf(), log=lambda *_: None), direct)

        paper = make()
        sim = SimMT5(paper, expected_timeframe=15)
        cfg_live = self.conf(server_time={"tz": "America/New_York", "plus_hours": 7})
        logs = []
        live = MT5Broker(cfg_live, mt5=sim, sleep=lambda x: None, clock=paper.now)
        got = self.drive(Engine(live, cfg_live, log=logs.append), paper)

        self.assertGreater(len(ref), 15)
        self.assertEqual(len(got), len(ref))
        for col in ("symbol", "direction", "reason"):
            self.assertEqual(list(got[col]), list(ref[col]), col)
        for col in ("lots", "entry", "exit", "pnl"):
            np.testing.assert_allclose(got[col].to_numpy(dtype=float), ref[col].to_numpy(dtype=float), rtol=0, atol=1e-9, err_msg=col)
        self.assertEqual(list(pd.to_datetime(got.open_time)), list(pd.to_datetime(ref.open_time)))
        self.assertEqual(set(ref.symbol), {"EURUSD", "USDJPY"})
        self.assertTrue({"stop", "kapat"} <= set(ref.reason))
        self.assertFalse([x for x in logs if "HATA" in x or "RED" in x])
        self.assertAlmostEqual(paper.account().equity, direct.account().equity, places=6)
        entries = [r for r in sim.requests if r["action"] == sim.TRADE_ACTION_DEAL and "position" not in r]
        self.assertTrue(all(r["sl"] > 0 and r["magic"] == cfg_live.magic for r in entries))     # her emir stoplu
        self.assertTrue(any(r["action"] == sim.TRADE_ACTION_SLTP for r in sim.requests))        # stop taşıma da çalıştı

    def test_wrong_server_time_setting_is_detectable(self):
        # Sunucu saati yanlış tanımlanırsa (NY+7 yerine sabit UTC+0) sonuç doğrudan simülasyondan sapar:
        # yani üstteki eşleşme, zaman dönüşümü doğru olduğu için tutuyor.
        make, SimMT5 = self.brokers()
        direct = make()
        ref = self.drive(Engine(direct, self.conf(), log=lambda *_: None), direct)
        paper = make()
        cfg_bad = self.conf(server_utc_offset_hours=0)
        live = MT5Broker(cfg_bad, mt5=SimMT5(paper), sleep=lambda x: None, clock=paper.now)
        got = self.drive(Engine(live, cfg_bad, log=lambda *_: None), paper)
        same = len(got) == len(ref) and list(pd.to_datetime(got.open_time)) == list(pd.to_datetime(ref.open_time))
        self.assertFalse(same)

    def test_live_path_ignores_other_magic_and_survives_read_errors(self):
        make, SimMT5 = self.brokers()
        paper = make()
        sim = SimMT5(paper)
        cfg_live = self.conf(server_time={"tz": "America/New_York", "plus_hours": 7})
        live = MT5Broker(cfg_live, mt5=sim, sleep=lambda x: None, clock=paper.now)
        manual = paper.market_order("EURUSD", 1, 0.05, 0.5, "elle")          # başka kaynaktan (magic 0) pozisyon
        engine = Engine(live, cfg_live, log=lambda *_: None)
        errors = []

        def hook(n):
            sim.fail_positions = (n % 50 == 49)                              # ara sıra okuma hatası

        def step():
            try:
                engine_step()
            except RuntimeError as e:
                errors.append(str(e))
        engine_step, engine.step = engine.step, step
        self.drive(engine, paper, hook)
        self.assertGreater(len(errors), 3)                                   # hatalar yükseltildi, yutulmadı
        self.assertIn(manual.ticket, [p.ticket for p in paper.positions()])  # botun olmayan pozisyona dokunulmadı
        mine = [t for t in paper.closed if t["ticket"] != manual.ticket]
        self.assertGreater(len(mine), 10)
        by_symbol_time = {}
        for t in sorted(mine, key=lambda x: x["open_time"]):                 # aynı sembolde üst üste binen pozisyon yok
            last = by_symbol_time.get(t["symbol"])
            if last is not None:
                self.assertGreaterEqual(pd.Timestamp(t["open_time"]), pd.Timestamp(last))
            by_symbol_time[t["symbol"]] = t["close_time"]


class CommandLine(unittest.TestCase):
    """`python -m fitil` giriş noktası: yapılandırma, kilit, demo kontrolü ve döngü kurulumu."""

    def run_main(self, argv, sim, steps=3):
        import contextlib
        import io
        from unittest import mock
        import fitil.__main__ as cli
        ran = []

        def fake_run(engine):
            for _ in range(steps):
                engine.step()
            ran.append(engine)
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"MetaTrader5": sim}), mock.patch.object(Engine, "run", fake_run), \
                mock.patch.object(sys, "argv", ["fitil"] + argv), contextlib.redirect_stdout(out):
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return out.getvalue(), code, ran

    def setup_files(self, d, **extra):
        from mt5_sim import SimMT5
        df = m15(900)
        paper = PaperBroker({"EURUSD": df}, {"EURUSD": SPEC}, {"EURUSD": 1e5}, {"EURUSD": "quote"}, {"EURUSD": SPREAD},
                            equity=5_000.0, start=df.index[-1])
        conf = {"symbols": {"EURUSD": {"usd_beta": -1}}, "server_time": {"tz": "America/New_York", "plus_hours": 7}}
        conf.update(extra)
        path = Path(d) / "config.json"
        path.write_text(json.dumps(conf), encoding="utf-8")
        return SimMT5(paper), path

    def test_starts_in_signal_mode_on_demo_and_writes_state(self):
        with tempfile.TemporaryDirectory() as d:
            sim, path = self.setup_files(d)
            out, code, ran = self.run_main(["--config", str(path), "--workdir", d], sim)
            self.assertEqual(code, 0, out)
            self.assertIn("Hesap: DEMO", out)
            self.assertIn("dry-run", out)
            self.assertEqual(len(ran), 1)
            self.assertTrue(ran[0].dry_run)
            self.assertTrue((Path(d) / "fitil_state.json").exists())
            self.assertFalse([r for r in sim.requests])                      # sinyal modunda emir yok

    def test_trade_flag_and_reset_peak(self):
        with tempfile.TemporaryDirectory() as d:
            sim, path = self.setup_files(d)
            (Path(d) / "fitil_state.json").write_text(json.dumps({"peak_equity": 9_999.0, "adj": 1.0}), encoding="utf-8")
            out, code, ran = self.run_main(["--config", str(path), "--workdir", d, "--trade", "--reset-peak"], sim)
            self.assertEqual(code, 0, out)
            self.assertFalse(ran[0].dry_run)
            self.assertIn("EMİR GÖNDERİR", out)
            self.assertAlmostEqual(ran[0].state["peak_equity"], 5_000.0)

    def test_refuses_real_account_and_bad_config_with_clear_message(self):
        with tempfile.TemporaryDirectory() as d:
            sim, path = self.setup_files(d)
            sim.demo = False
            out, code, ran = self.run_main(["--config", str(path), "--workdir", d], sim)
            self.assertIn("DEMO hesap değil", str(code))                     # ham hata dökümü değil, açık mesaj
            self.assertEqual(ran, [])
            out, code, ran = self.run_main(["--config", str(path), "--workdir", d, "--allow-real"], sim)
            self.assertIn("Hesap: GERÇEK", out)
            sim2, bad = self.setup_files(d, risk_tiers={"1": 0.2})
            out, code, ran = self.run_main(["--config", str(bad), "--workdir", d], sim2)
            self.assertIn("Yapılandırma hatası", str(code))
            out, code, ran = self.run_main(["--config", str(Path(d) / "yok.json"), "--workdir", d], sim2)
            self.assertIn("bulunamadı", str(code))
            sim3, p3 = self.setup_files(d, symbols={"XAUUSD.m": {"usd_beta": -1}})
            out, code, ran = self.run_main(["--config", str(p3), "--workdir", d], sim3)
            self.assertIn("Sembol bulunamadı: XAUUSD.m", str(code))

    def test_turkish_output_survives_legacy_windows_code_page(self):
        import io
        import fitil.__main__ as cli
        from unittest import mock
        raw = io.BytesIO()
        legacy = io.TextIOWrapper(raw, encoding="cp437", errors="strict")       # Türkçe harfleri olmayan kod sayfası
        with self.assertRaises(UnicodeEncodeError):
            legacy.write("GİRİŞ özkaynak ş ğ"); legacy.flush()
        raw2 = io.BytesIO()
        out = io.TextIOWrapper(raw2, encoding="cp437", errors="strict")
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
            cli.use_utf8_output()
            print("GİRİŞ özkaynak ş ğ ÇEKİM")
            out.flush()
        self.assertEqual(raw2.getvalue().decode("utf-8").strip(), "GİRİŞ özkaynak ş ğ ÇEKİM")

    def test_journal_is_excel_readable_and_survives_unprintable_console(self):
        with tempfile.TemporaryDirectory() as d:
            df, _ = with_sweep_low(m15())

            def bad_console(msg):
                msg.encode("ascii")                                             # Türkçe karakterde UnicodeEncodeError
            e = Engine(at_end(df), cfg(), journal_path=Path(d) / "j.csv", log=bad_console)
            e.step()                                                            # çökmemeli
            e._journal(datetime(2024, 3, 13, 9, 0), "ÇIKIŞ", "EURUSD", neden="gün sonu")
            raw = (Path(d) / "j.csv").read_bytes()
            self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))                    # BOM: Excel UTF-8 olarak açar
            self.assertEqual(raw.count(b"\xef\xbb\xbf"), 1)                     # yalnızca dosya başında
            text = raw.decode("utf-8-sig")
            self.assertIn("GİRİŞ", text)
            self.assertIn("gün sonu", text)

    def test_second_instance_in_same_folder_is_refused(self):
        import fitil.__main__ as cli
        with tempfile.TemporaryDirectory() as d:
            first = cli.acquire_lock(Path(d) / "fitil.lock")
            try:
                with self.assertRaises(SystemExit):
                    cli.acquire_lock(Path(d) / "fitil.lock")
            finally:
                first.close()
            cli.acquire_lock(Path(d) / "fitil.lock").close()               # kilit bırakılınca yeniden alınabilir


class Plan(unittest.TestCase):
    def test_expectation_drives_outcome_and_brake_limits_damage(self):
        from fitil.plan import required_equity, simulate
        kw = dict(trades_per_day=6, paths=1500, weeks=26)
        good = simulate(1000.0, win_rate=0.45, avg_win=1.8, avg_loss=1.0, **kw)
        bad = simulate(1000.0, win_rate=0.35, avg_win=1.5, avg_loss=1.0, **kw)
        self.assertAlmostEqual(good["beklenti_R"], 0.26)
        self.assertGreater(good["yil_sonu_toplam_medyan"], 1000.0)
        self.assertLess(bad["yil_sonu_toplam_medyan"], 1000.0)
        self.assertGreater(bad["durma_olasiligi"], 0.9)
        self.assertGreater(bad["yil_sonu_toplam_p5"], 1000.0 * 0.75)     # fren kaybı yaklaşık %20'de keser
        self.assertLess(bad["haftalik_cekim_medyan"], 1.0)
        # oransallık: 10 kat sermaye, 10 kat çekim
        big = simulate(10_000.0, win_rate=0.45, avg_win=1.8, avg_loss=1.0, **kw)
        self.assertAlmostEqual(big["haftalik_cekim_medyan"] / good["haftalik_cekim_medyan"], 10.0, places=6)
        self.assertEqual(required_equity(100.0, win_rate=0.35, avg_win=1.5, avg_loss=1.0, **kw) > 1e6, True)


if __name__ == "__main__":
    unittest.main()
