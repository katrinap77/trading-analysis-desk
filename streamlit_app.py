"""Trading Analysis Desk — Streamlit version.

Multi-agent stock analyzer. Same flow as the browser version:
  1. User enters Anthropic API key + watchlist of tickers
  2. For each ticker, fetch live Yahoo Finance quote + 5d price trend
  3. Ask Claude for a structured BUY/SELL/HOLD verdict with 4 agent
     signals (fundamental, sentiment, technical, news) and bull/bear
     cases, risk rating
  4. Render each ticker as a card

The Node.js server (server.js) is not needed here — Python can call both
APIs directly, so this Streamlit app is fully self-contained.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import requests
import streamlit as st
from anthropic import Anthropic

# ───────────────────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Trading Analysis Desk",
    page_icon="📊",
    layout="centered",
)

MODEL = "claude-sonnet-4-6"

GREEN = "#059669"
RED = "#dc2626"
AMBER = "#d97706"
GREY = "#9ca3af"


# ───────────────────────────────────────────────────────────
# SESSION STATE
# ───────────────────────────────────────────────────────────
if "tickers" not in st.session_state:
    st.session_state.tickers = []
if "api_key" not in st.session_state:
    st.session_state.api_key = ""
if "results" not in st.session_state:
    st.session_state.results = {}
if "last_run" not in st.session_state:
    st.session_state.last_run = None


# ───────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────
def fetch_yahoo(ticker: str) -> dict[str, Any] | None:
    """Fetch live quote + 5d closes for a ticker via Yahoo Finance.
    Tries query1 then query2 (same pattern as the Node server)."""
    for host in ("query1", "query2"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/"
            f"{ticker}?interval=1d&range=5d"
        )
        try:
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            if not r.ok:
                continue
            data = r.json()
            result = (data.get("chart", {}).get("result") or [None])[0]
            if not result:
                continue
            meta = result.get("meta") or {}
            if not meta.get("regularMarketPrice"):
                continue
            closes = [
                c for c in (result.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
                if c is not None
            ]
            return {
                "live": True,
                "name": meta.get("longName") or meta.get("shortName") or ticker,
                "price": meta.get("regularMarketPrice"),
                "prev": meta.get("previousClose") or meta.get("chartPreviousClose"),
                "high52": meta.get("fiftyTwoWeekHigh"),
                "low52": meta.get("fiftyTwoWeekLow"),
                "vol": meta.get("regularMarketVolume"),
                "avg_vol": meta.get("averageDailyVolume3Month"),
                "mcap": meta.get("marketCap"),
                "currency": meta.get("currency") or "USD",
                "closes": closes,
            }
        except Exception:
            continue
    return None


def fmt_num(n: float | int | None) -> str:
    if n is None:
        return "—"
    n = float(n)
    if abs(n) >= 1e12:
        return f"{n/1e12:.2f}T"
    if abs(n) >= 1e9:
        return f"{n/1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"{n/1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"{n/1e3:.2f}K"
    return f"{n:.2f}"


def extract_json(raw: str) -> dict[str, Any]:
    s = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    s = re.sub(r"```\s*", "", s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON in Claude response")
    s = s[start : end + 1]
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    return json.loads(s)


def call_claude(api_key: str, ticker: str, mkt: dict[str, Any] | None) -> dict[str, Any]:
    """Send Yahoo context to Claude and parse the multi-agent JSON verdict."""
    if mkt:
        change = mkt["price"] - mkt["prev"]
        change_pct = (change / mkt["prev"] * 100) if mkt["prev"] else 0
        from_hi = ((mkt["price"] - mkt["high52"]) / mkt["high52"] * 100) if mkt["high52"] else 0
        from_lo = ((mkt["price"] - mkt["low52"]) / mkt["low52"] * 100) if mkt["low52"] else 0
        vol_ratio = (mkt["vol"] / mkt["avg_vol"]) if mkt.get("avg_vol") else None
        trend = (
            "upward" if len(mkt["closes"]) >= 2 and mkt["closes"][-1] > mkt["closes"][0]
            else "downward" if len(mkt["closes"]) >= 2
            else "N/A"
        )
        context = (
            f"\nLIVE YAHOO FINANCE DATA:"
            f"\n- Price: {mkt['currency']} {mkt['price']:.2f}"
            f"\n- Change: {change:+.2f} ({change_pct:+.2f}%)"
            f"\n- 52w High: {mkt['high52']:.2f} ({from_hi:+.1f}% from high)"
            f" | 52w Low: {mkt['low52']:.2f} ({from_lo:+.1f}% above low)"
            f"\n- Volume: {fmt_num(mkt['vol'])} | Avg Vol: {fmt_num(mkt['avg_vol'])}"
            f"{f' | Ratio: {vol_ratio:.2f}x' if vol_ratio else ''}"
            f"\n- Market Cap: {mkt['currency']} {fmt_num(mkt['mcap'])}"
            f"\n- 5-day trend: {trend}"
        )
    else:
        context = "\nNOTE: Live data unavailable. Use training knowledge for estimates."

    prompt = (
        f"You are a professional multi-agent stock trading analysis system for an "
        f"Interactive Brokers US equities account. Analyse: {ticker}\n"
        f"{context}\n\n"
        'Respond ONLY with raw JSON (no markdown, no preamble):\n'
        '{"company":"<full name>","verdict":"BUY|SELL|HOLD","confidence":<0-100>,'
        '"agents":{"fundamental":{"signal":"Bullish|Bearish|Neutral","note":"<1 sentence>"},'
        '"sentiment":{"signal":"Positive|Negative|Mixed","note":"<1 sentence>"},'
        '"technical":{"signal":"Bullish|Bearish|Neutral","note":"<1 sentence>"},'
        '"news":{"signal":"Positive|Negative|Mixed","note":"<1 sentence>"}},'
        '"bull":"<2 sentences>","bear":"<2 sentences>","summary":"<2-3 sentences>",'
        '"risk":"LOW|MEDIUM|HIGH","riskNote":"<1 sentence>"}'
    )

    client = Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(getattr(b, "text", "") for b in resp.content)
    return extract_json(text)


def analyze_ticker(api_key: str, ticker: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Returns (ticker, mkt, ai, error)."""
    try:
        mkt = fetch_yahoo(ticker)
    except Exception:
        mkt = None
    try:
        ai = call_claude(api_key, ticker, mkt)
    except Exception as e:
        return ticker, mkt, None, str(e)
    return ticker, mkt, ai, None


