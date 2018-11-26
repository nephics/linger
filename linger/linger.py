#!/usr/bin/env python3
"""Linger - Message queue and pubsub service with HTTP API

Copyright 2015-2018 Nephics AB
Licensed under the Apache License, Version 2.0
"""

import logging
import os
import os.path
import platform
import sqlite3
import sys
import time
import uuid

import tornado.concurrent
import tornado.ioloop
import tornado.web

from tornado.gen import coroutine
from tornado.options import (define, options, parse_config_file)

__version__ = '1.0.1'


define('config', type=str, help='path to config file',
       callback=lambda path: parse_config_file(path, final=False),
       group='application')
define('debug', default=False, help='run in debug mode', type=bool,
       group='application')
define('hlm', default=0, type=int, group='application',
       help='high-level mark, max number of messages to queue per channel')
define('port', default=8989, help='run on the given port', type=int,
       group='application')
define('dbfile', default=':memory:', type=str, help='database file',
       group='application')


class HighLevelMarkError(Exception):
    pass


class Listeners:
    """A class for notifying channel listeners."""

    # time to keep a future
    time_out = 120.0  # 2 mins

    def __init__(self):
        self.futures = []

        # the interval in ms between pruning callbacks (in ms)
        self.periodic_callback = tornado.ioloop.PeriodicCallback(
            self.heartbeat, 10000.0)
        self.periodic_callback.start()
        self.touch()

    def touch(self):
        self.ts = time.time()

    def now_ms(self):
        """Time in miliseconds"""
        return int(time.time() * 1000)

    def stop(self):
        """Stop the heartbeats"""
        self.periodic_callback.stop()

    def heartbeat(self):
        """Prune expired callbacks"""
        if not self.futures:
            return
        # expired callbacks get None
        now = time.time()
        keep = []
        for future, ts in self.futures:
            if future.done():
                continue
            elif ts < now:
                future.set_result(None)
            else:
                keep.append((future, ts))
        self.futures = keep

    def add_future(self, future):
        """Register a future for delivering a message to a listener"""
        self.futures.append((future, time.time() + self.time_out))
        self.touch()

    def deliver(self, msg):
        """Deliver message to first listener"""
        self.touch()
        while self.futures:
            future, _ = self.futures.pop(0)
            if future.done():
                continue
            future.set_result(msg)
            return True
        return False

    def __bool__(self):
        return bool(self.futures)

    def __len__(self):
        return len(self.futures)


class SQLDB:
    """A lightweight wrapper for sqlite"""

    def __init__(self, dbfile):
        self.dbfile = dbfile
        try:
            self.conn = sqlite3.connect(dbfile)
        except Exception as e:
            logging.error('Failed to open database file "{}". Error: {}'
                          .format(dbfile, e))
            sys.exit(1)
        self.conn.row_factory = sqlite3.Row

    def table_names(self):
        """Get the table names in the db (excluding sqlite internals and
        index tables)
        """
        cursor = self.conn.cursor()
        cursor.execute('SELECT name FROM sqlite_master')
        return [row[0] for row in cursor.fetchall()
                if not row[0].startswith('sqlite') and
                not row[0].startswith('idx_')]

    def execute(self, *args):
        """Execute a SQL query"""
        return self.conn.execute(*args)

    def executemany(self, *args):
        """Execute a SQL query"""
        return self.conn.executemany(*args)

    def cursor(self):
        """Get a database connection cursor"""
        return self.conn.cursor()

    def commit(self):
        """Commit the database to disc"""
        self.conn.commit()

    def close(self):
        """Close the database connection"""
        self.conn.close()

    def size(self):
        """The current file size of the database"""
        if self.dbfile == ':memory:':
            return -1
        return os.stat(self.dbfile).st_size

    def compact(self):
        """Compact the database and return the number of bytes saved"""
        before = self.size()
        self.execute('VACUUM')
        self.commit()
        after = self.size()
        return before - after


