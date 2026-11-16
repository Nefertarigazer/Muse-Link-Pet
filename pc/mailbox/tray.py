"""Retired compatibility module; the pet owns the mailbox lifecycle.

Never imported or started by either application entrance.
"""
from i18n import t
import threading


class Tray:
    def __init__(self, window, syncer, on_exit):
        import pystray
        from PIL import Image, ImageDraw
        self._window = window
        self._exiting = False
        self._on_exit = on_exit
        image = Image.new("RGBA", (64, 64), (22, 61, 76, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 18, 54, 46), outline="white", width=3)
        draw.line((10, 18, 32, 35, 54, 18), fill="white", width=3)
        self._icon = pystray.Icon("muselink-mailbox", image, t('mailbox.title'), pystray.Menu(
            pystray.MenuItem(t('mailbox.open'), lambda icon, item: self.open(), default=True),
            pystray.MenuItem(t('mailbox.sync_now'), lambda icon, item: syncer.request()),
            pystray.MenuItem(t('mailbox.exit'), lambda icon, item: self.exit())))

    def open(self):
        self._window.show()
        self._window.restore()

    def closing(self):
        if not self._exiting:
            self._window.hide()
            return False
        return True

    def start(self):
        threading.Thread(target=self._icon.run, name="mailbox-tray", daemon=True).start()

    def exit(self):
        self._exiting = True
        self._icon.stop()
        self._on_exit()
        self._window.destroy()

    def stop(self):
        self._icon.stop()
