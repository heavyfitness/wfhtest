# OHLC Sessions — Bull / Bear

A LuxAlgo-style **Sessions** indicator for TradingView (Pine Script v5), with one
change: each session box is colored by whether that session was **bullish or
bearish** instead of a fixed per-session color.

```
session close ≥ session open  →  GREEN (bullish)
session close <  session open  →  RED   (bearish)
```

Files:

- [`ohlc_sessions_bull_bear.pine`](./ohlc_sessions_bull_bear.pine) — the indicator.

## Install

1. Open TradingView → **Pine Editor** (bottom panel).
2. Paste the contents of `ohlc_sessions_bull_bear.pine`.
3. Click **Save**, then **Add to chart**.

## How it works

Each session box is anchored to the session's real **OHLC and timestamps**:

| Box edge | Value |
| -------- | ----- |
| Top      | Session **high** |
| Bottom   | Session **low** |
| Left     | Session **open time** |
| Right    | Session **close time** |

The fill/border/name color is decided live from **close vs. open** of the
session (green while the session is trading above its open, red while below;
it settles on the final color at the session close).

Because the box coordinates are **price/time based** (`xloc.bar_time` + price),
not bar-index or pixel based, the boxes stay locked to the same price levels and
clock times when you **switch timeframes** or **scroll/pan** around the chart —
exactly the behavior in your teacher's version.

## Settings

**General**
- **Timezone** — used to evaluate the session times. IANA names
  (`America/New_York`, `Europe/London`, `Asia/Tokyo`) auto-adjust for DST. A
  fixed offset like `GMT-4` does not.
- **Max boxes kept per session** — how many past occurrences of each session to
  keep drawn.

**Bull / Bear colors**
- Bullish / Bearish colors, fill transparency, border on/off + width, and a
  toggle for the session name label.

**Sessions 1–5** — each row is: enable ✓, name, and session time (`HHMM-HHMM`).
Sessions that cross midnight (e.g. Tokyo `1700-0500`) are handled automatically.

Defaults (in the selected timezone) match the reference layout:

| # | Name     | Time        |
| - | -------- | ----------- |
| 1 | New York | 0930–1700   |
| 2 | London   | 0300–1200   |
| 3 | Tokyo    | 1700–0500   |
| 4 | Pre-Mkt  | 0830–0930   |
| 5 | Custom   | (disabled)  |

Add or repurpose sessions by editing any row — overlapping sessions draw as
overlapping boxes, just like the LuxAlgo original.
