#!/usr/bin/python
import pygst
pygst.require("0.10")
import gst
import pygtk
import gtk.glade
import pyexiv2


class PictureFactory(gst.Bin):
    _FILTER_CAPS = "video/x-raw-yuv, format=(fourcc)I420, width=(int)720, height=(int)576, framerate=(fraction)0/1"

    def __init__(self, path, *args, **kwargs):
        gst.Bin.__init__(self, *args, **kwargs)
        self.uri = 'file://%s' % path
        if self.uri and gst.uri_is_valid(self.uri):
            self.urisrc = gst.element_make_from_uri(gst.URI_SRC, self.uri, "urisrc")
            self.add(self.urisrc)

            self.jpegdec = gst.element_factory_make('jpegdec','PictureJpegDec')
            self.add(self.jpegdec)

            self.queue = gst.element_factory_make('queue', 'PictureQueue')
            self.add(self.queue)
            self.flip = gst.element_factory_make('videoflip', 'flip')
            image = pyexiv2.metadata.ImageMetadata(path)
            image.read()
            if 'Exif.Image.Orientation' in image.exif_keys:
                orientation = image['Exif.Image.Orientation'].value
                if orientation == 1: # Nothing
                    pass
                elif orientation == 2:  # Vertical Mirror
                    self.flip.set_property('method', 'vertical-flip')
                elif orientation == 3:  # Rotation 180
                    self.flip.set_property('method', 'rotate-180')
                elif orientation == 4:  # Horizontal Mirror
                    self.flip.set_property('method', 'horizontal-flip')
                elif orientation == 5:  # Horizontal Mirror + Rotation 270
                    pass
                    #self.flip.set_property('method', 'horizontal-flip')
                elif orientation == 6:  # Rotation 270
                    self.flip.set_property('method', 'clockwise')
                elif orientation == 7:  # Vertical Mirror + Rotation 270
                    pass
                    #self.flip.set_property('method', 'horizontal-flip')
                elif orientation == 8:  # Rotation 90
                    self.flip.set_property('method', 'counterclockwise')
            self.add(self.flip)

            self.csp = gst.element_factory_make('ffmpegcolorspace', 'PictureCsp')
            self.add(self.csp)

            videoscale = gst.element_factory_make('videoscale', 'PictureVScale')
            videoscale.set_property("add_borders", True)
            self.add(videoscale)

            self.freeze = gst.element_factory_make('imagefreeze', 'PictureFreeze')
            self.add(self.freeze)

            capsfilter = gst.element_factory_make("capsfilter", "CapsFilter")
            caps = gst.Caps(self._FILTER_CAPS)
            capsfilter.set_property("caps", caps)
            self.add(capsfilter)

            # link elements
            gst.element_link_many(
                    self.urisrc,
                    self.jpegdec,
                    self.queue,
                    self.csp,
                    videoscale,
                    self.flip,
                    self.freeze,
                    capsfilter
                    )

            self.urisrc.sync_state_with_parent()
            self.jpegdec.sync_state_with_parent()
            self.queue.sync_state_with_parent()
            self.csp.sync_state_with_parent()
            videoscale.sync_state_with_parent()
            self.freeze.sync_state_with_parent()
            capsfilter.sync_state_with_parent()

            self.add_pad(gst.GhostPad('src', capsfilter.get_pad('src')))

class pipeline:

    def __init__(self):
        self._img_count = 0
        self._time_count = 0
        self._pipeline = gst.Pipeline("mypipeline")

        self._composition = gst.element_factory_make("gnlcomposition", "mycomposition")
        self._composition.connect("pad-added", self._on_pad)
        self._pipeline.add(self._composition)

        self._queue = gst.element_factory_make("queue2", "queueVideo")
        self._pipeline.add(self._queue)

        self.colorspace = gst.element_factory_make("ffmpegcolorspace", "ffcolorspace")
        self._pipeline.add(self.colorspace)
        self._queue.link(self.colorspace)

        sink = gst.element_factory_make("autovideosink", "sink")
        self._pipeline.add(sink)
        self.colorspace.link(sink)

        signals = {
                "on_play_clicked" : self.OnPlay,
                "on_stop_clicked" : self.OnStop,
                "on_quit_clicked" : self.OnQuit,
                }
        self.wTree = gtk.glade.XML("../gui.glade", "mainwindow")
        self.wTree.signal_autoconnect(signals)

    def add_image(self, path, duration, autozoom = True):
        image = gst.element_factory_make("gnlsource", "bin %s" % path)
        image.add(PictureFactory(path))

        self._composition.add(image)
        image.set_property("start", max(0, (self._time_count - 1) *  gst.SECOND))
        image.set_property("duration", (duration + 1) * gst.SECOND)
        image.set_property("priority", 3 - self._img_count)

        if self._time_count != 0 :
            self._make_transition(self._time_count * gst.SECOND, self._composition)

        self._time_count += duration
        self._img_count += 1

    def _make_transition(self, time, composition):
        bin = gst.Bin()
        alpha1 = gst.element_factory_make("alpha")
        queue = gst.element_factory_make("queue")
        smpte  = gst.element_factory_make("smptealpha")
        smpte.props.type = 21
        mixer  = gst.element_factory_make("videomixer")

        bin.add(alpha1, queue, smpte, mixer)
        alpha1.link(mixer)
        queue.link(smpte)
        smpte.link(mixer)

        controller = gst.Controller(smpte, "position")
        controller.set_interpolation_mode("position", gst.INTERPOLATE_LINEAR)
        controller.set("position", 0, 1.0)
        controller.set("position", 4.0 * gst.SECOND, 0.0)

        bin.add_pad(gst.GhostPad("sink1", alpha1.get_pad("sink")))
        bin.add_pad(gst.GhostPad("sink2", queue.get_pad("sink")))
        bin.add_pad(gst.GhostPad("src",   mixer.get_pad("src")))

        op = gst.element_factory_make("gnloperation")
        op.add(bin)
        op.props.start          = max(0, time - 1 * gst.SECOND)
        op.props.duration       = 2 * gst.SECOND
        op.props.media_start    = 0
        op.props.media_duration = 2 * gst.SECOND
        op.props.priority       = 1
        composition.add(op)

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
    p.add_image("/home/thomas/code/diapo/01.jpeg", 4)
    p.add_image("/home/thomas/code/diapo/02.jpeg", 4)
    #p.add_image("/home/thomas/code/diapo/36.jpeg", 4)
    #p.add_image("/home/thomas/code/diapo/rotation.jpeg", 4)
    #p.add_image("/home/thomas/code/diapo/03.jpeg", 4)
    p.play()
    gtk.main()

if __name__ == "__main__":
    __main__()
