import functools
import optparse
import logging
import re
import signal
import socket
import ssl
import sys

import aalib
import Image
import ImageStat

import tornado.httpclient
import tornado.ioloop
import tornado.iostream

logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

IRC_DISCONNECTED = 0
IRC_NICK = 1
IRC_CONNECTING = 2
IRC_CONNECTED = 3

PING_RE=re.compile('PING (?P<message>.+)')
CHANMSG_RE=re.compile(':(?P<username>[^!]+)!(?P<who>[^ ]+) PRIVMSG (?P<chan>#[^ ]+) :(?P<msg>.*)')
PRIVMSG_RE=re.compile(':(?P<username>[^!]+)!(?P<who>[^ ]+) PRIVMSG (?P<user>[^#][^ ]*) :(?P<msg>.*)')

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

class IRCConn(object):
    def __init__(self, nickname, io_loop=None):
        if not io_loop:
            io_loop = tornado.ioloop.IOLoop.instance()
        self.nickname = nickname
        self.io_loop = io_loop
        self.conn = None
        self._state = IRC_DISCONNECTED

    def on_connect(self):
        pass

    def on_chanmsg(self, channel, username, message):
        pass

    def on_privmsg(self, username, message):
        pass

    def connect(self, host, port, do_ssl=False, password=None):
        sock = None
        self._password = password
        for (family, socktype, proto, canonname, sockaddr) in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, 0):
            try:
                fd = socket.socket(family, socktype, proto)
                fd.connect(sockaddr)
                fd.setblocking(0)
                sock = fd
                break
            except socket.error:
                pass
        if not sock:
            raise socket.error("Unable to connect to %s:%s" % (host, port))
        if do_ssl:
            sock = ssl.wrap_socket(sock, server_side=False, do_handshake_on_connect=False)
            self.conn = tornado.iostream.SSLIOStream(sock, io_loop=self.io_loop)
        else:
            self.conn = tornado.iostream.IOStream(sock, io_loop=self.io_loop)
        self.conn.read_until("\n", self._handle_data)

    def _write(self, data, *args, **kwargs):
        logging.debug('<<< %s', data)
        self.conn.write(data + '\r\n', *args, **kwargs)

    def _handle_data(self, data):
        logging.debug(">>> %s", data.rstrip())
        ping_md = PING_RE.match(data)
        if ping_md:
            self._write("PONG " + ping_md.group('message'))
        if self._state == IRC_DISCONNECTED:
            if self._password:
                self._write("PASS %s" % self._password)
            self._state = IRC_NICK
        elif self._state == IRC_NICK:
            self._write("NICK %s" % self.nickname)
            self._write("USER %s 8 *  :The ASCII Art Bot" % self.nickname)
            self._state = IRC_CONNECTING
        elif self._state == IRC_CONNECTING:
            self.on_connect()
            self._state = IRC_CONNECTED
        elif self._state == IRC_CONNECTED:
            cmd = CHANMSG_RE.match(data)
            if cmd:
                self.on_chanmsg(cmd.group('chan'), cmd.group('username'), cmd.group('msg'))
            pmd = PRIVMSG_RE.match(data)
            if pmd:
                if pmd.group('user') == self.nickname:
                    self.on_privmsg(pmd.group('username'), pmd.group('msg'))
        self.conn.read_until("\n", self._handle_data)

    def join(self, channel):
        self._write("JOIN " + channel)

    def chanmsg(self, channel, message):
        for line in message.split("\n"):
            self._write("PRIVMSG %s :%s" % (channel, line))

    def privmsg(self, user, message):
        self.chanmsg(user, message)

    def quit(self, message, callback=None):
        def after_quit(*args, **kwargs):
            self.conn.close()
            callback()
        self._write("QUIT :%s" % message, callback=after_quit)

class AABot(IRCConn):
    def __init__(self, channel, nickname, io_loop=None, listen_channels=[]):
        self.channel = channel
        self.listen_channels = listen_channels
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
            if not response.headers.get('Content-Type', "").startswith("image/"):
                return
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

        umd = URI_RE.search(message)
        if umd:
            uri = umd.group(0)
            client.fetch(uri, on_get)
            client.fetch(head_request, on_head)

    def on_privmsg(user, message):
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
    (opts, args) = p.parse_args()
    c = AABot(opts.channel, opts.nick, listen_channels=opts.listen_channels)
    c.connect(opts.server, opts.port, opts.use_ssl, opts.password)
    signal.signal(signal.SIGINT, lambda *args: c.quit("Terminated by ASCII art", tornado.ioloop.IOLoop.instance().stop))
    tornado.ioloop.IOLoop.instance().start()
