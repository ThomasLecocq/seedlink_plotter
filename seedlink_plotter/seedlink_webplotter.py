#!/usr/bin/env python

import matplotlib
# Set the backend for matplotlib.
matplotlib.use("WxAgg")
matplotlib.rc('figure.subplot', hspace=0)
matplotlib.rc('font', family="monospace")

from obspy.seedlink.slpacket import SLPacket
from obspy.seedlink.slclient import SLClient
from obspy.core import UTCDateTime
from obspy.core.event import Catalog
from argparse import ArgumentParser
import threading
import time
import sys
import logging
import numpy as np



from bokeh.plotting import *
from bokeh.objects import Glyph
import calendar
import datetime



class SeedlinkUpdater(SLClient):
    def __init__(self, stream, myargs=None, lock=None):
        # loglevel NOTSET delegates messages to parent logger
        super(SeedlinkUpdater, self).__init__(loglevel="NOTSET")
        self.stream = stream
        self.lock = lock
        self.args = myargs

    def packetHandler(self, count, slpack):
        """
        Processes each packet received from the SeedLinkConnection.
        :type count: int
        :param count:  Packet counter.
        :type slpack: :class:`~obspy.seedlink.SLPacket`
        :param slpack: packet to process.
        :return: Boolean true if connection to SeedLink server should be
            closed and session terminated, false otherwise.
        """

        # check if not a complete packet
        if slpack is None or (slpack == SLPacket.SLNOPACKET) or \
                (slpack == SLPacket.SLERROR):
            return False

        # get basic packet info
        type = slpack.getType()

        # process INFO packets here
        if (type == SLPacket.TYPE_SLINF):
            return False
        if (type == SLPacket.TYPE_SLINFT):
            logging.info("Complete INFO:" + self.slconn.getInfoString())
            if self.infolevel is not None:
                return True
            else:
                return False

        # process packet data
        trace = slpack.getTrace()
        if trace is None:
            logging.info(self.__class__.__name__ + ": blockette contains no trace")
            return False

        # new samples add to the main stream which is then trimmed
        with self.lock:
            self.stream += trace
        return False

    def getTraceIDs(self):
        """
        Return a list of SEED style Trace IDs that the SLClient is trying to
        fetch data for.
        """
        ids = []
        for stream in self.slconn.getStreams():
            net = stream.net
            sta = stream.station
            for selector in stream.getSelectors():
                if len(selector) == 3:
                    loc = ""
                else:
                    loc = selector[:2]
                cha = selector[-3:]
                ids.append(".".join((net, sta, loc, cha)))
        ids.sort()
        return ids


