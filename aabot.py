import functools
import optparse
import logging
import re
import signal
import sys

import aalib
import Image
import ImageStat

import tornado.httpclient

from tornado_irc import IRCConn


# Ganked from
# http://daringfireball.net/2010/07/improved_regex_for_matching_urls, thank
# you John Gruber
URI_RE=re.compile(r'''(?xi)
\b
(                           # Capture 1: entire matched URL
  (?:
    [a-z][\w-]+:                # URL protocol and colon
    (?:
      /{1,3}                        # 1-3 slashes
      |                             #   or
      [a-z0-9%]                     # Single letter or digit or '%'
                                    # (Trying not to match e.g. "URI::Escape")
    )
    |                           #   or
    www\d{0,3}[.]               # "www.", "www1.", "www2." ... "www999."
    |                           #   or
    [a-z0-9.\-]+[.][a-z]{2,4}/  # looks like domain name followed by a slash
  )
  (?:                           # One or more:
    [^\s()<>]+                      # Run of non-space, non-()<>
    |                               #   or
    \(([^\s()<>]+|(\([^\s()<>]+\)))*\)  # balanced parens, up to 2 levels
  )+
  (?:                           # End with:
    \(([^\s()<>]+|(\([^\s()<>]+\)))*\)  # balanced parens, up to 2 levels
    |                                   #   or
    [^\s`!()\[\]{};:'".,<>?]        # not a space or one of these punct chars
  )
)
''')

class IRCScreen(aalib.AsciiScreen):
    '''Screen that uses mIRC escape sequences.'''

    _formats = {
        aalib.ATTRIBUTE_NORMAL: '\x0f%s',
        aalib.ATTRIBUTE_BOLD: '\x03\x02%s\x0f',
        aalib.ATTRIBUTE_BRIGHT: '\x03\x02%s\x0f',
        aalib.ATTRIBUTE_REVERSE: '\x030,1%s\x0f',
        aalib.ATTRIBUTE_DIM: '\x032%s\x0f',
    }

    def _get_default_settings(self):
        settings = aalib.Screen._get_default_settings(self)
        settings.options = aalib.OPTION_NORMAL_MASK | aalib.OPTION_BRIGHT_MASK | aalib.OPTION_DIM_MASK
        return settings


class AABot(IRCConn):
    def __init__(self, channel, nickname, max_image_length, io_loop=None, listen_channels=[]):
        self.channel = channel
        self.listen_channels = listen_channels
        self.max_image_length = max_image_length
        super(AABot, self).__init__(nickname, io_loop)

    def on_connect(self):
        self.join(self.channel)
        for chan in self.listen_channels:
            self.join(chan)

    def on_chanmsg(self, chan, user, message):
        client = tornado.httpclient.AsyncHTTPClient(io_loop=self.io_loop)

        def on_get(response):
            if response.error:
                logging.error(response.error)
            screen = IRCScreen(width=60, height=30)
            image = Image.open(response.buffer).convert('L').resize(screen.virtual_size)
            stat = ImageStat.Stat(image)
            if stat.rms[0] > 128.0:
                invert = True
            else:
                invert = False
            screen.put_image((0,0), image)
            self.chanmsg(self.channel, "Displaying %s for %s" % (response.request.url, user))
            self.chanmsg(self.channel, screen.render(dithering_mode=aalib.DITHER_FLOYD_STEINBERG, inversion=invert))

        def on_head(uri, response):
            if response.error:
                logging.error(response.error)
                return
            if not response.headers.get('Content-Type', "").startswith("image/"):
                return
            if int(response.headers.get('Content-Length', 0)) > self.max_image_length:
                return
            client.fetch(uri, on_get)

        umd = URI_RE.search(message)
        if umd:
            uri = umd.group(0)
            head_request = tornado.httpclient.HTTPRequest(uri, method="HEAD")
            client.fetch(head_request, functools.partial(on_head, uri))

    def on_privmsg(self, user, message):
        self.on_chanmsg(user, user, message)

if __name__ == '__main__':
    p = optparse.OptionParser()
    p.add_option("-s", "--server", dest="server", action="store", help="Host to connect to")
    p.add_option("-p", "--port", dest="port", action="store", type="int", default=6697, help="Port to connect to (default %default)")
    p.add_option("-c", "--channel", dest="channel", action="store", default="aabot", help="Channel to connect to (default %default)")
    p.add_option("-n", "--nick", dest="nick", action="store", default="aabot", help="nickname to use (default %default)")
    p.add_option("-l", "--listen-channel", dest="listen_channels", action="append", default=[], help="channels to listen on (default %default)")
    p.add_option("--ssl", dest="use_ssl", action="store_true", default=False, help="Use SSL (default %default)")
    p.add_option("--password", dest="password", action="store", default=None, help="Password (default %default)")
    p.add_option("-v", "--verbose", dest="verbose", action="store_true", help="Be more verbose")
    p.add_option("--max-image-length", dest="max_image_length", action="store", type="int", default=2097152, help="Max size of images to fetch (default %default bytes)")
    (opts, args) = p.parse_args()
    if opts.verbose:
        logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    else:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    c = AABot(opts.channel, opts.nick, opts.max_image_length, listen_channels=opts.listen_channels)
    c.connect(opts.server, opts.port, opts.use_ssl, opts.password)
    signal.signal(signal.SIGINT, lambda *args: c.quit("Terminated by ASCII art", tornado.ioloop.IOLoop.instance().stop))
    tornado.ioloop.IOLoop.instance().start()
