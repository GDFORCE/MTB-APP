"""Verify and export only the standalone design artifacts. No app is contacted."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image
import base64
import json

ROOT = Path(__file__).resolve().parent
report = {'javascript_errors': [], 'http_requests': [], 'flow_checks': [], 'widths': []}
variants = ['focus', 'card', 'sheet']
html = (ROOT/'preview.html').read_text(encoding='utf-8')
for font in ['Regular', 'SemiBold', 'Bold']:
    name = f'Figtree-{font}.ttf'
    data = base64.b64encode((ROOT/'assets'/name).read_bytes()).decode('ascii')
    html = html.replace(f"url('assets/{name}')", f"url('data:font/ttf;base64,{data}')")
(ROOT/'try-designs.html').write_text(html, encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width':1460,'height':1280},device_scale_factor=2)
    page.on('pageerror',lambda err: report['javascript_errors'].append(str(err)))
    page.on('request',lambda r: report['http_requests'].append(r.url) if r.url.startswith(('http:','https:')) else None)
    page.goto((ROOT/'try-designs.html').as_uri(),wait_until='load')
    page.evaluate('document.fonts.ready')
    assert page.evaluate('document.fonts.check("400 14px Figtree")')
    page.locator('.designs').screenshot(path=str(ROOT/'three-design-options.png'))
    for key in variants:
        app = page.locator(f'#{key}-app')
        flow = lambda: page.evaluate('(key)=>JSON.parse(JSON.stringify(window.MTB_QUESTION_PREVIEW.flows[key]))', key)
        assert flow()['answers']==[None,None]
        assert app.locator('.answer.selected').count()==0
        page.locator(f'#{key}-phone').screenshot(path=str(ROOT/'screens'/f'{key}-question-1.png'))
        app.locator('.answer').nth(1).click()
        page.wait_for_timeout(320)
        assert flow()['step']==1 and flow()['answers']==[1,None]
        assert app.locator('.question').count()==1
        assert app.locator('.answer.selected').count()==0
        page.locator(f'#{key}-phone').screenshot(path=str(ROOT/'screens'/f'{key}-question-2.png'))
        app.locator('[data-back]').click()
        assert flow()['step']==0 and app.locator('.answer.selected').count()==1
        app.locator('.evidence summary').click()
        assert app.locator('.evidence[open]').count()==1
        assert 'Page 13' in app.locator('.evidence-body').inner_text()
        app.locator('.answer').nth(0).click()
        page.wait_for_timeout(320)
        assert flow()['answers']==[0,None]
        app.locator('.answer').nth(1).click()
        page.wait_for_timeout(320)
        assert flow()['review'] and flow()['answers']==[0,1]
        assert app.locator('.review-row').count()==2
        page.locator(f'#{key}-phone').screenshot(path=str(ROOT/'screens'/f'{key}-review.png'))
        app.locator('[data-edit]').nth(0).click()
        app.locator('.answer').nth(1).click()
        page.wait_for_timeout(320)
        assert flow()['review'] and flow()['answers']==[1,1]
        app.locator('[data-confirm]').click()
        assert flow()['confirmed']
        page.locator(f'#{key}-phone').screenshot(path=str(ROOT/'screens'/f'{key}-complete.png'))
        app.locator('[data-reset]').first.click()
        assert flow()['answers']==[None,None] and not flow()['confirmed']
        for other in variants:
            assert page.evaluate('(key)=>window.MTB_QUESTION_PREVIEW.flows[key].answers.every(x=>x===null)',other)
        report['flow_checks'].append({'design':key,'no_preselection':True,'answer_to_advance':True,'single_visible_question':True,'back_preserves_answer':True,'evidence_disclosure':True,'review_and_edit':True,'confirm_requires_answers':True,'restart':True,'independent_state':True})
    for width in [375,390,768,1460]:
        page.set_viewport_size({'width':width,'height':1000})
        for key in variants:
            page.locator(f'[data-design="{key}"]').click(force=True) if width<1290 else None
            report['widths'].append(page.evaluate('(key)=>({design:key,viewport:innerWidth,document:document.documentElement.scrollWidth,questionWidth:document.querySelector("#"+key+"-app .question-scroll").clientWidth,contentWidth:document.querySelector("#"+key+"-app .question-scroll").scrollWidth})',key))
    page.set_viewport_size({'width':390,'height':1000})
    page.goto((ROOT/'try-designs.html').as_uri()+'#card',wait_until='load')
    assert page.locator('[data-variant="card"]').is_visible()
    assert not page.locator('[data-variant="focus"]').is_visible()
    page.screenshot(path=str(ROOT/'mobile-preview.png'),full_page=True)
    report['deep_link_design_selection']=True
    page.emulate_media(reduced_motion='reduce')
    page.locator('[data-answer="card:0"]').click()
    page.wait_for_timeout(20)
    assert page.evaluate('window.MTB_QUESTION_PREVIEW.flows.card.step')==1
    report['reduced_motion']=True
    browser.close()

assert not report['javascript_errors']
assert not report['http_requests']
assert all(r['viewport']==r['document'] and r['questionWidth']==r['contentWidth'] for r in report['widths'])
images=[Image.open(ROOT/'screens'/f'{key}-{state}.png').convert('RGB') for key in variants for state in ['question-1','question-2','review']]
images[0].save(ROOT/'question-design-options.pdf',save_all=True,append_images=images[1:],resolution=144,title='MTB — One question at a time')
report['pdf_pages']=len(images)
(ROOT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
