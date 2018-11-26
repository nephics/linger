import logging
import signal
import sys

import tornado.httpserver
from tornado.gen import coroutine, with_timeout, TimeoutError
from tornado.options import options, parse_command_line

from . import linger


def handle_signals(http_server, shutdown_callback=None):
    """Make the http server shutdown on SIGINT and SIGTERM"""

    state = {}
    io_loop = tornado.ioloop.IOLoop.current()

    @coroutine
    def on_shutdown():
        """Perform a controlled shutdown"""

        if state.get('shutdown'):
            # shutdown is already in progress
            return

        state['shutdown'] = True
        logging.info('Initiating shutdown')

        # stop accepting requests
        http_server.stop()

        try:
            # force close all open connections
            yield with_timeout(io_loop.time()+1.0, http_server.close_all_connections())
        except TimeoutError:
            logging.error('TimeoutError when closing all connections.')

        if shutdown_callback:
            shutdown_callback()

        io_loop.stop()
        logging.info('Shutdown completed')
        sys.exit(0)

    def sdcb(sig, frame):
        """handle SIGTERM (kill) and SIGINT (Ctrl-C) signals"""
        return io_loop.add_callback_from_signal(on_shutdown)

    signal.signal(signal.SIGINT, sdcb)
    signal.signal(signal.SIGTERM, sdcb)


def main():
    parse_command_line()
    application, settings = linger.make_app()
    http_server = tornado.httpserver.HTTPServer(
        application, xheaders=not options.debug)
    http_server.listen(options.port)
    logging.info('Starting server at port %d' % options.port)
    if options.debug:
        logging.debug('Running in debug mode')

    # make the server stop on SIGINT and SIGTERM
    handle_signals(http_server, settings.get('shutdown_callback'))

    tornado.ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()