# ───────────────────────────────────────────────────────────
# UI — HEADER
# ───────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
      .desk-title {
        font-family: 'IBM Plex Mono', monospace;
        font-weight: 600;
        letter-spacing: -0.02em;
      }
      .desk-tag {
        display: inline-block;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.15em;
        color: #059669;
        border: 1px solid rgba(5,150,105,0.4);
        padding: 3px 8px;
        border-radius: 3px;
        margin-right: 10px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div><span class="desk-tag">LIVE DATA</span>'
    '<span class="desk-title">📊 Trading Analysis Desk</span></div>',
    unsafe_allow_html=True,
)
st.caption("Multi-agent equity analysis · Yahoo Finance + Claude")

st.divider()

# ───────────────────────────────────────────────────────────
# API KEY
# ───────────────────────────────────────────────────────────
st.session_state.api_key = st.text_input(
    "Anthropic API Key",
    type="password",
    value=st.session_state.api_key,
    placeholder="sk-ant-api03-...",
    help="Your key stays in this session only. Get one at console.anthropic.com.",
)

# ───────────────────────────────────────────────────────────
# TICKERS
# ───────────────────────────────────────────────────────────
st.markdown("### Watchlist")

with st.form("add_ticker", clear_on_submit=True):
    col1, col2 = st.columns([4, 1])
    with col1:
        new_t = st.text_input(
            "Add ticker (e.g. AAPL, NVDA, MSFT)",
            label_visibility="collapsed",
            placeholder="Add ticker (e.g. AAPL, NVDA, MSFT) — comma-separate to add several",
        )
    with col2:
        submit = st.form_submit_button("+ Add", use_container_width=True)
    if submit and new_t.strip():
        parts = [p.strip().upper() for p in re.split(r"[,\s]+", new_t) if p.strip()]
        for p in parts:
            if p not in st.session_state.tickers:
                st.session_state.tickers.append(p)
        st.rerun()

if st.session_state.tickers:
    chip_cols = st.columns(min(len(st.session_state.tickers), 6) or 1)
    for i, t in enumerate(st.session_state.tickers):
        with chip_cols[i % len(chip_cols)]:
            if st.button(f"× {t}", key=f"rm_{t}", help=f"Remove {t}", use_container_width=True):
                st.session_state.tickers.remove(t)
                st.rerun()
