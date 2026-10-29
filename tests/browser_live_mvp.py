"""Live browser acceptance against a disposable, seeded Django database.

Run through run_browser_checks.py. Never run against customer/business data.
"""
import argparse,json,os,re
from pathlib import Path
from playwright.sync_api import sync_playwright,expect

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',default='http://127.0.0.1:3000')
args=parser.parse_args();base=args.base_url.rstrip('/')
expect.set_options(timeout=30000)
with sync_playwright() as p:
 launch={'headless':True}
 if os.getenv('CHROMIUM_EXECUTABLE'):launch['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
 if os.getenv('CHROMIUM_ARGS_JSON'):launch['args']=[a for a in json.loads(Path(os.environ['CHROMIUM_ARGS_JSON']).read_text()) if a not in ('--disable-web-security','--allow-running-insecure-content')]
 browser=p.chromium.launch(**launch)
 errors=[]
 staff=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
 office=staff.new_page();office.on('pageerror',lambda e:errors.append(str(e)))
 office.goto(base+'/staff/')
 expect(office.get_by_role('heading',name='دخول الموظفين')).to_be_visible()
 print('Observed staff login headings:',office.get_by_role('heading').all_text_contents())
 office.get_by_label('اسم المستخدم',exact=True).fill(os.environ['LIVE_TEST_USERNAME'])
 office.get_by_label('كلمة المرور',exact=True).fill(os.environ['LIVE_TEST_PASSWORD'])
 office.get_by_role('button',name='دخول',exact=True).click()
 expect(office.get_by_role('button',name='أحياء التوصيل',exact=True)).to_be_visible()
 office.get_by_role('button',name='أحياء التوصيل',exact=True).click()
 area=office.locator('form[data-zone="homs-al-waer"]')
 expect(area).to_be_visible()
 area.get_by_label('أجور التوصيل (ل.س)',exact=True).fill('15000')
 area.get_by_label('الحي مفعل',exact=True).check()
 area.get_by_role('button',name='حفظ إعدادات الحي',exact=True).click()
 expect(office.get_by_role('status')).to_contain_text('حفظت إعدادات التوصيل')
 customer=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
 page=customer.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
 page.goto(base)
 expect(page.get_by_role('heading',name='تصفّح العناية')).to_be_visible()
 add=page.get_by_role('button',name='أضف سيروم تجريبي إلى السلة',exact=True)
 expect(add).to_be_visible()
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),'mobile horizontal overflow'
 print('Observed live customer catalog:',page.get_by_role('heading',level=3).all_text_contents())
 add.click();page.get_by_role('button',name=re.compile('السلة')).first.click()
 expect(page.get_by_role('dialog')).to_be_visible()
 page.get_by_label('الاسم الكامل',exact=True).fill('زبون تجربة آلية')
 page.get_by_label('رقم الهاتف',exact=True).fill('0991234567')
 page.get_by_label('حي التوصيل',exact=True).select_option('homs-al-waer')
 page.get_by_label('العنوان وأقرب نقطة علام',exact=True).fill('عنوان وهمي لاختبار المتجر')
 with page.expect_response(lambda r:'/checkout/submit/' in r.url and r.request.method=='POST') as placed:
  page.get_by_role('button',name='تأكيد الطلب والدفع عند الاستلام',exact=True).click()
 response=placed.value
 assert response.status==201,(response.status,response.text())
 receipt=response.json()
 expect(page.get_by_role('heading',name='وصل طلبك إلى المتجر')).to_be_visible()
 assert receipt['total_syp']==160000
 page.get_by_role('link',name='تتبّع الطلب',exact=True).click()
 expect(page.get_by_text(receipt['order_number'],exact=True)).to_be_visible()
 tracking_response=page.request.get(base+'/api/v1/orders/track/'+receipt['tracking_token']+'/')
 assert tracking_response.status==200
 public=tracking_response.json()
 assert 'customer_phone' not in public and 'unit_cost_syp' not in json.dumps(public)
 assert 'no-store' in tracking_response.headers.get('cache-control','')
 office.get_by_role('button',name='الطلبات',exact=True).click()
 row=office.locator('#ordersTable tr').filter(has_text=receipt['order_number'])
 row.get_by_role('button',name='تفاصيل',exact=True).click()
 office.get_by_role('button',name='تأكيد الطلب',exact=True).click()
 expect(office.locator('#dispatchOrder')).to_be_visible()
 office.locator('#dispatchOrder').get_by_label('اسم المندوب',exact=True).fill('مندوب اختبار')
 office.get_by_role('button',name='تسجيل التسليم للمندوب',exact=True).click()
 expect(office.get_by_label('المبلغ المحصل من العميل',exact=True)).to_be_visible()
 office.get_by_label('مرجع إثبات التحصيل',exact=True).fill('LIVE-TEST-COLLECTION')
 office.get_by_role('button',name='تأكيد التسليم والتحصيل',exact=True).click()
 expect(office.locator('#orderDetail')).to_contain_text('تم التسليم')
 page.get_by_role('button',name='تحديث الحالة',exact=True).click()
 expect(page.locator('.status-badge')).to_have_text('تم التسليم للعميل')
 csrf=next(c['value'] for c in staff.cookies() if c['name']=='csrftoken')
 detail=office.request.get(base+'/api/v1/staff/orders/').json()['orders'][0]
 orderid=detail['id'];line=detail['lines'][0];batchid=line['allocations'][0]['batch_id']
 office.get_by_role('button',name='التحصيل والتسويات',exact=True).click()
 form=office.locator('#remitForm')
 form.get_by_label('مرجع التسوية',exact=True).fill('LIVE-TEST-REMIT')
 form.get_by_label('اسم المندوب',exact=True).fill('مندوب اختبار')
 form.get_by_label('أرقام الطلبات الداخلية المفصولة بفاصلة',exact=True).fill(str(orderid))
 form.get_by_label('الاقتطاعات (ل.س)',exact=True).fill('5000')
 form.get_by_label('شرح الاقتطاع',exact=True).fill('اقتطاع وهمي لاختبار التسوية')
 form.get_by_role('button',name='تسجيل المبلغ المستلم',exact=True).click()
 expect(office.locator('#remittancesTable')).to_contain_text('LIVE-TEST-REMIT')
 summary=office.request.get(base+'/api/v1/staff/remittances/').json()['summary']
 assert summary['collected_held_by_courier_syp']==0 and summary['remitted_net_syp']==155000,summary
 office.get_by_role('button',name='المرتجعات',exact=True).click()
 office.get_by_text('استلام مرتجع',exact=True).click()
 form=office.locator('#returnForm')
 form.get_by_label('رقم سطر الطلب',exact=True).fill(str(line['id']))
 form.get_by_label('رقم الدفعة',exact=True).fill(str(batchid))
 form.get_by_label('الكمية المرتجعة',exact=True).fill('1')
 form.get_by_label('المبلغ المصرح بإعادته (ل.س)',exact=True).fill('145000')
 form.get_by_label('العبوة مغلقة والختم سليم',exact=True).check()
 form.get_by_label('ظروف التخزين سليمة',exact=True).check()
 form.get_by_role('button',name='استلام في الحجر',exact=True).click()
 expect(office.locator('#returnsTable')).to_contain_text('في الحجر')
 office.once('dialog',lambda d:d.accept())
 office.get_by_role('button',name='قبول إعادة البيع',exact=True).click()
 expect(office.locator('#returnsTable')).to_contain_text('أعيد للمخزون')
 office.get_by_label('طريقة إعادة المال',exact=True).last.fill('نقد تجريبي')
 office.get_by_role('button',name='سجل دفع المرتجع',exact=True).click()
 expect(office.locator('#returnsTable')).to_contain_text('مكتمل')
 summary=office.request.get(base+'/api/v1/staff/remittances/').json()['summary']
 assert summary['refunds_paid_syp']==145000 and summary['net_received_after_refunds_syp']==10000,summary
 assert not errors,errors
 browser.close()
 print('PASS: actual Django/Next mobile purchase → staff confirm/dispatch → delivery/collection → courier remittance → quarantine return → inspect/restock → refund; private live tracking; no browser page errors.')
