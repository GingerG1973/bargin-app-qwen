# Bargin — UK retailer price watcher

Bargin watches UK retailers for prices that shouldn't exist, and reads deal
communities for the ones it would never have found on its own. It runs on your
Mac, stores everything in one SQLite file, and talks to your phone through ntfy.

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e .
cp .env.example .env
./.venv/bin/python -m bargin.cli init
bargin serve
```

Then open <http://127.0.0.1:8500>.

See [INSTRUCTIONS.md](INSTRUCTIONS.md) for full documentation.

## Features

- **Price tracking** — Monitor product pages across UK retailers
- **Glitch detection** — Alert on mispricings that shouldn't exist
- **Deal feeds** — Poll RSS and Reddit for community-found deals
- **Claude integration** — AI-powered selector repair, triage, and lead reading
- **Web UI** — PWA-ready interface with tabs for Watching, Search, Alerts, Social, Activity, Cost, Admin
- **SQLite storage** — Everything in one file, safe to copy

## Configuration

All settings in `.env`:

Product prices use **GBP (£)**, UK dates use `en-GB` with the
`Europe/London` timezone, and eBay searches use the `EBAY_GB` marketplace.
Anthropic usage is billed and displayed separately in **USD ($)**.

| Setting | Purpose |
|---|---|
| `BARGIN_NTFY_TOPIC` | Push notifications to your phone |
| `ANTHROPIC_API_KEY` | Claude capabilities (selector repair, triage, etc.) |
| `BARGIN_EBAY_CLIENT_ID` / `_SECRET` | eBay search integration |
| `BARGIN_REDDIT_CLIENT_ID` / `_SECRET` | Reddit deal feeds |
| `BARGIN_ZENROWS_API_KEY` | Paid fetch tier for WAF-blocked pages |

## Commands

| Command | Does |
|---|---|
| `bargin add <url>` | Track a product |
| `bargin run --force` | Check everything now |
| `bargin watch` | Run continuously in the foreground |
| `bargin list` | Dashboard in the terminal |
| `bargin serve` | Web UI + JSON API on :8500 |
| `bargin doctor` | Is it configured? |
| `bargin preflight --push` | Does it actually work? |
| `bargin zenrows-status` | Show today's paid-fetch usage |
| `bargin llm-status` | Show today's Claude budget usage |

## Architecture

- **Database**: `data/bargin.db` (SQLite)
- **Retailers**: `registry/retailers.yaml` (curated) and `registry/local.yaml` (user-written)
- **Logs**: `data/watcher.log`

## License

MIT