class LingerQueue:

    # time-to-live for channels, after they are last used
    channel_ttl = 3 * 60.0  # 3 minutes

    msg_max_size = 256 * 1000  # 256 KB in bytes

    # recognized configuration keys
    _config_keys = ('server_id',)

    def __init__(self, dbfile=':memory:', hlm=0):
        self.dbfile = dbfile
        self.hlm = hlm
        self.stats = {'start': int(time.time())}

        # mapping of channel name -> Listerners
        # for channels that have active listeners
        self.channels = {}

        self.db = SQLDB(dbfile)

        if 'config' not in self.db.table_names():
            self.init_db()
        else:
            if dbfile != ':memory:':
                bts = self.db.compact()
                logging.info('Compacted database, saved {} bytes'.format(bts))
            self.restore_from_db()

        self.periodic_callback = tornado.ioloop.PeriodicCallback(
            self.heartbeat, 1000)
        self.periodic_callback.start()

    def init_db(self):
        # default config
        self.config = {
            'server_id': uuid.uuid4().hex  # a random server_id
        }
        # create tables
        self.db.execute('create table config (key unique,value)')
        # columns of table 'messages'
        # id        unique id
        # body      the message body
        # mimetype  mime-type
        # topic     message topic
        # timeout   visibility timeout (seconds)
        # priority  message priority
        # channel   channel name
        # ts        timestamp (when added to the queue)
        # linger    message retention (seconds)
        # purge     when to purge the message (timestamp)
        # deliver   max devliver count
        # dcount    delivered count
        # show      timestamp when message should be shown
        #           (show>0 is in timeout and may not be visible)
        self.db.execute(
            'create table messages (id integer primary key autoincrement, '
            'body, mimetype, topic, timeout, priority, channel, ts, linger, '
            'purge, deliver, dcount, show)')
        # columns of table 'subscriptions'
        # topic      topic name
        # channel    channel name
        # timeout    visibility timeout (seconds)
        # priority   message priority
        # linger     message retention (seconds)
        # deliver    max deliver count
        # ts         timestamp (when created)
        self.db.execute(
            'create table subscriptions (topic,channel,timeout,priority,'
            'linger,deliver,ts, primary key (topic, channel))')
        # save config
        self.db.executemany(
            'insert or replace into config (key,value) values (?,?)',
            [(k, self.config[k]) for k in self._config_keys])
        self.db.commit()

    def restore_from_db(self):
        # load config
        self.config = {r['key']: r['value'] for r in
                       self.db.execute('select * from config')
                       if r['key'] in self._config_keys}

    def stop(self):
        self.periodic_callback.stop()
        for listeners in self.channels.values():
            listeners.stop()
        self.db.close()

    def heartbeat(self):
        """Heartbeat function, called periodically from the IOLoop."""

        now = time.time()

        # purge messages that have exceed their rentention time
        purge = [r['id'] for r in self.db.execute(
                    'select id from messages where purge>0 and purge<?',
                    (now,))]
        for msg_id in purge:
            logging.debug('Exceeded retention on msg {}'.format(msg_id))
            self.stats['msg-retention'] = (
                self.stats.get('msg-retention', 0) + 1)
            self.count_deleted(msg_id)

        # try to deliver messages that exceeds the visibility timeout
        visible = (dict((k, r[k]) for k in r.keys()) for r in self.db.execute(
                   'select * from messages where show>0 and show<=? and not '
                   '(purge>0 and purge<?) order by priority', (now, now)))
        undelivered = []
        for msg in visible:
            logging.debug('Exceeded timeout on msg {}'.format(msg['id']))
            self.stats['msg-timeouts'] = self.stats.get('msg-timeouts', 0) + 1
            if msg['deliver'] == 0 or msg['dcount'] < msg['deliver']:
                # try to deliver the message
                if not self.deliver_message(msg):
                    # count it as shown
                    self.stats['msg-show'] = self.stats.get('msg-show', 0) + 1
                    undelivered.append(msg['id'])
            else:
                # message delivered to many times, purge it
                purge.append(msg['id'])
                self.count_deleted(msg['id'])

        if undelivered:
            # set messages to be visible (out of timeout)
            self.db.executemany('update messages set show=0.0 where id=?',
                                ((i,) for i in undelivered))
        if purge:
            self.db.executemany('delete from messages where id=?',
                                ((i,) for i in purge))
        if purge or undelivered:
            self.db.commit()

        # check if channel has listeners, otherwise remove it when timed out
        unused = [(ch, lst) for ch, lst in self.channels.items()
                  if not lst and lst.ts + self.channel_ttl < now]
        for channel, listeners in unused:
            # channel is no longer in use
            logging.debug('Removing empty channel {}'.format(channel))
            listeners.stop()
            del self.channels[channel]
            self.stats['channel-remove'] = (
                self.stats.get('channel-remove', 0) + 1)

    def server_stats(self):
        s = self.stats.copy()
        times = os.times()
        total = self.db.execute('select count(*) from messages').fetchone()[0]
        urgent = self.db.execute(
            'select count(*) from messages where priority<0').fetchone()[0]
        normal = self.db.execute(
            'select count(*) from messages where priority=0').fetchone()[0]
        now = time.time()
        hidden = self.db.execute(
            'select count(*) from messages where ts>0 and ts>?',
            (now,)).fetchone()[0]
        topic_count = self.db.execute(
            'select count(distinct topic) from subscriptions').fetchone()[0]
        sub_count = self.db.execute(
            'select count(*) from subscriptions').fetchone()[0]
        chan_count = len(self.list_channels())
        msg_max_no = self.db.execute(
            'select max(id) from messages').fetchone()[0]
        s.update({
            'current-topics': topic_count,
            'current-subscriptions': sub_count,
            'current-channels': chan_count,
            'current-messages': total,
            'current-messages-ready': total - hidden,
            'current-messages-hidden': hidden,
            'current-messages-urgent': urgent,
            'current-messages-niced': total - normal - urgent,
            'current-uptime': int(time.time() - self.stats['start']),
            'db-file': self.dbfile,
            'db-size': self.db.size(),
            'pid': os.getpid(),
            'rusage-utime': times[0],
            'rusage-stime': times[1],
            'version': __version__,
            'msg-max-id': msg_max_no,
            'msg-max-size': self.msg_max_size,
            'id': self.config['server_id'],
            'hostname': platform.uname()[1]
        })
        return s

    #
    #  channel and message functions

    def add_message(self, chan_name, body, mime_type, priority, timeout,
                    deliver, linger, topic=''):
        """Add message to the queue"""
        msg_size = len(body)
        if msg_size == 0:
            raise ValueError('Message is empty.')
        if msg_size > self.msg_max_size:
            raise ValueError('The message size {} bytes exceeed the maximum '
                             'allowed {} bytes.'.format(
                                 msg_size, self.msg_max_size))
        msgs_count = self.db.execute(
            'select count(*) from messages where channel=?',
            (chan_name,)).fetchone()[0]
        if self.hlm > 0 and msgs_count >= self.hlm:
            raise HighLevelMarkError(
                'Channel {} is at the high-level mark with {} messages'
                .format(chan_name, msgs_count))
        now = time.time()
        purge = now + linger if linger > 0 else 0

        msg = {
            'body': body,           # message body
            'mimetype': mime_type,  # mime-type
            'topic': topic,         # message topic
            'timeout': timeout,     # visibility timeout (seconds)
            'priority': priority,   # message priority
            'channel': chan_name,   # channel name
            'ts': now,              # timestamp (when added to the queue)
            'linger': linger,       # message retention (seconds)
            'purge': purge,         # when to purge the message (timestamp)
            'deliver': deliver,     # max delivery count
            'dcount': 0,            # delivered count
            'show': 0.0             # timestamp when message should be shown
        }

        # queue message for delivery
        c = self.db.execute(
            'insert into messages (body, mimetype, topic, timeout, priority,'
            'channel , ts, linger, purge, deliver, dcount, show) values '
            '(:body, :mimetype, :topic, :timeout, :priority, :channel, :ts, '
            ':linger, :purge, :deliver, :dcount, :show)', msg)
        msg['id'] = c.lastrowid     # set message id
        self.db.commit()

        logging.debug('Adding message {}'.format(msg['id']))
        self.stats['msg-add'] = self.stats.get('msg-add', 0) + 1

        if not self.deliver_message(msg):
            # count it as shown (but not deliveried)
            self.stats['msg-show'] = self.stats.get('msg-show', 0) + 1

        return msg['id']

    def deliver_message(self, msg):
        """Attempt to deliver message to current listeners"""
        listeners = self.channels.get(msg['channel'])
        if listeners is not None and listeners.deliver(msg):
            # message is delivered right away
            self.count_delivered(msg['id'])
            self.hide_message(msg)
            return True
        return False

    def hide_message(self, msg):
        """Hide message (timeout from queue) after delivery"""
        msg['show'] = time.time() + msg['timeout']
        msg['dcount'] += 1
        self.db.execute('update messages set show=:show, dcount=:dcount '
                        'where id=:id', msg)
        self.db.commit()
        self.stats['msg-hide'] = self.stats.get('msg-hide', 0) + 1

    def get_message(self, chan_name, nowait=False):
        """Get message from channel, returns a Future"""
        self.stats['msg-get'] = self.stats.get('msg-get', 0) + 1
        future = tornado.concurrent.Future()

        row = self.db.execute(
            'select * from messages where channel=? and show<=? '
            'order by priority', (chan_name, time.time())).fetchone()

        if not row:
            # no messages
            if nowait:
                # empty reply, right away
                future.set_result(None)
            else:
                # hold on to the reply
                channel = self.channels.get(chan_name)
                if channel is None:
                    logging.debug('Creating channel {}'.format(chan_name))
                    channel = Listeners()
                    self.channels[chan_name] = channel
                    self.stats['channel-create'] = (
                        self.stats.get('channel-create', 0) + 1)
                channel.add_future(future)
        else:
            # a message is ready for delivery
            msg = {k: row[k] for k in row.keys()}
            self.count_delivered(msg['id'])
            self.hide_message(msg)
            future.set_result(msg)

        return future

    def drain_channel(self, chan_name):
        self.stats['channel-drain'] = self.stats.get('channel-drain', 0) + 1
        c = self.db.execute('delete from messages where channel=?',
                            (chan_name,)).rowcount
        logging.debug('Drained {} messages from channel {}'
                      .format(c, chan_name))
        self.db.commit()

    def channel_stats(self, chan_name):
        ready_count = self.db.execute(
                'select count(*) from messages where channel=? and show=0',
                (chan_name,)
            ).fetchone()[0]
        hidden_count = self.db.execute(
                'select count(*) from messages where channel=? and show>0',
                (chan_name,)
            ).fetchone()[0]
        return {'ready': ready_count, 'hidden': hidden_count}

    def count_delivered(self, msg_id):
        logging.debug('Delivering message {}'.format(msg_id))
        self.stats['delivered'] = self.stats.get('delivered', 0) + 1

    def count_deleted(self, msg_id):
        logging.debug('Deleting message {}'.format(msg_id))
        self.stats['msg-delete'] = self.stats.get('msg-delete', 0) + 1

    def touch_message_from_id(self, msg_id):
        row = self.db.execute('select show, timeout from messages where id=?',
                              (msg_id,)).fetchone()
        if row is None:
            return False
        show = time.time() + row['timeout']
        self.db.execute('update messages set show=? where id=?', (show, msg_id))
        self.db.commit()
        self.stats['msg-touch'] = self.stats.get('msg-touch', 0) + 1
        return True

    def delete_message_from_id(self, msg_id):
        delete_count = self.db.execute(
            'delete from messages where id=?', (msg_id,)).rowcount
        self.db.commit()
        if delete_count != 1:
            logging.debug('Attempt at deleting non-existent message {}'
                          .format(msg_id))
            return False
        self.count_deleted(msg_id)
        return True

    def add_subscription(self, chan_name, topic, priority, timeout, deliver,
                         linger):
        sub = {
            'channel': chan_name,  # channel name
            'topic': topic,        # topic name
            'timeout': timeout,    # visibility timeout (seconds)
            'priority': priority,  # message priority
            'linger': linger,      # message retention (seconds)
            'deliver': deliver,    # max delivery count
            'ts': time.time()      # timestamp (when created)
        }

        self.db.execute(
            'insert or replace into subscriptions values (:topic,:channel,'
            ':timeout,:priority,:linger,:deliver,:ts)', sub)
        self.db.commit()

        self.stats['subscription-add'] = self.stats.get('sub-add', 0) + 1
        logging.debug('Subscribing {} -> {}'.format(chan_name, topic))

    def delete_subscription(self, chan_name, topic):
        self.db.execute(
            'delete from subscriptions where topic=? and channel=?',
            (topic, chan_name))
        self.db.commit()

        self.stats['subscription-delete'] = self.stats.get('sub-delete', 0) + 1
        logging.debug('Unsubscribing {} -> {}'.format(chan_name, topic))

    def list_channels(self):
        channels = set(r[0] for r in self.db.execute(
            'select distinct channel from messages union '
            'select distinct channel from subscriptions'))
        channels.update(self.channels.keys())
        return list(sorted(channels))

    def list_topics(self):
        topics = [r[0] for r in self.db.execute(
            'select distinct topic from subscriptions order by topic')]
        return topics

    def list_topics_for_channel(self, chan_name):
        topics = [r[0] for r in self.db.execute(
            'select distinct topic from subscriptions where channel=? '
            'order by topic', (chan_name,))]
        return topics

    def list_topic_subscribers(self, topic):
        channels = [r[0] for r in self.db.execute(
            'select channel from subscriptions where topic=? '
            'order by channel', (topic,))]
        return channels

    def publish_message(self, topic, body, mime_type):
        """Publish a message on a topic"""
        published = {}

        subscriptions = [
            {k: row[k] for k in row.keys()} for row in self.db.execute(
                'select * from subscriptions where topic=?', (topic,))]

        if not subscriptions:
            logging.debug('Publishing on {}, no subscribers'.format(topic))
            return published

        logging.debug('Publishing on {}, {} subscribers'.format(
            topic, len(subscriptions)))
        mpk = ('timeout', 'priority', 'linger', 'deliver')
        for sub in subscriptions:
            chan_name = sub['channel']
            params = {k: sub[k] for k in mpk}
            try:
                msg_id = self.add_message(chan_name, body, mime_type,
                                          topic=topic, **params)
            except HighLevelMarkError as e:
                logging.warning(e)
            else:
                published[chan_name] = msg_id
                self.stats['publish'] = self.stats.get('publish', 0) + 1
        return published


