import time
import unittest

from tornado.escape import url_escape, json_decode
from tornado.gen import sleep
from tornado.testing import (AsyncTestCase, AsyncHTTPTestCase, gen_test,
                             main as testing_main)
from tornado.options import options

from linger import linger

options.logging = None


class UnitTestMethods(AsyncTestCase):

    def setUp(self):
        super().setUp()
        self.q = linger.LingerQueue()
        self.kwargs = {  # default add_message kwargs
            'chan_name': 'test',
            'body': 'test msg',
            'mime_type': 'text/plain',
            'priority': 0,
            'timeout': 30,
            'deliver': 0,
            'linger': 0,
            'topic': ''
        }

    def check_msg(self, msg, orig, delay=0.1):
        """Check that msg match the original"""
        now = time.time()
        self.assertIsNotNone(msg)
        self.assertEqual(msg['channel'], orig['chan_name'])
        self.assertEqual(msg['body'], orig['body'])
        self.assertEqual(msg['mimetype'], orig['mime_type'])
        self.assertEqual(msg['topic'], orig['topic'])
        self.assertEqual(msg['timeout'], orig['timeout'])
        self.assertEqual(msg['priority'], orig['priority'])
        self.assertTrue(msg['ts'] < now)
        self.assertEqual(msg['linger'], orig['linger'])
        self.assertEqual(msg['purge'], 0)
        self.assertEqual(msg['deliver'], orig['deliver'])
        self.assertEqual(msg['dcount'], 1)
        # assume that less than delay sec has passed since message was added:
        self.assertTrue(msg['show'] >= orig['timeout'] - delay + now)
        self.assertTrue(msg['show'] <= orig['timeout'] + now)

    @gen_test
    def test_add_get(self):
        """Add msg, get it again"""
        self.q.add_message(**self.kwargs)
        future = self.q.get_message(self.kwargs['chan_name'])
        msg = yield future
        self.check_msg(msg, self.kwargs)

    @gen_test
    def test_add_get_nowait(self):
        """Add msg, get it again without waiting"""
        self.q.add_message(**self.kwargs)
        future = self.q.get_message(self.kwargs['chan_name'], nowait=True)
        msg = yield future
        self.check_msg(msg, self.kwargs)

    @gen_test
    def test_get_wait_add(self):
        """Ask for msg, wait, add msg, get it again"""
        future = self.q.get_message(self.kwargs['chan_name'])
        self.q.add_message(**self.kwargs)
        msg = yield future
        self.check_msg(msg, self.kwargs)

    @gen_test
    def test_drain_channel(self):
        """Add msgs to channel, drain, check channel is empty"""
        count = 3
        for i in range(count):
            self.q.add_message(**self.kwargs)
        chan_name = self.kwargs['chan_name']
        channels = self.q.list_channels()

        # test: exists only this one channel
        self.assertEqual(channels, [chan_name])

        # test: all and only the messages added are ready, none are hidden
        stats = self.q.channel_stats(chan_name)
        self.assertEqual(stats, {'ready': count, 'hidden': 0})

        # test: no messages left in channel
        self.q.drain_channel(chan_name)
        stats = self.q.channel_stats(chan_name)
        self.assertEqual(sum(stats.values()), 0)

    @gen_test
    def test_subscribe_unsubscribe(self):
        """Subscribe and unsubscribe topic"""
        self.kwargs['topic'] = 'some-topic'
        kwargs = {k: self.kwargs[k] for k in (
            'chan_name', 'topic', 'priority', 'timeout', 'deliver', 'linger')}
        self.q.add_subscription(**kwargs)

        chan_name = kwargs['chan_name']
        topic = kwargs['topic']
        channels = self.q.list_channels()

        # test: exists only this one channel
        self.assertEqual(channels, [chan_name])

        # test: exists only this one topic (globally)
        topics = self.q.list_topics()
        self.assertEqual(topics, [topic])

        # test: exists only this one topic for this channel
        topics = self.q.list_topics_for_channel(chan_name)
        self.assertEqual(topics, [topic])

        # subscribe first channel to another topic
        all_topics = [topic]
        kwargs['topic'] = 'another-topic'  # 2nd topic
        all_topics.append(kwargs['topic'])
        all_topics.sort()
        self.q.add_subscription(**kwargs)

        # test: exists only these two topics (globally)
        topics = self.q.list_topics()
        self.assertEqual(topics, all_topics)

        # test: exists only these two topics for this channel
        topics = self.q.list_topics_for_channel(chan_name)
        self.assertEqual(topics, all_topics)

        # subscribe another channel to the second topic
        all_channels = [chan_name]
        kwargs['chan_name'] = 'another-test'  # 2nd channel
        all_channels.append(kwargs['chan_name'])
        all_channels.sort()
        self.q.add_subscription(**kwargs)

        # test: exists only these two topics (globally)
        topics = self.q.list_topics()
        self.assertEqual(topics, all_topics)

        # test: exists only these two topics for the first channel
        topics = self.q.list_topics_for_channel(chan_name)
        self.assertEqual(topics, all_topics)

        # test: exists only the second topic for the second channel
        topics = self.q.list_topics_for_channel(kwargs['chan_name'])
        self.assertEqual(topics, [kwargs['topic']])

        # test: the first topic has only the first channel as subscriber
        channels = self.q.list_topic_subscribers(topic)
        self.assertEqual(channels, [chan_name])

        # test: the second topic has both channels as subscribers
        channels = self.q.list_topic_subscribers(kwargs['topic'])
        self.assertEqual(channels, all_channels)

        # unsucsbribe the second channel from the second topic
        self.q.delete_subscription(kwargs['chan_name'], kwargs['topic'])

        # test: the second topic has only the first channel as subscriber
        channels = self.q.list_topic_subscribers(kwargs['topic'])
        self.assertEqual(channels, [chan_name])

        # unsucsbribe the first channel from the first topic
        self.q.delete_subscription(chan_name, topic)

        # test: exists only the first channel
        channels = self.q.list_channels()
        self.assertEqual(channels, [chan_name])

        # test: exists only the second topic (globally)
        topics = self.q.list_topics()
        self.assertEqual(topics, [kwargs['topic']])

        # unsucsbribe the first channel from the second topic
        self.q.delete_subscription(chan_name, kwargs['topic'])

        # test: exists no topics (globally)
        topics = self.q.list_topics()
        self.assertTrue(len(topics) == 0)

        # test: are no more channels
        channels = self.q.list_channels()
        self.assertTrue(len(channels) == 0)

    @gen_test
    def test_publish_subscribe(self):
        """Publish-Subscribe test"""
        self.kwargs['topic'] = 'some-topic'
        sub_kwargs = {k: self.kwargs[k] for k in (
            'chan_name', 'topic', 'priority', 'timeout', 'deliver', 'linger')}
        # add subscription twice (and confirm that there is no "double-delivery")
        self.q.add_subscription(**sub_kwargs)
        self.q.add_subscription(**sub_kwargs)

        # publish message on topic
        pub_kwargs = {k: self.kwargs[k] for k in (
            'topic', 'body', 'mime_type')}
        self.q.publish_message(**pub_kwargs)
        # get message from channel (subscribed to topic)
        msg = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.check_msg(msg, self.kwargs)
        # check for double-delivery
        msg2 = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.assertIsNone(msg2)

    @gen_test
    def test_timeout(self):
        """Timeout test"""
        self.kwargs['timeout'] = 1  # hide for only 1 sec
        self.q.add_message(**self.kwargs)
        future = self.q.get_message(self.kwargs['chan_name'], nowait=True)
        msg = yield future

        # test: no message ready
        future = self.q.get_message(self.kwargs['chan_name'], nowait=True)
        msg2 = yield future
        self.assertIsNone(msg2)

        # test: message is ready again
        yield sleep(1.2)
        future = self.q.get_message(self.kwargs['chan_name'], nowait=True)
        msg3 = yield future
        self.assertEqual(msg['dcount'], 1)
        self.assertEqual(msg3['dcount'], 2)
        self.assertTrue(msg['show'] < msg3['show'])
        del msg['dcount']
        del msg['show']
        del msg3['dcount']
        del msg3['show']
        self.assertEqual(msg, msg3)

    @gen_test
    def test_linger(self):
        """Linger test"""
        self.kwargs['linger'] = 1  # purge after 1 sec
        self.q.add_message(**self.kwargs)

        # test: message has been purged
        yield sleep(1.2)
        msg = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.assertIsNone(msg)

    @gen_test
    def test_deliver(self):
        """Deliver test"""
        self.kwargs['deliver'] = 1  # deliver only once
        self.kwargs['timeout'] = 1  # hide for only 1 sec
        self.q.add_message(**self.kwargs)
        msg = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.check_msg(msg, self.kwargs)

        # test: message has been purged
        yield sleep(1.2)
        future = self.q.get_message(self.kwargs['chan_name'], nowait=True)
        msg2 = yield future
        self.assertIsNone(msg2)

    @gen_test
    def test_priority(self):
        """Priority test"""
        # add message with priority 0
        self.kwargs['priority'] = 0
        self.kwargs['body'] = '1'
        self.q.add_message(**self.kwargs)

        # add message with priority 1
        self.kwargs['priority'] = 1
        self.kwargs['body'] = '2'
        self.q.add_message(**self.kwargs)

        # add message with priority -1
        self.kwargs['priority'] = -1
        self.kwargs['body'] = '0'
        self.q.add_message(**self.kwargs)

        for i in range(3):
            msg = yield self.q.get_message(
                self.kwargs['chan_name'], nowait=True)
            self.assertEqual(msg['priority'], i-1)
            self.assertEqual(int(msg['body']), i)

    @gen_test
    def test_touch(self):
        """Timeout-Touch test"""
        self.kwargs['timeout'] = 1  # hide for only 1 sec
        self.q.add_message(**self.kwargs)

        msg = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.check_msg(msg, self.kwargs)

        # touch msg 3 times, before it is shown again
        for _ in range(3):
            yield sleep(0.9)
            self.assertTrue(self.q.touch_message_from_id(msg['id']))
            # test that the message is not delivered (because it is hidden)
            msg2 = yield self.q.get_message(
                self.kwargs['chan_name'], nowait=True)
            self.assertIsNone(msg2)
        yield sleep(1.2)
        # test that msg is timed out and re-delivered
        msg3 = yield self.q.get_message(self.kwargs['chan_name'], nowait=True)
        self.assertIsNotNone(msg3)
        self.assertEqual(msg['id'], msg3['id'])


