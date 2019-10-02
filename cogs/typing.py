import discord
from discord.ext import commands
import data.database as db
import random
import asyncio
from operator import itemgetter
import helpers.utilityfunctions as util
import arrow


class Typing(commands.Cog):

    def __init__(self, client):
        self.client = client
        self.all_words = db.get_from_data_json(['wordlists', 'english'])

    def get_wordlist(self, wordcount):
        wordlist = []
        while len(wordlist) < wordcount:
            word = random.choice(self.all_words)
            if not wordlist or not wordlist[-1] == word:
                wordlist.append(word)
        return wordlist

    def calculate_entry(self, message, words_message, wordlist):
        time = message.created_at - words_message.created_at
        user_words = message.content.split()
        total_keys = 0
        corrent_keys = 0
        for user_word, correct_word in zip(user_words, wordlist):
            total_keys += len(correct_word) + 1
            if user_word == correct_word:
                corrent_keys += len(correct_word) + 1

        wpm = (corrent_keys / 5) / (time.total_seconds() / 60)
        accuracy = (corrent_keys / total_keys) * 100
        return wpm, accuracy

    @commands.group()
    async def typing(self, ctx):
        """typing speed competition"""
        await util.command_group_help(ctx)

    @typing.command(name='test')
    async def typing_test(self, ctx, wordcount: int = 50):
        if wordcount < 10:
            return await ctx.send("Minimum word count is 10!")
        if wordcount > 250:
            return await ctx.send("Maximum word count is 250!")

        wordlist = self.get_wordlist(wordcount)
        og_msg = await ctx.send(f"```\n{' '.join(wordlist)}\n```")

        def check(_message):
            return _message.author == ctx.author and _message.channel == ctx.channel

        try:
            message = await self.client.wait_for('message', timeout=300.0, check=check)
        except asyncio.TimeoutError:
            return await ctx.send("Too slow.")

        else:
            if ' ' in message.content:
                return await ctx.send(f"{ctx.author.mention} Stop cheating >:(")

            wpm, accuracy = self.calculate_entry(message, og_msg, wordlist)
            await ctx.send(f"{ctx.author.mention} **{int(wpm)} WPM / {int(accuracy)}% ACC**")
            save_wpm(ctx.author, wpm, accuracy, wordcount, 0)

    @typing.command(name='race')
    async def typing_race(self, ctx, wordcount: int = 50):
        if wordcount < 10:
            return await ctx.send("Minimum word count is 10!")
        if wordcount > 250:
            return await ctx.send("Maximum word count is 250!")

        content = discord.Embed(title=f":keyboard: Starting a new typing race | {wordcount} words", color=discord.Color.gold())
        content.description = "React with :notepad_spiral: to enter the race.\n" \
                              "React with :white_check_mark: to start the race."
        content.add_field(name="Participants", value=f"**{ctx.author}**")
        enter_message = await ctx.send(embed=content)

        note_emoji = "🗒"
        check_emoji = "✅"

        players = set()
        players.add(ctx.author)
        race_in_progress = False

        await enter_message.add_reaction(note_emoji)
        await enter_message.add_reaction(check_emoji)

        def check(_reaction, _user):
            return _reaction.message.id == enter_message.id \
                   and _reaction.emoji in [note_emoji, check_emoji] \
                   and not _user == ctx.bot.user

        while not race_in_progress:
            try:
                reaction, user = await ctx.bot.wait_for('reaction_add', timeout=300.0, check=check)
            except asyncio.TimeoutError:
                try:
                    for emoji in []:
                        await enter_message.remove_reaction(emoji, ctx.bot.user)
                except discord.errors.NotFound:
                    pass
                break
            else:
                if reaction.emoji == note_emoji:
                    players.add(user)
                    content.remove_field(0)
                    content.add_field(name="Participants", value='\n'.join(f"**{x}**" for x in players))
                    await enter_message.edit(embed=content)
                elif reaction.emoji == check_emoji:
                    if len(players) < 2:
                        await ctx.send("You can't race alone!")
                        await enter_message.remove_reaction(check_emoji, user)
                    else:
                        race_in_progress = True

        if not race_in_progress:
            return await ctx.send("Race aborted. Not enough players or timed out.")

        words_message = await ctx.send("Starting race in 3...")
        i = 2
        while i > 0:
            await asyncio.sleep(1)
            await words_message.edit(content=f"Starting race in {i}...")
            i -= 1

        await asyncio.sleep(1)

        wordlist = self.get_wordlist(wordcount)
        await words_message.edit(content=f"```\n{' '.join(wordlist)}\n```")

        results = {}
        for player in players:
            results[str(player.id)] = 0

        completed_players = set()

        while race_in_progress:
            def check(_message):
                return _message.author in players \
                       and _message.channel == ctx.channel \
                       and _message.author.id not in completed_players

            try:
                message = await self.client.wait_for('message', timeout=300.0, check=check)
            except asyncio.TimeoutError:
                race_in_progress = False

            else:
                if ' ' in message.content:
                    return await ctx.send(f"{ctx.author.mention} Stop cheating >:(")

                wpm, accuracy = self.calculate_entry(message, words_message, wordlist)
                await ctx.send(f"{message.author.mention} **{int(wpm)} WPM / {int(accuracy)}% ACC**")
                save_wpm(ctx.author, wpm, accuracy, wordcount, 1)

                results[str(message.author.id)] = wpm
                completed_players.add(message.author)

                if completed_players == players:
                    race_in_progress = False

        content = discord.Embed(title=":keyboard: Race complete!", color=discord.Color.green())
        rows = []
        for i, player in enumerate(sorted(results.items(), key=itemgetter(1), reverse=True), start=1):
            member = ctx.guild.get_member(int(player[0]))
            if i == 1:
                db.execute("INSERT OR IGNORE INTO typeracer VALUES(?, ?, ?)", (member.id, ctx.guild.id, 0))
                db.execute("UPDATE typeracer SET wins = wins + 1 WHERE (guild_id = ? AND user_id = ?)",
                           (ctx.guild.id, member.id))

            rows.append(f"{f'`{i}.`' if i > 1 else ':crown:'} **{int(player[1])} WPM ** — "
                        f"{member.name}")

        content.description = '\n'.join(rows)
        await ctx.send(embed=content)

    @typing.command(name='history')
    async def typing_history(self, ctx, user: discord.Member = None):
        if user is None:
            user = ctx.author

        data = db.query("SELECT * FROM typingdata WHERE user_id = ? ORDER BY `timestamp` DESC", (user.id,))
        if data is None:
            return await ctx.send(("You haven't" if user is ctx.author else f"**{user.name}** hasn't")
                                  + " taken any typing tests yet!")

        content = discord.Embed(title=f":stopwatch: Typing test history for {user.name}", color=discord.Color.orange())
        content.set_footer(text=f"Total {len(data)} typing tests taken")
        rows = []
        for row in data:
            rows.append(f"`{int(row[2])}` WPM **/** `{int(row[3])}%` ACC **/** {row[4]} words ( {arrow.get(row[0]).humanize()} )")
        await util.send_as_pages(ctx, content, rows)

    @typing.command(name='stats')
    async def typing_stats(self, ctx, user: discord.Member = None):
        if user is None:
            user = ctx.author

        data = db.query("SELECT * FROM typingdata WHERE user_id = ? ORDER BY `timestamp` DESC", (user.id,))
        if data is None:
            return await ctx.send(("You haven't" if user is ctx.author else f"**{user.name}** hasn't")
                                  + " taken any typing tests yet!")

        racedata = db.query("SELECT wins FROM typeracer WHERE user_id = ?", (user.id,))
        wpm_list = [x[2] for x in data]
        wpm_avg = sum(wpm_list) / len(wpm_list)
        acc_list = [x[3] for x in data]
        acc_avg = sum(acc_list) / len(acc_list)
        wpm_list_re = [x[2] for x in data[:10]]
        wpm_avg_re = sum(wpm_list_re) / len(wpm_list_re)
        acc_list_re = [x[3] for x in data[:10]]
        acc_avg_re = sum(acc_list_re) / len(acc_list_re)
        wins = racedata[0][0] if racedata is not None else 0
        content = discord.Embed(title=f":keyboard: {user.name} typing stats", color=discord.Color.gold())
        content.description = f"Tests taken: **{len(data)}**\n" \
                              f"Races won: **{wins}**\n" \
                              f"Average WPM: **{int(wpm_avg)}**\n" \
                              f"Average Accuracy: **{acc_avg:.1f}%**\n" \
                              f"Recent average WPM: **{int(wpm_avg_re)}**\n" \
                              f"Recent average Accuracy: **{acc_avg_re:.1f}%**\n"

        await ctx.send(embed=content)


def setup(client):
    client.add_cog(Typing(client))


def save_wpm(user, wpm, accuracy, wordcount, race):
    db.execute("INSERT INTO typingdata VALUES (?, ?, ?, ?, ?, ?)",
               (arrow.utcnow().timestamp, user.id, wpm, accuracy, wordcount, race))
