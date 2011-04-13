import logging
import re
import socket
import ssl
import sys

import aalib
import Image

import tornado.httpclient
import tornado.ioloop
import tornado.iostream

logging.basicConfig(stream=sys.stderr, level=logging.DEBUG)

def fetch_as_aa(uri, cb, io_loop=None):
    if not io_loop:
        io_loop = tornado.ioloop.IOLoop.instance()
    def after_fetch(response):
        if response.error:
            logging.error(response.error)
        screen = aalib.AsciiScreen(width=80, height=40)
        image = Image.open(response.buffer).convert('L').resize(screen.virtual_size)
        screen.put_image((0,0), image)
        cb(screen.render())
    client = tornado.httpclient.AsyncHTTPClient(io_loop=io_loop)
    client.fetch(uri, after_fetch)

IRC_DISCONNECTED = 0
IRC_NICK = 1
IRC_CONNECTING = 2
IRC_CONNECTED = 3

PING_RE=re.compile('PING (?P<message>.+)')
CHANMSG_RE=re.compile(':(?P<username>[^!]+)!(?P<who>[^ ]+) PRIVMSG (?P<chan>[^ ]+) :(?P<msg>.*)')

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

class IRCConn(object):
    def __init__(self, chan, io_loop=None):
        self.chan = chan
        if not io_loop:
            io_loop = tornado.ioloop.IOLoop.instance()
        self._io_loop = io_loop
        self.conn = None
        self._state = IRC_DISCONNECTED

    def connect(self, host, port, do_ssl=False, password=None):
        sock = None
        self._password = password
        for (family, socktype, proto, canonname, sockaddr) in socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM, 0):
            try:
                fd = socket.socket(family, socktype, proto)
                fd.connect(sockaddr)
                sock = fd
                break
            except socket.error:
                pass
        if not sock:
            raise socket.error("Unable to connect to %s:%s" % (host, port))
        if do_ssl:
            sock = ssl.wrap_socket(sock, server_side=False, do_handshake_on_connect=False)
            self.conn = tornado.iostream.SSLIOStream(sock, io_loop=self._io_loop)
        else:
            self.conn = tornado.iostream.IOStream(sock, io_loop=self._io_loop)
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
            self._write("NICK aabot")
            self._write("USER  aabot 8 *  : AABOT")
            self._state = IRC_CONNECTING
        elif self._state == IRC_CONNECTING:
            self._write("JOIN " + self.chan)
            self._state = IRC_CONNECTED
        elif self._state == IRC_CONNECTED:
            cmd = CHANMSG_RE.match(data)
            if cmd:
                message = cmd.group('msg')
                umd = URI_RE.match(message)
                if umd:
                    uri = umd.group(0)
                    if uri.lower().split(".")[-1] in ("jpg", "jpeg", "gif", "ico", "png", "tiff"):
                        fetch_as_aa(uri, self._after_fetch, self._io_loop)
        self.conn.read_until("\n", self._handle_data)

    def _after_fetch(self, data):
        for line in data.split("\n"):
            self._write("PRIVMSG %s :%s" % (self.chan, line))

if __name__ == '__main__':
    c = IRCConn("#aabot")
    tornado.ioloop.IOLoop.instance().start()