else:
    st.caption("No tickers added yet. Try AAPL, NVDA, MSFT, GOOGL.")

# ───────────────────────────────────────────────────────────
# RUN
# ───────────────────────────────────────────────────────────
run_label = "↺ Re-Analyse Watchlist" if st.session_state.results else "▶ Run Multi-Agent Analysis"
disabled = not st.session_state.tickers or not st.session_state.api_key
if st.button(run_label, type="primary", use_container_width=True, disabled=disabled):
    if not st.session_state.api_key:
        st.error("Enter your Anthropic API key first.")
    elif not st.session_state.tickers:
        st.error("Add at least one ticker.")
    else:
        st.session_state.results = {}
        progress = st.progress(0, text=f"Analysing 0 / {len(st.session_state.tickers)}...")
        status_area = st.status("Running multi-agent analysis...", expanded=True)
        done = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(analyze_ticker, st.session_state.api_key, t): t
                for t in st.session_state.tickers
            }
            for fut in as_completed(futures):
                t = futures[fut]
                ticker, mkt, ai, err = fut.result()
                st.session_state.results[ticker] = {"mkt": mkt, "ai": ai, "err": err}
                done += 1
                with status_area:
                    if err:
                        st.write(f"⚠ {ticker} — {err}")
                    else:
                        verdict = (ai or {}).get("verdict", "?")
                        confidence = (ai or {}).get("confidence", 0)
                        st.write(f"✓ {ticker} — {verdict} ({confidence}%)")
                progress.progress(done / len(st.session_state.tickers), text=f"Analysing {done} / {len(st.session_state.tickers)}...")
        st.session_state.last_run = datetime.now()
        status_area.update(label=f"✓ Done — {done} tickers analysed", state="complete")
        st.rerun()

if st.session_state.last_run:
    st.caption(f"// updated {st.session_state.last_run.strftime('%H:%M:%S')}")

st.divider()

# ───────────────────────────────────────────────────────────
# RESULTS
# ───────────────────────────────────────────────────────────
def signal_color(sig: str) -> str:
    sig = (sig or "").lower()
    if sig in ("bullish", "positive"):
        return GREEN
    if sig in ("bearish", "negative"):
        return RED
    return AMBER


def conf_color(c: int) -> str:
    if c >= 72:
        return GREEN
    if c >= 50:
        return AMBER
    return RED


