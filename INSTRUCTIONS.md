# Bargin — operating instructions

How to run it. For *why* it is built this way, see [README.md](README.md);
this file assumes you don't care right now and just want it working.

Bargin watches UK retailers for prices that shouldn't exist, and reads deal
communities for the ones it would never have found on its own. It runs on your
Mac, stores everything in one SQLite file, and talks to your phone through ntfy.

---

## 1. First run

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e .
cp .env.example .env
./.venv/bin/python -m bargin.cli init
```

Once installed, `bargin` is on the venv's path — the rest of this file writes
it as `bargin`, meaning `./.venv/bin/bargin`.

**Set one thing in `.env` before anything else:**

```
BARGIN_NTFY_TOPIC=some-hard-to-guess-string
```

Install **ntfy** on your iPhone, subscribe to that exact string, then:

```bash
bargin preflight --push
```

That sends a real notification. If it arrives, the part of this app that
matters works. Everything else is optional.

> The topic is the only secret in the ntfy model — anyone who knows the string
> can read your alerts. Treat it like a password.

---

## 2. Credentials

Every one of these is optional. The app degrades rather than breaks.

| Setting | Unlocks | Without it |
|---|---|---|
| `BARGIN_NTFY_TOPIC` | Alerts on your phone | Alerts still appear in the **Alerts** tab, just never buzz |
| `ANTHROPIC_API_KEY` | All ten Claude capabilities | Deterministic ladder still runs; repair, triage and lead-reading are off |
| `BARGIN_EBAY_CLIENT_ID` / `_SECRET` | eBay in search results | Search falls back to retailer search blocks |
| `BARGIN_REDDIT_CLIENT_ID` / `_SECRET` | Reddit deal feeds | RSS feeds still work; reddit feeds fail with a robots disallow |
| `BARGIN_ZENROWS_API_KEY` | Paid fetch for pages a WAF refuses | Those pages just fail |

**Where to get them**

- **Anthropic** — <https://console.anthropic.com>. You want an API key
  (`sk-ant-api…`), not an OAuth token (`sk-ant-oat…`). Both work, but they use
  different headers; Admin → Claude shows which one it detected.
- **eBay** — <https://developer.ebay.com> → My Account → Application Keys. Take
  the **Production** keyset, not Sandbox. It arrives *disabled* until you either
  comply with their marketplace account-deletion notification process or apply
  for an exemption. This app is read-only and stores no eBay user data, which is
  the basis for an exemption request.
- **Reddit** — <https://www.reddit.com/prefs/apps>, type **script**. Reddit
  forbids crawling in robots.txt and this app won't route around one, so the
  API is the only way in. Also set `BARGIN_REDDIT_USER_AGENT` to
  `macos:bargin:0.9 (by /u/yourname)` — reddit rate-limits generic agents harder.

After changing `.env`, restart anything that's running. Then:

```bash
bargin doctor      # is it configured?
bargin preflight   # does it actually work?
```

Those answer different questions. `doctor` checks that strings are present.
`preflight` spends real requests — a live Claude call whose *answer* is
checked, a real eBay search, every feed and tracked page fetched. Run
`preflight` before you trust it overnight.

---

## 3. Day to day

```bash
bargin serve
```

Then <http://127.0.0.1:8787>. On iPhone, open that URL in Safari and
**Share → Add to Home Screen** — it's already a standalone PWA.

| Tab | What it's for |
|---|---|
| **Watching** | Paste a product URL to track it. Suspected mispricings sit at the top. |
| **Search** | Find the same product at other retailers, so a real price move can be told apart from one shop's markup drifting. |
| **Alerts** | Every notification the app has produced, whether or not a push carried it. Unread mispricings sort to the top. |
| **Social** | Deal feeds. Add one, poll it, and turn a post into a tracked product. |
| **Activity** | The raw event log — every price change, including the ones that didn't warrant an alert. |
| **Cost** | What Claude and the paid fetch tier have actually cost. Sums over recorded calls, never estimates. |
| **Admin** | Configuration status, the Claude panel, the retailer registry, and a live API log. |

### Tracking a product

Paste the URL and press **Track**. It fetches the page once before committing,
so it can tell you if you're already watching that item somewhere else, and
offer to attach the new retailer to the existing product rather than creating a
duplicate.

### The fast lane

A product's row has a **glitch watch** toggle. On, it's checked every 5 minutes
instead of the usual 20-minute floor. Use it sparingly — one fast-lane target
costs about as many requests as forty normal ones.

### Deal feeds

Social → **Manage feeds**. Paste `r/UKDeals`, `UKDeals`, or a full RSS URL —
they all normalise. **Test it first** fetches the feed and reports what came
back without saving anything; use it to find URLs that work before committing.

Feeds poll themselves during the ordinary sweep, each on its own interval. A
post Claude triages as a lead pushes to your phone, capped at five per sweep.

---

## 4. Commands

| Command | Does |
|---|---|
| `bargin add <url>` | Track a product. `--target 220` to alert below a price. |
| `bargin run --force` | Check everything now |
| `bargin watch` | Run continuously in the foreground |
| `bargin list` | Dashboard in the terminal |
| `bargin serve` | Web UI + JSON API on :8787 |
| `bargin history <product_id>` | Price history, `--days 90` |
| `bargin doctor` | Is it configured? |
| `bargin preflight --push` | Does it actually work? |
| `bargin notify-test` | Send one test push |
| `bargin social` | Poll the deal feeds now. `--triage` to have Claude read them. |
| `bargin social-add r/UKDeals` | Add a feed |
| `bargin search <query>` | Search across sources |
| `bargin search-test <Retailer>` | Run one retailer's search block, showing every step |
| `bargin search-author <Retailer> "kettle" --save` | Have Claude write that retailer's search block from the live page, save it, and check it |
| `bargin glitch-test <url> --price 29` | Dry-run the glitch gate against a real page |
| `bargin ua <domain> <honest\|browser>` | Show or override per-domain User-Agent policy |

---

## 5. Running unattended

```bash
./deploy/install.sh
```

Installs a launchd agent that runs `bargin run` every 15 minutes. That single
sweep covers price checks, feed polling and alerts — nothing else to schedule.

```bash
launchctl kickstart -k gui/$(id -u)/com.bargin.watcher   # run now
tail -f data/watcher.log                                 # logs
launchctl bootout gui/$(id -u)/com.bargin.watcher        # remove
```

**launchd does not run while the Mac is asleep.** Either enable *Prevent
automatic sleeping on power adapter* in System Settings → Battery → Options, or
accept overnight gaps until this moves to an always-on host.

---

## 6. When something is wrong

Work down this list. Each step tells you which of the next ones to skip.

**Start here, always:**

```bash
bargin preflight
```

Every failure prints the fix underneath it.

### "The UI looks the same after I pulled"

The header shows `v0.9.0 · ui <hash>`. Compare that hash with the file on disk:

```bash
shasum -a 256 bargin/web/index.html | cut -c1-7
```

Same → you're seeing current code, look elsewhere. Different → either the
process is stale (restart `serve`) or you have a non-editable install. Check
`ui_path` in <http://127.0.0.1:8787/api/health>: if it isn't inside your
checkout, run `pip uninstall bargin && pip install -e .`.

### "A product stopped updating"

Admin → its row shows the full router trace. If Claude is configured, selector
repair runs automatically on the next sweep and writes a new selector. If it
isn't, that's the fix.

### "A URL won't track"

The error names the reason. The two that need different responses:

- **`robots.txt disallows`** — the site stated a policy. Nothing in this app
  overrides that, paid tier included. There is no fix.
- **`403 Forbidden`** — a bot wall. The app retries once as a browser and
  records that per domain. If it still fails, that domain is a candidate for
  the paid tier (`zenrows: true` in the registry).

### "Search returns nothing"

`bargin search-test Argos "kettle"` prints every step — the URL, the status,
how many rows matched, how many were dropped and why. That tells you which step
is wrong. The search blocks shipped in `registry/retailers.yaml` are commented
out and **unverified**; `bargin search-author <Retailer> "kettle" --save` writes
a real one from the live page.

### "Claude isn't doing anything"

Admin → Claude shows the credential *shape* (never the secret), the model, and
every recent call with what was sent and what came back. Each of the ten
capabilities has a **Try** button that runs the real function against a sample
with a checkable right answer. A full sweep of all ten costs about 4p.

### "I'm not getting alerts"

Check the Alerts tab first. If they're there but your phone is quiet, it's the
push channel: `bargin preflight --push`. If the tab is empty too, nothing has
happened worth alerting about — check Activity for events that were recorded
but suppressed by the alert policy.

---

## 7. What it costs

Claude is the only thing metered by default, and the Cost tab shows real
recorded spend rather than an estimate.

- A daily cap of **$1.00** (`BARGIN_LLM_DAILY_BUDGET_USD`) is checked before
  any tokens are spent. Set it to 0 to remove it.
- All ten capabilities on one product is about **$0.07** on Sonnet 5.
- Steady state is far lower — the expensive capabilities fire only when
  something breaks, and they amortise over a retailer rather than a product.

The cap exists because every expensive capability runs on a *failure* path. A
retailer that changes its markup and starts failing every check is exactly the
situation where selector repair would be retried, unattended, all night.

If you enable the paid fetch tier, it has its own cap
(`BARGIN_ZENROWS_DAILY_MAX_REQUESTS`, default 200) and its own line on the Cost
page.

---

## 8. Where things live

| | |
|---|---|
| Database | `data/bargin.db` (SQLite, one file, safe to copy) |
| Settings | `.env` — never committed |
| Retailers | `registry/retailers.yaml` (curated) and `registry/local.yaml` (written by the Admin form) |
| Logs | `data/watcher.log` when running under launchd |

Back up `data/bargin.db` and `.env` and you have backed up everything.
