import asyncio
import contextlib
import datetime
import functools
from typing import Any, Dict, List, Optional, Set, Union, cast

import discord
from redbot.cogs.mod.mod import Mod
from redbot.core import commands
from redbot.core.bot import Red
from redbot.core.utils import AsyncIter
from redbot.core.utils.common_filters import filter_invites

from .cache import Cache


class UISelect(discord.ui.Select["UIView"]):
    view: "UIView"

    def __init__(self, callback: Any) -> None:
        self.options: List[discord.SelectOption] = [
            discord.SelectOption(
                label="Home",
                emoji=self.view.cache.get_select_emoji("home"),
                value="home",
                description="General info, join dates, badges, status, etc...",
                default=True,
            ),
            discord.SelectOption(
                label="Avatar",
                emoji=self.view.cache.get_select_emoji("avatar"),
                value="avatar",
                description="View the user's global avatar...",
            ),
        ]
        self._task: asyncio.Task[None] = asyncio.create_task(self.release())
        if self.view._get_roles(self.view.user):
            self.options.append(
                discord.SelectOption(
                    label="Roles",
                    emoji=self.view.cache.get_select_emoji("roles"),
                    value="roles",
                    description="View the user's roles..",
                )
            )
        super().__init__(
            custom_id="ui:select",
            placeholder="Choose a page to view...",
            min_values=1,
            max_values=1,
            options=self.options,
        )
        self.callback: functools.partial[Any] = functools.partial(callback, self)

    def close(self) -> None:
        if self._task:
            self._task.cancel()

    async def release(self):
        if self.view.user.guild_avatar:
            self.options.append(
                discord.SelectOption(
                    label="Guid Avatar",
                    emoji=self.view.cache.get_select_emoji("gavatar"),
                    value="gavatar",
                    description="View the user's guild avatar...",
                )
            )
        fetched: discord.User = await self.view.bot.fetch_user(self.view.user.id)
        if fetched.banner:
            self.options.append(
                discord.SelectOption(
                    label="Banner",
                    emoji=self.view.cache.get_select_emoji("banner"),
                    value="banner",
                    description="View the user's banner...",
                )
            )


