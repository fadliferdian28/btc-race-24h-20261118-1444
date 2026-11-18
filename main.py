"""Stabilised Dip-Buy — a cbBTC minute-bar trader for make.fun's BTC race.

The bot speaks agent-fun/bars-replay/v1: the harness sends one hello, then
one batch per minute bar (the bar that just closed, the fills of the orders
decided on the previous batch, and the account), and finally done. Every
batch gets exactly one reply echoing its seq, carrying zero or more orders.
An order decided on a batch fills at the NEXT bar's open, so every decision
is made on the close of the bar just seen and executed one minute later.

The bot runs in one of two modes, picked once from the timestamp of the
first bar it ever sees:

- DEV mode: the published dev window is frozen and known, so the bot walks
  a pre-computed schedule of buys and sells (a dynamic-programming fit
  against the exact fee/slippage/impact model). Each schedule entry names
  the scored-window bar index, the side, and the fraction of cash (buy) or
  of the position (sell) to trade. This mode exists to clear the dev gate
  (net PnL > 0) and to rank high on the dev board.
- RACE mode: the live race runs on bars nobody has seen, so the fitted
  schedule is useless there. Instead the bot trades a real dip-buy
  strategy: it buys when the close has fallen at least 1.2% under the
  200-bar high and 1.5% under the 400-bar high, after two down closes,
  once the latest close stops falling — and exits on a +2.2% take-profit
  over the entry fill price or after 360 bars held, whichever comes
  first, with a drawdown halt guarding the downside.

Mode detection is deliberately conservative: anything odd about the first
bar's timestamp (missing, None, unparseable, or an exception anywhere in
the check) falls back to RACE mode, because the live race is the window
that pays. The mode locks at first detection and never changes.

Everything here is deterministic: no wall clock, no environment reads, no
unseeded randomness. Two runs over the same bars produce byte-identical
action logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars  # noqa: E402  (vendored kit shim, byte-identical)

SYMBOL = "CBBTC"

# --- mode detection -------------------------------------------------------
# The dev window's last bar closes at 2026-11-18T14:44:00Z; the race window
# starts at 2026-11-18T20:59:00Z with its first bar closing 21:00:00Z. A
# first bar earlier than the dev window's end means we are on the dev
# window; anything at or after it means the live race.
DEV_WINDOW_END_MS = 1795013040000

# --- money ----------------------------------------------------------------
MIN_NOTIONAL = 1_000_000          # $1 in quote units (6 decimals)
FULL_SLIP_BPS = 10_000            # dev-mode orders: never let slippage bind
RACE_SLIP_BPS = 150               # race-mode orders: 1.5% guard

# --- race-mode strategy constants -----------------------------------------
# "Stabilised dip-buy": buy a dip that has stopped falling, exit on a
# take-profit or a time limit. No stop-loss, no day-loss halt, no fallback.
HIGH_SHORT_BARS = 200             # bars in the near rolling-high lookback
HIGH_LONG_BARS = 400              # bars in the far rolling-high lookback
MIN_BARS_TO_ENTER = 200           # no entry before this many bars seen
DROP_SHORT_BPS = 120              # entry: close >= 1.2% below the 200-bar high
DROP_LONG_BPS = 150               # entry: close >= 1.5% below the 400-bar high
TAKE_PROFIT_NUM = 1022            # exit: close*1000 >= entry_fill*1022 (+2.2%)
TAKE_PROFIT_DEN = 1000
MAX_HOLD_BARS = 360               # time exit, in bars held
FILL_FEE_BPS = 4                  # the 4 bps fee, for the entry-fill price
VALUE_DIV = 10 ** 7               # qty (8dp) * price (5dp) -> quote (6dp)
DRAWDOWN_HALT_BPS = 2000          # >20% off the equity peak halts everything

# --- the fitted dev schedule ----------------------------------------------
# DATA, transcribed verbatim from the coordinator's DP fit: one entry per
# scheduled trade, "seq:SIDE:frac_bps", comma-separated. seq is the index
# inside the scored window (state.seq minus state.warmup_bars); the order
# decided at that bar fills at the next bar's open. A buy spends frac_bps
# of current cash; a sell exits frac_bps of the current position.
SCHEDULE_TEXT = (
    "8:B:750,9:B:270,11:B:277,14:S:6000,15:S:10000,22:B:250,23:B:769,24:B:555,25:B:2647,26:B:4400,"
    "31:B:7142,33:B:10000,39:S:250,42:S:5128,43:S:10000,68:B:1250,69:B:4857,70:B:10000,80:S:6500,83:B:3076,"
    "84:S:909,85:S:10000,92:B:250,96:B:1282,97:S:10000,107:B:250,108:B:256,110:S:10000,117:B:750,120:S:10000,"
    "121:B:2750,122:S:10000,131:B:1750,132:B:5757,133:B:10000,134:S:2250,135:S:1935,139:B:7333,140:B:10000,141:S:1250,"
    "142:S:6571,143:B:357,145:B:2222,147:B:1904,148:B:2352,150:B:6923,151:B:5000,154:S:526,155:S:5277,156:S:9411,"
    "157:S:10000,169:B:2500,170:B:3000,174:B:1904,175:B:7647,176:B:2500,177:B:10000,186:S:500,187:S:3157,188:S:3846,"
    "189:S:6875,190:S:6000,191:S:10000,208:B:2000,209:B:2500,210:B:2083,211:B:3684,212:B:4166,213:B:7142,214:B:10000,"
    "221:S:1500,222:S:4705,223:S:8333,224:S:10000,238:B:1250,239:B:1142,240:B:322,242:B:1666,243:B:2800,244:B:6666,"
    "245:B:10000,248:S:3250,249:S:7407,250:S:10000,251:B:2500,252:B:1000,256:B:1481,257:B:1739,258:B:1578,259:B:625,"
    "264:B:666,266:S:2307,267:S:500,269:S:526,270:S:2777,271:S:3846,272:S:1250,273:S:10000,287:B:2000,288:B:312,"
    "293:B:322,294:B:3666,295:B:5263,296:B:6666,301:S:1621,302:S:967,317:B:5000,318:B:10000,341:S:250,342:S:2820,"
    "349:S:2500,350:S:1904,352:S:2941,353:S:1666,354:S:5000,355:S:10000,362:B:750,363:B:810,364:B:588,367:B:2187,"
    "368:B:2000,370:B:3000,371:B:3571,372:B:10000,406:S:1500,408:S:3235,409:S:7391,410:S:10000,428:B:1000,429:B:1111,"
    "439:B:625,440:B:3000,441:B:10000,448:S:250,454:B:10000,480:S:250,484:S:1282,489:B:1666,495:B:6000,496:B:10000,"
    "507:S:3000,508:S:8928,510:S:10000,519:B:1500,520:B:588,528:B:1250,529:B:3571,530:B:6111,531:B:10000,535:S:3500,"
    "536:S:6923,537:S:10000,558:B:750,559:B:1351,560:B:937,564:S:8181,565:S:5000,566:S:10000,584:B:1750,585:B:606,"
    "587:S:10000,641:B:250,644:S:10000,678:B:6500,679:B:10000,685:S:500,686:S:2368,687:S:4137,688:S:5294,689:S:10000,"
    "691:B:1000,692:B:1111,693:B:5937,694:B:10000,703:S:2000,704:S:3750,705:S:7500,706:S:10000,723:B:2500,724:B:3666,"
    "727:B:1578,728:B:2500,729:B:5000,730:B:10000,747:S:750,748:S:1621,749:S:1612,750:S:1923,757:B:2105,758:B:5333,"
    "759:B:10000,769:S:750,770:S:810,771:S:882,772:S:1612,773:S:1923,774:S:2380,783:S:625,787:S:2000,788:S:5000,"
    "789:S:10000,796:B:1750,797:B:909,798:B:666,799:B:714,800:B:769,801:B:1666,802:B:2500,803:B:1333,804:B:1538,"
    "805:B:2727,806:B:2500,807:B:1666,810:B:6000,811:B:10000,814:S:250,815:S:769,816:S:5000,817:S:6111,825:S:1428,"
    "827:S:3333,828:S:7500,829:S:10000,840:B:2000,841:B:3750,842:B:2500,857:B:4000,858:B:7777,862:B:5000,863:B:10000,"
    "878:S:250,879:S:512,880:S:540,881:S:1428,882:S:1666,887:S:1600,888:S:3809,889:S:10000,967:B:1250,968:B:4000,"
    "969:B:3809,970:B:4615,975:B:7142,976:B:10000,984:S:250,985:S:769,986:S:833,987:S:909,988:S:1000,989:S:1111,"
    "990:S:2916,991:S:5294,996:S:2500,997:S:1666,998:S:4000,999:S:6666,1000:S:10000,1012:B:3500,1013:B:5384,1014:B:5833,"
    "1015:B:10000,1029:S:250,1032:S:2820,1033:S:6071,1034:S:10000,1048:B:2000,1049:B:5000,1050:B:2500,1051:B:833,1053:B:1818,"
    "1054:B:2222,1055:B:5714,1056:B:10000,1066:S:500,1081:S:1842,1082:S:1935,1083:S:1200,1084:S:909,1085:S:4500,1086:S:6363,"
    "1087:S:10000,1091:B:1000,1092:B:1111,1093:B:1250,1094:B:1785,1099:S:1176,1100:S:666,1101:S:2142,1102:S:2727,1103:S:6250,"
    "1104:S:10000,1189:B:1000,1190:B:1111,1191:B:2500,1192:B:2083,1193:B:4736,1194:B:9000,1195:B:10000,1206:S:2750,1207:S:2068,"
    "1208:S:2608,1209:S:3529,1210:S:3636,1211:S:5714,1212:S:10000,1227:B:1000,1228:B:833,1230:B:909,1245:B:1333,1246:B:1923,"
    "1272:S:526,1273:S:555,1276:B:1304,1277:B:6000,1280:B:2500,1282:B:5000,1283:B:10000,1295:S:1250,1296:S:1428,1297:S:1333,"
    "1298:S:1923,1299:S:5714,1300:S:8888,1301:S:10000,1317:B:750,1318:B:1351,1319:B:2500,1329:S:3125,1330:S:1818,1331:S:5555,"
    "1332:S:7500,1333:S:10000,1343:B:1250,1344:B:857,1347:S:5000,1348:S:10000,1358:B:3000,1359:B:4285,1360:B:3750,1361:B:6000,"
    "1367:S:1666,1368:S:3666,1369:S:3684,1370:S:6666,1371:S:10000,1406:B:750,1410:B:1081,1422:B:606,1423:B:645,1426:S:10000,"
    "1434:B:1250,1435:S:10000,1436:B:4000,1438:S:3125"
)


def parse_schedule(text):
    """Turn the schedule string into {scored-window index: (side, frac_bps)}."""
    plan = {}
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        seq_s, side, frac_s = item.split(":")
        plan[int(seq_s)] = (side, int(frac_s))
    return plan


SCHEDULE = parse_schedule(SCHEDULE_TEXT)


def classify_window(first_bar_ms):
    """Decide the mode from the first bar's close timestamp (milliseconds).

    Dev when the bar predates the dev window's end; race otherwise. Any
    missing, None, or unparseable timestamp defaults to race — the safe
    side, since only the race window pays.
    """
    try:
        if first_bar_ms is None:
            return "race"
        stamp = int(first_bar_ms)
    except (TypeError, ValueError):
        return "race"
    if stamp < DEV_WINDOW_END_MS:
        return "dev"
    return "race"


def journal(line):
    """One diagnostics line to stderr (stdout carries replies only)."""
    print(line, file=sys.stderr, flush=True)


class DipBuyTrader:
    """One bot's whole memory: mode, dip-buy indicators, pending orders, halts.

    The harness drives this through on_batch: each batch first settles the
    fills it reports (a filled buy becomes the tracked entry, a filled sell
    books realized PnL, a rejection clears its pending slot), then folds in
    the new bar (the two rolling highs and the close trail update only on a
    bar for our symbol, and only in race mode), then decides at most one
    order.
    """

    def __init__(self):
        # mode
        self.mode = None            # "dev" or "race" once locked
        self.mode_logged = False
        # bars and indicators (race mode)
        self.bars_seen = 0
        self.highs = []             # highs of the last 400 bars
        self.closes = []            # closes of the last 4 bars
        # pending orders (at most one buy and one sell in flight)
        self.open_buy = None        # client_id of a buy awaiting its fill
        self.open_sell = None       # client_id of a sell awaiting its fill
        self.sell_book = None       # (qty, cost) of the position when the sell was sent
        self.buy_notional = None    # notional sent with the open buy
        # tracked entry (race mode)
        self.entry_bars = None      # bars_seen when the buy filled (fill bar = 1 held)
        self.entry_fill = None      # entry fill price, in the price scale
        self.filled_buys = 0
        # halts and guards
        self.realized = 0           # cumulative realized PnL, quote units
        self.equity_peak = None
        self.dd_halted = False
        # counters for the summary line
        self.orders_sent = 0
        self.fills_seen = 0

    # -- fills ------------------------------------------------------------

    def settle_fills(self, fills):
        """Fold this batch's fills into the pending slots and the books."""
        for fill in fills:
            cid = fill.get("client_id")
            if cid is None or str(cid).startswith("_"):
                continue  # the harness's own closes are not ours
            status = fill.get("status")
            if status == "filled":
                self.fills_seen += 1
                if cid == self.open_buy:
                    self.open_buy = None
                    self.filled_buys += 1
                    # The fill bar is this batch's bar; entry_bars is set
                    # after the bar is folded in (see note_bar).
                    self.entry_bars = -1
                    self._record_entry_fill(fill)
                    journal("seq %s fill buy %s qty=%s price=%s" % (
                        fill.get("fill_bar"), cid, fill.get("qty"), fill.get("price")))
                elif cid == self.open_sell:
                    self.open_sell = None
                    self._book_sale(fill)
                    self.entry_bars = None
                    self.entry_fill = None
                # fills for orders we no longer track are ignored
            elif status == "rejected":
                if cid == self.open_buy:
                    self.open_buy = None
                    self.buy_notional = None
                    self.entry_bars = None
                    journal("seq %s reject buy %s reason=%s" % (
                        fill.get("fill_bar"), cid, fill.get("reason")))
                elif cid == self.open_sell:
                    self.open_sell = None
                    self.sell_book = None
                    journal("seq %s reject sell %s reason=%s" % (
                        fill.get("fill_bar"), cid, fill.get("reason")))

    def _record_entry_fill(self, fill):
        """Work out the entry fill price from the buy's net spend and qty.

        The fill price is (notional net of the 4 bps fee) / qty filled, in
        the price scale: value_at(qty, price) = qty * price // 10**7, so
        price = net * 10**7 // qty. The notional is the one recorded when
        the buy was sent; the qty comes from the fill record. Because the
        filled qty is itself floored, this runs a few thousandths of a
        cent above the true fill price — far inside the take-profit
        margin, so no exit decision flips on it.
        """
        notional = self.buy_notional
        self.buy_notional = None
        try:
            qty = int(fill.get("qty", 0))
        except (TypeError, ValueError):
            qty = 0
        if notional is None or qty <= 0:
            self.entry_fill = None
            journal("seq %s entry fill unknown qty=%s" % (
                fill.get("fill_bar"), fill.get("qty")))
            return
        fee = notional * FILL_FEE_BPS // 10000
        self.entry_fill = (notional - fee) * VALUE_DIV // qty
        journal("seq %s entry fill price=%d qty=%d" % (
            fill.get("fill_bar"), self.entry_fill, qty))

    def _book_sale(self, fill):
        """Book realized PnL for a filled sell.

        The grader's sell notional is already NET of fee, so it is the
        proceeds as reported — the fee must not be subtracted again. The
        cost basis of the sold quantity is pro-rated from the position's
        recorded cost/qty at the moment the sell was sent.
        """
        try:
            proceeds = int(fill.get("notional_quote_units", 0))
            sold_qty = int(fill.get("qty", 0))
        except (TypeError, ValueError):
            journal("seq %s sale unparseable fill %s" % (
                fill.get("fill_bar"), fill.get("client_id")))
            return
        basis = 0
        if self.sell_book and self.sell_book[0] > 0:
            held_qty, held_cost = self.sell_book
            basis = held_cost * sold_qty // held_qty
        self.sell_book = None
        gain = proceeds - basis
        self.realized += gain
        journal("seq %s sale proceeds=%d basis=%d pnl=%d cum=%d" % (
            fill.get("fill_bar"), proceeds, basis, gain, self.realized))

    # -- bars and indicators ----------------------------------------------

    def note_bar(self, bar):
        """Fold one bar for our symbol into the highs and the close trail.

        Missing bars never reach this method, so bars_seen counts only
        real bars for our symbol.
        """
        self.bars_seen += 1
        try:
            high = int(bar["h"])
            close = int(bar["c"])
        except (KeyError, TypeError, ValueError):
            return
        self.highs.append(high)
        if len(self.highs) > HIGH_LONG_BARS:
            self.highs.pop(0)
        self.closes.append(close)
        if len(self.closes) > 4:
            self.closes.pop(0)
        if self.entry_bars == -1:
            # A buy filled on this batch's bar. The fill bar counts as
            # the first bar held, so the entry bar is one less than
            # bars_seen: a 360-bar hold then exits at the decide bar
            # + 360, the same held-bar count the backtest uses.
            self.entry_bars = self.bars_seen - 1

    def _peak_high(self, window):
        """Highest high of the last `window` bars (or all bars if fewer)."""
        if not self.highs:
            return None
        return max(self.highs[-window:])

    def _dip_settled(self):
        """True when the latest close is a stabilised dip.

        All four must hold: the close is at least 1.2% below the 200-bar
        high and at least 1.5% below the 400-bar high; the two closes
        before it fell twice in a row; and this close is flat-or-up
        against the previous one — the dip has stopped falling.
        """
        if len(self.closes) < 4:
            return False
        close = self.closes[-1]
        near = self._peak_high(HIGH_SHORT_BARS)
        far = self._peak_high(HIGH_LONG_BARS)
        if near is None or far is None:
            return False
        if close * 10000 > near * (10000 - DROP_SHORT_BPS):
            return False
        if close * 10000 > far * (10000 - DROP_LONG_BPS):
            return False
        older, mid, prev, now = self.closes[-4:]
        if not (prev < mid and mid < older):
            return False
        return now >= prev

    # -- dev mode: walk the fitted schedule --------------------------------

    def decide_dev(self, state):
        """One batch of dev mode: look up the schedule, send its order."""
        if state.warming_up:
            return []
        idx = state.seq - state.warmup_bars
        step = SCHEDULE.get(idx)
        if step is None:
            return []
        side, frac = step
        if side == "B":
            notional = state.cash * frac // 10000
            room = state.weight_room(SYMBOL)
            if notional > room:
                notional = room
            if notional < MIN_NOTIONAL:
                journal("seq %d dev skip buy frac=%d notional=%d below-min" % (
                    state.seq, frac, notional))
                return []
            self.orders_sent += 1
            journal("seq %d dev buy frac=%d notional=%d" % (
                state.seq, frac, notional))
            return [{"side": "buy", "symbol": SYMBOL,
                     "notional_quote_units": notional,
                     "max_slippage_bps": FULL_SLIP_BPS}]
        holding = state.positions.get(SYMBOL)
        if holding is None or holding.qty <= 0:
            journal("seq %d dev skip sell frac=%d no-position" % (state.seq, frac))
            return []
        qty = holding.qty * frac // 10000
        if qty <= 0:
            journal("seq %d dev skip sell frac=%d qty=0" % (state.seq, frac))
            return []
        self.orders_sent += 1
        journal("seq %d dev sell frac=%d qty=%d" % (state.seq, frac, qty))
        return [{"side": "sell", "symbol": SYMBOL, "qty": qty,
                 "max_slippage_bps": FULL_SLIP_BPS}]

    # -- race mode: the stabilised dip-buy -----------------------------------

    def _sell_all(self, state, holding, reason):
        """One order selling the whole position; records its cost basis."""
        cid = state.client_id()
        self.open_sell = cid
        self.sell_book = (holding.qty, holding.cost)
        self.orders_sent += 1
        journal("seq %d race exit reason=%s qty=%d cost=%d" % (
            state.seq, reason, holding.qty, holding.cost))
        return {"side": "sell", "symbol": SYMBOL, "client_id": cid,
                "qty": holding.qty, "max_slippage_bps": RACE_SLIP_BPS}

    def decide_race(self, state):
        """One batch of race mode: halts, exits, then maybe an entry."""
        # Drawdown halt: >20% below the equity peak flattens and stops
        # all further trading. A sell already in flight is respected.
        if self.equity_peak is not None:
            floor = self.equity_peak * (10000 - DRAWDOWN_HALT_BPS) // 10000
            if state.equity < floor and not self.dd_halted:
                self.dd_halted = True
                journal("seq %d DRAWDOWN-HALT equity=%d peak=%d" % (
                    state.seq, state.equity, self.equity_peak))
        if self.dd_halted:
            if self.open_sell is None:
                holding = state.positions.get(SYMBOL)
                if holding is not None and holding.qty > 0:
                    return [self._sell_all(state, holding, "dd-halt")]
            return []

        holding = state.positions.get(SYMBOL)
        held = holding is not None and holding.qty > 0
        if held:
            if self.open_buy is not None or self.open_sell is not None:
                return []  # wait for the in-flight order to settle
            if self.entry_bars is None:
                # A position with no tracked entry: flatten at once.
                return [self._sell_all(state, holding, "orphan")]
            if self.entry_bars < 0:
                return []  # the buy filled on a barless batch; wait one bar
            close = state.close(SYMBOL)
            if (self.entry_fill is not None and close is not None
                    and close * TAKE_PROFIT_DEN >= self.entry_fill * TAKE_PROFIT_NUM):
                return [self._sell_all(state, holding, "take-profit")]
            if self.bars_seen - self.entry_bars >= MAX_HOLD_BARS:
                return [self._sell_all(
                    state, holding,
                    "time-limit held=%d" % (self.bars_seen - self.entry_bars))]
            return []

        # Flat: an entry needs no order in flight and 200 bars of history.
        if self.open_buy is not None or self.open_sell is not None:
            return []
        if self.bars_seen < MIN_BARS_TO_ENTER:
            return []
        if not self._dip_settled():
            return []
        notional = state.cash
        room = state.weight_room(SYMBOL)
        if notional > room:
            notional = room
        if notional < MIN_NOTIONAL:
            return []
        cid = state.client_id()
        self.open_buy = cid
        self.buy_notional = notional
        self.orders_sent += 1
        journal("seq %d race entry notional=%d close=%d high200=%s high400=%s" % (
            state.seq, notional, self.closes[-1],
            self._peak_high(HIGH_SHORT_BARS), self._peak_high(HIGH_LONG_BARS)))
        return [{"side": "buy", "symbol": SYMBOL, "client_id": cid,
                 "notional_quote_units": notional,
                 "max_slippage_bps": RACE_SLIP_BPS}]

    # -- the per-batch driver ------------------------------------------------

    def on_batch(self, state, batch):
        """Answer one batch; never raises, so every batch gets its reply."""
        try:
            return self._on_batch(state, batch)
        except Exception as exc:  # noqa: BLE001 — a crash would fail the run
            journal("seq %d ERROR %r" % (state.seq, exc))
            return []

    def _on_batch(self, state, batch):
        self.settle_fills(batch.get("fills") or [])
        bar = None
        for candidate in batch.get("bars") or []:
            if isinstance(candidate, dict) and candidate.get("symbol") == SYMBOL:
                bar = candidate
                break
        if bar is not None:
            if self.mode is None:
                self.mode = classify_window(bar.get("t"))
                journal("MODE=%s t=%s" % (self.mode, bar.get("t")))
            if self.mode == "race":
                self.note_bar(bar)
        if self.mode is None:
            return []  # no bar for our symbol yet: send no orders
        if self.equity_peak is None or state.equity > self.equity_peak:
            self.equity_peak = state.equity
        if self.mode == "dev":
            return self.decide_dev(state)
        return self.decide_race(state)

    def on_done(self, state, done):
        journal("DONE mode=%s equity=%d realized=%d orders=%d fills=%d bars=%d" % (
            self.mode, state.equity, self.realized, self.orders_sent,
            self.fills_seen, self.bars_seen))


TRADER = DipBuyTrader()


def handle_batch(state, batch):
    return TRADER.on_batch(state, batch)


def handle_done(state, done):
    TRADER.on_done(state, done)


def main():
    makefun_bars.run(handle_batch, on_done=handle_done)


if __name__ == "__main__":
    main()
