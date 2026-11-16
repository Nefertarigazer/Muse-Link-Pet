// Isolated preview bridge. Run with playwright-cli run-code --filename.
async (page) => {
  const check=(ok,msg)=>{if(!ok)throw new Error(msg);};
  const base='http://127.0.0.1:18877', errors=[], results=[], bubbles=[];
  const layout=await(await page.request.get(`${base}/layout.json`)).json(), c=layout.char_rect;
  const areas={center:{x:0,y:0,w:1140,h:680},left:{x:c.x,y:0,w:1140-c.x,h:680},
    right:{x:0,y:0,w:c.x+c.w,h:680},top:{x:0,y:c.y,w:1140,h:680-c.y},
    bottom:{x:0,y:0,w:1140,h:c.y+c.h}};
  for(const x of ['left','right'])for(const y of ['top','bottom']) {
    const a=areas[x],b=areas[y];areas[`${x}-${y}`]={x:a.x,y:b.y,w:a.w,h:b.h};
  }
  const bridge=({area})=>{
    window.r11Area=area;window.r11State={state:'idle',unread:3};window.r11Regions=[];window.r11Calls=[];window.r11Tag='en';
    const ok=data=>({ok:true,data}),bundle=async()=>({tag:window.r11Tag,dir:'ltr',messages:await(await fetch(`/i18n/${window.r11Tag}.json`)).json()});
    window.pywebview={api:{config:async()=>ok({pack:'jolly',sound:false,poll_ms:60000}),
      i18n:async()=>ok(await bundle()),languages:async()=>ok([{tag:'en',name:'English'},{tag:'zh-Hans',name:'\u7b80\u4f53\u4e2d\u6587'},{tag:'zh-Hant',name:'\u7e41\u9ad4\u4e2d\u6587'}]),
      set_language:async tag=>{window.r11Tag=tag;return ok(await bundle());},state:async()=>ok(window.r11State),
      screen:async()=>ok(window.r11Area),region:async rs=>{window.r11Regions=rs;return ok(null);},activate:async()=>ok(null),
      open_mailbox:async()=>{window.r11Calls.push(['mailbox']);return ok(null);},
      channels:async()=>ok({items:[]}),done:async id=>{window.r11Calls.push(['done',id]);return ok({delivered:false});},
      say:async(text,id)=>{window.r11Calls.push(['say',text,id]);return ok({via:'bridge'});}}};
  };
  const probe=async(p,selector,v,label)=>{
    const r=await p.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
    check(r.x>=v.x-.1&&r.y>=v.y-.1&&r.x+r.w<=v.x+v.w+.1&&r.y+r.h<=v.y+v.h+.1,`${label} outside avail ${JSON.stringify({r,v})}`);
    const covered=await p.locator(selector).evaluate(el=>{const r=el.getBoundingClientRect();return window.r11Regions.some(q=>q.x<=r.x&&q.y<=r.y&&q.x+q.w>=r.right&&q.y+q.h>=r.bottom);});
    check(covered,`${label} missing native region`);return r;
  };
  const bubble=async(p,dpi,edge,text,v)=>{
    const b=await p.locator('.busy').evaluate(async el=>{
      const r=el.getBoundingClientRect(),pet=document.querySelector('#pet').getBoundingClientRect();
      const orbs=[...document.querySelectorAll('.orb.on')].map(o=>o.getBoundingClientRect());
      const ov=(a,q)=>a.left<q.right&&a.right>q.left&&a.top<q.bottom&&a.bottom>q.top;
      const dot=el.querySelector('.busy-dot'),cs=getComputedStyle(el);
      return {side:el.dataset.side,w:r.width,h:r.height,
        clearPet:!ov(r,pet),clearOrbs:orbs.every(o=>!ov(r,o)),
        hasSvg:el.querySelectorAll('svg').length>0,
        pseudo:getComputedStyle(el,'::after').content,
        background:cs.backgroundColor,shadow:cs.boxShadow,radius:cs.borderRadius,
        hasText:el.classList.contains('has-text'),
        dotDisplay:dot?getComputedStyle(dot).display:'none',
        covered:window.r11Regions.some(q=>q.x<=r.x&&q.y<=r.y&&q.x+q.w>=r.right&&q.y+q.h>=r.bottom),
        rect:{x:r.x,y:r.y,w:r.width,h:r.height}};
    });
    check(!b.hasSvg&&['none','normal'].includes(b.pseudo),'clean capsule without SVG tail');
    check(b.background!=='rgba(0, 0, 0, 0)'&&b.shadow!=='none','frosted pill surface and shadow');
    check(b.hasText?(b.dotDisplay!=='none'):(b.dotDisplay==='none'),'busy dot matches text state');
    check(b.covered,`pill missing native region ${JSON.stringify({dpi,edge,text,b})}`);
    check(b.clearPet&&b.clearOrbs,`pill overlaps pet or orb ${JSON.stringify({dpi,edge,text,b})}`);
    check(b.rect.x>=v.x-.1&&b.rect.y>=v.y-.1&&b.rect.x+b.rect.w<=v.x+v.w+.1&&b.rect.y+b.rect.h<=v.y+v.h+.1,'pill outside avail');
    bubbles.push({dpi,edge,text,...b});
  };
  const noOverlap=(a,b)=>a.x>=b.x+b.w||a.x+a.w<=b.x||a.y>=b.y+b.h||a.y+a.h<=b.y;
  const checkBubbleClear=async(p,v,label)=>{
    const bub=await probe(p,'#bubble',v,`${label}/bubble`);
    const petBox=await p.locator('#pet').evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
    const alertBox=await p.locator('.alert').evaluate(el=>{const r=el.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};});
    check(noOverlap(bub,petBox),`${label} bubble overlaps pet`);
    check(noOverlap(bub,alertBox),`${label} bubble overlaps alert`);
    const orbBoxes=await p.locator('.orb.on').evaluateAll(els=>els.map(el=>{const r=el.getBoundingClientRect();return {id:el.dataset.id,x:r.x,y:r.y,w:r.width,h:r.height};}));
    for(const o of orbBoxes) check(noOverlap(bub,o),`${label} bubble overlaps ${o.id}`);
  };
  for(const dpi of [1,1.5,2]) {
    const context=await page.context().browser().newContext({viewport:{width:1140,height:680},deviceScaleFactor:dpi,reducedMotion:'reduce'});
    try {
      await context.addInitScript(bridge,{area:areas.center});const p=await context.newPage();p.on('pageerror',e=>errors.push(String(e)));
      await p.goto(`${base}/index.html?clean&hover=1`);await p.waitForFunction(()=>Boolean(window.MusePet));
      check(await p.evaluate(()=>devicePixelRatio)===dpi,'DPR mismatch');
      await p.waitForTimeout(120);
      const pet=await p.locator('#pet').boundingBox();
      for(const [edge,v] of Object.entries(areas))for(const waiting of [false,true]) {
        await p.keyboard.press('Escape');
        await p.evaluate(async({v,waiting})=>{window.r11Area=v;window.r11State={state:waiting?'waiting_user':'working',unread:3,
          ...(waiting?{ask:{req_id:'r11-wait',mode:'choice',summary:'Fixture question'}}:{activity:'shell'})};
          window.MusePet.avail(v);await window.MusePet.poke();},{v,waiting});
        await p.locator("#pet").hover();
        await p.waitForTimeout(70);
        for(const id of ['channel','inbox','session','talk',...(waiting?['done']:[])])await probe(p,`.orb-${id}`,v,`${dpi}/${edge}/${waiting}/${id}`);
        check(await p.locator('.unread-badge').textContent()==='3','unread badge');
        if(!waiting) {
          for(const text of [null,'Fixture phase','一二三四五六七八九👩‍💻']) {
            await p.evaluate(async text=>{window.r11State={state:'working',unread:3,...(text?{brief:text,brief_id:String(Math.random()),brief_at:new Date().toISOString()}: {})};await window.MusePet.poke();},text);
            await p.waitForTimeout(30);await probe(p,'.busy',v,`${edge}/pill/${text}`);
            await bubble(p,dpi,edge,text,v);
            if(dpi===1&&['left','right','center'].includes(edge))await p.screenshot({path:`output/playwright/r12/${edge}-${text?(text==='Fixture phase'?'text':'long'):'dots'}.png`});
          }
          // Multiline pill: validate height-based lift and top clamp.
          await p.locator('.busy-text').evaluate(el=>{el.style.whiteSpace='normal';el.style.maxWidth='30px';el.style.overflowWrap='anywhere';});
          await p.evaluate(()=>window.MusePet.avail(window.r11Area));await probe(p,'.busy',v,`${edge}/multiline-pill`);
          await bubble(p,dpi,edge,'multiline',v);
          await p.locator('.busy-text').evaluate(el=>{el.style.whiteSpace='';el.style.maxWidth='';});
        } else {
          await p.waitForTimeout(40);
          await checkBubbleClear(p,v,`${dpi}/${edge}/waiting-hover`);
          const talkPos=await p.locator('.orb-talk').evaluate(el=>({x:el.offsetLeft,y:el.offsetTop}));
          const donePos=await p.locator('.orb-done').evaluate(el=>({x:el.offsetLeft,y:el.offsetTop}));
          check(talkPos.y===donePos.y&&Math.abs(Math.abs(talkPos.x-donePos.x)-56)<=1,`done beside talk ${JSON.stringify({talkPos,donePos})}`);
          await p.locator('.orb-done').click();
          check(await p.evaluate(()=>window.r11Calls.some(q=>q[0]==='done'&&q[1]==='r11-wait')),'Done ReqId');
        }
        await p.evaluate(()=>window.MusePet.talk());await p.waitForTimeout(60);
        const short=await probe(p,'#panel',v,`${edge}/talk-short`);check(Math.abs(short.w-300)<.1,'talk width');
        if(edge==='center') {
          const value=await p.locator('#panel').evaluate(el=>({shift:getComputedStyle(el).getPropertyValue('--talk-shift'),y:el.offsetTop,h:el.offsetHeight,rig:document.querySelector('#rig').offsetTop}));
          const expected=292+38+1-short.h-short.h*.05;
          check(Math.abs(short.y-expected)<.6,`talk -5% ${JSON.stringify({short,expected,value})}`);
          const z=await p.evaluate(()=>({cel:Number(getComputedStyle(document.querySelector('.pet-celebrate')).zIndex),panel:Number(getComputedStyle(document.querySelector('#panel')).zIndex),orbs:Number(getComputedStyle(document.querySelector('#orbs')).zIndex)}));
          check(z.cel>z.panel&&z.cel>z.orbs,`celebrate z-index ${JSON.stringify(z)}`);
        }
        await p.locator('#say').fill('One\nTwo\nThree\nFour\nFive\nSix\nSeven');await p.waitForTimeout(60);
        const long=await probe(p,'#panel',v,`${edge}/talk-long`);check(long.h>short.h,'textarea growth');
        await p.keyboard.press('Escape');
        await p.locator('#pet').click({button:'right'});await p.locator('#language-trigger').hover();await p.locator('#language-list').waitFor({state:'visible'});
        await probe(p,'#context-card',v,`${edge}/menu`);await probe(p,'#language-list',v,`${edge}/flyout`);
        const tag=['en','zh-Hans','zh-Hant'][results.length%3];await p.locator(`[data-language="${tag}"]`).click();
        await p.waitForFunction(tag=>document.documentElement.lang===tag,tag);
        const after=await p.locator('#pet').boundingBox();check(Math.abs(after.x-pet.x)<.1&&Math.abs(after.y-pet.y)<.1,'pet moved with UI');
        const arc=await p.locator('#rig').getAttribute('data-arc');results.push({dpi,edge,waiting,arc});
        if(waiting&&dpi===1)await p.screenshot({path:`output/playwright/r12/${edge}-waiting.png`});
      }
      // Existing keyboard flyout behavior and all waiting modes remain usable.
      await p.keyboard.press('Escape');
      await p.evaluate(async v=>{window.r11Area=v;window.MusePet.avail(v);await window.MusePet.poke();},areas.center);
      await p.locator('#pet').click({button:'right'});await p.locator('#language-trigger').focus();await p.keyboard.press('ArrowRight');
      check(await p.locator('#language-list button').first().evaluate(el=>el===document.activeElement),'keyboard flyout entry');
      await p.locator('#language-list button').first().hover();await p.mouse.move(10,10);await p.waitForTimeout(260);check(await p.locator('#language-list').isHidden(),'flyout collapse');
      await p.keyboard.press('Escape');
      for(const mode of ['info','choice','input']) {
        await p.evaluate(async({v,mode})=>{window.r11Area=v;window.r11State={state:'waiting_user',ask:{req_id:'r11-mode-'+mode,mode,summary:'Fixture'}};
          window.MusePet.avail(v);await window.MusePet.poke();},{v:areas['right-bottom'],mode});
        await p.waitForTimeout(50);
        if (mode !== 'input') await probe(p,'.orb-done',areas['right-bottom'],`waiting-${mode}`);
        else check(await p.locator('.orb-done').evaluate(el=>!el.classList.contains('on')),'input hides done');
        await probe(p,'.orb-talk',areas['right-bottom'],`waiting-${mode}`);
        await checkBubbleClear(p,areas['right-bottom'],`waiting-${mode}`);
      }
      // Verify hover retracts promptly on leave and immediately on pointer(false).
      await p.goto(`${base}/index.html?clean`);await p.waitForFunction(()=>Boolean(window.MusePet));
      await p.evaluate(async v=>{window.r11Area=v;window.r11State={state:'idle',unread:0};window.MusePet.avail(v);await window.MusePet.poke();},areas.center);
      await p.locator('#pet').hover();await p.waitForTimeout(40);
      check((await p.locator('.orb.on').count())===4,'hover opens 4 orbs');
      const hoverTalk=await p.locator('.orb-talk').evaluate(el=>({x:el.offsetLeft,y:el.offsetTop}));
      await p.mouse.move(10,10);await p.waitForTimeout(140);
      check((await p.locator('.orb.on').count())===0,'hover retracts promptly on leave');
      await p.evaluate(async v=>{window.r11State={state:'waiting_user',ask:{req_id:'r13-talk',mode:'info',summary:'Test'}};await window.MusePet.poke();},areas.center);
      await p.waitForTimeout(40);
      const waitTalk=await p.locator('.orb-talk').evaluate(el=>({x:el.offsetLeft,y:el.offsetTop}));
      check(waitTalk.x===hoverTalk.x&&waitTalk.y===hoverTalk.y,'talk matches hover position');
      await p.locator('#pet').hover();await p.waitForTimeout(40);
      check((await p.locator('.orb.on').count())===5,'waiting+hover shows 5 orbs');
      await p.evaluate(()=>window.MusePet.pointer(false));await p.waitForTimeout(20);
      check((await p.locator('.orb.on').count())===2,'pointer(false) retracts immediately');
      // Force each talk clamp independently, including a reduced bottom bound.
      await p.evaluate(async v=>{window.r11Area=v;window.MusePet.avail(v);await window.MusePet.poke();window.MusePet.talk();},areas.center);
      await p.waitForTimeout(60);
      for(const v of [{x:0,y:250,w:1140,h:430},{x:0,y:0,w:1140,h:300}]) {
        await p.evaluate(v=>{window.r11Area=v;window.MusePet.avail(v);},v);await probe(p,'#panel',v,'talk clamp');
      }
      await p.keyboard.press('Escape');
      // Same one-piece glass material in dark mode, on each side.
      await p.emulateMedia({colorScheme:'dark'});
      for(const edge of ['left','right']) {
        await p.evaluate(async v=>{window.r11Area=v;window.r11State={state:'working',activity:'shell'};window.MusePet.avail(v);await window.MusePet.poke();},areas[edge]);
        await p.waitForTimeout(40);await bubble(p,dpi,edge,'dark',areas[edge]);
        if(dpi===1)await p.screenshot({path:`output/playwright/r12/${edge}-dark.png`});
      }
      await p.emulateMedia({colorScheme:'light'});
      // Sample the native region during the pill's entry spring.
      await p.evaluate(async v=>{window.r11Area=v;window.r11State={state:'idle'};window.MusePet.avail(v);await window.MusePet.poke();},areas.center);
      await p.emulateMedia({reducedMotion:'no-preference'});
      await p.evaluate(async()=>{window.r11State={state:'working',activity:'shell'};await window.MusePet.poke();});
      for(let i=0;i<8;i++){await probe(p,'.busy',areas.center,'pill animation');await p.waitForTimeout(55);}
      await p.emulateMedia({reducedMotion:'reduce'});
      // Real input/send path and badge disappearance, then inbox action.
      await p.evaluate(async v=>{window.r11Area=v;window.r11State={state:'idle',unread:0};window.MusePet.avail(v);await window.MusePet.poke();},areas.center);
      await p.evaluate(()=>window.MusePet.talk());await p.locator('#say').fill('R11 fixture');await p.locator('.send').click();
      check(await p.evaluate(()=>window.r11Calls.some(q=>q[0]==='say'&&q[1]==='R11 fixture')),'send failed');
      await p.keyboard.press('Escape');await p.locator('#pet').hover();await p.locator('.orb-inbox').click();check(await p.evaluate(()=>window.r11Calls.some(q=>q[0]==='mailbox')),'inbox action');
      check(await p.locator('.unread-badge').isHidden(),'badge failed to clear');
    }finally{await context.close();}
  }
  check(errors.length===0,`page errors: ${errors}`);
  const bad=bubbles.filter(b=>!(b.clearPet&&b.clearOrbs&&b.covered&&!b.hasSvg));
  check(bad.length===0,`pill issue: ${JSON.stringify(bad.slice(0,3))}`);
  check(bubbles.filter(b=>['center','left','right'].includes(b.edge)).every(b=>b.side==='bottom'),'open-bottom pills sit centered below pet');
  return {cases:results.length,bubbleCases:bubbles.length,errors,results};
}
