aabot
=====

**aabot** is an IRC bot that listens for URLs and, if they resolve to
images, converts those images to ASCII art and writes it back to IRC.

It was developed by [mailto:jbrown@yelp.com](James Brown). If you think
it's cool, maybe you want to 
[http://www.yelp.com/careers?country=US](come work at Yelp). :-)

Usage
-----
Run `aabot --help` for a summary of options. In general, you will need
to pass in at least a server host.

To run, it will require the following:
 * [http://www.tornadoweb.org/](Tornado) 1.0 or higher
 * [http://jwilk.net/software/python-aalib](python-aalib) 0.2 or higher

Rendering is done using [http://www.mirc.com/colors.html](mIRC color codes), which
are also supported by [http://xchat.org/](XChat) and [http://www.irssi.org/](irssi).

Contributing
------------
If you make a pull request, I might take it.

License
-------
This work is available under the ISC (OpenBSD) license. The full contents
of this license are available as LICENSE.