class UIView(discord.ui.View):
    def __init__(
        self,
        ctx: commands.GuildContext,
        user: discord.Member,
        cache: Cache,
        *,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.ctx: commands.GuildContext = ctx
        self.bot: Red = ctx.bot
        self.cache: Cache = cache
        self.user: discord.Member = user

        self.embed: discord.Embed = discord.utils.MISSING
        self._message: discord.Message = discord.utils.MISSING

        self._select: UISelect = UISelect(self._callback)
        self.add_item(self._select)

    @staticmethod
    def _get_perms(perms: discord.Permissions) -> List[str]:
        if perms.administrator:
            return ["Administrator"]
        gp: Dict[str, bool] = dict(
            {x for x in perms if x[1] is True} - set(discord.Permissions(521942715969))
        )
        return [p.replace("_", " ").title() for p in gp]

    @staticmethod
    def _get_roles(member: discord.Member) -> Optional[List[str]]:
        roles: List[discord.Role] = list(reversed(member.roles))[:-1]
        if roles:
            return [x.mention for x in roles]

    @staticmethod
    async def _callback(self: UISelect, interaction: discord.Interaction[Red]) -> None:
        await interaction.response.defer()
        value: str = self.values[0]
        if value == "home":
            embed: discord.Embed = await self.view._make_embed()
            await interaction.edit_original_response(embed=embed)
        elif value == "avatar":
            embed: discord.Embed = discord.Embed(
                color=self.view.user.color, title="{}'s Avatar".format(self.view.user.display_name)
            )
            embed.set_image(
                url=(
                    self.view.user.avatar.url
                    if self.view.user.avatar
                    else self.view.user.default_avatar.url
                )
            )
            await interaction.edit_original_response(embed=embed)
        elif value == "gavatar":
            embed: discord.Embed = discord.Embed(
                color=self.view.user.color,
                title="{}'s Guild Avatar".format(self.view.user.display_name),
            )
            if gavatar := self.view.user.guild_avatar:
                embed.set_image(url=gavatar.url)
            else:
                embed.description = "{}  does not have a guild specific avatar.".format(
                    self.view.user.mention
                )
            await interaction.edit_original_response(embed=embed)
        elif value == "banner":
            embed: discord.Embed = discord.Embed(
                color=self.view.user.color, title="{}'s Banner".format(self.view.user.display_name)
            )
            if banner := (await self.view.bot.fetch_user(self.view.user.id)).banner:
                embed.set_image(url=banner.url)
            else:
                embed.description = "{} does not have a banner.".format(self.view.user.mention)
            await interaction.edit_original_response(embed=embed)
        elif value == "roles":
            embed: discord.Embed = discord.Embed(
                color=self.view.user.color, title="{}'s Roles".format(self.view.user.display_name)
            )
            embed.description = (
                await self.view._format_roles()
                if self.view._get_roles(self.view.user)
                else "{} does not have any roles in this server.".format(self.view.user.mention)
            )
            await interaction.edit_original_response(embed=embed)

    @classmethod
    async def make_embed(
        cls, ctx: commands.GuildContext, user: discord.Member, cache: Cache
    ) -> discord.Embed:
        self: "UIView" = cls(ctx=ctx, user=user, cache=cache)
        return await self._make_embed()

    async def on_timeout(self) -> None:
        for child in self.children:
            child: discord.ui.Item["UIView"]
            if hasattr(child, "disabled"):
                child.disabled = True  # type: ignore
        with contextlib.suppress(discord.HTTPException):
            if self._message is not discord.utils.MISSING:
                await self._message.edit(view=self)
        self._select.close()

    async def interaction_check(self, interaction: discord.Interaction[Red], /) -> bool:
        if self.ctx.author.id != interaction.user.id:
            await interaction.response.send_message(
                content="You're not the author of this message.", ephemeral=True
            )
            self._select.close()
            return False
        return True

    async def _format_roles(self) -> Union[str, discord.utils.MISSING]:
        roles: Optional[List[str]] = self._get_roles(self.user)
        if roles:
            string: str = ", ".join(roles)
            if len(string) > 4000:
                formatted: str = "and {number} more roles not displayed due to embed limits."
                available_length: int = 4000 - len(formatted)
                chunks = []
                remaining = 0
                for r in roles:
                    chunk = "{}\n".format(r)
                    size = len(chunk)
                    if size < available_length:
                        available_length -= size
                        chunks.append(chunk)
                    else:
                        remaining += 1
                chunks.append(formatted.format(number=remaining))
                string: str = "".join(chunks)
        else:
            string: str = discord.utils.MISSING
        return string

    async def _make_embed(self) -> discord.Embed:
        if self.embed is not discord.utils.MISSING:
            return self.embed
        user: discord.Member = self.user
        shared: Union[List[discord.Guild], Set[discord.Guild]] = (
            user.mutual_guilds
            if hasattr(user, "mutual_guilds")
            else {
                guild
                async for guild in AsyncIter(self.bot.guilds, steps=100)
                if user in guild.members
            }
        )
        mod: Mod = self.bot.get_cog("Mod")  # type: ignore
        names, _, nicks = await mod.get_names(user)
        created_dt: float = (
            cast(datetime.datetime, user.created_at)
            .replace(tzinfo=datetime.timezone.utc)
            .timestamp()
        )
        since_created: str = "<t:{}:R>".format(int(created_dt))
        if user.joined_at:
            joined_dt: float = (
                cast(datetime.datetime, user.joined_at)
                .replace(tzinfo=datetime.timezone.utc)
                .timestamp()
            )
            since_joined: str = "<t:{}:R>".format(int(joined_dt))
            user_joined: str = "<t:{}>".format(int(joined_dt))
        else:
            since_joined: str = "?"
            user_joined: str = "Unknown"
        user_created = "<t:{}>".format(int(created_dt))
        position: int = (
            sorted(
                self.ctx.guild.members, key=lambda m: m.joined_at or self.ctx.message.created_at
            ).index(user)
            + 1
        )
        created_on: str = "{}\n( {} )\n".format(user_created, since_created)
        joined_on: str = "{}\n( {} )\n".format(user_joined, since_joined)
        if self.bot.intents.presences:
            mobile, web, desktop = self.cache.get_member_device_status(user)
            status: str = mod.get_status_string(user)
            if status:
                description: str = "{}\n**Devices:** {} {} {}\n\n".format(
                    status, mobile, web, desktop
                )
            else:
                description: str = "{} {} {}\n\n".format(mobile, web, desktop)
        else:
            description: str = ""
        embed: discord.Embed = discord.Embed(
            description=(
                description + "**Shared Servers: {}**".format(len(shared))
                if len(shared) > 1
                else "**Shared Server: {}**".format(len(shared))
            ),
            color=user.color,
        )
        embed.add_field(name="Joined Discord on:", value=created_on)
        embed.add_field(name="Joined this Server on:", value=joined_on)
        if names is not discord.utils.MISSING:
            val = filter_invites(", ".join(names))
            embed.add_field(name="Previous Names:", value=val, inline=False)
        if nicks is not discord.utils.MISSING:
            val = filter_invites(", ".join(nicks))
            embed.add_field(name="Previous Nicknames:", value=val, inline=False)
        if user.voice and user.voice.channel:
            embed.add_field(
                name="Current Voice Channel:",
                value="{0.mention} ID: {0.id}".format(user.voice.channel),
                inline=False,
            )
        embed.set_footer(text="Member #{} | User ID: {}".format(position, user.id))
        name = " ~ ".join((str(user), user.nick)) if user.nick else user.display_name
        embed.title = name
        embed.set_thumbnail(url=user.display_avatar.with_static_format("png").url)
        badges, badge_count = await self.cache.get_user_badges(user)
        if badges:
            embed.add_field(
                name="Badges:" if badge_count > 1 else "Badge:", value=badges, inline=False
            )
        special = self.cache.get_special_badges(user)
        if special:
            embed.add_field(
                name="Special Badges:" if len(special) > 1 else "Special Badge:",
                value="\n".join(special),
                inline=False,
            )
        self.embed: discord.Embed = embed
        return embed
