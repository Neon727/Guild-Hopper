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
MAX_SERVERS_TO_KEEP = 50   # Stay in this many servers before leaving the oldest
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
    "join_queue": [],          # NEW: Tracks the order of joined servers
    "seen_invites": [],
    "pending_invites": [],
    "deferred_invites": [],
    "known_hubs": [],
    "last_join_invite": None,
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
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def generate_permanent_invite(client, guild):
    """Tries to create an infinite, permanent invite for the server."""
    for channel in guild.text_channels:
        # We need Create Invite permission
        perms = channel.permissions_for(guild.me)
        if not perms.create_instant_invite:
            continue
            
        try:
            # max_age=0 means never expires. max_uses=0 means infinite uses.
            invite = await channel.create_invite(max_age=0, max_uses=0, unique=False, reason="Hub hopper backup")
            return invite.code
        except discord.Forbidden:
            continue
        except discord.HTTPException as exc:
            print(f"  [invite] failed to create in #{channel.name}: {exc}")
            
    return None # Couldn't create one

# ─────────────────────────────────────────────────────────────────────────────
# Invite discovery
# ─────────────────────────────────────────────────────────────────────────────

async def scan_guild(client, guild):
    candidates = []
    invite_dates = set() # NEW: To track unique days

    for channel in guild.text_channels:
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.read_message_history):
            continue

        try:
            async for msg in channel.history(limit=HISTORY_LIMIT):
                codes = INVITE_RE.findall(msg.content or "")
                if codes:
                    invite_dates.add(msg.created_at.date()) # Record the day
                    
                for code in codes:
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
    return candidates, len(invite_dates)

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
    # 1. Combine learned hubs and static fallback hubs
    all_hubs = list(client.state.get("known_hubs", []))
    
    # Add static hubs to the list if they aren't already there
    for raw in FALLBACK_HUBS:
        match = INVITE_RE.search(raw)
        code = match.group(1) if match else raw.strip().rstrip("/").split("/")[-1]
        if code and not any(h["code"] == code for h in all_hubs):
            all_hubs.append({"code": code, "id": None, "name": "Static Fallback"})

    random.shuffle(all_hubs)

    for hub in all_hubs:
        code = hub["code"]
        if hub["id"] and hub["id"] in client.state["joined_guild_ids"]:
            continue # Already visited this learned hub

        invite = await resolve_invite(client, code)
        if invite is None or invite.guild is None:
            continue
            
        if invite.guild.id in client.state["joined_guild_ids"]:
            continue

        print(f"  [hub] trying {code} ({hub.get('name')})")
        await asyncio.sleep(random.uniform(*HUB_DELAY))
        guild = await try_join(client, code)
        if guild is not None:
            client.state["last_join_invite"] = code # Save the code used to join
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

        candidates = []
        if client.pending_invites:
            candidates.extend(client.pending_invites)
            client.pending_invites.clear()
            client.persist()

        print(f"\n=== Scanning: {current.name} ({current.id}) ===")
        history_candidates, days_spanned = await scan_guild(client, current)
        candidates.extend(history_candidates)
        
        # ── HUB DETECTION LOGIC ──
        # If we found >= 5 invites spread across >= 2 different days, it's a hub!
                # ── HUB DETECTION LOGIC ──
        if len(history_candidates) >= 5 and days_spanned >= 2:
            if not any(h["id"] == current.id for h in state["known_hubs"]):
                print(f"  [hub-detect] This server is a hub! Generating a permanent invite...")
                
                # Try to generate a permanent link
                permanent_code = await generate_permanent_invite(client, current)
                
                # Fall back to the link we used if we don't have permissions to make one
                code_to_save = permanent_code if permanent_code else state.get("last_join_invite")
                
                if code_to_save:
                    state["known_hubs"].append({
                        "code": code_to_save,
                        "id": current.id,
                        "name": current.name
                    })
                    client.persist()
                    print(f"  [hub-detect] Saved hub with code: {code_to_save}")

        print(f"  {len(candidates)} total candidate(s) found in this server")

        joined = None
        for i, code in enumerate(candidates):
            invite = await resolve_invite(client, code)
            if invite is None or invite.guild is None: continue
            if invite.guild.id == current.id: continue
            if invite.guild.id in state["joined_guild_ids"]: continue

            if len(client.guilds) >= 100:
                print("  [cap] at 100 guilds — leaving current guild first")
                try: await current.leave()
                except: pass
                current = None

            await asyncio.sleep(random.uniform(*JOIN_DELAY))
            try:
                joined = await try_join(client, code)
            except RuntimeError as exc:
                if str(exc) == "captcha": return
                raise

            if joined is not None:
                state["last_join_invite"] = code # Save the invite for hub learning
                leftover = candidates[i+1:]
                if leftover:
                    print(f"  [defer] saving {len(leftover)} untried invite(s) to backlog")
                    client.deferred_invites.extend(leftover)
                    client.persist()
                break

        if joined is None:
            print("  no usable invites in this server.")
            if client.deferred_invites:
                print(f"  [backlog] checking {len(client.deferred_invites)} deferred invite(s)")
                while client.deferred_invites and joined is None:
                    code = client.deferred_invites.pop(0)
                    client.persist()
                    
                    invite = await resolve_invite(client, code)
                    if invite is None or invite.guild is None: continue
                    if invite.guild.id in state["joined_guild_ids"]: continue

                    await asyncio.sleep(random.uniform(*JOIN_DELAY))
                    try:
                        joined = await try_join(client, code)
                    except RuntimeError as exc:
                        if str(exc) == "captcha": return
                        raise
                        
                    if joined is not None:
                        state["last_join_invite"] = code

            if joined is None:
                print("  backlog empty/failed — falling back to hubs")
                joined = await join_from_hubs(client)

        if joined is None:
            print("  hubs exhausted — idling 5 minutes")
            await asyncio.sleep(300)
            continue

                # ── 4. Bookkeeping & Queue Management ─────────────────────────────
        state["joined_count"] += 1
        if joined.id not in state["joined_guild_ids"]:
            state["joined_guild_ids"].append(joined.id)
            
        # Add the new server to the end of the queue
        if joined.id not in state["join_queue"]:
            state["join_queue"].append(joined.id)
            
        state["current_guild_id"] = joined.id
        client.persist()
        print(f"  [✓] joined #{state['joined_count']}: {joined.name} ({joined.id})")
        print(f"  [queue] Currently in {len(client.guilds)} / {MAX_SERVERS_TO_KEEP} kept servers")

        await asyncio.sleep(5) # Let channel cache populate

        # ── 5. Cleanup: Leave the oldest server if we are over the limit ──
        # We check > MAX_SERVERS_TO_KEEP to ensure we leave BEFORE hitting the hard 100 cap
        while len(client.guilds) > MAX_SERVERS_TO_KEEP and len(state["join_queue"]) > 1:
            oldest_id = state["join_queue"].pop(0) # Pop from the front (oldest)
            
            # Never leave the server we are currently scanning
            if oldest_id == joined.id:
                state["join_queue"].append(oldest_id)
                break
                
            old_guild = client.get_guild(oldest_id)
            if old_guild:
                # Don't leave if we own the server
                if old_guild.owner_id == client.user.id:
                    print(f"  [leave] cannot leave {old_guild.name} (we own it)")
                    continue # Drop it from the queue, don't try to leave again
                
                try:
                    await old_guild.leave()
                    print(f"  [leave] left oldest server: {old_guild.name}")
                except discord.HTTPException as exc:
                    print(f"  [leave] failed to leave {old_guild.name}: {exc}")
            
            client.persist()

        # Set the new server as the current one to scan next loop
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