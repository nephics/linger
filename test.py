
import sys

import tornado.httpclient
from tornado.escape import url_escape, json_decode


def main():

    #
    #  prepare tests
    #

    http = tornado.httpclient.HTTPClient()

    channel = 'test'
    topic = 'some-topic'

    base_url = 'http://127.0.0.1:8989'
    channel_url = base_url + '/channels/' + channel
    topic_path = '/topics/' + topic
    topic_url = base_url + topic_path
    subscribe_url = channel_url + topic_path
    messages_url = base_url + '/messages/'

    msgs = []

    def is_clean():
        resp = http.fetch(base_url + '/stats')
        data = json_decode(resp.body)
        assert data['current-messages'] == 0

    try:
        is_clean()
    except AssertionError:
        print('Linker contains messages. Tests cannot be run.')
        sys.exit(1)

    def e(s):
        return 'msg={}'.format(url_escape(s))

    def post(url, msg, params='?linger=10'):
        resp = http.fetch(url + params, method='POST', body=e(msg))
        assert resp.code == 202
        data = json_decode(resp.body)
        msgs.append((data['id'], msg))

    def sub(url, params='?linger=10'):
        resp = http.fetch(url + params, method='PUT', body='')
        assert resp.code == 204

    def unsub(url):
        resp = http.fetch(url, method='DELETE')
        assert resp.code == 204

    def publish(url, msg, chan=None):
        resp = http.fetch(url, method='POST', body=e(msg))
        assert resp.code == 202
        data = json_decode(resp.body)
        if chan:
            # the channel should be a subscriber
            assert next(iter(data.keys())) == chan
        else:
            # no subscribers
            assert len(data) == 0
        for i in data.values():
            msgs.append((i, msg))

    def get(url):
        resp = http.fetch(url)
        assert resp.code == 200
        msg_id, msg = msgs.pop(0)
        assert int(resp.headers['X-LINGER-MSG-ID']) == msg_id
        assert resp.body.decode() == msg
        return msg_id

    def delete(msgid):
        resp = http.fetch(messages_url + str(msgid), method='DELETE')
        assert resp.code == 204

    #
    # perfom tests
    #

    # post message to channel
    post(channel_url, 'Do this!')

    # create topic subscription
    sub(subscribe_url)

    # publish to topic
    publish(topic_url, 'Have you heard?', channel)

    # get the first message from channel
    msg_id = get(channel_url)

    # delete the message
    delete(msg_id)

    # delete the topic subscription
    unsub(subscribe_url)

    # publish to topic
    publish(topic_url, 'Not getting through!')

    # post message to channel
    post(channel_url, 'Now do that!')

    # get the remaining messages and delete them
    msg_id = get(channel_url)
    delete(msg_id)
    msg_id = get(channel_url)
    delete(msg_id)

    assert len(msgs) == 0
    is_clean()

    print('Tests completed. No errors.')

if __name__ == '__main__':
    main()
