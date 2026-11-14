"""Çalıştırma:  cd trading && python -m unittest discover -s tests -v"""
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bot"))

from fitil.broker import PaperBroker, Pos, Spec, round_lots_down   # noqa: E402
from fitil.config import Config, StrategyParams                    # noqa: E402
from fitil.engine import Engine                                    # noqa: E402
from fitil.mt5_broker import MT5Broker                             # noqa: E402
from fitil.strategy import evaluate, htf_bias, liquidity_levels    # noqa: E402

SPREAD = 0.00002
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
    p = StrategyParams()
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
        sig = evaluate(df, StrategyParams(), SPREAD)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.direction, sig.setup), (1, "sweep"))
        self.assertLess(sig.stop, df.low.iloc[-1])
        self.assertIn(sig.score, (1, 2, 3))

    def test_no_sweep_when_bar_closes_below_level(self):
        df, pdl = with_sweep_low(m15())
        df.iloc[-1] = [pdl + 0.0001, pdl + 0.00015, pdl - 0.0004, pdl - 0.0003]   # gerçek kırılım
        sig = evaluate(df, StrategyParams(), SPREAD)
        self.assertTrue(sig is None or sig.setup != "sweep" or sig.direction != 1)

    def test_wide_spread_rejects_trade(self):
        df, _ = with_sweep_low(m15())
        self.assertIsNone(evaluate(df, StrategyParams(), spread=0.0005))    # stop < 10 x spread

    def test_mirror_gives_short_on_high_sweep(self):
        df, _ = with_sweep_low(m15())
        mirrored = df.copy()
        for col, src in (("open", "open"), ("close", "close"), ("high", "low"), ("low", "high")):
            mirrored[col] = 2.2 - df[src]
        sig = evaluate(mirrored, StrategyParams(), SPREAD)
        self.assertIsNotNone(sig)
        self.assertEqual((sig.direction, sig.setup), (-1, "sweep"))
        self.assertGreater(sig.stop, mirrored.high.iloc[-1])

    def test_signal_uses_only_closed_bars_no_lookahead(self):
        df, _ = with_sweep_low(m15())
        future = pd.concat([df, m15(50, end="2024-03-13 21:30") * 3.0])
        self.assertEqual(evaluate(df, StrategyParams(), SPREAD), evaluate(future.iloc[:len(df)], StrategyParams(), SPREAD))

    def test_bias_follows_trend(self):
        self.assertEqual(htf_bias(m15(drift=2e-5, wig=0.0001), StrategyParams()), 1)
        self.assertEqual(htf_bias(m15(drift=-2e-5, wig=0.0001), StrategyParams()), -1)

    def test_too_little_history_returns_none(self):
        self.assertIsNone(evaluate(m15(200), StrategyParams(), SPREAD))


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
        sent=[], pos=[])
    import time as _t
    m.initialize = lambda: True
    m.shutdown = lambda: None
    m.last_error = lambda: (0, "ok")
    m.account_info = lambda: types.SimpleNamespace(trade_mode=0 if demo else 2, equity=5000.0, balance=5000.0,
                                                   margin_free=4000.0, currency="USD")
    m.symbol_info = lambda s: types.SimpleNamespace(digits=5, point=0.00001, volume_min=0.01, volume_step=0.01,
                                                    volume_max=50.0, trade_stops_level=10, trade_mode=4, filling_mode=2)
    m.symbol_select = lambda s, v: True
    m.symbol_info_tick = lambda s: types.SimpleNamespace(bid=1.1000, ask=1.1001, time=_t.time() + server_offset_h * 3600)
    m.order_calc_profit = lambda typ, s, vol, a, b: (b - a) * 100_000 * vol * (1 if typ == 0 else -1)
    m.order_calc_margin = lambda typ, s, vol, price: vol * 1000.0
    m.positions_get = lambda: tuple(m.pos)

    def order_send(req):
        m.sent.append(req)
        if req["action"] == 1 and "position" not in req:
            m.pos.append(types.SimpleNamespace(ticket=777, symbol=req["symbol"], type=req["type"], volume=req["volume"],
                                               price_open=req["price"], sl=req["sl"], magic=req["magic"],
                                               time=_t.time() + server_offset_h * 3600))
        return types.SimpleNamespace(retcode=10009, order=555, price=req.get("price", 0.0), comment="done")
    m.order_send = order_send
    return m


class MT5Adapter(unittest.TestCase):
    def test_refuses_real_account_by_default(self):
        with self.assertRaises(RuntimeError):
            MT5Broker(cfg(), mt5=fake_mt5(demo=False))
        MT5Broker(cfg(), allow_real=True, mt5=fake_mt5(demo=False))

    def test_server_offset_detected_and_times_are_utc(self):
        b = MT5Broker(cfg(), mt5=fake_mt5(server_offset_h=3))
        self.assertEqual(b._offset, timedelta(hours=3))
        self.assertLess(abs((b.tick("EURUSD").time - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds()), 5)

    def test_order_request_carries_stop_magic_and_filling(self):
        m = fake_mt5()
        b = MT5Broker(cfg(), mt5=m)
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
        b = MT5Broker(cfg(), mt5=m)
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


if __name__ == "__main__":
    unittest.main()
