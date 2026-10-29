"""Deterministic browser tests of storefront checkout recovery and price review.

Uses intercepted API responses to exercise browser behavior; real backend acceptance
is a separate check. Install playwright and set CHROMIUM_EXECUTABLE if needed.
Usage: python tests/browser_mvp.py --base-url http://127.0.0.1:3001
"""
import argparse
import copy
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:3001')
parser.add_argument('--screenshots',default='')
args=parser.parse_args()
BASE=args.base_url.rstrip('/')
expect.set_options(timeout=15000)
products=[{'slug':'demo-serum','name_ar':'سيروم الترطيب التجريبي','name_en':'Demo hydrating serum','brand_name_en':'MEDEE','brand_name_ar':'ميدي','category_slug':'serums','category_name_ar':'سيرومات','description_ar':'منتج للعرض والاختبار','ingredients_text':'Demo ingredients','directions_ar':'بيانات تجريبية','warnings_ar':'للعرض فقط','concern_tags':['ترطيب'],'variants':[{'sku':'DEMO-30','pack_size_label_ar':'30 مل','price_syp':145000,'currency':'SYP','pao_months':12,'available_quantity':6,'image_url':''}]}]
out_of_stock=copy.deepcopy(products[0]);out_of_stock.update(slug='demo-out-of-stock',name_ar='منتج محلي غير متوفر');out_of_stock['variants'][0].update(sku='LOCAL-OOS',price_syp=210000,available_quantity=0,orderable=False);products.append(out_of_stock)
for i in range(1,19):
 products.append({'slug':f'source-{i:02}','name_ar':f'منتج مرجعي {i:02}','name_en':f'Source item {i:02}','brand_name_en':'SOURCE','brand_name_ar':'كتالوج المصدر','category_slug':'source-care','category_name_ar':'عناية من المصدر','description_ar':'سعر مرجعي بالدولار للتصفح فقط','ingredients_text':'','directions_ar':'','warnings_ar':'','concern_tags':[],'catalog_origin':'pharmacy-distribution','variants':[{'sku':'MEDEE-VIT-C' if i==1 else f'SOURCE-{i:02}','pack_size_label_ar':'','price_syp':None,'currency':'SYP','pao_months':None,'available_quantity':0,'image_url':'','orderable':False,'reference_price':{'amount':'29.40','currency':'USD'}}]})
