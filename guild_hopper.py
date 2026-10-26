import asyncio
import json
import os
import random
import re
from pathlib import Path

import discord

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

# Paste your token directly here OR set the DISCORD_TOKEN env var.
# Keep this file private — it contains a live credential.
TOKEN = os.getenv("DISCORD_TOKEN", "PASTE_YOUR_TOKEN_HERE")

STATE_FILE = Path("hopper_state.json")

INVITE_RE = re.compile(
    r"(?:https?://)?(?:discord\.gg|discord(?:app)?\.com/invite)/([A-Za-z0-9-]+)"
)

HISTORY_LIMIT = 500

CHANNEL_DELAY = (1.5, 4.0)
JOIN_DELAY    = (30, 90)     # bumped from 20-60 to reduce flag risk
HUB_DELAY     = (30, 90)

PENDING_CAP = 200            # max live-captured invites held in memory

FALLBACK_HUBS = [
    # "discord.gg/exampleHub1",
    # "discord.gg/exampleHub2",
]

# ─────────────────────────────────────────────────────────────────────────────
# Persistent state
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_STATE = {
    "joined_count": 0,
    "joined_guild_ids": [],
    "seen_invites": [],
    "pending_invites": [],     # Live-captured from on_message
    "deferred_invites": [],    # NEW: Found in history, but skipped
    "current_guild_id": None,
}

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for key, value in DEFAULT_STATE.items():
                data.setdefault(key, value)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_STATE)

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────────────
# Invite discovery
# ─────────────────────────────────────────────────────────────────────────────

async def scan_guild(client, guild):
    """Walk readable channels in *guild* and return unseen invite codes."""
    candidates = []

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history):
            continue

        try:
            async for msg in channel.history(limit=HISTORY_LIMIT):
                for code in INVITE_RE.findall(msg.content or ""):
                    if code in client.seen_invites:
                        continue
                    client.seen_invites.add(code)
                    candidates.append(code)
        except discord.Forbidden:
            continue
        except discord.HTTPException as exc:
            print(f"  [history] #{channel.name}: {exc}")
        await asyncio.sleep(random.uniform(*CHANNEL_DELAY))

    client.persist()
    return candidates

async def resolve_invite(client, code):
    try:
        return await client.fetch_invite(code, with_expiration=True)
    except (discord.NotFound, discord.HTTPException):
        return None

