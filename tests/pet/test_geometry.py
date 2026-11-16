"""Native window and API bounds follow UI scale, independently of the sprite."""
import json
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
GEOMETRY = ROOT / 'pc/pet/geometry.py'
sys.path.insert(0, str(ROOT / 'pc'))


class GeometryTest(unittest.TestCase):
    def test_measured_atlas_alpha_union_matches_shared_character_rect(self):
        from PIL import Image
        from pet.geometry import CHAR_RECT
        with Image.open(ROOT / 'pc/pet/ui/packs/jolly/spritesheet.png') as image:
            boxes = [image.crop((column*192, row*208, (column+1)*192, (row+1)*208)).getbbox()
                     for row, count in enumerate((6,8,8,4,5,8,6,6,6)) for column in range(count)]
            bounds = tuple(fn(box[i] for box in boxes) for i, fn in enumerate((min,min,max,max)))
            self.assertEqual(bounds, (3,13,189,203))
            # Dark mouth pixels on the idle frame, measured within the face.
            mouth = [(x,y) for y in range(116,130) for x in range(86,108)
                     if max(image.getpixel((x,y))[:3]) < 100]
            self.assertEqual((min(x for x,y in mouth),min(y for x,y in mouth),
                              max(x for x,y in mouth),max(y for x,y in mouth)), (91,121,101,123))
        scale = 134/208*.7
        expected = dict(x=598+62+(3-96)*scale, y=292+128+(13-208)*scale,
                        w=186*scale, h=190*scale)
        for key in expected:
            self.assertAlmostEqual(CHAR_RECT[key], expected[key])

    def test_full_design_window_matches_frontend_contract(self):
        geometry = runpy.run_path(str(GEOMETRY))
        contract = json.loads((ROOT / 'pc/pet/ui/fixtures/contract.json').read_text(encoding='utf-8'))
        self.assertEqual(geometry['UI_SCALE'], 1)
        self.assertEqual((geometry['CORE_WIDTH'], geometry['CORE_HEIGHT']), (340, 440))
        self.assertEqual((geometry['WINDOW_WIDTH'], geometry['WINDOW_HEIGHT']), (1140, 680))
        self.assertEqual((contract['window']['width'], contract['window']['height']), (1140, 680))
        self.assertEqual(geometry['LAYOUT']['sprite_scale'], 0.7)
        self.assertNotIn('scale', geometry['LAYOUT'])

    def test_sprite_scale_cannot_change_native_bounds(self):
        layout = dict(ui_scale=1, sprite_scale=0.3, window=dict(width=340, height=440))
        with mock.patch.object(Path, 'read_text', return_value=json.dumps(layout)):
            geometry = runpy.run_path(str(GEOMETRY))
        self.assertEqual((geometry['WINDOW_WIDTH'], geometry['WINDOW_HEIGHT']), (340, 440))

    def test_visible_character_anchor_at_each_dpi_and_negative_work_area(self):
        from pet.app import Shell
        from pet.geometry import CHAR_RECT, clamp_character
        for dpi in (96, 144, 192):
            for left, top, right, bottom in ((0,0,1920,1040),(-2560,-200,0,1200)):
                area=SimpleNamespace(Left=left,Top=top,Right=right,Bottom=bottom)
                screen=mock.Mock();screen.PrimaryScreen.WorkingArea=area
                screen.FromRectangle.return_value.WorkingArea=area
                shell=Shell.__new__(Shell);shell.dragging=False;shell.window=mock.Mock()
                shell.window.native.Handle.ToInt64.return_value=1
                shell._user=mock.Mock();shell._user.GetDpiForWindow.return_value=dpi
                shell._ui=lambda fn:fn();shell.push_avail=mock.Mock()
                scale=dpi/96;c=CHAR_RECT
                expected=clamp_character(right,bottom,scale,(left,top,right,bottom))
                with mock.patch.dict(sys.modules,{'System.Drawing':SimpleNamespace(Rectangle=mock.Mock()),
                        'System.Windows.Forms':SimpleNamespace(Screen=screen)}):
                    for saved in (None,(right,bottom),expected):
                        shell._load_position=lambda:saved;shell.anchor()
                        args=shell._user.SetWindowPos.call_args.args
                        self.assertEqual(args[2:4],expected)
                        self.assertEqual(args[-1],0x10|0x04)
                        self.assertAlmostEqual(args[2]+(c['x']+c['w'])*scale,right,delta=1.01)
                        self.assertAlmostEqual(args[3]+(c['y']+c['h'])*scale,bottom,delta=1.01)

    def test_legacy_position_migration_and_sprite_coordinate_roundtrip(self):
        import tempfile
        from pet.app import Shell,POSITION_FILE
        with tempfile.TemporaryDirectory() as temp:
            shell=Shell.__new__(Shell);shell.directory=Path(temp);shell.window=mock.Mock()
            shell.window.native.Handle.ToInt64.return_value=1;shell._user=mock.Mock()
            for dpi in (96,144,192):
                shell._user.GetDpiForWindow.return_value=dpi
                path=shell.directory/POSITION_FILE
                path.write_text(json.dumps(dict(x=1000,y=200)))
                expected=(round(1000-400*dpi/96),200)
                self.assertEqual(shell._load_position(),expected)
                shell._save_position(*expected)
                self.assertEqual(json.loads(path.read_text())['version'],2)
                self.assertEqual(shell._load_position(),expected)

    def test_avail_is_window_work_area_intersection_in_logical_pixels(self):
        from pet.geometry import available
        for scale in (1,1.5,2):
            for area in ((0,0,1920,1040),(-2560,-200,0,1200)):
                x,y=area[0]-500*scale,area[1]-200*scale
                self.assertEqual(available(x,y,1140,680,scale,area),dict(x=500,y=200,w=640,h=480))

    def test_native_region_scales_logical_rectangles_exactly_once(self):
        from pet.app import Shell
        for dpi in (96, 144, 192):
            shell = Shell.__new__(Shell)
            shell.window = mock.Mock()
            shell.window.native.Handle.ToInt64.return_value = 1
            shell._user = mock.Mock()
            shell._user.GetDpiForWindow.return_value = dpi
            shell._gdi = mock.Mock()
            shell._gdi.CreateRectRgn.side_effect = [1, 2]
            shell._ui = lambda callback: callback()
            rect = dict(x=311, y=350, w=130, h=30)
            shell.region([rect])
            self.assertEqual(shell._gdi.CreateRectRgn.call_args.args,
                             tuple(round(n * dpi / 96) for n in (311, 350, 441, 380)))
            shell._user.SetWindowRgn.assert_called_once()


if __name__ == '__main__':
    unittest.main()
