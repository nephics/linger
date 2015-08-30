#!/usr/bin/env python3

import json

from datetime import timedelta

from tornado.concurrent import Future
from tornado.gen import coroutine, sleep, with_timeout, TimeoutError
from tornado.httpclient import AsyncHTTPClient
from tornado.ioloop import IOLoop
from tornado.web import RequestHandler, Application


td = timedelta(seconds=5)


class ConfirmHandler(RequestHandler):

    def get(self):
        f = self.settings['confirm']
        f.set_result(self.request.full_url())


@coroutine
def send_confirmation(confirm_future):

    headers = {
        'x-amz-sns-message-type': 'SubscriptionConfirmation',
        'x-amz-sns-message-id': '165545c9-2a5c-472c-8df2-7ff2be2b3b1b',
        'x-amz-sns-topic-arn': 'arn:aws:sns:us-west-2:123456789012:MyTopic',
        'Content-Type': 'text/plain; charset=UTF-8',
        'User-Agent': 'Amazon Simple Notification Service Agent'
    }

    body = """{
  "Type" : "SubscriptionConfirmation",
  "MessageId" : "165545c9-2a5c-472c-8df2-7ff2be2b3b1b",
  "Token" : "2336412f37fb687f5d51e6e241d09c805a5a57b30d712f794cc5f6a988666d92768dd60a747ba6f3beb71854e285d6ad02428b09ceece29417f1f02d609c582afbacc99c583a916b9981dd2728f4ae6fdb82efd087cc3b7849e05798d2d2785c03b0879594eeac82c01f235d0e717736",
  "TopicArn" : "arn:aws:sns:us-west-2:123456789012:MyTopic",
  "Message" : "You have chosen to subscribe to the topic arn:aws:sns:us-west-2:123456789012:MyTopic.\\nTo confirm the subscription, visit the SubscribeURL included in this message.",
  "SubscribeURL" : "http://127.0.0.1:8889/?Action=ConfirmSubscription&TopicArn=arn:aws:sns:us-west-2:123456789012:MyTopic&Token=2336412f37fb687f5d51e6e241d09c805a5a57b30d712f794cc5f6a988666d92768dd60a747ba6f3beb71854e285d6ad02428b09ceece29417f1f02d609c582afbacc99c583a916b9981dd2728f4ae6fdb82efd087cc3b7849e05798d2d2785c03b0879594eeac82c01f235d0e717736",
  "Timestamp" : "2012-04-26T20:45:04.751Z",
  "SignatureVersion" : "1",
  "Signature" : "EXAMPLEpH+DcEwjAPg8O9mY8dReBSwksfg2S7WKQcikcNKWLQjwu6A4VbeS0QHVCkhRS7fUQvi2egU3N858fiTDN6bkkOxYDVrY0Ad8L10Hs3zH81mtnPk5uvvolIC1CXGu43obcgFxeL3khZl8IKvO61GWB6jI9b5+gLPoBc1Q=",
  "SigningCertURL" : "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-f3ecfb7224c7233fe7bb5f59f96de52f.pem"
}"""

    http = AsyncHTTPClient()

    try:
        yield with_timeout(td, http.fetch(
            'http://127.0.0.1:8989/channels/test', headers=headers,
            method='POST', body=body))
    except TimeoutError:
        print('Failed to deliver SNS subscription (timeout).')
        return False

    try:
        url = yield with_timeout(td, confirm_future)
    except TimeoutError:
        print('Failed to confirm SNS subscription (timeout).')
        return False
    else:
        if url != json.loads(body)['SubscribeURL']:
            print('URL mismatch in SNS subscription confirmation.')
            return False

    return True


@coroutine
def send_notification():

    headers = {
        'x-amz-sns-message-type': 'Notification',
        'x-amz-sns-message-id': '22b80b92-fdea-4c2c-8f9d-bdfb0c7bf324',
        'x-amz-sns-topic-arn': 'arn:aws:sns:us-west-2:123456789012:MyTopic',
        'x-amz-sns-subscription-arn': 'arn:aws:sns:us-west-2:123456789012:'
                                      'MyTopic:c9135db0-26c4-47ec-8998-'
                                      '413945fb5a96',
        'Content-Type': 'text/plain; charset=UTF-8',
        'User-Agent': 'Amazon Simple Notification Service Agent'
    }

    body = """{
  "Type" : "Notification",
  "MessageId" : "22b80b92-fdea-4c2c-8f9d-bdfb0c7bf324",
  "TopicArn" : "arn:aws:sns:us-west-2:123456789012:MyTopic",
  "Subject" : "My First Message",
  "Message" : "Hello world!",
  "Timestamp" : "2012-05-02T00:54:06.655Z",
  "SignatureVersion" : "1",
  "Signature" : "EXAMPLEw6JRNwm1LFQL4ICB0bnXrdB8ClRMTQFGBqwLpGbM78tJ4etTwC5zU7O3tS6tGpey3ejedNdOJ+1fkIp9F2/LmNVKb5aFlYq+9rk9ZiPph5YlLmWsDcyC5T+Sy9/umic5S0UQc2PEtgdpVBahwNOdMW4JPwk0kAJJztnc=",
  "SigningCertURL" : "https://sns.us-west-2.amazonaws.com/SimpleNotificationService-f3ecfb7224c7233fe7bb5f59f96de52f.pem",
  "UnsubscribeURL" : "https://sns.us-west-2.amazonaws.com/?Action=Unsubscribe&SubscriptionArn=arn:aws:sns:us-west-2:123456789012:MyTopic:c9135db0-26c4-47ec-8998-413945fb5a96"
}"""

    chan = 'http://127.0.0.1:8989/channels/test'

    http = AsyncHTTPClient()

    try:
        yield with_timeout(td, http.fetch(
            chan, headers=headers, method='POST', body=body))
    except TimeoutError:
        print('Failed to deliver SNS notification (timeout).')
        return

    try:
        resp = yield with_timeout(td, http.fetch(chan))
    except TimeoutError:
        print('Failed to deliver SNS notification (timeout).')
        return

    if resp.body.decode() != body:
        print('Received something else:')
    else:
        # delete the message
        n = resp.headers['x-linger-msg-id']
        yield http.fetch('http://127.0.0.1:8989/messages/{}'.format(n),
                         method='DELETE')

    return True


@coroutine
def run_tests(settings, io_loop):
    yield sleep(1)
    ok = yield send_confirmation(settings['confirm'])
    if ok:
        ok = yield send_notification()
        if ok:
            print('Tests completed. No errors.')

    io_loop.stop()


@coroutine
def main():

    settings = dict(confirm=Future())

    application = Application([
        (r"/", ConfirmHandler),
    ], **settings)

    application.listen(8889)
    io_loop = IOLoop.current()

    io_loop.add_future(run_tests(settings, io_loop), lambda f: f.result())

    io_loop.start()


if __name__ == "__main__":
    main()
