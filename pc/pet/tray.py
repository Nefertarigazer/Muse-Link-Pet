"""Tray follows the mailbox lifecycle and shares the application's icon."""
from i18n import t
from pathlib import Path
import threading

ICON_PATH = Path(__file__).resolve().parent / 'pet.ico'


class Tray:
    def __init__(self, shell, actions, on_exit, name="Muse Pet"):
        import pystray
        from PIL import Image
        self.shell, self.on_exit = shell, on_exit
        self._exiting = False
        with Image.open(ICON_PATH) as source:
            image = source.convert('RGBA')
        self._icon = pystray.Icon('muselink-pet', image, name, pystray.Menu(
            pystray.MenuItem(t('pet.tray.show', name=name), lambda icon, item: shell.show(), default=True),
            pystray.MenuItem(t('pet.talk', name=name), lambda icon, item: shell.show(talk=True)),
            pystray.MenuItem(t('pet.open_mailbox'), lambda icon, item: actions.open_mailbox()),
            pystray.MenuItem(t('pet.exit'), lambda icon, item: self.exit())))

    def closing(self):
        if not self._exiting:
            self.shell.hide()
            return False
        return True

    def start(self):
        threading.Thread(target=self._icon.run, name='pet-tray', daemon=True).start()

    def exit(self):
        self._exiting = True
        self.stop()
        self.on_exit()
        self.shell.window.destroy()

    def stop(self):
        self._icon.stop()