zone={'code':'test-zone','city':'Homs','district_name_ar':'حي الاختبار','fee_syp':15000,'estimated_hours':24}
receipt={'order_number':'HOMS-TEST-001','tracking_token':'private-test-token','total_syp':160000,'hold_expires_at':'2026-10-30T08:00:00Z','status':'PENDING_CONFIRMATION','currency':'SYP'}
tracked={**receipt,'status_label_ar':'بانتظار التأكيد','district_snapshot_ar':zone['district_name_ar'],'subtotal_syp':145000,'delivery_fee_syp':15000,'created_at':'2026-10-29T20:00:00Z','lines':[{'sku':'DEMO-30','product_name_ar':products[0]['name_ar'],'pack_size_ar':'30 مل','unit_price_syp':145000,'quantity':1,'line_total_syp':145000}]}
with sync_playwright() as p:
 launch={'headless':True}
 if os.environ.get('CHROMIUM_EXECUTABLE'): launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
 if os.environ.get('CHROMIUM_ARGS_JSON'):
  launch['args']=[a for a in json.loads(Path(os.environ['CHROMIUM_ARGS_JSON']).read_text()) if a not in ('--disable-web-security','--allow-running-insecure-content')]
 browser=p.chromium.launch(**launch)
 context=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
 page=context.new_page()
 console_errors=[]
 page.on('pageerror',lambda e:console_errors.append(str(e)))
 state={'mode':'timeout','price':145000,'posts':[]}
 def route_api(route):
  path=route.request.url.split('/api/v1/')[1]
  if path=='catalog/products/':
   data=copy.deepcopy(products);data[0]['variants'][0]['price_syp']=state['price'];route.fulfill(json={'products':data,'demo_mode':True});return
  if path=='delivery-zones/':route.fulfill(json=[zone]);return
  if path.startswith('orders/track/'):
   route.fulfill(json=tracked);return
  if path=='checkout/submit/':
   state['posts'].append({'key':route.request.headers['idempotency-key'],'body':route.request.post_data_json})
   if state['mode']=='timeout' and len(state['posts'])==1:route.abort('failed');return
   if state['mode']=='price' and len(state['posts'])==1:
    state['price']=155000;route.fulfill(status=409,json={'detail':'تغير السعر','code':'price_changed','current_total_syp':170000});return
   result={**receipt,'total_syp':state['posts'][-1]['body']['expected_total_syp']};route.fulfill(status=201,json=result);return
  route.fulfill(status=404,json={'detail':'not found'})
 context.route('**/api/v1/**',route_api)
 def fill_checkout():
  page.get_by_label('الاسم الكامل',exact=True).fill('زبون الاختبار')
  page.get_by_label('رقم الهاتف',exact=True).fill('0991234567')
  page.get_by_label('حي التوصيل',exact=True).select_option('test-zone')
  page.get_by_label('العنوان وأقرب نقطة علام',exact=True).fill('عنوان اختبار تجريبي')
 def add_and_open():
  page.get_by_role('button',name='أضف سيروم الترطيب التجريبي إلى السلة',exact=True).click()
  page.get_by_role('button',name='السلة',exact=False).first.click()
  expect(page.get_by_role('dialog')).to_be_visible()
 page.goto(BASE)
 expect(page.get_by_role('heading',name='تصفّح العناية')).to_be_visible()
 expect(page.get_by_role('button',name='أضف سيروم الترطيب التجريبي إلى السلة',exact=True)).to_be_visible()
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile overflow'
 expect(page.locator('article.product-card')).to_have_count(20)
 local_out=page.locator('article.product-card').filter(has=page.get_by_role('heading',name='منتج محلي غير متوفر',exact=True))
 expect(local_out.locator('.price')).to_have_text(page.evaluate('new Intl.NumberFormat("ar-SY", {maximumFractionDigits:0}).format(210000)+" ل.س"'))
 expect(local_out.get_by_role('button',name='أضف منتج محلي غير متوفر إلى السلة',exact=True)).to_be_disabled()
 source=page.locator('article.product-card').filter(has=page.get_by_role('heading',name='منتج مرجعي 01',exact=True))
 expect(source.get_by_text('29.40 USD',exact=True)).to_be_visible()
 expect(source.get_by_text('سعر المصدر · مرجعي',exact=True)).to_be_visible()
 expect(source.get_by_text('للتصفح',exact=True)).to_be_visible()
 expect(source.get_by_role('button',name='أضف منتج مرجعي 01 إلى السلة',exact=True)).to_have_count(0)
 assert '0 ل.س' not in source.inner_text()
 source.get_by_role('button',name='عرض تفاصيل منتج مرجعي 01 من المصدر',exact=True).click()
 dialog=page.get_by_role('dialog')
 expect(dialog.get_by_text('29.40 USD',exact=True)).to_be_visible()
 expect(dialog.get_by_text('لم يورد المصدر قائمة المكوّنات.',exact=True)).to_be_visible()
 expect(dialog.get_by_role('button',name='أضف للسلة',exact=True)).to_have_count(0)
 page.get_by_role('button',name='إغلاق التفاصيل',exact=True).click()
 page.get_by_label('البحث عن منتج',exact=True).fill('MEDEE-VIT-C')
 expect(page.locator('article.product-card')).to_have_count(1)
 expect(page.get_by_role('heading',name='منتج مرجعي 01',exact=True)).to_be_visible()
 page.get_by_label('البحث عن منتج',exact=True).fill('Source item 01')
 expect(page.locator('article.product-card')).to_have_count(1)
 page.get_by_label('البحث عن منتج',exact=True).fill('')
 page.get_by_label('الماركة',exact=True).select_option('SOURCE')
 expect(page.locator('article.product-card')).to_have_count(18)
 page.get_by_label('الماركة',exact=True).select_option('')
 page.get_by_label('المتوفر فقط',exact=True).check()
 expect(page.locator('article.product-card')).to_have_count(1)
 page.get_by_label('المتوفر فقط',exact=True).uncheck()
 # A manipulated or old saved cart must not convert a reference price into a payable SYP amount.
 page.evaluate('localStorage.setItem("homs_skincare_cart_v2", JSON.stringify({"MEDEE-VIT-C":1}))')
 page.reload()
 expect(page.locator('article.product-card')).to_have_count(20)
 page.get_by_role('button',name='السلة',exact=False).first.click()
 fill_checkout()
 expect(page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True)).to_be_disabled()
 expect(page.get_by_text('غير متاح للطلب؛ أزله من السلة',exact=True)).to_be_visible()
 assert len(state['posts'])==0
 page.get_by_role('button',name='إزالة',exact=True).click()
 page.get_by_role('button',name='إغلاق السلة',exact=True).click()
 if args.screenshots:
  Path(args.screenshots).mkdir(parents=True,exist_ok=True)
  page.screenshot(path=str(Path(args.screenshots)/'storefront-mobile.png'),full_page=True)
 page.get_by_label('البحث عن منتج',exact=True).fill('غير موجود')
 expect(page.get_by_role('heading',name='لم نجد منتجات بهذه الخيارات')).to_be_visible()
 page.get_by_label('البحث عن منتج',exact=True).fill('')
 add_and_open();fill_checkout()
 page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True).click()
 expect(page.get_by_role('alert').filter(has_text='لم تصلنا نتيجة مؤكدة')).to_be_visible()
 expect(page.get_by_role('button',name='التحقق من الطلب السابق',exact=True)).to_be_enabled()
 first=state['posts'][0]
 page.reload()
 expect(page.get_by_role('dialog')).to_be_visible()
 page.get_by_role('button',name='التحقق من الطلب السابق',exact=True).click()
 expect(page.get_by_role('heading',name='وصل طلبك إلى المتجر')).to_be_visible()
 assert len(state['posts'])==2 and state['posts'][1]==first, 'retry must reuse exact key and body'
 page.get_by_role('link',name='تتبّع الطلب',exact=True).click()
 expect(page.get_by_role('heading',name='رحلة طلب العناية')).to_be_visible()
 expect(page.get_by_text('HOMS-TEST-001',exact=True)).to_be_visible()
 page.goto(BASE+'/orders')
 expect(page.get_by_role('link',name='تتبّع الطلب',exact=True)).to_be_visible()
 # A confirmed price conflict must refresh the displayed price and require another click.
 page.goto(BASE)
 state.update(mode='price',posts=[],price=145000)
 page.reload()
 add_and_open();fill_checkout()
 page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True).click()
 expect(page.get_by_role('alert').filter(has_text='تغيّر السعر')).to_be_visible()
 expect(page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True)).to_be_enabled()
 assert len(state['posts'])==1, 'price conflict cannot resubmit automatically'
 page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True).click()
 expect(page.get_by_role('heading',name='وصل طلبك إلى المتجر')).to_be_visible()
 assert state['posts'][0]['body']['expected_total_syp']==160000
 assert state['posts'][1]['body']['expected_total_syp']==170000
 assert state['posts'][0]['key']!=state['posts'][1]['key']
 # Offline cart remains locally saved and cannot submit.
 state.update(mode='normal',posts=[])
 page.goto(BASE)
 add_and_open();fill_checkout()
 context.set_offline(True)
 expect(page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True)).to_be_disabled()
 assert len(state['posts'])==0
 context.set_offline(False)
 expect(page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True)).to_be_enabled()
 assert page.evaluate('JSON.parse(localStorage.getItem("homs_skincare_cart_v2"))["DEMO-30"]')==1
 page.get_by_role('button',name='إزالة',exact=True).click()
 expect(page.get_by_role('heading',name='سلتك جاهزة لاختيارك الأول')).to_be_visible()
 page.get_by_role('button',name='إغلاق السلة',exact=True).click()
 page.set_viewport_size({'width':1440,'height':1000})
 if args.screenshots:page.screenshot(path=str(Path(args.screenshots)/'storefront-desktop.png'),full_page=True)
 assert not console_errors, console_errors
 browser.close()
 print('PASS: 18 reference products, SKU search, priced local out-of-stock product, USD decimals, browse-only details, source search/filter, stale-cart guard, mobile layout, search, persisted same-key retry after lost response/reload, tracking, recent orders, explicit price review, offline cart, removal; no page errors.')
