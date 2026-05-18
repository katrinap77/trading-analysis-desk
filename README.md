# Trading Analysis Desk

Multi-agent equity analyzer. Paste an Anthropic API key, build a watchlist of US tickers, and the app fetches live Yahoo Finance data and a structured BUY/SELL/HOLD verdict from Claude for each one — with separate fundamental, sentiment, technical, and news agent signals plus bull/bear cases and a risk rating.

## Two ways to run it

### 1. Browser version (`index.html` + `server.js`)

The original Node.js version. Run:

```bash
node server.js
```

Then open `http://localhost:3000`.

### 2. Streamlit version (`streamlit_app.py`)

Same flow, native Python — no Node server needed because Streamlit calls Yahoo and Anthropic directly.

Run locally:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run streamlit_app.py
```

Deploy to Streamlit Cloud at [share.streamlit.io](https://share.streamlit.io):

1. **Create app** → pick this repo
2. **Branch:** `main`, **Main file:** `streamlit_app.py`
3. Pick an unguessable subdomain if you want it semi-private
4. Deploy

The user pastes their own Anthropic key each session — no secrets to configure.

## How it works

1. For each ticker in the watchlist, fetch Yahoo Finance live data (price, change, 52-week high/low, volume, market cap, 5-day trend) via the public `query1`/`query2` chart endpoint
2. Send that context to Claude (sonnet-4-6) with a structured multi-agent prompt
3. Parse the returned JSON into:
   - **Verdict:** BUY / SELL / HOLD with confidence 0–100
   - **Four agents:** fundamental, sentiment, technical, news — each with a signal and one-sentence note
   - **Bull case** + **Bear case** (2 sentences each)
   - **Trader synthesis** (2–3 sentences)
   - **Risk rating:** LOW / MEDIUM / HIGH
4. Render each ticker as a card

Tickers analyse in parallel (5 workers) so a 10-ticker watchlist completes in ~10–15 seconds.

## Disclaimer

Educational only. Not financial advice. Yahoo Finance data is delayed.