class RequestHandler(tornado.web.RequestHandler):

    @property
    def queue(self):
        return self.settings['queue']

    def check_xsrf_cookie(self):
        pass

    def set_default_headers(self):
        self.set_header('Server', 'Linger {}'.format(
            self.queue.config['server_id']))
        self.set_header('Etag', '"{}"'.format(uuid.uuid4().hex))
        self.set_header('Content-Type', 'text/plain')

    def write_error(self, status_code, **kwargs):
        if self.settings.get("serve_traceback") and "exc_info" in kwargs:
            super().write_error(status_code, **kwargs)
        else:
            self.finish('HTTP {} {}\n'.format(status_code, self._reason))


class ReqParamMixin:

    def req_params(self):
        try:
            priority = int(self.get_argument('priority', 0))
        except ValueError:
            self.send_error(400, reason='Invalid message priority.')
            return

        try:
            timeout = int(self.get_argument('timeout', 30))
            if timeout < 1:
                raise ValueError()
        except ValueError:
            self.send_error(400, reason='Invalid message visibility timeout.')
            return

        try:
            deliver = int(self.get_argument('deliver', 0))
            if deliver < 0:
                raise ValueError()
        except ValueError:
            self.send_error(400, reason='Invalid message delivery limit.')
            return

        try:
            linger = int(self.get_argument('linger', 0))
            if linger < 0:
                raise ValueError()
        except ValueError:
            self.send_error(400, reason='Invalid message delivery limit.')
            return

        return dict(priority=priority, timeout=timeout, deliver=deliver,
                    linger=linger)


