import discord
import re
import arrow
import asyncio
import random
from discord.ext import commands, tasks
from modules import queries, util, log, emojis
from libraries import emoji_literals

logger = log.get_logger(__name__)
command_logger = log.get_command_logger()


class Events(commands.Cog):
    """Event handlers for various discord events"""

    def __init__(self, bot):
        self.bot = bot
        self.stfu_regex = re.compile(r"(?:^|\W){0}(?:$|\W)".format("stfu"), flags=re.IGNORECASE)
        self.statuses = [
            ("watching", lambda: f"{len(self.bot.guilds)} servers"),
            ("listening", lambda: f"{len(set(self.bot.get_all_members()))} users"),
            ("playing", lambda: "misobot.xyz"),
        ]
        self.activities = {"playing": 0, "streaming": 1, "listening": 2, "watching": 3}
        self.current_status = None
        self.status_loop.start()
        self.guildlog = 652916681299066900

    def cog_unload(self):
        self.status_loop.cancel()

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        """Runs when any command is completed succesfully."""
        # prevent double invocation for subcommands
        if ctx.invoked_subcommand is None:
            command_logger.info(log.log_command(ctx))
            if ctx.guild is not None:
                await queries.save_command_usage(ctx)

    @commands.Cog.listener()
    async def on_ready(self):
        """Runs when the bot connects to the discord servers."""
        latencies = self.bot.latencies
        logger.info(f"Loading complete | running {len(latencies)} shards")
        for shard_id, latency in latencies:
            logger.info(f"Shard [{shard_id}] - HEARTBEAT {latency}s")

    @tasks.loop(minutes=3.0)
    async def status_loop(self):
        try:
            await self.next_status()
        except Exception as e:
            logger.error(e)

    @status_loop.before_loop
    async def before_status_loop(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)  # avoid rate limit from discord in case of rapid reconnect
        logger.info("Starting status loop")

    async def next_status(self):
        """switch to the next status message."""
        new_status_id = self.current_status
        while new_status_id == self.current_status:
            new_status_id = random.randrange(0, len(self.statuses))

        status = self.statuses[new_status_id]
        self.current_status = new_status_id

        await self.bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType(self.activities[status[0]]), name=status[1]()
            )
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        """Called when the bot joins a new guild."""
        blacklisted = await self.bot.db.execute(
            "SELECT reason FROM blacklisted_guild WHERE guild_id = %s", guild.id, one_value=True
        )
        if blacklisted:
            logger.info(f"Tried to join guild {guild}. Reason for blacklist: {blacklisted}")
            return await guild.leave()

        logger.info(f"New guild : {guild}")
        content = discord.Embed(color=discord.Color.green())
        content.title = "New guild!"
        content.description = (
            f"Miso just joined **{guild}**\nWith **{guild.member_count-1}** members"
        )
        content.set_thumbnail(url=guild.icon_url)
        content.set_footer(text=f"#{guild.id}")
        content.timestamp = arrow.utcnow().datetime
        logchannel = self.bot.get_channel(self.guildlog)
        await logchannel.send(embed=content)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        """Called when the bot leaves a guild."""
        logger.info(f"Left guild {guild}")
        blacklisted = await self.bot.db.execute(
            "SELECT reason FROM blacklisted_guild WHERE guild_id = %s", guild.id, one_value=True
        )
        if blacklisted:
            return

        content = discord.Embed(color=discord.Color.red())
        content.title = "Left guild!"
        content.description = (
            f"Miso just left **{guild}**\nWith **{guild.member_count-1}** members :("
        )
        content.set_thumbnail(url=guild.icon_url)
        content.set_footer(text=f"#{guild.id}")
        content.timestamp = arrow.utcnow().datetime
        logchannel = self.bot.get_channel(self.guildlog)
        await logchannel.send(embed=content)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Called when a new member joins a guild."""
        # log event
        logging_channel_id = await self.bot.db.execute(
            "SELECT member_log_channel_id FROM logging_settings WHERE guild_id = %s",
            member.guild.id,
            one_value=True,
        )
        if logging_channel_id:
            logging_channel = member.guild.get_channel(logging_channel_id)
            if logging_channel is not None:
                embed = discord.Embed(color=discord.Color.green())
                embed.set_author(name=str(member), icon_url=member.avatar_url)
                await logging_channel.send(embed=embed)

        # welcome message
        greeter = await self.bot.db.execute(
            "SELECT channel_id, is_enabled, message_format FROM greeter_settings WHERE guild_id = %s",
            member.guild.id,
            one_row=True,
        )
        if greeter:
            channel_id, is_enabled, message_format = greeter
            if is_enabled:
                greeter_channel = member.guild.get_channel(channel_id)
                if greeter_channel is not None:
                    try:
                        await greeter_channel.send(
                            embed=util.create_welcome_embed(member, member.guild, message_format)
                        )
                    except discord.errors.Forbidden:
                        pass

        # add autoroles
        roles = await self.bot.db.execute(
            "SELECT role_id FROM autorole WHERE guild_id = %s", member.guild.id, as_list=True
        )
        for role_id in roles:
            role = member.guild.get_role(role_id)
            if role is None:
                continue
            try:
                await member.add_roles(role)
            except discord.errors.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_member_ban(self, guild, user):
        """Called when user gets banned from a server."""
        channel_id = await self.bot.db.execute(
            "SELECT ban_log_channel_id FROM logging_settings WHERE guild_id = %s",
            guild.id,
            one_value=True,
        )
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if channel is not None:
            try:
                await channel.send(
                    embed=discord.Embed(
                        description=f":hammer: User banned **{user}** {user.mention}",
                        color=int("f4900c", 16),
                        timestamp=arrow.utcnow().datetime,
                    )
                )
            except discord.errors.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Called when member leaves a guild."""
        # log event
        logging_channel_id = await self.bot.db.execute(
            "SELECT member_log_channel_id FROM logging_settings WHERE guild_id = %s",
            member.guild.id,
            one_value=True,
        )
        if logging_channel_id:
            logging_channel = member.guild.get_channel(logging_channel_id)
            if logging_channel is not None:
                embed = discord.Embed(color=discord.Color.red())
                embed.set_author(name=str(member), icon_url=member.avatar_url)
                await logging_channel.send(embed=embed)

        # goodbye message
        goodbye = await self.bot.db.execute(
            "SELECT channel_id, is_enabled, message_format FROM goodbye_settings WHERE guild_id = %s",
            member.guild.id,
            one_row=True,
        )
        if goodbye:
            channel_id, is_enabled, message_format = goodbye
            if is_enabled:
                channel = member.guild.get_channel(channel_id)
                if channel is not None:
                    if message_format is None:
                        message_format = "Goodbye **{user}** {mention}"

                    try:
                        await channel.send(
                            util.create_goodbye_message(member, member.guild, message_format)
                        )
                    except discord.errors.Forbidden:
                        pass

    @commands.Cog.listener()
    async def on_message_delete(self, message):
        """Listener that gets called when any message is deleted."""
        # ignore bots
        if message.author.bot:
            return

        # ignore DMs
        if message.guild is None:
            return

        # ignore empty messages
        if len(message.content) == 0 and len(message.attachments) == 0:
            return

        # ignored channels
        ignored_channels = await self.bot.db.execute(
            "SELECT channel_id FROM message_log_ignore WHERE guild_id = %s",
            message.guild.id,
            as_list=True,
        )
        if message.channel.id in ignored_channels:
            return

        channel_id = await self.bot.db.execute(
            "SELECT message_log_channel_id FROM logging_settings WHERE guild_id = %s",
            message.guild.id,
            one_value=True,
        )
        if channel_id:
            channel = message.guild.get_channel(channel_id)
            if channel is not None and message.channel != channel:
                try:
                    await channel.send(embed=util.message_embed(message))
                except discord.errors.Forbidden:
                    pass

    @commands.Cog.listener()
    async def on_message(self, message):
        """Listener that gets called on every message."""
        # ignore DMs
        if message.guild is None:
            return

        # votechannels
        votechannel_type = await self.bot.db.execute(
            "SELECT voting_type FROM voting_channel WHERE channel_id = %s",
            message.channel.id,
            one_value=True,
        )
        if votechannel_type == "rating":
            for e in ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]:
                await message.add_reaction(e)
        elif votechannel_type == "voting":
            await message.add_reaction(emojis.UPVOTE)
            await message.add_reaction(emojis.DOWNVOTE)

        # xp gain
        message_xp = util.xp_from_message(message)
        currenthour = message.created_at.hour
        for activity_table in [
            "user_activity",
            "user_activity_day",
            "user_activity_week",
            "user_activity_month",
            "user_activity_year",
        ]:
            await self.bot.db.execute(
                f"""
                INSERT INTO {activity_table} (guild_id, user_id, is_bot, h{currenthour})
                    VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    h{currenthour} = h{currenthour} + VALUES(h{currenthour}),
                    message_count = message_count + 1
                """,
                message.guild.id,
                message.author.id,
                message.author.bot,
                message_xp,
            )

        # if bot account, ignore everything after this
        if message.author.bot:
            return

        guild_settings = await self.bot.db.execute(
            "SELECT levelup_messages, autoresponses FROM guild_settings WHERE guild_id = %s",
            message.guild.id,
            one_row=True,
        )
        if guild_settings:
            announce_levelup, autoresponses = guild_settings
        else:
            announce_levelup = False
            autoresponses = True

        # log emojis
        unicode_emojis = util.find_unicode_emojis(message.content)
        custom_emojis = util.find_custom_emojis(message.content)

        unicode_values = []
        for emoji_name in unicode_emojis:
            unicode_values += [
                message.guild.id,
                message.author.id,
                emoji_name,
            ]
        if unicode_values:
            await self.bot.db.execute(
                f"""
                INSERT INTO unicode_emoji_usage (guild_id, user_id, emoji_name)
                    VALUES {', '.join("(%s, %s, %s)" for _ in unicode_emojis)}
                ON DUPLICATE KEY UPDATE
                    uses = uses + 1
                """,
                *unicode_values,
            )

        custom_values = []
        for emoji_name, emoji_id in custom_emojis:
            custom_values += [
                message.guild.id,
                message.author.id,
                emoji_name,
                emoji_id,
            ]
        if custom_values:
            await self.bot.db.execute(
                f"""
                INSERT INTO custom_emoji_usage (guild_id, user_id, emoji_name, emoji_id)
                    VALUES {', '.join("(%s, %s, %s, %s)" for _ in custom_emojis)}
                ON DUPLICATE KEY UPDATE
                    uses = uses + 1
                """,
                *custom_values,
            )

        if autoresponses:
            await self.easter_eggs(message)

        # level up message
        if announce_levelup:
            activity_data = await self.bot.db.execute(
                "SELECT * FROM user_activity WHERE user_id = %s AND guild_id = %s",
                message.author.id,
                message.guild.id,
                one_row=True,
            )
            if activity_data:
                xp = sum(activity_data[3:])
                level_before = util.get_level(xp - message_xp)
                level_now = util.get_level(xp)

                if level_now > level_before:
                    try:
                        await message.channel.send(
                            f"{message.author.mention} just leveled up! (level **{level_now}**)",
                            delete_after=5,
                        )
                    except discord.errors.Forbidden:
                        pass

    async def easter_eggs(self, message):
        """Easter eggs handler."""
        # stfu
        if self.stfu_regex.findall(message.content) and random.randint(0, 1) == 0:
            try:
                await message.channel.send("no u")
            except discord.errors.Forbidden:
                pass

        stripped_content = message.content.lower().strip("!.?~ ")

        # hi
        if stripped_content == "hi" and random.randint(0, 19) == 0:
            try:
                await message.channel.send("hi")
            except discord.errors.Forbidden:
                pass

        # hello there
        elif stripped_content == "hello there" and random.randint(0, 2) == 0:
            try:
                await message.channel.send("General Kenobi")
            except discord.errors.Forbidden:
                pass

        # git gud
        if message.content.lower().startswith("git "):
            gitcommand = re.search(r"git (\S+)", message.content)
            if gitcommand is None:
                return
            gitcommand = gitcommand.group(1)
            if gitcommand == "--help":
                msg = (
                    "```\n"
                    "usage: git [--version] [--help] [-C <path>] [-c <name>=<value>]\n"
                    "           [--exec-path[=<path>]] [--html-path] [--man-path] [--info-path]\n"
                    "           [-p | --paginate | --no-pager] [--no-replace-objects] [--bare]\n"
                    "           [--git-dir=<path>] [--work-tree=<path>] [--namespace=<name>]\n"
                    "           <command> [<args>]```"
                )
            elif gitcommand == "--version":
                msg = "`git version 2.28.0`"
            elif gitcommand in [
                "init",
                "reset",
                "rebase",
                "fetch",
                "commit",
                "push",
                "pull",
                "checkout",
                "status",
                "init",
                "add",
                "clone",
            ]:
                return
            else:
                msg = f"`git: '{gitcommand}' is not a git command. See 'git --help'.`"

            try:
                await message.channel.send(msg)
            except discord.errors.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Starboard event handler."""
        user = self.bot.get_user(payload.user_id)
        if user.bot:
            return

        starboard_settings = await self.bot.db.execute(
            """
            SELECT is_enabled, channel_id, reaction_count, emoji_name, emoji_id, emoji_type
            FROM starboard_settings WHERE guild_id = %s
            """,
            payload.guild_id,
            one_row=True,
        )

        if not starboard_settings:
            return

        (
            is_enabled,
            board_channel_id,
            required_reaction_count,
            emoji_name,
            emoji_id,
            emoji_type,
        ) = starboard_settings
        board_channel = self.bot.get_channel(board_channel_id)
        if not is_enabled or board_channel is None:
            return

        if (
            emoji_type == "custom"
            and emoji_id is not None
            and payload.emoji.id is not None
            and payload.emoji.id == emoji_id
        ) or (
            emoji_type == "unicode"
            and emoji_name is not None
            and emoji_literals.UNICODE_TO_NAME.get(payload.emoji.name) == emoji_name
        ):
            message_channel = self.bot.get_channel(payload.channel_id)

            # trying to star a starboard message
            if message_channel.id == board_channel_id:
                return
            try:
                message = await message_channel.fetch_message(payload.message_id)
            except (discord.errors.Forbidden, discord.errors.NotFound):
                return

            for react in message.reactions:
                if emoji_type == "custom":
                    if (
                        isinstance(react.emoji, (discord.Emoji, discord.PartialEmoji))
                        and react.emoji.id == payload.emoji.id
                    ):
                        reaction_count = react.count
                        break
                else:
                    if react.emoji == payload.emoji.name:
                        reaction_count = react.count
                        break

            if reaction_count < required_reaction_count:
                return

            board_message_id = await self.bot.db.execute(
                "SELECT starboard_message_id FROM starboard_message WHERE original_message_id = %s",
                payload.message_id,
                one_value=True,
            )
            emoji_display = (
                "⭐" if emoji_type == "custom" else emoji_literals.NAME_TO_UNICODE[emoji_name]
            )

            board_message = None
            if board_message_id:
                try:
                    board_message = await message_channel.fetch_message(board_message_id)
                except discord.errors.NotFound:
                    pass

            if board_message is None:
                # message is not on board yet, or it was deleted
                content = discord.Embed(color=int("ffac33", 16))
                content.set_author(name=f"{message.author}", icon_url=message.author.avatar_url)
                jump = f"\n\n[context]({message.jump_url})"
                content.description = message.content[: 2048 - len(jump)] + jump
                content.timestamp = message.created_at
                content.set_footer(
                    text=f"{reaction_count} {emoji_display} #{message.channel.name}"
                )
                if len(message.attachments) > 0:
                    content.set_image(url=message.attachments[0].url)

                try:
                    board_message = await board_channel.send(embed=content)
                    await self.bot.db.execute(
                        """
                        INSERT INTO starboard_message (original_message_id, starboard_message_id)
                            VALUES(%s, %s)
                        ON DUPLICATE KEY UPDATE
                            starboard_message_id = VALUES(starboard_message_id)
                        """,
                        payload.message_id,
                        board_message.id,
                    )
                except discord.errors.Forbidden:
                    pass

            else:
                # message is on board, update star count
                content = board_message.embeds[0]
                content.set_footer(
                    text=f"{reaction_count} {emoji_display} #{message.channel.name}"
                )
                await board_message.edit(embed=content)


def setup(bot):
    bot.add_cog(Events(bot))
