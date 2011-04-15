aabot
=====

**aabot** is an IRC bot that listens for URLs and, if they resolve to
images, converts those images to ASCII art and writes it back to IRC.

It was developed by [James Brown](mailto:jbrown@yelp.com). If you think
it's cool, maybe you want to 
[come work at yelp](http://www.yelp.com/careers?country=US). :-)

Usage
-----
Run `aabot --help` for a summary of options. In general, you will need
to pass in at least a server host.

To run, it will require the following:

 * [Tornado](http://www.tornadoweb.org/) 1.0 or higher
 * [python-aalib](http://jwilk.net/software/python-aalib) 0.2 or higher

Rendering is done using [mIRC color codes](http://www.mirc.com/colors.html), which
are also supported by [XChat](http://xchat.org/) and [irssi](http://www.irssi.org/).

Contributing
------------
If you make a pull request, I might take it.

License
-------
This work is available under the ISC (OpenBSD) license. The full contents
of this license are available as LICENSE.
