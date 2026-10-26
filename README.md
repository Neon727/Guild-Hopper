# Guild Hopper

A self-bot that scans the servers you're in for Discord invite links, joins the
servers those links point to, leaves the old server, and repeats. It keeps a
running count of how many servers it has successfully joined and survives
restarts via a JSON state file.

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

## What it actually does

The loop has four stages:

1. **Scan** — walk every text channel in the current server where the account
   has `View Channel` and `Read Message History`, pull the last
   `HISTORY_LIMIT` messages from each, and regex out any
   `discord.gg/<code>` or `discord.com/invite/<code>` links.
2. **Capture live** — while scanning runs, an `on_message` listener also grabs
   invite codes the instant they're posted, before anyone can delete them.
   These are queued separately and drained first on the next loop.
3. **Join** — for each candidate, fetch the invite, validate it (not expired,
   not the current server, not a server already visited), wait a randomized
   delay, then accept it.
4. **Leave & repeat** — after the join succeeds, leave the previous server and
   make the new one current. Increment `joined_count`. Loop.

If a server yields no usable invites, the script falls back to a list of
permanent "hub" invites you supply. If the hubs are also exhausted, it idles
for 5 minutes and retries the current server.

### What it does *not* do

- It does not solve CAPTCHAs. It halts when one appears.
- It does not read DMs or channels the account can't already see.
- It does not scrape users, messages, or any data beyond invite codes.
- It does not bypass the 100-guild cap — it leaves the current server before
  joining when at the limit.

---

## Files

| File | Purpose |
|---|---|
| `guild_hopper.py` | The script. |
| `hopper_state.json` | Created on first run. Stores `joined_count`, the list of guild IDs already visited, the set of invite codes already seen, and any pending live-captured invites. Delete this to reset. |

`hopper_state.json` is human-readable and safe to edit by hand if you want to
clear a specific guild or invite from the "seen" lists.

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

Either edit the `TOKEN =` line near the top of the script, or set an
environment variable. **The environment variable is safer** — a hardcoded
token can leak into backups, screenshots, and chat history.

```bash
# macOS / Linux
export DISCORD_TOKEN="your_token_here"

# Windows PowerShell (current session only)
$env:DISCORD_TOKEN = "your_token_here"
```

> On Windows, `setx` only affects *new* terminals. If you set the variable
> with `setx`, close and reopen your terminal, or the script will keep reading
> the old (or empty) value.

### 3. Add hub fallbacks

Open `guild_hopper.py` and fill in `FALLBACK_HUBS` with **permanent, never-expiring**
invites to large public servers that regularly contain invite links:

```python
FALLBACK_HUBS = [
    "discord.gg/yourHubInvite1",
    "discord.gg/yourHubInvite2",
]
```

If you skip this step, the script will idle forever when it lands in a server
that has no invites in its history.

### 4. Make sure you're already in at least one server

The script picks a starting server from `hopper_state.json` if one is saved,
otherwise from `client.guilds` (whichever the library returns first). If you're
in zero servers, it exits immediately.

### 5. Run it

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
| `JOIN_DELAY` | `(30, 90)` | Random seconds before each join attempt. **This is your main lever against being flagged.** Lower = faster ban. |
| `HUB_DELAY` | `(30, 90)` | Random seconds before each hub-fallback join. |
| `PENDING_CAP` | `200` | Maximum live-captured invites held in memory. Prevents runaway growth in very active servers. |
| `FALLBACK_HUBS` | `[]` | Permanent invites tried when the current server yields nothing usable. |

`HISTORY_LIMIT` counts per channel. On a 200-channel server with limit 500,
expect a lot of paginated requests. The library will sleep internally on rate
limits.

`JOIN_DELAY` is set fairly conservatively by default. You can lower it, but
every second you shave off increases the chance that the next action is a
forced re-verification (which kills your token) rather than a successful join.

---

## Architecture & Design Notes

A few deliberate choices were made to prevent the bot from crashing or getting instantly banned:

