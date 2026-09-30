#!/usr/bin/env python3
"""Optional tests for the complete local reference applications.
Requires Python Playwright and Chromium. No package is downloaded by this script.
Example: python tests/browser_smoke.py --browser /usr/bin/chromium --output /tmp/lui-tests
Uses in-memory HTML loading, so it also works when file/local HTTP navigation is
restricted. Linked starter/START resources are embedded for that test only.
This validates DOM, CSS, script and rendering, not the browser's file URL policy.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]

def embed_resources(path):
    text = path.read_text(encoding='utf-8')
    def css(m):
        return '<style>'+ (path.parent/m.group(1)).read_text()+'</style>'
    text = re.sub(r'<link rel="stylesheet" href="([^"]+)">', css, text)
    def js(m):
        return '<script>'+(path.parent/m.group(1)).read_text()+'</script>'
    text = re.sub(r'<script src="([^"]+)"></script>', js, text)
    def image(m):
        p = path.parent/m.group(1)
        return 'src="data:image/png;base64,'+base64.b64encode(p.read_bytes()).decode()+'"'
    return re.sub(r'src="([^"]+\.png)"',image,text)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',default=None)
    parser.add_argument('--output',type=Path,default=None)
    args=parser.parse_args()
    out=args.output or Path(tempfile.mkdtemp(prefix='lui-browser-'))
    out.mkdir(parents=True,exist_ok=True)
    from playwright.sync_api import sync_playwright
    checks=[];errors=[];external=[];graphics={}
    def check(name,condition,detail=None):
        checks.append({'name':name,'status':'PASS' if condition else 'FAIL','detail':detail})
        if not condition: raise AssertionError(name+': '+str(detail))
    with sync_playwright() as p:
        launch={'headless':True,'args':['--no-sandbox','--enable-unsafe-swiftshader']}
        if args.browser:launch['executable_path']=args.browser
        browser=p.chromium.launch(**launch)
        version=browser.version
        def page_for(name,viewport=None,reduced='no-preference'):
            page=browser.new_page(viewport=viewport or {'width':1440,'height':1050},reduced_motion=reduced)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('request',lambda r:external.append(r.url) if r.url.startswith(('http://','https://')) else None)
            page.set_content(embed_resources(ROOT/name))
            return page
        page=page_for('assets/reference.html')
        for theme in ['mineral','depth','stone']:
            page.locator(f'[data-theme="{theme}"]').click()
            page.wait_for_timeout(150)
            check('style-theme-'+theme,page.evaluate('LuminaDemo.state.theme')==theme)
            page.mouse.move(1,1);page.wait_for_timeout(350)
            page.screenshot(path=str(out/(theme+'.png')),full_page=True)
        check('style-no-horizontal-overflow',page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        page.locator('#touch').click()
        timing=page.evaluate("document.getElementById('organism-motion').getAnimations().map(a=>a.effect.getTiming())")
        check('touch-uses-600ms-source-enter',any(a['duration']==600 for a in timing),timing)
        check('contact-mark',page.evaluate('LuminaDemo.state.marks')==1)
        page.locator('#motion-toggle').click();page.locator('#touch').click()
        check('motion-disabled-instant',page.evaluate('!ResearchMotion.canMove() && document.getElementById("organism-motion").getAnimations().length===0'))
        page.locator('#texture-toggle').click()
        check('texture-off',page.evaluate('getComputedStyle(document.querySelector(".grain")).opacity')=='0')
        page.locator('#clear-traces').click()
        check('marks-cleared',page.evaluate('LuminaDemo.state.marks')==0)
        page.close()
        mobile=page_for('assets/reference.html',{'width':390,'height':844})
        mobile.locator('[data-theme="stone"]').click();mobile.mouse.move(1,1)
        check('mobile-390-no-overflow',mobile.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        mobile.screenshot(path=str(out/'mobile.png'),full_page=True)
        mobile.set_viewport_size({'width':320,'height':700})
        check('mobile-320-no-overflow',mobile.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        mobile.close()
        reduced=page_for('assets/reference.html',reduced='reduce')
        reduced.locator('#motion-toggle').click();reduced.locator('#motion-toggle').click();reduced.locator('#touch').click()
        check('system-reduced-motion-not-overridden',reduced.evaluate('!ResearchMotion.canMove() && document.getAnimations().length===0'))
        reduced.close()
        g=page_for('assets/motion-reference.html')
        g.evaluate("LuminaMotionDemo.setPalette('stone')")
        modes=['dithering','metaballs','threads','particles','interactive','dots']
        for mode in modes:
            g.evaluate('(m)=>LuminaMotionDemo.chooseField(m)',mode)
            g.wait_for_timeout(150)
            state=g.evaluate('LuminaMotionDemo.state')
            if state['sceneError']:
                graphics[mode]={'status':'UNAVAILABLE','reason':state['sceneError']}
            else:
                sc=state['motion']['scenes']
                check('one-canvas-'+mode,len(sc)==1 and g.locator('#field-host canvas').count()==1)
                a=g.locator('#field-host canvas').screenshot()
                g.wait_for_timeout(300)
                b=g.locator('#field-host canvas').screenshot()
                graphics[mode]={'status':'PASS','frames':sc[0]['frames'],'two_frame_difference':a!=b}
        check('2d-dots-available',graphics['dots']['status']=='PASS')
        g.locator('#field-stage').scroll_into_view_if_needed()
        bounds=g.locator('#field-host').bounding_box()
        g.mouse.move(bounds['x']+70,bounds['y']+90)
        g.mouse.click(bounds['x']+200,bounds['y']+150)
        a=g.locator('#field-host canvas').screenshot();g.wait_for_timeout(150);b=g.locator('#field-host canvas').screenshot()
        check('dot-grid-impulse-changes-frame',a!=b)
        g.locator('#quiet-input').focus()
        n=g.evaluate('ResearchMotion.state.scenes[0].frames');g.wait_for_timeout(200)
        check('writing-pauses-scene',g.evaluate('ResearchMotion.state.writing && ResearchMotion.state.scenes[0].frames')==n)
        g.locator('#ambient-toggle').focus();g.wait_for_timeout(250)
        g.locator('#speed-slider').focus();g.keyboard.press('ArrowRight')
        check('slider-keyboard-step',g.locator('#speed-slider').get_attribute('aria-valuenow')=='0.85')
        g.keyboard.press('Home');check('slider-min',g.locator('#speed-slider').get_attribute('aria-valuenow')=='0.15')
        g.keyboard.press('End');check('slider-max',g.locator('#speed-slider').get_attribute('aria-valuenow')=='1.50')
        # Capture cover count at the exact content-replacement callback.
        g.evaluate('''() => {window.coverCounts=[];const f=document.getElementById('pixel-face'),old=f.replaceChildren;f.replaceChildren=function(...args){coverCounts.push([...document.querySelectorAll('#pixel-card .pixelated-image-card__pixel')].filter(e=>e.style.display==='block').length);return old.apply(this,args)};}''')
        g.locator('#pixel-flip').click();g.wait_for_timeout(140)
        counts=g.evaluate("[...document.querySelectorAll('#pixel-card .pixelated-image-card__pixel')].filter(e=>e.style.display==='block').length")
        check('pixel-cover-intermediate',0<counts<144,counts)
        g.wait_for_timeout(550)
        check('pixel-swap-when-fully-covered',g.evaluate('coverCounts[0]')==144,g.evaluate('coverCounts'))
        check('pixel-mask-cleaned',g.locator('.source-pixel-grid').count()==0)
        g.evaluate("()=>{LuminaMotionDemo.flipPixel();LuminaMotionDemo.flipPixel();LuminaMotionDemo.flipPixel()}")
        g.wait_for_timeout(700)
        check('pixel-latest-request-wins',g.evaluate('Number(document.getElementById("pixel-face").dataset.version)===LuminaMotionDemo.state.pixelVersion'))
        g.locator('#enter-demo').click()
        check('content-enter-duration',g.evaluate('document.getElementById("enter-sample").getAnimations()[0].effect.getTiming().duration')==600)
        g.locator('#cursor-demo').hover();g.wait_for_timeout(220)
        check('target-cursor-active',g.locator('#target-cursor').get_attribute('data-active')=='true')
        g.locator('#cursor-demo').click();check('cursor-button-key-action',len(g.locator('#cursor-feedback').inner_text())>0)
        g.locator('#decrypt-demo').click();g.wait_for_timeout(700)
        check('decrypt-final',g.locator('#decrypt-sample').inner_text()=='LUMINA / 001')
        g.locator('#scramble-sample .scramble-char').nth(2).hover();g.wait_for_timeout(130)
        changed=g.locator('#scramble-sample').inner_text();g.mouse.move(1,1);g.wait_for_timeout(100)
        check('scramble-local-and-restored',changed!='FORM / MATTER / CONTINUITY' and g.locator('#scramble-sample').inner_text()=='FORM / MATTER / CONTINUITY')
        g.locator('#split-demo').click()
        times=g.evaluate('[...document.querySelectorAll("#split-sample .split-char")].map(el=>el.getAnimations()[0]?.effect.getTiming())')
        check('split-650ms-30ms-stagger',times[0]['duration']==650 and times[1]['delay']==30,times[:2])
        g.locator('#type-demo').click();g.wait_for_timeout(80)
        check('type-intermediate',0<len(g.locator('#type-sample').inner_text())<len('这是一条新的本地消息。原有内容保持安静。'))
        g.wait_for_timeout(700)
        check('type-final',g.locator('#type-sample').inner_text()=='这是一条新的本地消息。原有内容保持安静。')
        g.locator('#wizard-next').click();check('stepper-validates-input','请先填写' in g.locator('#wizard-error').inner_text())
        g.locator('#new-title').fill('岩灰测试');g.locator('#wizard-next').click()
        check('stepper-busy-during-transition',g.evaluate('LuminaMotionDemo.state.wizardBusy'))
        g.wait_for_timeout(500);check('stepper-step2',g.evaluate('LuminaMotionDemo.state.wizardStep')==2)
        g.locator('#new-question').fill('具有克制的身体感。');g.locator('#wizard-next').click();g.wait_for_timeout(500)
        check('stepper-retains-real-input',g.locator('#review-question').inner_text()=='具有克制的身体感。')
        g.locator('#wizard-next').click();check('stepper-local-confirmation','本页已确认' in g.locator('#wizard-result').inner_text())
        g.locator('#motion-toggle').click();g.locator('#type-demo').click()
        check('type-immediate-when-disabled',g.locator('#type-sample').inner_text()=='这是一条新的本地消息。原有内容保持安静。')
        check('master-off-controls-ambient',g.evaluate('!ResearchMotion.state.enabled && !ResearchMotion.state.ambient'))
        g.locator('#motion-toggle').click();g.evaluate("LuminaMotionDemo.chooseField('dots')")
        g.evaluate('window.scrollTo(0,0)');g.mouse.move(1,1);g.wait_for_timeout(500)
        g.screenshot(path=str(out/'motion-gallery.png'),full_page=True)
        g.set_viewport_size({'width':390,'height':844})
        check('gallery-mobile-no-overflow',g.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        g.close()
        gr=page_for('assets/motion-reference.html',reduced='reduce')
        gr.locator('#pixel-flip').click();check('reduced-pixel-immediate',gr.locator('.source-pixel-grid').count()==0 and gr.locator('#pixel-face').get_attribute('data-version')=='1')
        gr.locator('#type-demo').click();check('reduced-text-immediate',gr.locator('#type-sample').inner_text()=='这是一条新的本地消息。原有内容保持安静。');gr.close()
        starter=page_for('examples/starter.html')
        starter.locator('[data-lui-theme-choice="stone"]').click();check('starter-theme',starter.evaluate('document.documentElement.dataset.luiTheme')=='stone')
        starter.locator('#draft').fill('可保留的草稿');starter.locator('#save').click()
        check('starter-storage-failure-safe',starter.locator('#draft').input_value()=='可保留的草稿' and bool(starter.locator('#status').inner_text()))
        starter.locator('#clear').click();check('starter-clear',starter.locator('#draft').input_value()=='');starter.close()
        check('no-uncaught-script-errors',not errors,errors)
        check('no-network-requests',not external,external)
        browser.close()
    report={'created':datetime.now(timezone.utc).isoformat(),'browser':'Chromium '+version,'load_mode':'set_content / in-memory; file:// and localhost navigation blocked by environment policy; no policy changes made','viewports':[[1440,1050],[390,844],[320,700]],'checks':checks,'graphics':graphics,'errors':errors,'network_requests':external,'storage_test':'Storage failure path verified on opaque origin; persisted reload not tested.'}
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'checks':len(checks),'status':'PASS','graphics':graphics,'output':str(out)},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