def render_ticker_card(ticker: str, mkt: dict[str, Any] | None, ai: dict[str, Any] | None, err: str | None):
    with st.container(border=True):
        if err and not ai:
            st.error(f"⚠ {ticker}: {err}")
            return

        # Header row: ticker + price
        col_h1, col_h2 = st.columns([3, 2])
        with col_h1:
            badge = "🟢 LIVE" if mkt else "🟡 EST"
            st.markdown(f"### {ticker} <span style='font-size:11px;color:{GREY};font-weight:400'>{badge}</span>", unsafe_allow_html=True)
            if ai and ai.get("company"):
                st.caption(ai["company"])
        with col_h2:
            if mkt:
                change = mkt["price"] - mkt["prev"]
                change_pct = (change / mkt["prev"] * 100) if mkt["prev"] else 0
                price_color = GREEN if change >= 0 else RED
                arrow = "▲" if change >= 0 else "▼"
                st.markdown(
                    f"<div style='text-align:right;font-family:monospace'>"
                    f"<div style='font-size:22px;font-weight:600'>{mkt['currency']} {mkt['price']:,.2f}</div>"
                    f"<div style='color:{price_color};font-size:13px'>{arrow} {change:+.2f} ({change_pct:+.2f}%)</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

        # Verdict + confidence
        verdict = ai.get("verdict", "?") if ai else "?"
        confidence = int(ai.get("confidence", 0)) if ai else 0
        v_color = {"BUY": GREEN, "SELL": RED, "HOLD": AMBER}.get(verdict, GREY)
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:14px;padding:10px 0'>"
            f"<span style='background:{v_color}22;color:{v_color};border:1px solid {v_color}55;"
            f"padding:5px 14px;border-radius:4px;font-family:monospace;font-weight:600;letter-spacing:0.08em'>"
            f"{verdict}</span>"
            f"<span style='font-family:monospace;font-size:11px;color:{GREY};text-transform:uppercase;letter-spacing:0.08em'>"
            f"Confidence</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        st.progress(min(confidence, 100) / 100, text=f"{confidence}%")

        # Market stats
        if mkt:
            s1, s2, s3, s4 = st.columns(4)
            s1.metric("52W High", f"{mkt['high52']:,.2f}" if mkt.get("high52") else "—")
            s2.metric("52W Low", f"{mkt['low52']:,.2f}" if mkt.get("low52") else "—")
            s3.metric("Volume", fmt_num(mkt.get("vol")))
            s4.metric("Mkt Cap", fmt_num(mkt.get("mcap")))

        # Agents
        if ai and ai.get("agents"):
            agents = ai["agents"]
            agent_cols = st.columns(4)
            for col, key in zip(agent_cols, ["fundamental", "sentiment", "technical", "news"]):
                with col:
                    a = agents.get(key, {})
                    sig = a.get("signal", "—")
                    sc = signal_color(sig)
                    st.markdown(
                        f"<div style='font-family:monospace;font-size:10px;color:{GREY};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:2px'>"
                        f"{key}</div>"
                        f"<div style='color:{sc};font-weight:600;font-size:13px;margin-bottom:4px'>{sig}</div>"
                        f"<div style='font-size:11px;color:#4b5563;line-height:1.4'>{a.get('note','')}</div>",
                        unsafe_allow_html=True,
                    )

        # Bull / Bear
        if ai:
            bc1, bc2 = st.columns(2)
            with bc1:
                st.markdown(
                    f"<div style='border-left:3px solid {GREEN};padding-left:10px;'>"
                    f"<div style='color:{GREEN};font-family:monospace;font-size:11px;font-weight:600;letter-spacing:0.08em;margin-bottom:4px'>▲ BULL CASE</div>"
                    f"<div style='font-size:13px;line-height:1.55'>{ai.get('bull','')}</div></div>",
                    unsafe_allow_html=True,
                )
            with bc2:
                st.markdown(
                    f"<div style='border-left:3px solid {RED};padding-left:10px;'>"
                    f"<div style='color:{RED};font-family:monospace;font-size:11px;font-weight:600;letter-spacing:0.08em;margin-bottom:4px'>▼ BEAR CASE</div>"
                    f"<div style='font-size:13px;line-height:1.55'>{ai.get('bear','')}</div></div>",
                    unsafe_allow_html=True,
                )

            # Synthesis
            st.markdown(
                f"<div style='background:#f8f9fa;border-radius:6px;padding:12px;margin-top:14px'>"
                f"<div style='font-family:monospace;font-size:10px;color:{GREY};text-transform:uppercase;letter-spacing:0.1em;margin-bottom:6px'>"
                f"TRADER SYNTHESIS</div>"
                f"<div style='font-size:13px;line-height:1.6'>{ai.get('summary','')}</div></div>",
                unsafe_allow_html=True,
            )

            # Risk
            risk = ai.get("risk", "—")
            r_color = {"LOW": GREEN, "MEDIUM": AMBER, "HIGH": RED}.get(risk, GREY)
            st.markdown(
                f"<div style='display:flex;align-items:center;gap:10px;margin-top:10px;font-size:12px'>"
                f"<span style='font-family:monospace;color:{GREY};text-transform:uppercase;letter-spacing:0.08em;font-size:11px'>Risk</span>"
                f"<span style='background:{r_color}22;color:{r_color};border:1px solid {r_color}55;"
                f"padding:3px 10px;border-radius:4px;font-family:monospace;font-weight:600;font-size:11px;letter-spacing:0.08em'>{risk}</span>"
                f"<span style='color:#4b5563'>{ai.get('riskNote','')}</span></div>",
                unsafe_allow_html=True,
            )


if st.session_state.results:
    # Render in the order of the watchlist
    for t in st.session_state.tickers:
        if t in st.session_state.results:
            r = st.session_state.results[t]
            render_ticker_card(t, r["mkt"], r["ai"], r["err"])
elif st.session_state.tickers:
    st.info("Click **Run Multi-Agent Analysis** to fetch live data and Claude verdicts for your watchlist.")

st.divider()
st.caption(
    "Live data from Yahoo Finance · Multi-agent analysis via Claude Sonnet 4.6 · "
    "**Not financial advice — for educational use only.**"
)
