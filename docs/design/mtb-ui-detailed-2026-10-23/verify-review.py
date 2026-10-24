from pathlib import Path
from playwright.sync_api import sync_playwright
import hashlib
import json

ROOT=Path(__file__).resolve().parent
audit=json.loads((ROOT/'source-inventory.json').read_text(encoding='utf-8'))
data=json.loads((ROOT/'screen-manifest.json').read_text(encoding='utf-8'))
report={'javascript_errors':[],'missing_routes':[],'render_checks':[],'source_drift':[],'navigation':{}}
for route in audit['routes']:
    if not any(s['route']==route['id'] for s in data['screens']):report['missing_routes'].append(route['id'])
for item in audit['snapshots']:
    file=ROOT.parents[2]/item['file']
    if hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:report['source_drift'].append(item['file'])
with sync_playwright() as p:
    browser=p.chromium.launch(channel='chrome',headless=True)
    page=browser.new_page(viewport={'width':1600,'height':1260},device_scale_factor=1.5)
    page.on('pageerror',lambda e:report['javascript_errors'].append(str(e)))
    page.goto((ROOT/'preview.html').as_uri(),wait_until='load')
    page.evaluate('document.fonts.ready')
    report['render_checks']=page.evaluate('''()=>MTB_DESIGN_TEST.screens.map(s=>{MTB_DESIGN_TEST.go(s.key);const m=document.querySelector('.product-scroll');return {key:s.key,hasContent:!!m&&m.innerText.length>5,horizontalOverflow:m.scrollWidth>m.clientWidth};})''')
    page.evaluate('MTB_DESIGN_TEST.go("sponsor-home")')
    report['navigation']['sponsor']=page.locator('.bottom-nav button').all_inner_texts()
    assert report['navigation']['sponsor']==['Home','Trials','Messages','Audits','Me']
    page.screenshot(path=str(ROOT/'review-workspace.png'),full_page=True)
    for key in ['sponsor-home','pi-home','crc-home','kit-patient','medications','chat','versions','about-study','admin-index','add-trial','add-patient','calendar-settings']:
        page.evaluate('(key)=>MTB_DESIGN_TEST.go(key)',key)
        page.locator('#device').screenshot(path=str(ROOT/'screens'/f'{key}.png'))
    page.evaluate('MTB_DESIGN_TEST.go("crc-home")')
    report['navigation']['crc']=page.locator('.bottom-nav button').all_inner_texts()
    assert report['navigation']['crc']==['Home','Patients','Actions','Messages','Calendar','Me']
    page.evaluate('MTB_DESIGN_TEST.go("pi-home")')
    report['navigation']['pi']=page.locator('.bottom-nav button').all_inner_texts()
    assert report['navigation']['pi']==['Home','My Trials','Messages','Calendar','Me']
    page.evaluate('MTB_DESIGN_TEST.go("kit-patient");MTB_DESIGN_TEST.setRole("sponsor")')
    assert page.locator('#product [data-action="Event"]').count()==0
    assert page.locator('#product [data-action="Set anchor date"]').count()==0
    report['kit_read_only_preview']=True
    page.evaluate('MTB_DESIGN_TEST.go("sponsor-home");MTB_DESIGN_TEST.activate("Submit Schedule")')
    assert page.evaluate('location.hash')=='#trials'
    report['submit_schedule_still_selects_trial']=True
    for state in ['loading','empty','error','permission','missing-model','ready']:
        page.evaluate('(s)=>MTB_DESIGN_TEST.setState(s)',state)
        assert page.locator('.product-scroll').inner_text()
    report['state_variants']=6
    page.locator('[data-mode="inventory"]').click()
    page.locator('#inventory-search').fill('clinical/kit-patient')
    assert page.locator('.inventory-table tbody tr').count()>0
    page.locator('.inventory-table [data-source]').first.click()
    assert page.locator('#review-dialog').is_visible()
    page.locator('[data-close-dialog]').click()
    page.locator('[data-mode="decisions"]').click()
    page.screenshot(path=str(ROOT/'button-decisions-preview.png'),full_page=True)
    report['source_inspection_and_decisions']=True
    browser.close()
assert not report['javascript_errors']
assert not report['missing_routes']
assert all(x['hasContent'] for x in report['render_checks'])
(ROOT/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'routes_covered':len(audit['routes']),'states_rendered':len(report['render_checks']),'horizontal_overflow':[x['key'] for x in report['render_checks'] if x['horizontalOverflow']],'source_drift':report['source_drift'],'javascript_errors':report['javascript_errors'],'navigation':report['navigation']},indent=2))
