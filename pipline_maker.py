#!/usr/bin/python
import pygst
pygst.require("0.10")
import gst
import pygtk
import gtk.glade

_FILTER_CAPS = "video/x-raw-yuv, format=(fourcc)I420, width=(int)720, height=(int)576, framerate=(fraction)0/1"

class PictureFactory(gst.Bin):
    """ Picture Factory """

    def __init__(self, path, *args, **kwargs):
        gst.Bin.__init__(self, *args, **kwargs)
        self.uri = 'file://%s' % path
        if self.uri and gst.uri_is_valid(self.uri):
            self.urisrc = gst.element_make_from_uri(gst.URI_SRC, self.uri, "urisrc")
            self.add(self.urisrc)

            self.jpegdec = gst.element_factory_make('jpegdec','PictureJpegDec')
            self.add(self.jpegdec)

            self.flip = gst.element_factory_make('videoflip', 'flip')
            self.flip.set_property('method', 'clockwise')
            self.add(self.flip)

            self.crop = gst.element_factory_make('videobox', 'crop')
            self.crop.set_property('autocrop', True)
            self.add(self.crop)

            self.queue = gst.element_factory_make('queue', 'PictureQueue')
            self.add(self.queue)

            self.csp = gst.element_factory_make('ffmpegcolorspace', 'PictureCsp')
            self.add(self.csp)

            self.freeze = gst.element_factory_make('imagefreeze', 'PictureFreeze')
            self.add(self.freeze)


            # link elements
            gst.element_link_many(
                    self.urisrc,
                    self.jpegdec,
                    self.flip,
                    self.queue,
                    self.csp,
                    self.freeze
                    )
            self.urisrc.sync_state_with_parent()
            self.jpegdec.sync_state_with_parent()
            self.queue.sync_state_with_parent()
            self.csp.sync_state_with_parent()
            self.freeze.sync_state_with_parent()

            self.add_pad(gst.GhostPad('src', self.freeze.get_pad('src')))

class pipeline:

    def __init__(self):
        self._img_count = 0
        self._time_count = 0
        self._pipeline = gst.Pipeline("mypipeline")

        self._composition = gst.element_factory_make("gnlcomposition", "mycomposition")
        self._composition.connect("pad-added", self._on_pad)
        self._pipeline.add(self._composition)

        colorspace = gst.element_factory_make("ffmpegcolorspace", "ffcolorspace")
        self._pipeline.add(colorspace)

        videoscale = gst.element_factory_make('videoscale', 'PictureVScale')
        videoscale.set_property("add_borders", True)
        self._pipeline.add(videoscale)

        capsfilter = gst.element_factory_make("capsfilter", "CapsFilter")
        caps = gst.Caps(_FILTER_CAPS)
        capsfilter.set_property("caps", caps)
        self._pipeline.add(capsfilter)

        self._queue = gst.element_factory_make("queue2", "queueVideo")
        self._pipeline.add(self._queue)

        sink = gst.element_factory_make("ximagesink", "sink")
        sink.set_property("force-aspect-ratio", True)
        self._pipeline.add(sink)


        gst.element_link_many(
                colorspace,
                self._queue,
                videoscale,
                capsfilter,
                sink
                )

        signals = {
                "on_play_clicked" : self.OnPlay,
                "on_stop_clicked" : self.OnStop,
                "on_quit_clicked" : self.OnQuit,
                }
        self.wTree = gtk.glade.XML("gui.glade", "mainwindow")
        self.wTree.signal_autoconnect(signals)

    def add_image(self, path, duration, autozoom = True):
        image = gst.element_factory_make("gnlsource", "bin %s" % path)
        _bin= PictureFactory(path)
        image.add(_bin)

        self._composition.add(image)
        image.set_property("start", self._time_count *  gst.SECOND)
        image.set_property("duration", (self._time_count + duration) * gst.SECOND)

        self._time_count += duration
        self._img_count += 1

    def _on_pad(self, comp, pad):
        print "pad added!"
        convpad = self._queue.get_compatible_pad(pad, pad.get_caps())
        pad.link(convpad)

    def OnPlay(self, widget):
        print "play"
        self._pipeline.set_state(gst.STATE_PLAYING)

    def OnStop(self, widget):
        print "stop"
        self._pipeline.set_state(gst.STATE_NULL)

    def OnQuit(self, widget):
        print "quitting"
        gtk.main_quit()

    def play(self):
        self._pipeline.set_state(gst.STATE_PLAYING)

def __main__():
    p = pipeline()
    p.add_image("/home/thomas/code/diapo/01.jpeg", 2)
    p.add_image("/home/thomas/code/diapo/02.jpeg", 2)
    p.add_image("/home/thomas/code/diapo/36.jpeg", 2)
    p.add_image("/home/thomas/code/diapo/rotation.jpeg", 2)
    p.add_image("/home/thomas/code/diapo/03.jpeg", 2)
    p.play()
    gtk.main()

if __name__ == "__main__":
    __main__()
