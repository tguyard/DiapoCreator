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
            urisrc = gst.element_make_from_uri(gst.URI_SRC, uri, "urisrc")
            self.add(urisrc)

            jpegdec = gst.element_factory_make('jpegdec','pic-jpegdec')
            self.add(jpegdec)

            queue = gst.element_factory_make('queue', "pic-queue")
            self.add(queue)
            flip = gst.element_factory_make('videoflip', 'pic-flip')
            image = pyexiv2.metadata.ImageMetadata(path)
            image.read()
            if 'Exif.Image.Orientation' in image.exif_keys:
                orientation = image['Exif.Image.Orientation'].value
                if orientation == 1: # Nothing
                    pass
                elif orientation == 2:  # Vertical Mirror
                    flip.set_property('method', 'vertical-flip')
                elif orientation == 3:  # Rotation 180
                    flip.set_property('method', 'rotate-180')
                elif orientation == 4:  # Horizontal Mirror
                    flip.set_property('method', 'horizontal-flip')
                elif orientation == 5:  # Horizontal Mirror + Rotation 270
                    pass
                elif orientation == 6:  # Rotation 270
                    flip.set_property('method', 'clockwise')
                elif orientation == 7:  # Vertical Mirror + Rotation 270
                    pass
                elif orientation == 8:  # Rotation 90
                    flip.set_property('method', 'counterclockwise')
            self.add(flip)

            csp = gst.element_factory_make('ffmpegcolorspace', 'pic-colorspace')
            self.add(csp)

            videoscale = gst.element_factory_make('videoscale', 'pic-scale')
            videoscale.set_property("add-borders", False)
            self.add(videoscale)

            freeze = gst.element_factory_make('imagefreeze', 'pic-freeze')
            self.add(freeze)

            capsfilter = gst.element_factory_make("capsfilter", "pic-capsfilter")
            caps = gst.Caps(FILTER_CAPS)
            capsfilter.set_property("caps", caps)
            self.add(capsfilter)

            # link elements
            gst.element_link_many(
                    urisrc,
                    jpegdec,
                    videoscale,
                    queue,
                    csp,
                    flip,
                    capsfilter,
                    freeze
                    )

            self.add_pad(gst.GhostPad('src', freeze.get_pad('src')))

def find_media_duration(path):
    d = gst.parse_launch("filesrc name=source ! decodebin2 ! fakesink")
    source = d.get_by_name("source")
    source.set_property("location", path)
    d.set_state(gst.STATE_PLAYING)
    d.get_state()
    format = gst.Format(gst.FORMAT_TIME)
    duration = d.query_duration(format)[0]
    d.set_state(gst.STATE_NULL)
    return duration

class Pipeline:

    def __init__(self):
        self.q = Queue.Queue()
        self._img_count = 0
        self._time = 0       # ns
        self._time_audio = 0 # ns
        self._pipeline = gst.Pipeline("mypipeline")

        # video
        self._vcomposition = gst.element_factory_make("gnlcomposition", "video-composition")
        self._vcomposition.connect("pad-added", self._video_on_pad)
        self._pipeline.add(self._vcomposition)

        self._vqueue = gst.element_factory_make("queue", "video-queue")
        self._pipeline.add(self._vqueue)
        colorspace = gst.element_factory_make("ffmpegcolorspace", "colorspace")
        self._pipeline.add(colorspace)

        vsink = gst.element_factory_make("xvimagesink", "video-sink")
        vsink.set_property("force-aspect-ratio", True)
        self._pipeline.add(vsink)

        gst.element_link_many(self._vqueue, colorspace, vsink)

        # audio
        self._acomposition = gst.element_factory_make("gnlcomposition", "audio-composition")
        self._acomposition.connect("pad-added", self._audio_on_pad)
        self._pipeline.add(self._acomposition)

        self._aqueue = gst.element_factory_make("queue", "audio-queue")
        self._pipeline.add(self._aqueue)

        asink = gst.element_factory_make("autoaudiosink", "audio-sink")
        self._pipeline.add(asink)

        gst.element_link_many(self._aqueue, asink)


    def add_image(self, path, duration, autozoom = True):
        image = gst.element_factory_make("gnlsource", "bin %s" % path)
        image.add(PictureFactory(path))

        transition_duration = min(5 * gst.SECOND, duration / 3)
        self._vcomposition.add(image)
        if self._img_count == 0:
            image.set_property("start", 0)
            image.set_property("duration", duration)
            image.set_property("media_duration", duration)
        else:
            image.set_property("start", self._time - transition_duration)
            image.set_property("duration", duration + transition_duration)
            image.set_property("media_duration", duration + transition_duration)
        image.set_property("media_start", 0)
        image.set_property("priority", 1 + self._img_count % 2)

        #if self._time != 0 :
        #    self._make_transition(self._time, transition_duration)

        self._time += duration
        self._img_count += 1

    def add_music(self, path, duration):
        source = gst.element_factory_make("gnlfilesource", "music-src")
        source.set_property("location", path)
        source.set_property("start", self._time_audio)
        source.set_property("duration", duration)
        source.set_property("media_start", 0)
        source.set_property("media_duration", duration)
        self._acomposition.add(source)
        self._time_audio += duration

    def _make_transition(self, time, transition_duration):
        bin = gst.Bin()
        caps = gst.Caps(FILTER_CAPS)

        alpha1 = gst.element_factory_make("alpha", "transition-alpha1")
        queue = gst.element_factory_make("queue", "transition-queue")
        alpha2  = gst.element_factory_make("alpha", "transition-alpha2")
        scale = gst.element_factory_make("videoscale", "transition-scale")
        scale.set_property("add-borders", True)

        mixer  = gst.element_factory_make("videomixer", "transition-mixer")

        bin.add(queue, alpha2, scale, alpha1, mixer)
        gst.element_link_many(alpha1, mixer)
        gst.element_link_many(queue, alpha2, scale, mixer)

        controller = gst.Controller(alpha2, "alpha")
        controller.set_interpolation_mode("alpha", gst.INTERPOLATE_LINEAR)
        controller.set("alpha", 0, 0.0)
        controller.set("alpha", transition_duration, 1.0)
        self.q.put(controller)

        bin.add_pad(gst.GhostPad("sink2", alpha1.get_pad("sink")))
        bin.add_pad(gst.GhostPad("sink1", queue.get_pad("sink")))
        bin.add_pad(gst.GhostPad("src",   mixer.get_pad("src")))

        op = gst.element_factory_make("gnloperation")
        op.add(bin)
        op.props.start          = self._time - transition_duration
        op.props.duration       = transition_duration
        op.props.media_start    = 0
        op.props.media_duration = transition_duration
        op.props.priority       = 0
        self._vcomposition.add(op)

    def _audio_on_pad(self, comp, pad):
        convpad = self._aqueue.get_compatible_pad(pad, pad.get_caps())
        pad.link(convpad)

    def _video_on_pad(self, comp, pad):
        convpad = self._vqueue.get_compatible_pad(pad, pad.get_caps())
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
    p.add_music("/home/thomas/multimedia/musique/01 - Kids.mp3", find_media_duration("/home/thomas/multimedia/musique/01 - Kids.mp3"))
    p.play()

if __name__ == "__main__":
    __main__()