async def try_join(client, code):
    """Validate and accept an invite. Returns the Guild, or None on failure."""
    try:
        invite = await client.fetch_invite(
            code, with_counts=True, with_expiration=True
        )
    except (discord.NotFound, discord.HTTPException):
        return None

    if invite is None or invite.guild is None:
        return None

    if invite.expires_at and invite.expires_at < discord.utils.utcnow():
        return None

    try:
        # discord.py-self exposes Invite.accept(); if your version lacks it,
        # replace this line with: await client.accept_invite(code)
        await invite.accept()
        return invite.guild
    except discord.HTTPException as exc:
        # 400 + code -1 is Discord's "CAPTCHA required" signal.
        if getattr(exc, "status", None) == 400 and getattr(exc, "code", None) == -1:
            print("  [join] CAPTCHA challenge detected — halting join attempts.")
            raise RuntimeError("captcha") from exc
        print(f"  [join] failed for {code}: {exc}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Hub fallback
# ─────────────────────────────────────────────────────────────────────────────

async def join_from_hubs(client):
    random.shuffle(FALLBACK_HUBS)
    for raw in FALLBACK_HUBS:
        match = INVITE_RE.search(raw)
        code = match.group(1) if match else raw.strip().rstrip("/").split("/")[-1]
        if not code or code in client.seen_invites:
            continue

        print(f"  [hub] trying {code}")
        await asyncio.sleep(random.uniform(*HUB_DELAY))
        guild = await try_join(client, code)
        if guild is not None and guild.id not in client.state["joined_guild_ids"]:
            return guild
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

async def run_hopper(client):
    state = client.state

    current = None
    if state.get("current_guild_id"):
        current = client.get_guild(state["current_guild_id"])
    if current is None:
        current = next(iter(client.guilds), None)

    if current is None:
        print("No current guild. Join one manually or populate FALLBACK_HUBS.")
        return

    while True:
        state["current_guild_id"] = current.id
        client.persist()

        # ── 1. Gather Candidates from CURRENT server ──────────────────────
        candidates = []
        
        # Live-captured from on_message (prioritized)
        if client.pending_invites:
            print(f"  [cache] {len(client.pending_invites)} pending invite(s)")
            candidates.extend(client.pending_invites)
            client.pending_invites.clear()
            client.persist()

        # History scan of the current server
        print(f"\n=== Scanning: {current.name} ({current.id}) ===")
        candidates.extend(await scan_guild(client, current))
        print(f"  {len(candidates)} total candidate(s) found in this server")

        # ── 2. Try to join from the current server ────────────────────────
        joined = None
        
        for i, code in enumerate(candidates):
            invite = await resolve_invite(client, code)
            if invite is None or invite.guild is None:
                continue
            if invite.guild.id == current.id:
                continue
            if invite.guild.id in state["joined_guild_ids"]:
                continue

            # Respect the 100-guild cap
            if len(client.guilds) >= 100:
                print("  [cap] at 100 guilds — leaving current guild first")
                try:
                    await current.leave()
                except discord.HTTPException as exc:
                    print(f"  [leave] failed: {exc}")
                current = None

            await asyncio.sleep(random.uniform(*JOIN_DELAY))
            try:
                joined = await try_join(client, code)
            except RuntimeError as exc:
                if str(exc) == "captcha":
                    print("  [stop] CAPTCHA required. Halting join loop.")
                    return
                raise

            if joined is not None:
                # SUCCESS! Save all remaining untried links to the backlog
                leftover = candidates[i+1:]
                if leftover:
                    print(f"  [defer] saving {len(leftover)} untried invite(s) to backlog")
                    client.deferred_invites.extend(leftover)
                    client.persist()
                break # Break out of the candidates loop

        # ── 3. Fallbacks (Only if the current server was a dead end) ──────
        if joined is None:
            print("  no usable invites in this server.")
            
            # Fallback A: Check the deferred backlog
            if client.deferred_invites:
                print(f"  [backlog] checking {len(client.deferred_invites)} deferred invite(s)")
                while client.deferred_invites and joined is None:
                    code = client.deferred_invites.pop(0)
                    client.persist() # Save immediately in case it crashes mid-join
                    
                    invite = await resolve_invite(client, code)
                    if invite is None or invite.guild is None:
                        continue
                    if invite.guild.id in state["joined_guild_ids"]:
                        continue

                    await asyncio.sleep(random.uniform(*JOIN_DELAY))
                    try:
                        joined = await try_join(client, code)
                    except RuntimeError as exc:
                        if str(exc) == "captcha":
                            print("  [stop] CAPTCHA required. Halting join loop.")
                            return
                        raise
            
            # Fallback B: Hubs
            if joined is None:
                print("  backlog empty/failed — falling back to hubs")
                joined = await join_from_hubs(client)

        if joined is None:
            print("  hubs exhausted — idling 5 minutes")
            await asyncio.sleep(300)
            continue

        # ── 4. Bookkeeping & Leave Old Server ─────────────────────────────
        state["joined_count"] += 1
        if joined.id not in state["joined_guild_ids"]:
            state["joined_guild_ids"].append(joined.id)
        state["current_guild_id"] = joined.id
        client.persist()
        print(f"  [✓] joined #{state['joined_count']}: {joined.name} ({joined.id})")

        await asyncio.sleep(5) # Let cache populate

        if current is not None and current.id != joined.id:
            try:
                await current.leave()
                print(f"  [leave] left {current.name}")
            except discord.HTTPException as exc:
                print(f"  [leave] failed: {exc}")

        current = joined

# ─────────────────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────────────────

class HopperClient(discord.Client):
    async def on_ready(self):
        self.state = load_state()
        self.seen_invites = set(self.state["seen_invites"])
        self.pending_invites = list(self.state.get("pending_invites", []))
        self.deferred_invites = list(self.state.get("deferred_invites", [])) # NEW
        print(f"Logged in as {self.user} ({self.user.id})")
        print(f"Joined count so far: {self.state['joined_count']}")
        print(f"Backlog: {len(self.deferred_invites)} deferred, {len(self.pending_invites)} pending")
        try:
            await run_hopper(self)
        except KeyboardInterrupt:
            print("Interrupted by user.")
        finally:
            await self.close()

    def persist(self):
        self.state["seen_invites"] = list(self.seen_invites)
        self.state["pending_invites"] = list(self.pending_invites)
        self.state["deferred_invites"] = list(self.deferred_invites) # NEW
        save_state(self.state)

    async def on_message(self, message):
        # Capture invite codes the moment they're posted, before deletion.
        if not message.guild:
            return
        if self.user and message.author.id == self.user.id:
            return

        codes = INVITE_RE.findall(message.content or "")
        if not codes:
            return

        new = False
        for code in codes:
            if code in self.seen_invites:
                continue
            if len(self.pending_invites) >= PENDING_CAP:
                break
            self.seen_invites.add(code)
            self.pending_invites.append(code)
            new = True
            print(f"  [cache] captured {code} from #{message.channel.name}")

        if new:
            self.persist()

# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not TOKEN or TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise SystemExit("Set DISCORD_TOKEN or paste your token into TOKEN.")

    client = HopperClient()
    client.run(TOKEN)

if __name__ == "__main__":
    main()