- **`JOIN_DELAY` is not a performance knob.** It exists specifically to mimic human pacing. Lowering it to 1-2 seconds is the fastest way to trigger a CAPTCHA or get the token revoked.
- **The 100-Guild Cap is handled via "Leave-First".** If the bot is in 100 servers, it leaves the current one *before* trying to join the new one. If the join then fails, you end up guildless until the hub fallback saves you.
- **The CAPTCHA path halts the loop.** If Discord returns a `400` with code `-1`, the script stops. It does not spam retries. Retrying through a CAPTCHA is what escalates a soft flag to a permanent ban.
- **State is shared on the Client.** Because `on_message` (live capture) and `scan_guild` (history scanning) both write to `seen_invites`, they share the same in-memory state (`client.state`, `client.seen_invites`). This prevents race conditions where one overwrites the other.
- **Deleted messages are cached.** `on_message` grabs invite codes the millisecond they are posted and saves them to `pending_invites`. If a user deletes their message immediately after posting, the bot still has the code.
- **State is saved synchronously.** `client.persist()` writes to disk immediately after any state change. If the script crashes or the token dies, you don't lose your progress or your cached invites.

---

## Reading the output

```
Logged in as yourname (1234567890)
Joined count so far: 0

=== Scanning: Some Server (1111111111) ===
  [cache] captured abc123 from #general
  [cache] captured xyz789 from #advertise
  [cache] 2 pending invite(s)
  5 candidate(s)
  [✓] joined #1: Another Server (2222222222)
  [leave] left Some Server
```

Key lines:

- `[cache] captured <code>` — an invite was seen in a live message and queued
  before anyone could delete it.
- `[history] #channel: ...` — a channel's history couldn't be read (usually
  permissions changed since you joined).
- `[cap] at 100 guilds` — the script is at the guild limit and will leave the
  current server before attempting the join.
- `[hub] trying <code>` — the hub fallback is being used.
- `[join] CAPTCHA challenge detected` — the script **stops**. This is not a bug;
  it's the script refusing to hammer Discord after being told to prove it's
  human. To continue you'd have to solve the CAPTCHA manually, which defeats
  the purpose.

---

## Troubleshooting

### `LoginFailure: Improper token has been passed` / `401 Unauthorized`

The token is dead or not being read. Check in this order:

1. **Is it a user token or a bot token?** Bot tokens come from the Developer
   Portal and will not work. User tokens come from the Discord *client* —
   DevTools → Network → `authorization` header, or Application →
   Local Storage → `token`.
2. **Did you re-verify your email recently?** Re-verification invalidates every
   existing token. Re-extract from a fresh, logged-in browser session.
3. **Are there hidden characters?** Check with:
   ```python
   print(repr(token))
   ```
   Look for `\n`, leading spaces, or zero-width characters. `client.run()`
   strips whitespace but not zero-width characters.
4. **Are both `discord.py` and `discord.py-self` installed?** See the
   "Requirements" section above.
5. **Is your terminal reading the value you think it is?**
   ```powershell
   echo $env:DISCORD_TOKEN
   ```

### `NameError: name 'main' is not defined`

The `def main():` block is missing from the bottom of the file. Re-paste the
entrypoint section.

### `TypeError: scan_guild() takes 2 positional arguments but 3 were given`

The function signature and the call site are out of sync. Either revert
`scan_guild` to 3 arguments, or fully apply the caching patch (which changes
both the signature and the call site). Mixing half the patch in is the usual
cause.

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

### The script joins a few servers, then gets stuck

Likely causes:

- It hit the guild cap and every leave-first attempt is failing.
- Every candidate it finds is expired or already visited — check
  `seen_invites` and `joined_guild_ids` in `hopper_state.json`.
- It's being rate-limited hard. The library sleeps on 429 responses, but if the
  console goes quiet for several minutes at a time that's what's happening.

### The console is silent for a long time

Normal during history scans on large servers, and normal during `JOIN_DELAY`
waits. If it's silent for more than 10 minutes with no output at all, kill it
and check the log — the library usually prints rate-limit notices.

---

## Resetting

Delete `hopper_state.json` to reset `joined_count`, clear the visited-guild
list, and re-scan every server from scratch. The next run starts from whichever
guild the library returns first.

---

## Legal-ish notes

- The token in `guild_hopper.py` is a credential. Keep the file out of version
  control. If it's ever accidentally shared, change your Discord password to
  invalidate it.
- Reading `Read Message History` in a server is not the same as *permission* to
  scrape it for invite links. Server owners can and do ban accounts for it.
- The "100 guilds" cap is a hard limit on free accounts. The script respects
  it by leaving before joining — but if the join then fails, you're guildless
  until the hub fallback (or a manual join) gets you somewhere new.

If you want the invite-discovery half without the join automation, the
`scan_guild` + `on_message` logic works as a standalone harvester — it writes
invite codes to a queue, and you join manually. That removes the highest-risk
part of the loop. It's the version that doesn't keep killing your token.