"""Çalıştırma:  cd trading && python -m unittest discover -s tests -v"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

from fxlab.backtest import Params, run            # noqa: E402
from fxlab.data import load_mt5_csv, synthetic, validate  # noqa: E402
from fxlab.instruments import default_universe    # noqa: E402
from fxlab.metrics import summarize               # noqa: E402
from fxlab.strategy import atr, donchian_state, ensemble_score  # noqa: E402
from fxlab.walkforward import walk_forward        # noqa: E402

U = default_universe()


def free(sym):
    return U[sym].with_costs(spread=0.0, slippage=0.0, commission_per_lot=0.0, swap_long=0.0, swap_short=0.0)


def ramp(n=400, start=1.0, step=0.001, noise=0.0005):
    """Düzgün yükselen seri: her çubukta yeni zirve."""
    close = start + step * np.arange(n)
    idx = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({"open": close - step / 2, "high": close + noise, "low": close - step / 2 - noise,
                         "close": close}, index=idx)


class Instruments(unittest.TestCase):
    def test_pnl_quote_usd(self):
        self.assertAlmostEqual(U["EURUSD"].pnl_usd(1.0, 1, 1.1000, 1.1010), 100.0)
        self.assertAlmostEqual(U["XAUUSD"].pnl_usd(0.5, -1, 2000.0, 1990.0), 500.0)
        self.assertAlmostEqual(U["WTI"].pnl_usd(1.0, 1, 80.0, 79.0), -1000.0)

    def test_pnl_base_usd_converted(self):
        # 1 lot USDJPY, 100 pip: 100.000 * 1.00 JPY = 100.000 JPY -> / 151 = 662.25 USD
        self.assertAlmostEqual(U["USDJPY"].pnl_usd(1.0, 1, 150.0, 151.0), 100_000 / 151.0)

    def test_lots_round_down_never_up(self):
        i = U["EURUSD"]
        self.assertEqual(i.round_lots(0.239), 0.23)
        self.assertEqual(i.round_lots(0.009), 0.0)
        self.assertEqual(i.round_lots(1e9), i.volume_max)
        self.assertEqual(i.round_lots(float("nan")), 0.0)
        self.assertEqual(i.round_lots(0.30), 0.30)


class Signals(unittest.TestCase):
    def test_no_lookahead(self):
        df = synthetic(600, seed=3)
        cut = 400
        changed = df.copy()
        changed.iloc[cut:] *= 1.5
        for f in (lambda d: atr(d, 20), lambda d: donchian_state(d, 55), lambda d: ensemble_score(d, (20, 60, 120))):
            pd.testing.assert_series_equal(f(df).iloc[:cut], f(changed).iloc[:cut])

    def test_donchian_breakout_direction(self):
        self.assertEqual(donchian_state(ramp(), 20).iloc[-1], 1.0)
        down = ramp()
        down[["open", "high", "low", "close"]] = 3.0 - down[["open", "low", "high", "close"]].values
        self.assertEqual(donchian_state(down, 20).iloc[-1], -1.0)

    def test_mql5_stateless_scan_matches_python_state(self):
        # Signals.mqh içindeki DonchianState taramasının birebir Python karşılığı
        def scan(df, n, max_scan=500):
            r = df.iloc[::-1].reset_index(drop=True)      # r[0] = son kapanmış çubuk
            j = 0
            while j < max_scan and j + n < len(r):
                hi, lo = r.high.iloc[j + 1:j + n + 1].max(), r.low.iloc[j + 1:j + n + 1].min()
                if r.close.iloc[j] > hi:
                    return 1
                if r.close.iloc[j] < lo:
                    return -1
                j += 1
            return 0
        df = synthetic(700, seed=11)
        for n in (20, 60, 120):
            state = donchian_state(df, n)
            for end in (250, 400, 555, 700):
                self.assertEqual(scan(df.iloc[:end], n), state.iloc[end - 1], (n, end))

    def test_atr_matches_manual(self):
        df = synthetic(50, seed=1)
        tr_last = max(df.high.iloc[-1] - df.low.iloc[-1], abs(df.high.iloc[-1] - df.close.iloc[-2]),
                      abs(df.low.iloc[-1] - df.close.iloc[-2]))
        self.assertAlmostEqual(atr(df, 1).iloc[-1], tr_last)


class Engine(unittest.TestCase):
    def test_uptrend_makes_one_long_and_profit(self):
        res = run({"EURUSD": ramp()}, {"EURUSD": free("EURUSD")}, Params())
        self.assertEqual(len(res.trades), 1)
        t = res.trades.iloc[0]
        self.assertEqual(t.direction, 1)
        self.assertGreater(t.pnl, 0)
        self.assertAlmostEqual(res.equity.iloc[-1], Params().initial_equity + t.pnl, places=6)

    def test_position_risk_matches_budget(self):
        p = Params(risk_per_trade=0.01)
        res = run({"EURUSD": ramp()}, {"EURUSD": free("EURUSD")}, p)
        t = res.trades.iloc[0]
        df = ramp()
        a = atr(df, p.atr_period).shift(1).loc[t.entry_time]     # sinyal çubuğunun ATR'si
        risk = t.lots * p.stop_atr * a * 100_000
        self.assertLessEqual(risk, p.initial_equity * p.risk_per_trade * 1.0001)
        self.assertGreater(risk, p.initial_equity * p.risk_per_trade * 0.95)

    def test_entry_is_next_bar_open_after_signal(self):
        df = ramp()
        p = Params()
        score = ensemble_score(df, p.lookbacks)
        ok = (score.abs() >= p.entry_threshold - 1e-9) & atr(df, p.atr_period).notna()
        signal_bar = ok.idxmax()
        res = run({"EURUSD": df}, {"EURUSD": free("EURUSD")}, p)
        t = res.trades.iloc[0]
        self.assertEqual(t.entry_time, df.index[df.index.get_loc(signal_bar) + 1])
        self.assertAlmostEqual(t.entry, df.open.loc[t.entry_time])

    def test_gap_through_stop_fills_at_open_and_loses_more_than_1R(self):
        df = ramp(300)
        crash = df.index[250]
        df.loc[crash:, ["open", "high", "low", "close"]] *= 0.8      # stopun çok altına boşluk
        res = run({"EURUSD": df}, {"EURUSD": free("EURUSD")}, Params())
        t = res.trades.iloc[0]
        self.assertEqual(t.reason, "stop")
        self.assertEqual(t.exit_time, crash)
        self.assertAlmostEqual(t.exit, df.open.loc[crash])
        self.assertLess(t.exit, df.close.iloc[249] - 3 * atr(df, 20).iloc[249])

    def test_costs_reduce_result(self):
        df = synthetic(1500, regime_len=150, regime_drift=0.0015, seed=5)
        a = run({"EURUSD": df}, {"EURUSD": free("EURUSD")}, Params())
        b = run({"EURUSD": df}, {"EURUSD": U["EURUSD"]}, Params())
        self.assertGreater(len(b.trades), 3)
        self.assertLess(b.equity.iloc[-1], a.equity.iloc[-1])
        self.assertGreater(b.trades.costs.sum(), 0)

    def test_short_side_pays_spread(self):
        df = ramp()
        df[["open", "high", "low", "close"]] = 3.0 - df[["open", "low", "high", "close"]].values
        a = run({"EURUSD": df}, {"EURUSD": free("EURUSD")}, Params())
        b = run({"EURUSD": df}, {"EURUSD": free("EURUSD").with_costs(spread=0.0010)}, Params())
        self.assertEqual(a.trades.iloc[0].direction, -1)
        self.assertGreater(a.trades.iloc[0].pnl, b.trades.iloc[0].pnl)

    def _four_usd_shorts(self, **kw):
        syms = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"]
        data = {s: ramp(start=1.0 + 0.1 * i) for i, s in enumerate(syms)}
        res = run(data, {s: free(s) for s in syms}, Params(risk_per_trade=0.01, **kw))
        first_day = res.trades[res.trades.entry_time == res.trades.entry_time.min()]
        stop_dist = 3.0 * atr(ramp(), 20).shift(1).loc[first_day.entry_time.iloc[0]]
        risk = (first_day.lots * stop_dist * 100_000).sum()      # aynı gün alınan toplam başlangıç riski
        return res, first_day, risk

    def test_usd_cluster_cap_blocks_stacked_usd_shorts(self):
        # Dört parite aynı gün aynı USD yönünde sinyal verir; sınır %2, işlem başı %1.
        res, first_day, risk = self._four_usd_shorts(max_usd_net_risk=0.02, max_portfolio_risk=1.0)
        self.assertEqual(len(first_day), 2)
        self.assertLessEqual(risk, 0.02 * Params().initial_equity)
        self.assertGreater(res.skipped["usd_cap"], 0)

    def test_portfolio_cap_limits_total_risk(self):
        # Sınır %2,5: iki tam pozisyon + küçültülmüş üçüncü; dördüncüye yer yok.
        res, first_day, risk = self._four_usd_shorts(max_usd_net_risk=1.0, max_portfolio_risk=0.025)
        self.assertEqual(len(first_day), 3)
        self.assertLess(first_day.lots.min(), first_day.lots.max() * 0.6)
        self.assertLessEqual(risk, 0.025 * Params().initial_equity)
        self.assertGreater(res.skipped["portfolio_cap"], 0)

    def test_equity_equals_initial_plus_trade_pnl(self):
        # Muhasebe değişmezi: son özkaynak = başlangıç + işlemlerin net K/Z toplamı (kısmi kapanış dahil)
        df = synthetic(1500, regime_len=150, regime_drift=0.0015, seed=5)
        for pr in (0.0, 1.0):
            res = run({"EURUSD": df}, {"EURUSD": U["EURUSD"]}, Params(partial_r=pr))
            self.assertAlmostEqual(res.equity.iloc[-1], Params().initial_equity + res.trades.pnl.sum(), places=4)

    def test_partial_take_profit_locks_gain_when_price_reverses(self):
        # Fiyat +1R'yi geçer, sonra girişin altına çöker: kısmi kârsız zarar, kısmi kârla kazanç.
        df = ramp(200)
        peak = df.index[150]
        after = df.index[151:]
        fall = df.close.loc[peak] - 0.004 * np.arange(1, len(after) + 1)
        df.loc[after, "close"] = fall
        df.loc[after, "open"] = fall + 0.002
        df.loc[after, "high"] = fall + 0.0025
        df.loc[after, "low"] = fall - 0.0005
        p0 = Params(lookbacks=(120,), stop_atr=30.0)         # geç giriş, uzak stop: çıkış sinyalle
        plain = run({"EURUSD": df}, {"EURUSD": free("EURUSD")}, p0).trades.iloc[0]
        part = run({"EURUSD": df}, {"EURUSD": free("EURUSD")},
                   Params(lookbacks=(120,), stop_atr=30.0, partial_r=0.2)).trades.iloc[0]
        self.assertLess(plain.pnl, 0)
        self.assertGreater(part.pnl, 0)
        self.assertEqual(part.reason, "stop")                 # kalan yarı başabaşta kapanır
        self.assertAlmostEqual(part.exit, part.entry, delta=0.002)   # başabaş stopu (açılış boşluğu payıyla)

    def test_random_walk_with_costs_is_not_profitable_on_average(self):
        # Kenar (edge) olmayan veride maliyetli strateji ortalamada para kazanmamalı.
        finals = []
        for seed in range(8):
            df = synthetic(1500, seed=100 + seed)
            finals.append(run({"EURUSD": df}, {"EURUSD": U["EURUSD"]}, Params()).equity.iloc[-1])
        self.assertLess(np.mean(finals), Params().initial_equity)

    def test_start_end_window(self):
        df = synthetic(1200, seed=2)
        res = run({"EURUSD": df}, {"EURUSD": U["EURUSD"]}, Params(), start=df.index[600], end=df.index[900])
        self.assertEqual(res.equity.index[0], df.index[600])
        self.assertEqual(res.equity.index[-1], df.index[900])
        if len(res.trades):
            self.assertGreaterEqual(res.trades.entry_time.min(), df.index[600])


class DataAndWF(unittest.TestCase):
    def test_load_mt5_export(self):
        text = ("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
                "2024.01.02\t00:00:00\t1.10400\t1.10450\t1.09380\t1.09410\t1000\t0\t12\n"
                "2024.01.03\t00:00:00\t1.09410\t1.09650\t1.08920\t1.09210\t1000\t0\t12\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "EURUSD.csv"
            p.write_text(text)
            df = load_mt5_csv(p)
        self.assertEqual(len(df), 2)
        self.assertEqual(df.index[0], pd.Timestamp("2024-01-02"))
        self.assertAlmostEqual(df.close.iloc[1], 1.09210)

    def test_validate_rejects_broken_bar(self):
        df = synthetic(10)
        df.iloc[3, df.columns.get_loc("high")] = df.low.iloc[3] * 0.9
        with self.assertRaises(ValueError):
            validate(df)

    def test_walk_forward_runs_and_is_out_of_sample(self):
        data = {"EURUSD": synthetic(2600, regime_len=200, regime_drift=0.0012, seed=7),
                "XAUUSD": synthetic(2600, start_price=1500, vol=0.009, regime_len=180, regime_drift=0.0015, seed=8)}
        uni = {s: U[s] for s in data}
        folds, equity = walk_forward(data, uni, {"stop_atr": [2.0, 3.0]}, Params(),
                                     train_years=4, test_years=2, min_trades=5)
        self.assertGreaterEqual(len(folds), 2)
        self.assertTrue((pd.to_datetime(folds.test_bas) >= pd.to_datetime(folds.egitim_bas)).all())
        self.assertTrue(equity.index.is_monotonic_increasing and equity.index.is_unique)
        self.assertTrue(np.isfinite(summarize(equity, pd.DataFrame(columns=["pnl", "r", "costs"]))["azami_dusus"]))


if __name__ == "__main__":
    unittest.main()
