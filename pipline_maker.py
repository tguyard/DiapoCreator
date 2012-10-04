#!/usr/bin/python
import Queue
import sys
import gst
import pyexiv2
import gobject
import cv
from PIL import Image
import os
import tempfile

WIDTH=1920
HEIGHT=1080
FILTER_CAPS = "video/x-raw-yuv, format=(fourcc)I420, width=(int)%i, height=(int)%i, framerate=(fraction)0/1" % (WIDTH, HEIGHT)

class PictureVideoClip(gst.Bin):

    def __init__(self, path, filter_caps, *args, **kwargs):
        gst.Bin.__init__(self, *args, **kwargs)
        uri = 'file://%s' % path
        if uri and gst.uri_is_valid(uri):


            urisrc = gst.element_make_from_uri(gst.URI_SRC, uri, "urisrc")
            self.add(urisrc)

            jpegdec = gst.element_factory_make('jpegdec','pic-jpegdec')
            self.add(jpegdec)

            queue = gst.element_factory_make('queue', "pic-queue")
            self.add(queue)

            csp = gst.element_factory_make('ffmpegcolorspace', 'pic-colorspace')
            self.add(csp)

            videoscale = gst.element_factory_make('videoscale', 'pic-scale')
            videoscale.set_property("add-borders", False)
            self.add(videoscale)

            freeze = gst.element_factory_make('imagefreeze', 'pic-freeze')
            self.add(freeze)

            capsfilter = gst.element_factory_make("capsfilter", "pic-capsfilter")
            caps = gst.Caps(filter_caps)
            capsfilter.set_property("caps", caps)
            self.add(capsfilter)

            # link elements
            gst.element_link_many(
                    urisrc,
                    jpegdec,
                    videoscale,
                    queue,
                    csp,
                    capsfilter,
                    freeze
                    )

            self.add_pad(gst.GhostPad('src', freeze.get_pad('src')))

class Picture:
    def __init__(self, path):
        self._path = self._resize_and_rotate(path)
        #self._has_faces, self._bl_point, self._tr_point = self._detect_faces(self._path)

    def _resize_and_rotate(self, path):
        image = Image.open(path)

        image_metadata = pyexiv2.metadata.ImageMetadata(path)
        image_metadata.read()
        if 'Exif.Image.Orientation' in image_metadata.exif_keys:
            orientation = image_metadata['Exif.Image.Orientation'].value
            if orientation == 1: # Nothing
                pass
            elif orientation == 2:  # Vertical Mirror
                image = image.transpose(Image.FLIP_LEFT_RIGHT)
            elif orientation == 3:  # Rotation 180
                image = image.transpose(Image.ROTATE_180)
            elif orientation == 4:  # Horizontal Mirror
                image = image.transpose(Image.FLIP_TOP_BOTTOM)
            elif orientation == 5:  # Horizontal Mirror + Rotation 270
                image = image.transpose(Image.FLIP_TOP_BOTTOM).transpose(Image.ROTATE_270)
            elif orientation == 6:  # Rotation 270
                image = image.transpose(Image.ROTATE_270)
            elif orientation == 7:  # Vertical Mirror + Rotation 270
                image = image.transpose(Image.FLIP_LEFT_RIGHT).transpose(Image.ROTATE_270)
            elif orientation == 8:  # Rotation 90
                image = image.transpose(Image.ROTATE_90)

        size = image.size

        if float(size[0])/float(size[1]) > float(WIDTH)/float(HEIGHT):
            new_width = WIDTH
            new_height = (size[1] * WIDTH) / size[0]
        else:
            new_height = HEIGHT
            new_width = (size[0] * HEIGHT) / size[1]

        image = image.resize((new_width, new_height), Image.ANTIALIAS)
        tmp_image = Image.new("RGBA", (WIDTH, HEIGHT), (0,0,0,255))
        tmp_image.paste(image, ((WIDTH - new_width) / 2, (HEIGHT - new_height) / 2))
        image = tmp_image
        temp = tempfile.NamedTemporaryFile(delete=False, suffix='.jpeg')
        try:
            image.save(temp, 'jpeg')
        finally:
            temp.close()

        return temp.name

    def _detect_faces(self, path):
        image = cv.LoadImage(path)
        min_size = (20, 20)
        image_scale = 3
        haar_scale = 1.2
        min_neighbors = 1
        haar_flags = 0

        face_detected = False
        lower_point = (sys.maxint, sys.maxint)
        higher_point = (0, 0)

        gray = cv.CreateImage((image.width,image.height), 8, 1)
        small_img = cv.CreateImage((cv.Round(image.width / image_scale), cv.Round (image.height / image_scale)), 8, 1)

        cv.CvtColor(image, gray, cv.CV_BGR2GRAY)
        cv.Resize(gray, small_img, cv.CV_INTER_LINEAR)
        cv.EqualizeHist(small_img, small_img)

        cascade = cv.Load("./haarcascade_frontalface_alt.xml")
        if(cascade):
            t = cv.GetTickCount()
            faces = cv.HaarDetectObjects(small_img, cascade, cv.CreateMemStorage(0), haar_scale, min_neighbors, haar_flags, min_size)
            t = cv.GetTickCount() - t
            print "detection time = %gms" % (t/(cv.GetTickFrequency()*1000.))
            if faces:
                face_detected = True
                for ((x, y, w, h), n) in faces:
                    lower_point = (min(lower_point[0], x * image_scale), min(lower_point[1], y * image_scale))
                    lower_point = (max(higher_point[0], (x + w) * image_scale), max(higher_point[1], (y + h) * image_scale))

        return face_detected, lower_point, higher_point

    def get_as_gst_videoclip(self, filter_caps):
        return PictureVideoClip(self._path, FILTER_CAPS)

