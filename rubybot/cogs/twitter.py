from utils import checks, utilities
from discord.errors import Forbidden, InvalidArgument
from discord.ext import commands
#from seiutils import discordutils, twitutils
import discordutils
import twitutils
from threading import Lock
import asyncio
import json
import logging
import os
import tweepy


logger = logging.getLogger(__name__)


class TweetListener(tweepy.StreamListener):
    def __init__(self):
        super(TweetListener, self).__init__()

        self.lock = Lock()
        self.logger = logging.getLogger(f'{__name__}.Twitter')
        self.statuses = []

    def on_status(self, status):
        try:
            if twitutils.is_reply(status):
                return

            with self.lock:
                self.statuses.append(status)

        except Exception as e:
            self.logger.error(f'{e}')

    def on_timeout(self):
        self.logger.error('Timeout')

    def on_error(self, status_code):
        self.logger.error(f'{status_code}')

    def get_statuses(self):
        with self.lock:
            statuses = self.statuses[:]
            self.statuses.clear()

        return statuses


class Twitter(commands.Cog):
    def __init__(self, bot):
        self.logger = logging.getLogger(f'{__name__}.Twitter')
        self.bot = bot
        self.loop = None
        self.destinations = None

        path = os.path.join(os.getcwd(), 'data', 'tweets.json')
        with open(path) as f:
            self.destinations = json.load(f)

        self._init_api()
        self._init_follows()
        self._init_stream()

        @bot.event
        async def on_ready():
            self._start_stream()
            await self._start_retrieve_loop()

        @bot.event
        async def on_resumed():
            self._restart_stream()

    def _init_api(self):
        credentials = utilities.load_credentials()['twitter']

        self.logger.info('Initializing Twitter')

        c_key = credentials['client_key']
        c_secret = credentials['client_secret']
        a_token = credentials['access_token']
        a_secret = credentials['access_secret']
        self.auth = tweepy.OAuthHandler(c_key, c_secret)
        self.auth.set_access_token(a_token, a_secret)

        self.twitter = tweepy.API(self.auth)

        self.logger.info('Successfully logged into twitter')

    def _init_stream(self):
        self.listener = TweetListener()
        self.tweet_stream = tweepy.Stream(auth=self.auth, listener=self.listener)

    def _start_stream(self):
        self.logger.info('Starting tweepy stream')
        self.tweet_stream.filter(follow=self.follows, is_async=True)

    def _kill_stream(self):
        self.logger.info('Killing tweepy stream')
        self.tweet_stream.disconnect()

    def _init_follows(self):
        self.follows = list(self.destinations['destinations'].keys())

    def _restart_stream(self):
        self._kill_stream()
        self._start_stream()

    def _add_channel(self, user, channel_id):
        twitter_id = user.id_str
        try:
            channels = self.destinations['destinations'][twitter_id]
            if channel_id in channels:
                channels.remove(channel_id)
                self._update_json()
                return False
            channels.append(channel_id)
        except KeyError:
            channels = [channel_id, ]
            self.destinations['destinations'][twitter_id] = channels

        self._update_json()

        return True

    def _blacklist_channel(self, channel_id):
        try:
            channels = self.destinations['blacklist']
            if channel_id in channels:
                channels.remove(channel_id)
                self._update_json()
                return True
            channels.append(channel_id)
        except KeyError:
            channels = [channel_id, ]
            self.destinations['blacklist'] = channels

        self._update_json()

        return False

    def _update_json(self):
        self._init_follows()
        path = os.path.join(os.getcwd(), 'files', 'tweets.json')
        with open(path, 'w') as f:
            f.seek(0)  # <--- should reset file position to the beginning.
            json.dump(self.destinations, f, indent=4)

    def _get_user(self, info):
        return self.twitter.get_user(info)

    async def _start_retrieve_loop(self):
        while True:
            statuses = self.listener.get_statuses()
            targets = self.destinations['destinations']
            while len(statuses) > 0:
                status = statuses.pop(0)
                user_id = status.user.id

                embed = discordutils.embed_tweet(status)

                try:
                    channels = targets[user_id]
                except KeyError:
                    continue

                for channel in channels:
                    if channel in self.destinations['blacklist']:
                        continue
                    try:
                        channel = self.bot.get_channel(channel)
                        await channel.send_message(embed=embed)
                    except Forbidden as e:
                        logger.error(f'Forbidden to post in channel {channel}')
                        logger.error(f'{e}')
                    except InvalidArgument as e:
                        logger.error(f'{e}')

            await asyncio.sleep(1)

    @commands.command(hidden=True)
    @checks.is_owner()
    async def kill(self, ctx):
        await ctx.send('Killing stream...')
        self._kill_stream()
        await ctx.send('Stream killed!')

    @commands.command(hidden=True)
    @checks.is_owner()
    async def restart(self, ctx):
        await ctx.send('Restarting stream...')
        self._restart_stream()
        await ctx.send('Stream started!')

    @commands.group(hidden=True, pass_context=True, invoke_without_command=True)
    @checks.is_owner()
    async def stalk(self, ctx, info):
        channel = ctx.message.channel.id
        user = self._get_user(info)
        if self._add_channel(user, channel):
            await ctx.send(f'Added user {user.screen_name} to channel {ctx.message.channel.name} follow queue!')
        else:
            await ctx.send(f'Added user {user.screen_name} to channel {ctx.message.channel.name} unfollow queue!')

    @stalk.command(name='list', pass_context=True, hidden=True)
    async def slist(self, ctx):
        channel = ctx.message.channel.id

        stalks = []

        for key in self.follows:
            channels = self.destinations['destinations'][key]

            if channel in channels:
                stalks.append(key)

        if len(stalks):
            await ctx.send('Stalked twitter accounts on this channel: ' + str(stalks))
        else:
            await ctx.send('This channel is not stalking any twitter accounts.')

    @commands.command(hidden=True, pass_context=True)
    @checks.is_owner()
    async def blacklist(self, ctx):
        channel = ctx.message.channel.id
        if not self._blacklist_channel(channel):
            await ctx.send("Now blacklisting this channel.")
        else:
            await ctx.send("No longer blacklisting this channel.")


def setup(bot):
    bot.add_cog(Twitter(bot))