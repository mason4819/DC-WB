"""Role swap rule for one server (IDs live in config.py / .env).

  1. A member LOSES the watch role           -> the bot gives them the replacement role.
  2. A member who got the replacement role
     through rule 1 LOSES it                  -> the bot gives the watch role back.

Rule 2 only applies to members the bot swapped itself, so someone who received the replacement
role some other way is left alone.

How it works without a member cache (this bot keeps none, to save RAM):
- A role removal is only visible if the previous roles are known, so the bot remembers, per
  member, one of two states: "watch" (holds the watch role) or "swapped" (was given the
  replacement role by rule 1). Only members in one of these states are stored.
- It reads the raw GUILD_MEMBER_UPDATE payload for this one server, which carries the member's
  current roles, and compares it with the remembered state. Other servers cost one dictionary
  lookup per event, nothing else.
- The states live in memory and in the database (one row per member, written only when a state
  changes). When the bot connects it lists the members of this server once and re-checks every
  one of them, so removals that happened while the bot was offline or switching hosts are caught.
- The bot's own role changes arrive as events too, but they match the remembered state and do
  nothing, so the two rules can never loop.
"""
import asyncio
import logging
import time
from typing import Optional

import discord
from discord.ext import commands

import config
import database

log = logging.getLogger(__name__)

WATCH = "watch"        # the member holds the watch role
SWAPPED = "swapped"    # the bot gave this member the replacement role

# (previous state, new state, role to give or None)
Change = tuple[Optional[str], str, Optional[int]]