def find_media_duration(path):
    d = gst.parse_launch("filesrc name=source ! decodebin2 ! fakesink")
    source = d.get_by_name("source")
    source.set_property("location", path)
    print path
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


    def add_image(self, path, duration):
        print path
        image = gst.element_factory_make("gnlsource", "bin %s" % path)
        image.add(Picture(path).get_as_gst_videoclip(FILTER_CAPS))

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

        if self._time != 0 :
            self._make_transition(self._time, transition_duration)

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

class DiapoCreator:
    def __init__(self):
        self._pipe = Pipeline()
        self._imgs = list()
        self._musics = list()

    def add_image(self, path):
        self._imgs.append(path)

    def add_audio(self, path):
        self._musics.append(path)

    def terminate_section(self):
        if len(self._musics) == 0:
            raise Exception("No sound file found! abording ...")
        if len(self._imgs) == 0:
            raise Exception("No image found! abording ...")

        duration = 0
        for music in self._musics:
            music_duration = find_media_duration(music)
            duration += music_duration
            self._pipe.add_music(music, music_duration)

        image_duration = duration / len(self._imgs)
        for image in self._imgs:
            self._pipe.add_image(image, image_duration)

        self._imgs = list()
        self._musics = list()

    def terminate_diapo(self):
        self._pipe.play()

def __main__():
    gobject.threads_init()

    if len(sys.argv) == 1:
        print "Using current directory as DiapoCreator project"
    elif len(sys.argv) > 2:
        print "Too many arguments. Only one needed: the directory to use as a DiapoCreator project"
        return -1
    project_dir = sys.argv[1]

    creator = DiapoCreator()
    for dirname, dirnames, filenames in os.walk(project_dir):
        if dirname[0] == ".":
            continue
        for filename in filenames:
            path = os.path.join(dirname, filename)
            ignore, extension = os.path.splitext(path)
            if extension in (".mp3", ".wav", ".ogg"):
                creator.add_audio(path)
            elif extension in (".jpeg", ".jpg", ".JPG", ".JPEG", ".png", ".PNG"):
                creator.add_image(path)
        creator.terminate_section()
    creator.terminate_diapo()


if __name__ == "__main__":
    __main__()