class ChannelListHandler(RequestHandler):

    def get(self):
        """/channels - list channels"""
        channels = self.queue.list_channels()
        self.finish({'channels': channels})


class ChannelMessagesHandler(RequestHandler, ReqParamMixin):

    def prepare(self):
        self.future = None

    @coroutine
    def get(self, chan_name):
        """/channels/<channel> - get message from channel"""
        nowait = self.get_argument('nowait', None) is not None

        self.future = self.queue.get_message(chan_name, nowait)
        msg = yield self.future

        self.set_header('x-linger-channel', chan_name)

        if msg is None:
            self.set_status(204)
            self.finish()
            return

        # set response headers
        self.set_header('Content-Type', msg['mimetype'])
        self.set_header('x-linger-msg-id', msg['id'])
        self.set_header('x-linger-priority', msg['priority'])
        self.set_header('x-linger-timeout', msg['timeout'])
        self.set_header('x-linger-deliver', msg['deliver'])
        self.set_header('x-linger-delivered', msg['dcount'])
        self.set_header('x-linger-received', int(time.time() - msg['ts']))
        self.set_header('x-linger-linger', int(msg['linger']))
        self.set_header('x-linger-topic', msg['topic'])

        # deliver the message
        self.finish(msg['body'])

    def on_connection_close(self):
        if self.future and not self.future.done():
            logging.debug('Connection closed prematurely')
            self.future.set_result(None)

    def post(self, chan_name):
        """/channels/<channel> - add message to channel"""
        body = self.get_argument('msg', None)
        if body is None:
            body = self.request.body
            mime_type = self.request.headers.get('Content-Type', 'text/plain')
        else:
            mime_type = 'text/plain'

        params = self.req_params()
        if not params:
            return

        try:
            msg_id = self.queue.add_message(
                chan_name, body, mime_type, **params)
        except ValueError as e:
            self.send_error(400, reason=e.args[0])
            return
        except HighLevelMarkError as e:
            self.send_error(507, reason=e.args[0])
            return

        self.set_status(202)
        self.finish({'id': msg_id})

    def delete(self, chan_name):
        """/channels/<channel> - drain the channel"""
        self.queue.drain_channel(chan_name)
        self.set_status(204)


