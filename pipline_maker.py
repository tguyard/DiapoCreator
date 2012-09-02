#!/usr/bin/python
import Queue
import sys
import gst
import pygtk
import gtk.glade
import pyexiv2
import gobject

FILTER_CAPS = "video/x-raw-yuv, format=(fourcc)I420, width=(int)720, height=(int)576, framerate=(fraction)0/1"
class PictureFactory(gst.Bin):

    def __init__(self, path, *args, **kwargs):
        gst.Bin.__init__(self, *args, **kwargs)
        uri = 'file://%s' % path
        if uri and gst.uri_is_valid(uri):
            self.urisrc = gst.element_make_from_uri(gst.URI_SRC, uri, "urisrc")
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
                elif orientation == 6:  # Rotation 270
                    self.flip.set_property('method', 'clockwise')
                elif orientation == 7:  # Vertical Mirror + Rotation 270
                    pass
                elif orientation == 8:  # Rotation 90
                    self.flip.set_property('method', 'counterclockwise')
            self.add(self.flip)

            self.csp = gst.element_factory_make('ffmpegcolorspace', 'PictureCsp')
            self.add(self.csp)

            self.videoscale = gst.element_factory_make('videoscale', 'PictureVScale')
            self.videoscale.set_property("add-borders", False)
            self.add(self.videoscale)

            self.freeze = gst.element_factory_make('imagefreeze', 'PictureFreeze')
            self.add(self.freeze)

            self.capsfilter = gst.element_factory_make("capsfilter", "CapsFilter")
            caps = gst.Caps(FILTER_CAPS)
            self.capsfilter.set_property("caps", caps)
            self.add(self.capsfilter)

            # link elements
            gst.element_link_many(
                    self.urisrc,
                    self.jpegdec,
                    self.videoscale,
                    self.queue,
                    self.csp,
                    self.flip,
                    self.capsfilter,
                    self.freeze
                    )

            self.add_pad(gst.GhostPad('src', self.freeze.get_pad('src')))

class Pipeline:

    def __init__(self):
        self.q = Queue.Queue()
        self._TRANSITION_DURATION = 1 * gst.SECOND
        self._img_count = 0
        self._time_count = 0
        self._pipeline = gst.Pipeline("mypipeline")

        self._composition = gst.element_factory_make("gnlcomposition", "mycomposition")
        self._composition.connect("pad-added", self._on_pad)
        self._pipeline.add(self._composition)

        self._colorspace = gst.element_factory_make("ffmpegcolorspace", "ffcolorspace")
        self._pipeline.add(self._colorspace)

        sink = gst.element_factory_make("xvimagesink", "sink")
        sink.set_property("force-aspect-ratio", True)
        self._pipeline.add(sink)

        gst.element_link_many(self._colorspace, sink)

    def add_image(self, path, duration, autozoom = True):
        self.image = gst.element_factory_make("gnlsource", "bin %s" % path)
        self.image.add(PictureFactory(path))

        self._composition.add(self.image)
        if self._img_count == 0:
            self.image.set_property("start", 0)
            self.image.set_property("duration", duration)
            self.image.set_property("media_duration", duration)
        else:
            self.image.set_property("start", self._time_count - self._TRANSITION_DURATION)
            self.image.set_property("duration", duration + self._TRANSITION_DURATION)
            self.image.set_property("media_duration", duration + self._TRANSITION_DURATION)
        self.image.set_property("media_start", 0)
        self.image.set_property("priority", 1 + self._img_count % 2)

        #if self._time_count != 0 :
        #    self._make_transition(self._time_count, self._composition)

        self._time_count += duration
        self._img_count += 1

    def _make_transition(self, time, composition):
        bin = gst.Bin()
        caps = gst.Caps(FILTER_CAPS)

        alpha1 = gst.element_factory_make("alpha", "alpha1")
        queue = gst.element_factory_make("queue")
        alpha2  = gst.element_factory_make("alpha", "alpha2")
        scale = gst.element_factory_make("videoscale", "scale2")
        scale.set_property("add-borders", True)

        mixer  = gst.element_factory_make("videomixer")

        bin.add(queue, alpha2, scale, alpha1, mixer)
        gst.element_link_many(alpha1, mixer)
        gst.element_link_many(queue, alpha2, scale, mixer)

        controller = gst.Controller(alpha2, "alpha")
        controller.set_interpolation_mode("alpha", gst.INTERPOLATE_LINEAR)
        controller.set("alpha", 0, 0.0)
        controller.set("alpha", self._TRANSITION_DURATION, 1.0)
        self.q.put(controller)

        bin.add_pad(gst.GhostPad("sink2", alpha1.get_pad("sink")))
        bin.add_pad(gst.GhostPad("sink1", queue.get_pad("sink")))
        bin.add_pad(gst.GhostPad("src",   mixer.get_pad("src")))

        op = gst.element_factory_make("gnloperation")
        op.add(bin)
        op.props.start          = self._time_count - self._TRANSITION_DURATION
        op.props.duration       = self._TRANSITION_DURATION
        op.props.media_start    = 0
        op.props.media_duration = self._TRANSITION_DURATION
        op.props.priority       = 0
        composition.add(op)

    def _on_pad(self, comp, pad):
        print "pad added!"
        convpad = self._colorspace.get_compatible_pad(pad, pad.get_caps())
        pad.link(convpad)

    def play(self):
        loop = gobject.MainLoop(is_running=True)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        def on_message(bus, message, loop):
            if message.type == gst.MESSAGE_EOS:
                loop.quit()
            elif message.type == gst.MESSAGE_ERROR:
                print message
                loop.quit()
        bus.connect("message", on_message, loop)
        self._pipeline.set_state(gst.STATE_PLAYING)
        loop.run()
        self._pipeline.set_state(gst.STATE_NULL)


def __main__():
    gobject.threads_init()
    p = Pipeline()
    p.add_image("/home/thomas/code/diapo/36.jpeg", 2 * gst.SECOND)
    p.add_image("/home/thomas/code/diapo/01.jpeg", 2 * gst.SECOND)
    p.add_image("/home/thomas/code/diapo/02.jpeg", 2 * gst.SECOND)
    p.add_image("/home/thomas/code/diapo/rotation.jpeg", 2 * gst.SECOND)
    p.add_image("/home/thomas/code/diapo/03.jpeg", 2 * gst.SECOND)
    p.play()

if __name__ == "__main__":
    __main__()
