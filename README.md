# Guild Hopper

A self-bot that scans the servers you're in for Discord invite links, joins the
servers those links point to, and repeats. It keeps a running count of how many
servers it has successfully joined, maintains a safe buffer of servers, and
survives restarts via a JSON state file.

> **Read the disclaimer before you run this.** It is not a toy.

---

## ⚠️ Disclaimer

**This script violates Discord's Terms of Service.**

It operates as a *self-bot* — it authenticates as a normal user account and
automates actions (joining servers, reading message history) that Discord
intends to be performed manually. Discord's policy explicitly prohibits
automating user accounts outside the official bot API.

Concrete consequences you should expect:

- **Account termination.** Discord can permanently disable the account whose
  token is used. There is no appeal process that reliably reverses this for
  self-bot use.
- **Forced email re-verification.** Discord frequently responds to scripted
  logins by forcing email re-verification. This **invalidates every existing
  session token**, including the one the script is using, so the script dies
  mid-run and you have to re-extract the token. This is a warning shot, not
  the final action.
- **Phone locks.** After enough flags, joining new servers requires a verified
  phone number.
- **CAPTCHA walls.** Repeated joins trigger hCaptcha challenges. This script
  detects them and halts; it does not attempt to bypass them. Bypassing them
  with a paid solver service is an additional ToS violation.
- **Server-level bans.** Server owners generally dislike having their message
  history scraped for invites. You can be banned from the servers you're using
  as sources, which cuts off the pipeline.
- **Token theft risk.** The token in this file (or in your environment) is a
  master credential. Anyone who obtains it has full control of the account —
  DMs, servers, billing, everything. A leaked token cannot be "scoped down";
  it must be invalidated by changing the password.

**Do not run this on an account you care about.** If you want to see how the
mechanics work, run it on a throwaway account you're prepared to lose, or
better, adapt the *harvester* portion (invite discovery + manual join queue)
into an official bot that runs in servers you administer.

The author of this README is not responsible for accounts banned, servers
lost, or data exposed as a result of running this script.

---

## How it works (The 5 Stages)

1. **Scan** — Walk every text channel in the current server where the account
   has `View Channel` and `Read Message History`, pull the last
   `HISTORY_LIMIT` messages from each, and regex out any
   `discord.gg/<code>` or `discord.com/invite/<code>` links.
2. **Capture live** — While scanning runs, an `on_message` listener also grabs
   invite codes the instant they're posted, before anyone can delete them.
   These are queued separately and checked first.
3. **Join** — For each candidate, fetch the invite, validate it, wait a
   randomized delay, then accept it. 
4. **Defer** — If a server has multiple links, the bot only joins the first one.
   It saves all the leftover links to a **Deferred Backlog** so it can use them
   later if it lands in a dead-end server.
5. **Safe Queue** — Instead of instantly leaving servers (which flags the
   account), the bot stays in them. Once it hits `MAX_SERVERS_TO_KEEP`, it
   leaves the *oldest* server to make room for the new one.

### Hub Detection & Learning
If a server yields a massive amount of invites (e.g., 5+ invites spanning
multiple days), the bot flags it as a "Hub". It then tries to **generate its own
permanent, infinite-use invite** to that server. It saves this invite to a
`known_hubs` list. If the bot ever runs out of links, it uses these
self-generated hub invites to safely teleport back to a known active server.

### What it does *not* do

- It does not solve CAPTCHAs. It halts when one appears.
- It does not read DMs or channels the account can't already see.
- It does not scrape users, messages, or any data beyond invite codes.
- It does not bypass the 100-guild hard cap.

---

## Files

| File | Purpose |
|---|---|
| `guild_hopper.py` | The script. |
| `hopper_state.json` | Created on first run. Stores `joined_count`, `join_queue`, `seen_invites`, `deferred_invites`, `known_hubs`, and more. Delete this to reset completely. |

`hopper_state.json` is human-readable and safe to edit by hand if you want to
clear a specific guild or invite from the lists.

---

## Requirements

