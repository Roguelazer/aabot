import functools
import math
import optparse
import logging
import re
import signal
import sys
import traceback

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


def scale_image_to_fit(image, fill_width, fill_height):
        old_width = image.size[0]
        old_height = image.size[1]
        target_ratio = float(fill_width) / fill_height
        source_ratio = float(old_width) / old_height
        logging.debug("target ratio: %0.4f; source_ratio: %0.4f", target_ratio, source_ratio)
        if source_ratio > target_ratio:
            # scale to the width
            neww = fill_width
            newh = int(math.floor(1/source_ratio * neww))
        else:
            # scale to the height
            newh = fill_height
            neww = int(math.floor(source_ratio * newh))
        logging.debug("scaling (%d, %d) -> (%d, %d)", old_width, old_height, neww, newh)
        return image.resize((neww, newh), Image.ANTIALIAS)


class AABot(IRCConn):
    def __init__(self, channel, nickname, max_image_length, io_loop=None, listen_channels=[], pretend=False):
        self.channel = channel
        self.listen_channels = set(listen_channels)
        self.max_image_length = max_image_length
        self.count = 0
        self.pretend_mode = pretend
        self.scalew = 60
        self.scaleh = 20
        super(AABot, self).__init__(nickname, io_loop)

    def on_connect(self):
        self.join(self.channel)
        for chan in self.listen_channels:
            self.join(chan)

    def join(self, channel):
        super(AABot, self).join(channel)
        self.listen_channels.add(channel)

    def write_from(self, maybe_uri, to_channel, for_user):
        client = tornado.httpclient.AsyncHTTPClient(io_loop=self.io_loop)

        def on_get(response):
            if response.error:
                logging.error(response.error)
            screen = IRCScreen(width=self.scalew, height=self.scaleh)
            outsize = screen.virtual_size
            image = Image.open(response.buffer).convert('L')
            image = scale_image_to_fit(image, outsize[0], outsize[1])
            stat = ImageStat.Stat(image)
            if stat.rms[0] > 128.0:
                invert = True
            else:
                invert = False
            logging.debug('inverting: %s', invert)
            fill = Image.new('L', screen.virtual_size, '#fff' if invert else '#000')
            screen.put_image((0,0), fill)
            screen.put_image((0,0), image)
            self.count += 1
            rendered = screen.render(dithering_mode=aalib.DITHER_FLOYD_STEINBERG, inversion=invert)
            if self.pretend_mode:
                for line in rendered.split("\n"):
                    logging.debug(line)
            else:
                self.chanmsg(to_channel, "Displaying %s for %s" % (response.request.url, for_user))
                self.chanmsg(to_channel, rendered)

        def on_head(uri, response):
            if response.error:
                logging.error(response.error)
                return
            if not response.headers.get('Content-Type', "").startswith("image/"):
                return
            if int(response.headers.get('Content-Length', 0)) > self.max_image_length:
                return
            client.fetch(uri, on_get)

        umd = URI_RE.search(maybe_uri)
        if umd:
            uri = umd.group(0)
            head_request = tornado.httpclient.HTTPRequest(uri, method="HEAD")
            logging.debug("Issuing request %s", head_request)
            client.fetch(head_request, functools.partial(on_head, uri))

    def on_chanmsg(self, _, user, message):
        umd = URI_RE.search(message)
        if umd:
            uri = umd.group(0)
            self.write_from(uri, to_channel=self.channel, for_user=user)

    def on_privmsg(self, user, message):
        try:
            message = message.rstrip()
            command_and_maybe_args = message.split(' ')
            command = command_and_maybe_args[0].lower()
            if len(command_and_maybe_args) > 1:
                args = command_and_maybe_args[1:]
            else:
                args = []
            response = ""
            if command == 'info':
                response = "\n".join([
                    "aabot status:",
                    "I am listening on: [%s]" % ",".join(self.listen_channels),
                    "I am writing to: %s" % self.channel,
                    "I have written %d images" % self.count,
                    "I scale images to %dx%d" % (self.scalew, self.scaleh),
                ])
            elif command == 'join':
                channel = args[0]
                response = "Joining %s" % channel
                self.join(channel)
            elif command == 'aato':
                channel = args[0]
                uri = args[1]
                response = "Okay, writing from %s to %s" % (uri, channel)
                self.write_from(uri, to_channel=channel, for_user=user)
            elif command == 'aa':
                channel = self.channel
                uri = args[0]
                response = "Okay, writing from %s to %s" % (uri, channel)
                self.write_from(uri, to_channel=channel, for_user=user)
            elif command == 'scale':
                self.scalew = int(args[0])
                self.scaleh = int(args[1])
                response = "Okay, I will scale to %dx%d" % (self.scalew, self.scaleh)
            elif command == 'writeto':
                self.channel = args[0]
                response = "Okay, I will write to %s by default now" % (self.channel)
            elif command == 'quit':
                self.quit("Shutdown requested by %s" % user, callback=tornado.ioloop.IOLoop.instance().stop)
            else:
                response = "\n".join([
                    "Commands:",
                    "INFO -- Print help",
                    "JOIN #channel -- Read URLs from #channel",
                    "AATO #channel URI -- Write AA from URI to #channel",
                    "AA URI -- Write AA from URI to default channel"
                    "SCALE width height -- Change output scale to <width>x<height>",
                    "WRITETO #channel -- Write to #channel by default",
                    "QUIT -- Go away",
                ])
            if response:
                self.privmsg(user, response)
        except Exception, e:
            self.privmsg(user, "Error running command: %r" % e)
            traceback.print_exc()

if __name__ == '__main__':
    p = optparse.OptionParser()
    p.add_option("-s", "--server", dest="server", action="store", help="Host to connect to")
    p.add_option("-p", "--port", dest="port", action="store", type="int", default=6697, help="Port to connect to (default %default)")
    p.add_option("-c", "--channel", dest="channel", action="store", default="#aabot", help="Channel to connect to (default %default)")
    p.add_option("-n", "--nick", dest="nick", action="store", default="aabot", help="nickname to use (default %default)")
    p.add_option("-l", "--listen-channel", dest="listen_channels", action="append", default=[], help="channels to listen on (default %default)")
    p.add_option("--ssl", dest="use_ssl", action="store_true", default=False, help="Use SSL (default %default)")
    p.add_option("--password", dest="password", action="store", default=None, help="Password (default %default)")
    p.add_option("-v", "--verbose", dest="verbose", action="store_true", help="Be more verbose")
    p.add_option("-P", "--pretend", dest="pretend", action="store_true", help="Don't actually send the response")
    p.add_option("--max-image-length", dest="max_image_length", action="store", type="int", default=2097152, help="Max size of images to fetch (default %default bytes)")
    (opts, args) = p.parse_args()
    if opts.verbose:
        logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)
    else:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    c = AABot(opts.channel, opts.nick, opts.max_image_length, listen_channels=opts.listen_channels, pretend=opts.pretend)
    c.connect(opts.server, opts.port, opts.use_ssl, opts.password)
    signal.signal(signal.SIGINT, lambda *args: c.quit("Terminated by ASCII art", tornado.ioloop.IOLoop.instance().stop))
    tornado.ioloop.IOLoop.instance().start()
