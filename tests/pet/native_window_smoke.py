"""Explicit Windows integration: isolated pet + mailbox, real WebView2.

Run directly, never against installed data or a live executor/popup bus.
"""
import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'pc'))
import webview
from pet.api import Api
from pet.app import Shell
from pet.geometry import CHAR_RECT,WINDOW_WIDTH,WINDOW_HEIGHT
from pet.mailbox_window import MailboxWindow
from pet.tray import ICON_PATH
from mailbox.shell import static_server_class


def wait(fn,seconds=20):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        if fn():return
        time.sleep(.1)
    raise AssertionError('native fixture timeout')


def main():
    result={'pid':os.getpid(),'checks':[]};errors=[]
    with tempfile.TemporaryDirectory(prefix='muselink-r11-') as temp:
        root=Path(temp);bridge=root/'bridge';bridge.mkdir()
        config=root/'config.json';config.write_text(json.dumps({'bridge':{'root':str(bridge)}}))
        mailbox=MailboxWindow(config,Path(__file__).resolve().parents[2]/'pc/mailbox',root/'mailbox-state')
        mailbox.start()
        watch=mock.Mock();watch.snapshot.return_value={'state':'working','activity':'shell'}
        actions=mock.Mock();actions.open_mailbox=mailbox.open
        api=Api(watch,actions,mock.Mock(),{'sound':False},mailbox=mailbox)
        ui=Path(__file__).resolve().parents[2]/'pc/pet/ui'
        window=webview.create_window('R11 isolated pet',str(ui/'index.html'),js_api=api,
            width=WINDOW_WIDTH,height=WINDOW_HEIGHT,frameless=True,transparent=True,on_top=True,
            focus=False,resizable=False,easy_drag=False,shadow=False)
        shell=Shell(window,root);api._attach(shell)
        window.events.loaded+=shell.start
        def run():
            try:
                wait(lambda:shell.ready.is_set())
                wait(lambda:window.evaluate_js('Boolean(window.MusePet)'))
                wait(lambda:len(shell.rects)>0)
                result['dpi']=shell._user.GetDpiForWindow(shell.hwnd)
                result['avail']=shell.screen()
                result['checks'].append('pet native transparency/region initialized')
                # Native region includes UI but not empty spill in any direction.
                gdi=shell._gdi;u=shell._user
                gdi.PtInRegion.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int]
                u.GetWindowRgn.argtypes=[ctypes.c_void_p,ctypes.c_void_p]
                region=gdi.CreateRectRgn(0,0,0,0)
                try:
                    wait(lambda:u.GetWindowRgn(shell.hwnd,region)>0)
                    scale=result['dpi']/96
                    for x,y in [(1,1),(1139,1),(1,679),(1139,679)]:
                        assert not gdi.PtInRegion(region,round(x*scale),round(y*scale))
                    assert any(gdi.PtInRegion(region,round((r['x']+r['w']/2)*scale),round((r['y']+r['h']/2)*scale)) for r in shell.rects)
                finally:gdi.DeleteObject(region)
                result['checks'].append('all four empty spill corners outside native region')
                from System.Windows.Forms import Screen
                area=Screen.PrimaryScreen.WorkingArea
                result['edge_cases']=[];result['bubble_cases']=[]
                for horizontal,vertical in [('center','center'),('left','center'),('right','center'),
                        ('center','top'),('center','bottom'),('left','top'),('left','bottom'),
                        ('right','top'),('right','bottom')]:
                    scale=shell._user.GetDpiForWindow(shell.hwnd)/96
                    c=CHAR_RECT
                    px={'left':area.Left,'right':area.Right-c['w']*scale,
                        'center':area.Left+(area.Width-c['w']*scale)/2}[horizontal]
                    py={'top':area.Top,'bottom':area.Bottom-c['h']*scale,
                        'center':area.Top+(area.Height-c['h']*scale)/2}[vertical]
                    shell._save_position(px-c['x']*scale,py-c['y']*scale)
                    shell.anchor()
                    for waiting in (False,True):
                        watch.snapshot.return_value={'state':'waiting_user' if waiting else 'working',
                            'activity':'shell','ask':{'req_id':'native-fixture','mode':'input','summary':'Fixture'} if waiting else None}
                        window.evaluate_js('MusePet.poke()')
                        window.evaluate_js('document.querySelector("#pet").dispatchEvent(new PointerEvent("pointerenter"))')
                        time.sleep(.12)
                        v=shell.screen()
                        assert v['x'] <= c['x']+.01 and v['y'] <= c['y']+.01
                        assert v['x']+v['w'] >= c['x']+c['w']-.01
                        assert v['y']+v['h'] >= c['y']+c['h']-.01
                        valid=window.evaluate_js('''(()=>{const v=%s;return [...document.querySelectorAll('.orb.on')].every(el=>{
                          const r=el.getBoundingClientRect();return r.x>=v.x-.1&&r.y>=v.y-.1&&r.right<=v.x+v.w+.1&&r.bottom<=v.y+v.h+.1;});})()''' % json.dumps(v))
                        assert valid,(horizontal,vertical,waiting,v)
                        result['edge_cases'].append([horizontal,vertical,waiting])
                        if not waiting:
                            time.sleep(.45)  # measure after the capsule's entry spring
                            bubble=window.evaluate_js('''(()=>{const el=document.querySelector('.busy'),
                              b=el.getBoundingClientRect(),c=%s,pet=document.querySelector('#pet').getBoundingClientRect(),
                              orbs=[...document.querySelectorAll('.orb.on')].map(o=>o.getBoundingClientRect()),
                              ov=(a,q)=>a.left<q.right&&a.right>q.left&&a.top<q.bottom&&a.bottom>q.top;
                              return {side:el.dataset.side,gap:b.top-c.y-c.h,cx:(b.left+b.width/2)-(c.x+c.w/2),
                                clearPet:!ov(b,pet)&&!ov(b,{left:c.x,right:c.x+c.w,top:c.y,bottom:c.y+c.h}),
                                clearOrbs:orbs.every(o=>!ov(b,o)),
                                clean:el.querySelectorAll('svg').length===0&&getComputedStyle(el,'::after').content==='none',
                                rect:{x:b.x,y:b.y,w:b.width,h:b.height}};})()''' % json.dumps(c))
                            assert bubble['clean'] and bubble['clearPet'] and bubble['clearOrbs'],bubble
                            if (horizontal,vertical)==('center','center'):
                                assert 0<=bubble['gap']<=12 and abs(bubble['cx'])<=2,bubble
                            r=bubble.pop('rect')
                            assert r['x']>=v['x']-.1 and r['y']>=v['y']-.1 and r['x']+r['w']<=v['x']+v['w']+.1 and r['y']+r['h']<=v['y']+v['h']+.1,(r,v)
                            region=gdi.CreateRectRgn(0,0,0,0)
                            try:
                                assert u.GetWindowRgn(shell.hwnd,region)>0
                                for px,py in [(r['x']+2,r['y']+2),(r['x']+r['w']-2,r['y']+2),(r['x']+2,r['y']+r['h']-2),(r['x']+r['w']-2,r['y']+r['h']-2)]:
                                    assert gdi.PtInRegion(region,round(px*scale),round(py*scale))
                            finally:gdi.DeleteObject(region)
                            result['bubble_cases'].append([horizontal,vertical,bubble])
                result['checks'].append('18 real edge/corner state cases inside pushed avail')
                result['checks'].append('9 one-piece bubbles hugging the character with complete native outline coverage')
                mailbox.open('pet/chat');wait(lambda:mailbox._ready)
                child=mailbox.window
                wait(lambda:child.evaluate_js('Boolean(document.querySelector("#search-input")) && !document.querySelector("#winctl").hidden'))
                assert len(webview.windows)==2
                assert child.native.Icon is not None and child.native.ShowInTaskbar
                result['checks'].append('second window same process, pet icon, taskbar button')
                for _ in range(3):
                    child.destroy();wait(lambda:not child.native.Visible)
                    assert mailbox.window is child
                    mailbox.open('pet/reopened');wait(lambda:child.native.Visible)
                    assert mailbox.window is child and len(webview.windows)==2
                result['checks'].append('close/hide/reopen three cycles reuse one child')
                assert (root/'mailbox-state/window.json').is_file()
                result['checks'].append('mailbox geometry persists in fixture state directory')
                assert not any(t.name=='mailbox-tray' for t in threading.enumerate())
                result['checks'].append('no mailbox tray thread')
                result['servers']=[window._server.root_path,mailbox._server.root_path]
                assert Path(window._server.root_path)==ui
                assert Path(mailbox._server.root_path)==ui.parents[1]/'mailbox/ui'
                result['checks'].append('separate restricted static roots')
                # A mailbox failure is surfaced through the bridge; pet remains responsive.
                original=actions.open_mailbox
                actions.open_mailbox=mock.Mock(side_effect=RuntimeError('fixture'))
                assert not api.open_mailbox()['ok']
                assert window.evaluate_js('Boolean(window.MusePet)')
                actions.open_mailbox=original
                result['checks'].append('mailbox exception leaves pet responsive')
            except Exception as exc:
                import traceback
                errors.append(traceback.format_exc())
            finally:
                mailbox.stop();shell.stop();window.destroy()
        webview.start(run,gui='edgechromium',http_server=True,server=static_server_class(ui),icon=str(ICON_PATH))
        result['errors']=errors
        result['remaining_windows']=len(webview.windows)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if errors:raise AssertionError(errors[0])

if __name__=='__main__':main()
