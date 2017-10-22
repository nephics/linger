# Linger

*-- Message queue and pub-sub service with HTTP API*

## About

The [linger](https://github.com/nephics/linger) package provides the Linger server, which is a message queue and pub-sub service with a REST HTTP API.

The message queue distributes messages in channels from publishers to consumers. The message queue can be used in situations where a work queue is needed, e.g., for running time-consuming tasks asynchronously.

The pub-sub (publish-subscribe) service distribute messages from publishers to subscribers (both fan-in and fan-out). It can be used in situations where message notification is needed, e.g., for delivering status updates, or change notifications.

The HTTP API server is implemented in Python 3 using a non-blocking, single-threaded [Tornado web server](http://www.tornadoweb.org/).

## Message queue

Linger provides a message queue where messages are added by producers, and made available to consumers.

Producers may add messages to any named channel, which are created on-demand. 

Consumers may subscribe and consume messages from any named channel, but is limited to retrieving a message from one channel per request.

Messages are delivered to consumers at least once, and they may be re-delivered, if the consumer doesn't delete the delivered message.

Messages can be in one of two states: ready or hidden. Where 'ready' messages are those available for delivery to consumers, and hidden messages are not made available to consumers. A message is hidden during a visibility timeout, after being delivered to a consumer.

### The life of a message in the queue

1. A producer adds a new message to a named channel (the queue).
2. A consumer requests a message from the channel, the message is delivered to the consumer.
3. Once the message has been delivered, it will be hidden and not delivered again until the visibility timeout has passed. (This keeps multiple consumers from requesting the same message.)
4. When the consumer has successfully processed the message, they delete the message from the channel.

A message can be assigned a priority which is different from the default, so messages are delivered in a non-sequential order (an order not based on the time messages are added to the queue).

A retention (linger) period may be set on a message, to limit the lifetime of the message in the channel. This can be useful when the channel (for some reason) has no consumers, and you want to limit the age of messages in the channel.

## Pub-sub service

Linger provides pub-sub functionality where messages are posted by publishers, and made available to subscribers.

A message is published on a named topic, and is distributed by Linger to one or more subscribed channels. Each channel may have one or more subscribers that consume the messages of that channel.

### The life of a published message

1. A publisher post a new message to a named topic.
2. The message is distributed to channels subscribed to the topic.
3. A subscriber requests a message from a channel, the message is returned.
4. Once the message has been delivered, it will not be delivered again until the visibility timeout has passed.
5. When the publisher has successfully received a message, they delete the message from the channel.

Notice that step 3 to 5 here is the same as step 2 to 4 in "the life of a message in the queue". In a standard pub-sub model there is only one subscriber per channel, but Linger allows for multiple subscribers, which consume the messages delivered to the channel. Hence, multiple subscribers to same channel will not see the same messages.

When subscribing a channel to a topic, you can set the message priority, retention (linger) period, etc., which will be applied to messages on that topic delivered to the channel.

## Getting started

### Install

Install the `linger` script in `/usr/local/bin` (on Linux and OS X) with the following pip command:

    pip install https://github.com/nephics/linger/archive/master.zip

This will also ensure that the dependency package `tornado` is installed.

### Run

Start a Linger server by running the script:

    linger

from the command line, or use *systemd* or a process-daemon tool like [Supervisor](http://supervisord.org).

The default port is 8989. This and other options can be set from the command line, or using a config file (with path specified as a command line option). Command line options can be listed using argument `--help`.

By default the database is kept in memory, but the server can store the database on disk, both to reduce memory footprint and to allow for full restore in the event of a server/process restart. Use the `--dbfile` command line option to store the database on disk.

It is also possible to define a global a high-level mark limiting the number of messages in any channel. The default is no high-level mark, this setting may be changed using the `--hlm` command line option. 

## Security

The HTTP API is unauthenticated, all clients have unrestricted access to the full API, but you may use a reverse proxy like [nginx](http://nginx.org) to require authentication for network access, see [HTTP Basic Auth](http://nginx.org/en/docs/http/ngx_http_auth_basic_module.html).

## Limits and performance

Long-polling duration is limited to about 2 mins.

Messages are by default limited to 256 KB in size, and may contain any sequences of bytes.

The binlogs are rotated when the file size exceeds 1 MB, and unused binlogs are automatically removed.

Linger is not currently optimised with regard to memory usage, and it has not been tested for high-performance usage scenarios, such as delivering billions of messages a day. But, for most real world situations, Linger will serve you reliably.

## HTTP API overview

The Linger HTTP API consists of these methods:

* GET `/channels`  *- list channels*
* GET `/channels/<channel>` *- get message from channel*
* POST `/channels/<channel>` *- add message to channel*
* DELETE `/channels/<channel>` *- drain the channel*
* GET `/channels/<channel>/stats` *- get channel stats*
* GET `/channels/<channel>/topics` *- list topics a channel is subscribed to*
* PUT `/channels/<channel>/topics/<topic>` *- subscribe channel to a topic*
* DELETE `/channels/<channel>/topics/<topic>` *- unsubscribe channel from topic*
* GET `/topics` *- list topics*
* POST `/topics/<topic>` *- publish message on topic*
* GET `/topics/<topic>/channels` *- list channels subscribed to topic*
* DELETE `/messages/<msg-id>` *- delete message*
* GET `/stats` *- get server stats*

Where `<channel>` is the channel name, where messages can be added and removed. Channel names may contain characters from `a-z` `A-Z` `0-9` `_` `%` `-`. Remember to URL-encode the name when using it in requests, particularly important if the name includes space or slash.

Channels are created on-demand, and are automatically destroyed when they are empty and not in use.

The `<msg-id>` is an integer that identifies a message (uniquely across all channels).

The API methods are described in more detailed in the following sections, with examples using [cURL](http://curl.haxx.se).

## List channels

The list of current channels can be retrieved using a HTTP GET request to `/channels`. Example request:

    curl -X GET http://127.0.0.1:8989/channels

The server responds with HTTP status code 200, and the channel list is included in the response body, which is text encoded as JSON.
Example response:

    {"channels": ['test']}

## Add message to a channel

Add a message to a named channel using a HTTP POST request to `/channels/<channel>`. Example request:

    curl --data-urlencode msg='Do this and that!' \
         http://127.0.0.1:8989/channels/test

If you set any other content-type than "application/x-www-form-urlencoded" and "multipart/form-data", the request body is the message. Example request with JSON encoded message:

    curl -d '{"txt": "Do this and that!"}' \
         -H "Content-Type: application/json" \
         http://127.0.0.1:8989/channels/test

The server responds with HTTP status code 202, and the message id (an integer) encoded as JSON in the response body. Example response:

    {"id": 1}

You can set the message priority, visibility timeout, max delivery attempts, and message retention limit using query parameters:

    priority=10   message priority, a lower number means a
                  higher priority in the channel,
                  default is zero
                  (any integer is accepted)

    timeout=60    wait for 60 seconds before delivering the
                  message to another client, default is
                  30 seconds
                  (accepts an integer greater than zero)

    deliver=5     deliver the message at most 5 times before
                  discarding it, default is zero, which means
                  never discard the message
                  (any positive integer is accepted)

    linger=60     keep the message for 60 seconds, before
                  discarding it, default is zero, which means
                  never discard the message
                  (any positive integer is accepted)

The parameters can be combined in a request. Example request with JSON encoded message:

    curl -d '{"txt": "Do this and that!"}' \
         -H "Content-Type: application/json" \
         "http://127.0.0.1:8989/channels/test?priority=10&timeout=60&deliver=5&linger=60"

Example request with a text message:

    curl --data-urlencode msg='Do this and that!' \
         -d priority=10 -d timeout=60 -d deliver=5 \
         -d linger=60 http://127.0.0.1:8989/channels/test

If the channel is at the high-level-mark, the message cannot be added, and the server responds with the HTTP status code 507 Insufficient Storage.

## Get message from a channel

Get a message from a named channel using a HTTP GET request to `/channels/<channel>`. Example request:

    curl -i http://127.0.0.1:8989/channels/test

The server will either reply with HTTP status code 200, and the  message in the response body, or the server will hold on to the request (long-polling) until a message becomes available in the channel.

The server will automatically end a long-polling request after a couple of minutes. If there is no message available, the server replies with HTTP status code 204, and an empty response body.

The response headers with prefix `x-linger-` contains the Linger message meta data. Example:

    x-linger-msg-id: 1          # message ID
    x-linger-channel: test      # channel name
    x-linger-priority: 10       # priority
    x-linger-timeout: 60        # visibility timeout
    x-linger-deliver: 5         # max delivery attempts
    x-linger-delivered: 1       # count of delivery attempts (including this one)
    x-linger-received: 11       # seconds since the message was received by the channel
    x-linger-linger: 0          # message linger time in the channel (before being discarded)
    x-linger-topic: some-topic  # topic (if message was published to a topic)

The response content-type will be the same as specified when adding the message to the channel (default is text/plain).

By adding the `nowait` query parameter, you may prevent long-polling, and have the server send a response immediately. This implies that the server will return an empty reply, if there is no message waiting in the channel. Example request:

    curl http://127.0.0.1:8989/channels/test?nowait

## Drain the channel

Drain (discard) all messages from channel using a HTTP DELETE request to `/channels/<channel>`. Example request:

    curl -X DELETE http://127.0.0.1:8989/channels/test

The server responds with HTTP status code 204.


## Get channel stats

Retrieve statistics about the messages in a channel using a HTTP GET request to `/channels/<channel>/stats`. Example request:

    curl -X GET http://127.0.0.1:8989/channels/test/stats

The server responds with HTTP status code 200, and the response body contains a JSON encoded mapping of stats about messages in the channel.

Example response:

    {"ready": 2, "hidden": 0}


## List topics a channel is subscribed to

The list of topics a channel is subscribed to can be retrieved using a HTTP GET request to `/channels/<channel>/topics`. Example request:

    curl http://127.0.0.1:8989/channels/test/topics

Example response:

    {"topics": ["some-topic"]}

## Subscribe channel to a topic

Subscribe a channel to a named topic using a HTTP PUT request to `/channels/<channel>/topics/<topic>`. Example request:

    curl -X PUT http://127.0.0.1:8989/channels/test/topics/some-topic

The server responds with HTTP status code 204.

If you add query parameters, the subscription applies these parameters to all messages published to the channel on the specific topic. The possible query parameters are the same as available for when adding a message to a channel (see above). This includes message priority, visibility timeout, max delivery attempts, and message retention limit.

Example request limiting the message retention to 60 seconds for all messages published to the channel on the specific topic:

    curl -X PUT -d linger=60 http://127.0.0.1:8989/channels/test/topics/some-topic

## Unsubscribe channel from a topic

Unsubscribe a channel from a named topic using a HTTP DELETE request to `/channels/<channel>/topics/<topic>`. Example request:

    curl -X DELETE http://127.0.0.1:8989/channels/test/topics/some-topic

The server responds with HTTP status code 204.

## List topics

The list of topics with subscriptions can be retrieved using a HTTP GET request to `/topics`. Example request:

    curl http://127.0.0.1:8989/topics

The server responds with HTTP status code 200, and the response body contains a JSON encoded list of topics. Example response:

    {"topics": ["some-topic"]}


## Publish message on topic

Publish a message on a name topic using a POST request to `/topics/<topic>`. Example request:

    curl --data-urlencode msg='Have you heard!' \
         http://127.0.0.1:8989/topics/some-topic

The message is distributed to subscribed channels (if any). The message priority, etc., is determined by the channel subscriptions to the topic (see above).

The server responds with HTTP status code 202, and the response body contains a JSON encoded mapping of channels and message ids. Example response:

    {"test": 1}

## List channels subscribed to topic

The list of channels subscribed to a named topic can be retrieved using a HTTP GET request to `/topics/<topic>/channels`. Example request:

    curl http://127.0.0.1:8989/topics/some-topic/channels

The server responds with HTTP status code 200, and the response body contains a JSON encoded list of channels. Example response:

    {"channels": ["test"]}

## Delete message

Delete the specified message using a HTTP DELETE request to `/messages/<msg-id>`. Example request:

    curl -X DELETE http://127.0.0.1:8989/messages/1

If successful, the server responds with HTTP status code 204. If the message is not found, HTTP status code 404 is returned.

## Get server stats

Retrieve server statistics and runtime information using a HTTP GET request to `/stats`. Example request:

    curl -X GET http://127.0.0.1:8989/stats

The server responds with HTTP status code 200, and the response body contains a JSON encoded mapping of the available stats.

# Support

Support for the software can be provided on a commercial basis, please see [www.nephics.com](http://www.nephics.com) for contact information.

# License

The code and documentation is licensed under the Apache License v2.0, see more in the LICENSE file.