class RoleSwapCog(commands.Cog, name="RoleSwap"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.guild_id = config.ROLE_SWAP_GUILD_ID
        self.watch_id = config.ROLE_SWAP_WATCH_ROLE_ID
        self.give_id = config.ROLE_SWAP_GIVE_ROLE_ID
        self.enabled = bool(self.guild_id and self.watch_id and self.give_id and self.watch_id != self.give_id)
        self.states: dict[int, str] = {}
        self._guild_key = str(self.guild_id)
        self._ready_to_decide = False          # false until the saved states are loaded
        self._original_parser = None
        self._tasks: set[asyncio.Task] = set()
        self._reconcile_lock = asyncio.Lock()
        self._warned_at = float("-inf")

    # ------------------------------------------------------------------ setup / teardown
    async def cog_load(self) -> None:
        if not self.enabled:
            log.info("Role swap is not configured")
            return
        parsers = self.bot._connection.parsers
        self._original_parser = parsers.get("GUILD_MEMBER_UPDATE")
        if self._original_parser is None:
            log.error("Role swap cannot start: this discord.py version has no GUILD_MEMBER_UPDATE parser")
            self.enabled = False
            return
        parsers["GUILD_MEMBER_UPDATE"] = self._parse_member_update

    async def cog_unload(self) -> None:
        if self._original_parser is not None:
            self.bot._connection.parsers["GUILD_MEMBER_UPDATE"] = self._original_parser
            self._original_parser = None
        for task in list(self._tasks):
            task.cancel()

    # ------------------------------------------------------------------ events
    def _parse_member_update(self, data: dict) -> None:
        """Runs for every member update. Cheap for other servers: one comparison."""
        try:
            if self._ready_to_decide and data.get("guild_id") == self._guild_key:
                user_id = int(data["user"]["id"])
                change = self._decide(user_id, {int(role) for role in data.get("roles", ())})
                if change is not None:
                    self._spawn(self._apply(user_id, change))
        except Exception:
            log.exception("Role swap could not process a member update")
        self._original_parser(data)          # discord.py still does its normal work

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self.enabled:
            self._spawn(self._reconcile())

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        """A member who left starts from scratch if they come back."""
        if self.enabled and payload.guild_id == self.guild_id and self.states.pop(payload.user.id, None) is not None:
            await self._persist({payload.user.id: None})

    # ------------------------------------------------------------------ the rule
    def _decide(self, user_id: int, roles: set[int]) -> Optional[Change]:
        """Work out what (if anything) to do for one member and update the remembered state at once.

        Updating immediately, before any network call, means a second event for the same member
        can never trigger the same swap twice.
        """
        state = self.states.get(user_id)
        if self.watch_id in roles:
            if state == WATCH:
                return None
            self.states[user_id] = WATCH
            return state, WATCH, None                           # just remember it
        if state == WATCH:                                      # rule 1: the watch role is gone
            self.states[user_id] = SWAPPED
            return WATCH, SWAPPED, (None if self.give_id in roles else self.give_id)
        if state == SWAPPED and self.give_id not in roles:      # rule 2: the replacement is gone
            self.states[user_id] = WATCH
            return SWAPPED, WATCH, self.watch_id
        return None

    async def _apply(self, user_id: int, change: Change) -> None:
        previous, new_state, give = change
        if give is not None:
            reason = ("Role swap: the watch role was removed" if new_state == SWAPPED
                      else "Role swap: the replacement role was removed")
            try:
                await self.bot.http.add_role(self.guild_id, user_id, give, reason=reason)
            except discord.NotFound:                            # the member left, or the role was deleted
                self.states.pop(user_id, None)
                await self._persist({user_id: None})
                return
            except discord.Forbidden:
                self._warn(f"Discord refused to give role {give}. Give the bot 'Manage Roles' and move its role "
                           f"above both roles. Skipping member {user_id}.")
                self.states.pop(user_id, None)                  # do not retry on every later update
                await self._persist({user_id: None})
                return
            except discord.HTTPException:
                log.warning("Role swap: Discord error while giving role %s to %s; will retry on their next update",
                            give, user_id, exc_info=True)
                if previous is None:                            # put the memory back so the next event retries
                    self.states.pop(user_id, None)
                else:
                    self.states[user_id] = previous
                return
            log.info("Role swap: gave role %s to %s", give, user_id)
        await self._persist({user_id: new_state})

    async def _reconcile(self) -> None:
        """After connecting: load the saved states, then check every member of the server once."""
        async with self._reconcile_lock:
            guild = self.bot.get_guild(self.guild_id)
            if guild is None:
                log.info("Role swap: the bot is not in server %s", self.guild_id)
                return
            try:
                self.states = await database.load_role_swap(self.guild_id)
            except database.DatabaseError:
                log.warning("Role swap: could not read the saved states; it stays off until the next reconnect",
                            exc_info=True)
                return
            self._ready_to_decide = True

            batch: dict[int, Optional[str]] = {}
            seen: set[int] = set()
            complete = False
            try:
                async for member in guild.fetch_members(limit=None):
                    seen.add(member.id)
                    change = self._decide(member.id, {role.id for role in member.roles})
                    if change is None:
                        continue
                    if change[2] is None:
                        batch[member.id] = change[1]            # nothing to send, only to remember
                    else:
                        self._spawn(self._apply(member.id, change))
                complete = True
            except discord.HTTPException:
                log.warning("Role swap: could not list the members of server %s", self.guild_id, exc_info=True)
            if complete:                                        # forget people who are no longer here
                for user_id in [uid for uid in self.states if uid not in seen]:
                    self.states.pop(user_id)
                    batch[user_id] = None
            await self._persist(batch)
            log.info("Role swap checked %d members (%d remembered)", len(seen), len(self.states))

    # ------------------------------------------------------------------ helpers
    async def _persist(self, changes: dict[int, Optional[str]]) -> None:
        if not changes:
            return
        try:
            await database.save_role_swap(self.guild_id, changes)
        except database.DatabaseError:
            self._warn("could not save the role swap state (it is still kept in memory)")

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Role swap task failed", exc_info=task.exception())

    def _warn(self, text: str) -> None:
        """Log a repeating problem at most every 10 minutes."""
        if time.monotonic() - self._warned_at > 600:
            self._warned_at = time.monotonic()
            log.warning("Role swap: %s", text)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RoleSwapCog(bot))