def main():
    parser = ArgumentParser(prog='seedlink_plotter',
                            description='Plot a realtime seismogram drum of a station')

    parser.add_argument(
        '-s', '--seedlink_streams', type=str, required=True,
        help='The seedlink stream selector string. It has the format '
             '"stream1[:selectors1],stream2[:selectors2],...", with "stream" '
             'in "NETWORK"_"STATION" format and "selector" a space separated '
             'list of "LOCATION""CHANNEL", e.g. '
             '"IU_KONO:BHE BHN,MN_AQU:HH?.D".')
    parser.add_argument(
        '--scale', type=int, help='the scale to apply on data ex:50000', required=False)

    # Real-time parameters
    parser.add_argument('--seedlink_server', type=str,
                        help='the seedlink server to connect to with port. ex: rtserver.ipgp.fr:18000 ', required=True)
    parser.add_argument(
        '--x_scale', type=int, help='the number of minute to plot per line', default=60)
    parser.add_argument('-b', '--backtrace_time', type=float,
                        help='the number of hours to plot', required=True)
    parser.add_argument('--x_position', type=int,
                        help='the x position of the graph', required=False, default=0)
    parser.add_argument('--y_position', type=int,
                        help='the y position of the graph', required=False, default=0)
    parser.add_argument(
        '--x_size', type=int, help='the x size of the graph', required=False, default=800)
    parser.add_argument(
        '--y_size', type=int, help='the y size of the graph', required=False, default=600)
    parser.add_argument(
        '--title_size', type=int, help='the title size of each station in multichannel', required=False, default=10)
    parser.add_argument(
        '--time_legend_size', type=int, help='the size of time legend in multichannel', required=False, default=10)
    parser.add_argument(
        '--tick_format', type=str, help='the tick format of time legend ', required=False, default=None)
    parser.add_argument(
        '--time_tick_nb', type=int, help='the number of time tick', required=False)
    parser.add_argument(
        '--without-decoration', required=False, action='store_true',
        help=('the graph window will have no decorations. that means the '
              'window is not controlled by the window manager and can only '
              'be closed by killing the respective process.'))
    parser.add_argument(
        '--rainbow', help='', required=False, action='store_true')
    parser.add_argument(
        '--nb_rainbow_colors', help='the numbers of colors for rainbow mode', required=False, default=10)
    parser.add_argument(
        '--update_time', help='time in seconds between each graphic update', required=False, default=10, type=float)
    parser.add_argument('--events', required=False, default=None, type=float,
                        help='plot events using obspy.neries, specify minimum magnitude')
    parser.add_argument('--events_update_time', required=False, default=10, type=float,
                        help='time in minutes between each event data update')
    parser.add_argument('-f', '--fullscreen', default=False,
                        action="store_true",
                        help='set to full screen on startup')
    parser.add_argument('-v', '--verbose', default=False,
                        action="store_true", dest="verbose",
                        help='show verbose debugging output')
    parser.add_argument('--force', default=False, action="store_true",
                        help='skip warning message and confirmation prompt '
                             'when opening a window without decoration')
    # parse the arguments
    args = parser.parse_args()

    if args.verbose:
        loglevel = logging.DEBUG
    else:
        loglevel = logging.CRITICAL
    logging.basicConfig(level=loglevel)

    # before anything else: warn user about window without decoration
    if args.without_decoration and not args.force:
        warning_ = ("Warning: You are about to open a window without "
                    "decoration that is not controlled via your Window "
                    "Manager. You can exit with <Ctrl>-C (as long as you do "
                    "not switch to another window with e.g. <Alt>-<Tab>)."
                    "\n\nType 'y' to continue.. ")
        if raw_input(warning_) != "y":
            print "Aborting."
            sys.exit()

    # backtrace is now in second
    args.backtrace_time = 3600 * args.backtrace_time

    now = UTCDateTime()

    if any([x in args.seedlink_streams for x in ", ?*"]):
        multichannel = True
    else:
        multichannel = False

    if multichannel:
        if args.time_tick_nb is None:
            args.time_tick_nb = 5
        if args.tick_format is None:
            args.tick_format = '%H:%M:%S'
    else:
        if args.time_tick_nb is None:
            args.time_tick_nb = 13
        if args.tick_format is None:
            args.tick_format = '%d/%m/%y %Hh'

    stream = Stream()
    events = Catalog()
    lock = threading.Lock()

    # cl is the seedlink client
    cl = SeedlinkUpdater(stream, myargs=args, lock=lock)
    cl.slconn.setSLAddress(args.seedlink_server)
    cl.multiselect = args.seedlink_streams
    if multichannel:
        cl.begin_time = (now - args.backtrace_time).formatSeedLink()
    else:
        round_start = UTCDateTime(now.year, now.month, now.day, now.hour, 0, 0)
        round_start = round_start + 3600 - args.backtrace_time
        cl.begin_time = (round_start).formatSeedLink()
    cl.initialize()
    ids = cl.getTraceIDs()
    # start cl in a thread
    thread = threading.Thread(target=cl.run)
    thread.setDaemon(True)
    thread.start()

    # start another thread for event updating if requested
    # if args.events is not None:
        # eu = EventUpdater(stream=stream, events=events, myargs=args, lock=lock)
        # thread = threading.Thread(target=eu.run)
        # thread.setDaemon(True)
        # thread.start()

    # master = SeedlinkPlotter(stream=stream, events=events, myargs=args,
                             # lock=lock, multichannel=multichannel,
                             # trace_ids=ids)
    # master.mainloop()
    
    output_server("seedlink example",)
    x = [0,]
    y = [0,]
    # l = line(x,y, color="#0000FF", tools="pan,zoom,resize",
            # width=1280,height=250,title='OK',
            # x_axis_type = "datetime",
            # )
    x = {}
    y = {}
    
    # ds = DataSource()
    
    data = ColumnDataSource() 
    
    for id in ids:
        data.column_names.append("x_%s"%id)
        data.column_names.append("y_%s"%id)
        data.data["x_%s"%id] = [0,]
        data.data["y_%s"%id] = [0,]
        x[id]=[0,]
        y[id]=[0,]
        print dir(data)
        print data.column_names
        line("x_%s"%id,"y_%s"%id, color="#0000FF", tools="pan,zoom,resize,embed",
            width=1280,height=200,
            x_axis_type = "datetime",
            title=id, source=data,
            )
    
    renderer = [r for r in curplot().renderers if isinstance(r, Glyph)][0]
    ds = renderer.data_source
    session().store_obj(ds)
    f = open("www\live\index.html","w")
    html = """
    <html>
    <head>
    <title></title>
    </head>
    <body>
    %s
    </body>
    </html>"""
    f.write(html % renderer._build_server_snippet()[1])
    f.close()
    while 1:
        if len(stream) != 0:
            stream.merge()
            for id in ids:
                tr = stream.select(id=id)
                if tr:
                    # start = tr[0].stats.starttime.datetime
                    
                    end = tr[0].stats.endtime
                    tr = tr.slice(end-datetime.timedelta(seconds=120),end)
                    start = tr[0].stats.starttime.datetime
                    end = tr[0].stats.endtime.datetime
                    # x = pd.DateRange(start,end,offset=pd.datetools.Milli(int(1000./stream[0].stats.sampling_rate))).tolist()
                    y = tr[0].data
                    offset = calendar.timegm(start.utctimetuple())
                    print tr[0].id
                    x = (np.arange(tr[0].stats.npts)/float(tr[0].stats.sampling_rate)) + offset /10000
                    data.data["x_%s"%id] = x
                    data.data["y_%s"%id] = y
            
            ds._dirty = True
            session().store_obj(ds)
        time.sleep(0.01)


if __name__ == '__main__':
    main()
