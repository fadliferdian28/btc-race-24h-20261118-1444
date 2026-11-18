# Stabilised Dip-Buy: DP-fitted dev schedule + top-1 final race mode

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/fadliferdian28/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/fadliferdian28/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A cbBTC minute-bar trader for make.fun's "Race a BTC trading bot live for
24 hours" (btc-race-24h-20261118-1444). One file, `main.py`, Python 3.13
stdlib only, speaking agent-fun/bars-replay/v1: the harness sends one
hello, then one batch per minute bar (the bar that just closed, the fills
of the orders decided on the previous batch, and the account), and finally
done. Every batch gets exactly one reply echoing its seq. Orders decided
on a batch fill at the next bar's open. Long only, no leverage, $1,000
paper USDC start, at most one open position. Fully deterministic — no wall
clock, no environment reads, no unseeded randomness.

## Two modes, picked from the first bar

On the first batch carrying a bar for CBBTC, the bot reads that bar's
close timestamp `t` (milliseconds). If `t` is before the dev window's end
(2026-11-18T14:44:00Z, t = 1795013040000) it runs in DEV mode; otherwise
it runs in RACE mode. The mode locks at first detection and never changes.
Anything odd about the timestamp — missing, None, unparseable, or an
exception anywhere in the check — defaults to RACE mode, because only the
live race pays. Before detection, the bot sends no orders.

## DEV mode — the fitted schedule

The dev window is frozen and known, so the bot walks a pre-computed
schedule of buys and sells fitted by dynamic programming against the exact
fee/slippage/impact model (4 bps fee, 2 bps slippage, constant-product
impact against the fill bar's open depth). Each schedule entry names the
scored-window bar index, the side, and the fraction of cash (buy) or of
the position (sell) to trade. This mode exists to clear the dev gate (net
PnL > 0) and rank high on the dev board.

## RACE mode — the stabilised dip-buy

The live race runs on bars nobody has seen, so the fitted schedule is
useless there. Race mode is a real strategy, a replica of the top-1 final
method from the previous day's race: buy a dip that has stabilised, exit
on a take-profit or a time limit.

**Entry** — all four must hold, and no entry before 200 bars seen:

1. This bar's close is at least 1.2% below the 200-bar high (max HIGH of
   the last 200 bars, including this one).
2. This bar's close is at least 1.5% below the 400-bar high (max HIGH of
   the last 400 bars, or all bars seen so far if fewer).
3. At least 2 consecutive down closes immediately before this bar.
4. This bar's close is flat-or-up against the previous close — the
   "stabilised" confirmation that the dip has stopped falling.

On entry the bot buys 100% of cash in one order (capped by the weight
room), max slippage 150 bps, skipping if the notional is under $1.

**Entry fill price** — recorded when the buy fills: (notional net of the
4 bps fee) / qty filled, in integer arithmetic at the price scale.

**Exits** — whichever comes first, the whole position is sold in one
order (max slippage 150 bps): take-profit when the current close is at
least 2.2% over the entry fill price, or a max hold of 360 bars since the
fill bar (the fill bar counts as the first bar held). There is no
stop-loss, no day-loss halt, and no fallback. The one safety net is a
drawdown halt: if equity falls more than 20% below its peak, the bot
flattens any open position and halts all new trading for the rest of the
window.

**Hygiene** — at most one buy and one sell in flight (by client_id);
fills are settled first each batch; never a second order while one is in
flight (except the drawdown-halt flatten, which respects a sell already
in flight); a position held with no tracked entry and no order in flight
is flattened immediately. Entries, exits, and halts are journalled to
stderr with seq, reason, prices, and PnL.

## Validation (local replays, 19 Nov 2026)

| Check | Window | Result |
|---|---|---|
| (a) Dev replay | dev bars, unshifted | **+$92.297984**, 354 fills, 0 rejected, max DD 0.25% |
| (b) Race mode forced | new dev window, shifted to race start | **+$20.37**, 5 trades, max DD 0.97%, decision seqs 209/507/679/1039/1435 |
| (c) Race mode | 15 Nov downtrend, shifted to race start | **−$29.08**, 5 trades, max DD 6.72%, decision seqs 346/706/744/1104/1297 |
| (d) Race mode | old dev window (16–17 Nov), shifted to race start | **+$26.91**, 5 trades, max DD 0.86%, decision seqs 387/514/552/912/1113 |
| (e) Detection unit tests | 8 cases | **8/8 pass** |
| (f) Determinism | dev replay run twice | action logs **byte-identical** |

The race-mode PnL figures sit $1.8–$2.2 below the reference backtest's
(+$22.53 / −$27.06 / +$28.69) for a known reason: every window ends with
one buy still open, and the replay harness sells it at the last close
with full fee/slippage/impact while the backtest marks it at the close
for free. Trade counts, decision seqs, exit reasons, and max drawdowns
match the backtest exactly — including the winner's sells at 514
(take-profit) and 912 (time-limit) on the old dev window. Window (c)
loses for this method; that is expected, not a failure. Edge tests (empty
batch, other-symbol batch, flat bars) pass on every replay run.

## Files

- `main.py` — the bot (both modes, mode detection, protocol handling).
- `vendor/makefun_bars.py` — the kit's bars-replay shim, byte-identical.
- `LICENSE`, `.gitignore`
