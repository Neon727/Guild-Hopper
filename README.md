# guild_hopper

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
2. **Capture live** — while scanning runs, a `on_message` listener also grabs
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