class HTTPTestMethods(AsyncHTTPTestCase):

    channel = 'test'
    topic = 'some-topic'

    channel_url = '/channels/' + channel
    topic_path = '/topics/' + topic
    topic_url = topic_path
    subscribe_url = channel_url + topic_path
    messages_url = '/messages/'

    def get_app(self):
        application, self.settings = linger.make_app()
        return application

    def setUp(self):
        super().setUp()
        self.msgs = []

    def tearDown(self):
        cb = self.settings.get('shutdown_callback')
        if cb is not None:
            cb()
        super().tearDown()

    #
    # Test helper methods
    #

    def e(self, s):
        return 'msg={}'.format(url_escape(s))

    def is_clean(self):
        resp = self.fetch('/stats')
        self.assertEqual(resp.code, 200)
        data = json_decode(resp.body.decode())
        self.assertEqual(data['current-messages'], 0)

    def post(self, url, msg, params='?linger=10'):
        resp = self.fetch(url + params, method='POST', body=self.e(msg))
        self.assertEqual(resp.code, 202)
        data = json_decode(resp.body)
        self.msgs.append((data['id'], msg))

    def sub(self, url, params='?linger=10'):
        resp = self.fetch(url + params, method='PUT', body='')
        self.assertEqual(resp.code, 204)

    def unsub(self, url):
        resp = self.fetch(url, method='DELETE')
        self.assertEqual(resp.code, 204)

    def publish(self, url, msg, chan=None):
        resp = self.fetch(url, method='POST', body=self.e(msg))
        self.assertEqual(resp.code, 202)
        data = json_decode(resp.body)
        if chan:
            # the channel should be a subscriber
            self.assertEqual(next(iter(data.keys())), chan)
        else:
            # no subscribers
            self.assertEqual(len(data), 0)
        for i in data.values():
            self.msgs.append((i, msg))

    def get(self, url):
        resp = self.fetch(url)
        self.assertEqual(resp.code, 200)
        msg_id, msg = self.msgs.pop(0)
        self.assertEqual(int(resp.headers['X-LINGER-MSG-ID']), msg_id)
        self.assertEqual(resp.body.decode(), msg)
        return msg_id

    def delete(self, msgid):
        resp = self.fetch(self.messages_url + str(msgid), method='DELETE')
        self.assertEqual(resp.code, 204)

    #
    # Test methods
    #

    def test_all(self):
        """Run some simple HTTP tests.

        More thorough testing is performed in the lingerclient package.
        """
        self.is_clean()

        # post message to channel
        self.post(self.channel_url, 'Do this!')

        # create topic subscription
        self.sub(self.subscribe_url)

        # publish to topic
        self.publish(self.topic_url, 'Have you heard?', self.channel)

        # get the first message from channel
        msg_id = self.get(self.channel_url)

        # delete the message
        self.delete(msg_id)

        # delete the topic subscription
        self.unsub(self.subscribe_url)

        # publish to topic
        self.publish(self.topic_url, 'Not getting through!')

        # post message to channel
        self.post(self.channel_url, 'Now do that!')

        # get the remaining messages and delete them
        msg_id = self.get(self.channel_url)
        self.delete(msg_id)
        msg_id = self.get(self.channel_url)
        self.delete(msg_id)

        assert len(self.msgs) == 0
        self.is_clean()


def all():
    tests = unittest.defaultTestLoader.loadTestsFromTestCase(UnitTestMethods)
    tests.addTests(
        unittest.defaultTestLoader.loadTestsFromTestCase(HTTPTestMethods))
    return tests


if __name__ == '__main__':
    testing_main()