class ChannelStatsHandler(RequestHandler):

    def get(self, chan_name):
        """/channels/<channel>/stats - get channel stats"""
        chan_stats = self.queue.channel_stats(chan_name)
        self.finish(chan_stats)


class ChannelTopicListHandler(RequestHandler):

    def get(self, chan_name):
        """/channels/<channel>/topics - list topics a channel is subscribed to
        """
        topics = self.queue.list_topics_for_channel(chan_name)
        self.finish({'topics': topics})


class ChannelTopicSubHandler(RequestHandler, ReqParamMixin):

    def put(self, chan_name, topic_name):
        """/channels/<channel>/topics/<topic> - subscribe channel to a topic"""
        params = self.req_params()
        if not params:
            return

        self.queue.add_subscription(chan_name, topic_name, **params)
        self.set_status(204)

    def delete(self, chan_name, topic_name):
        """/channels/<channel>/topics/<topic> - unsubscribe channel from topic
        """
        self.queue.delete_subscription(chan_name, topic_name)
        self.set_status(204)


class TopicListHandler(RequestHandler):

    def get(self):
        """/topics - list topics"""
        topics = self.queue.list_topics()
        self.finish({'topics': topics})


class TopicHandler(RequestHandler):

    def post(self, topic):
        """/topics/<topic> - publish message on topic"""
        body = self.get_argument('msg', None)
        if body is None:
            body = self.request.body
            mime_type = self.request.headers.get('Content-Type', 'text/plain')
        else:
            mime_type = 'text/plain'

        try:
            published = self.queue.publish_message(topic, body, mime_type)
        except ValueError as e:
            self.send_error(400, reason=e.args[0])
            return

        self.set_status(202)
        self.finish(published)