- **Python 3.10+**
- **[`discord.py-self`](https://pypi.org/project/discord.py-self/)** — a fork of
  `discord.py` that supports user-account login. **Do not install plain
  `discord.py` alongside it**; they share the `discord` package name and will
  break authentication with a `401 Unauthorized`.
- A Discord **user token** (not a bot token).

### Verify you don't have a conflicting install

```bash
pip list | findstr discord    # Windows
pip list | grep discord       # macOS/Linux
```

The only line that should appear is `discord.py-self`. If `discord.py` is
present, uninstall it first:

```bash
pip uninstall discord.py
pip install -U --force-reinstall discord.py-self
```

---

## Setup

### 1. Install the library

```bash
python -m pip install -U discord.py-self
```

### 2. Provide your token

Edit the `TOKEN =` line near the top of the script. 

> **Security Note:** A hardcoded token can leak into backups and screenshots.
> The safest method is to use an environment variable:
> 
> ```bash
> # macOS / Linux
> export DISCORD_TOKEN="your_token_here"
> 
> # Windows PowerShell
> $env:DISCORD_TOKEN = "your_token_here"
> ```

### 3. Add starter hubs

Open `guild_hopper.py` and fill in `FALLBACK_HUBS` with **permanent, never-expiring**
invites to large public servers. The bot will learn its own hubs over time, but
you need at least one safe starting point in case it lands in an empty server.

```python
FALLBACK_HUBS = [
    "discord.gg/yourHubInvite1",
    "discord.gg/yourHubInvite2",
]
```

### 4. Run it

```bash
python guild_hopper.py
```

---

## Configuration

All the tunable values are at the top of the file.

| Name | Default | Meaning |
|---|---|---|
| `HISTORY_LIMIT` | `500` | Messages to read per channel. Higher = more invites found, more API calls, longer scans. |
| `CHANNEL_DELAY` | `(1.5, 4.0)` | Random seconds between channels while scanning. |
| `JOIN_DELAY` | `(30, 90)` | Random seconds before each join attempt. **Lower = faster ban.** |
| `HUB_DELAY` | `(30, 90)` | Random seconds before each hub-fallback join. |
| `PENDING_CAP` | `200` | Max live-captured invites held in memory. |
| `MAX_SERVERS_TO_KEEP` | `50` | How many servers to stay in before leaving the oldest one. |
| `FALLBACK_HUBS` | `[]` | Permanent invites tried when the current server yields nothing usable. |

### Why `MAX_SERVERS_TO_KEEP` matters

If a bot joins 50 servers and immediately leaves them, Discord's anti-spam
systems will flag the account within minutes. By keeping the bot in 50 servers,
it mimics a normal user who just hoards servers. When it joins the 51st server,
it quietly leaves the very first one it ever joined.

---

## Reading the output

```
=== Scanning: Some Server (1111111111) ===
  [cache] captured abc123 from #general
  [hub-detect] This server looks like a hub! Generating a permanent invite...
  [hub-detect] Saved hub with code: xyz789
  5 candidate(s) found in this server
  [defer] saving 4 untried invite(s) to backlog
  [✓] joined #1: Another Server (2222222222)
  [queue] Currently in 1 / 50 kept servers
```

Key lines:

- `[cache] captured <code>` — An invite was seen in a live message and queued
  before anyone could delete it.
- `[hub-detect]` — The bot realized this is a high-traffic server and generated
  its own permanent invite to save as an emergency fallback.
- `[defer]` — The bot found multiple links but only used one. The rest are saved
  for later.
- `[queue]` — Shows how many servers the bot is currently sitting in.
- `[leave] left oldest server` — Triggers when the bot hits 51 servers.
- `[join] CAPTCHA challenge detected` — The script **stops**. This is not a bug;
  it's refusing to hammer Discord after being told to prove it's human.

---

## Troubleshooting

### `LoginFailure: Improper token has been passed` / `401 Unauthorized`

1. **Is it a user token or a bot token?** Bot tokens will not work. User tokens
   come from DevTools → Network → `authorization`.
2. **Did you re-verify your email recently?** Re-verification invalidates every
   existing token. Re-extract from a fresh browser session.
3. **Are both `discord.py` and `discord.py-self` installed?** See the
   "Requirements" section above.

### `TypeError: scan_guild() takes 2 positional arguments but 3 were given`

The function signature and the call site are out of sync. Ensure you are using
the latest version of the script where `scan_guild(client, guild)` expects only
two arguments.

### `AttributeError: 'Invite' object has no attribute 'accept'`

Different versions of `discord.py-self` expose invite acceptance differently.
Try replacing:

```python
await invite.accept()
```

with:

```python
await client.accept_invite(code)
```

### The console is silent for a long time

Normal during history scans on large servers, and normal during `JOIN_DELAY`
waits. If it's silent for more than 10 minutes with no output at all, kill it
and check the log — the library usually prints rate-limit notices.

---

## Resetting

Delete `hopper_state.json` to reset `joined_count`, clear the queue, wipe the
learned hubs, and re-scan every server from scratch. The next run starts from
whichever guild the library returns first.

---

## Legal-ish notes

- The token in `guild_hopper.py` is a credential. Keep the file out of version
  control. If it's ever accidentally shared, change your Discord password to
  invalidate it.
- Reading `Read Message History` in a server is not the same as *permission* to
  scrape it for invite links. Server owners can and do ban accounts for it.
- The bot generates its own invites to servers it deems "Hubs". If you do not
  want the bot creating invites on your behalf, remove the
  `generate_permanent_invite` function.