class TopicChannelListHandler(RequestHandler):

    def get(self, topic):
        """/topics/<topic>/channels - list channels subscribed to topic"""
        channels = self.queue.list_topic_subscribers(topic)
        self.finish({'channels': channels})


class MessageTouchHandler(RequestHandler):

    def post(self, msg_id):
        """/messages/<msg-id>/touch - touch message (reset timeout)"""
        try:
            msg_id = int(msg_id)
            if msg_id < 0:
                raise ValueError()
        except ValueError:
            self.send_error(400, reason='Invalid message number.')

        if self.queue.touch_message_from_id(msg_id):
            self.set_status(204)
        else:
            self.set_status(404, reason='Message not found.')


class MessageHandler(RequestHandler):

    def delete(self, msg_id):
        """/messages/<msg-id> - delete message"""
        try:
            msg_id = int(msg_id)
            if msg_id < 0:
                raise ValueError()
        except ValueError:
            self.send_error(400, reason='Invalid message number.')

        if self.queue.delete_message_from_id(msg_id):
            self.set_status(204)
        else:
            self.set_status(404, reason='Message not found.')


class StatsHandler(RequestHandler):

    def get(self):
        """/stats - get server stats"""
        self.finish(self.queue.server_stats())


class HomeHandler(RequestHandler):

    def get(self):
        self.finish({'Linger': 'Welcome', 'version': __version__,
                     'id': self.queue.config['server_id'],
                     'hostname': platform.uname()[1]})


def make_app():
    linger_queue = LingerQueue(options.dbfile, options.hlm)

    settings = {
        'debug': options.debug,
        'queue': linger_queue,
        'shutdown_callback': linger_queue.stop,
    }

    handlers = {
        (r'/', HomeHandler),
        (r'/channels/([\w%-]+)/stats', ChannelStatsHandler),
        (r'/channels/([\w%-]+)/topics/([\w%-]+)', ChannelTopicSubHandler),
        (r'/channels/([\w%-]+)/topics', ChannelTopicListHandler),
        (r'/channels/([\w%-]+)', ChannelMessagesHandler),
        (r'/channels', ChannelListHandler),
        (r'/topics/([\w%-]+)/channels', TopicChannelListHandler),
        (r'/topics/([\w%-]+)', TopicHandler),
        (r'/topics', TopicListHandler),
        (r'/messages/(\d+)/touch', MessageTouchHandler),
        (r'/messages/(\d+)', MessageHandler),
        (r'/stats', StatsHandler)
    }

    application = tornado.web.Application(handlers, **settings)
    return application, settings
