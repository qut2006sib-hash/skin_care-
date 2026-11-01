"""Actual three-account browser acceptance on the runner's disposable database.

Run after browser_live_mvp.py, which activates the synthetic delivery zone.
Credentials and test users exist only in disposable browser acceptance data.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import secrets

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:3000')
args = parser.parse_args()
BASE = args.base_url.rstrip('/')
expect.set_options(timeout=30000)
SUFFIX = secrets.token_hex(4)
PASSWORD = secrets.token_urlsafe(24)
STORE_USER = 'browser_store_' + SUFFIX
STORE_NAME = 'صيدلية اختبار الحسابات ' + SUFFIX
CUSTOMER_USER = 'browser_customer_' + SUFFIX
SKU = 'BROWSER-PHARMACY-' + SUFFIX.upper()
PRODUCT_NAME = 'منتج مبيع الصيدلية للاختبار'

with sync_playwright() as playwright:
    launch = {'headless': True}
    if os.getenv('CHROMIUM_EXECUTABLE'):
        launch['executable_path'] = os.environ['CHROMIUM_EXECUTABLE']
    if os.getenv('CHROMIUM_ARGS_JSON'):
        launch['args'] = [a for a in json.loads(Path(os.environ['CHROMIUM_ARGS_JSON']).read_text()) if a not in ('--disable-web-security', '--allow-running-insecure-content')]
    browser = playwright.chromium.launch(**launch)
    errors = []

    def context(width=390):
        ctx = browser.new_context(viewport={'width': width, 'height': 900}, service_workers='block')
        ctx.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
        return ctx

    def no_overflow(page):
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'horizontal page overflow: ' + page.url

    def register(page, kind, username, store_name=None):
        page.goto(BASE + '/sign-in?type=' + kind)
        expect(page.get_by_role('heading', name=re.compile('تسجيل الدخول'))).to_be_visible()
        page.get_by_role('button', name='ليس لديك حساب؟ أنشئ حسابًا', exact=True).click()
        page.get_by_label('اسم المستخدم', exact=True).fill(username)
        if store_name:
            page.get_by_label('اسم المتجر أو الصيدلية', exact=True).fill(store_name)
        page.get_by_label('كلمة المرور', exact=True).fill(PASSWORD)
        with page.expect_response(lambda r: '/auth/register/' in r.url and r.request.method == 'POST') as registration:
            page.get_by_role('button', name='إنشاء الحساب', exact=True).click()
        assert registration.value.status == 201, registration.value.text()
        expect(page).to_have_url(re.compile('/store$' if kind == 'store' else '/account$'))
        no_overflow(page)

    def login(page, kind, username, password):
        page.goto(BASE + '/sign-in?type=' + kind)
        expect(page.get_by_role('heading', name=re.compile('تسجيل الدخول'))).to_be_visible()
        page.get_by_label('اسم المستخدم', exact=True).fill(username)
        page.get_by_label('كلمة المرور', exact=True).fill(password)
        with page.expect_response(lambda r: '/auth/login/' in r.url and r.request.method == 'POST') as signed_in:
            page.get_by_role('button', name='دخول', exact=True).click()
        assert signed_in.value.status == 200, signed_in.value.text()

    def mutate(page, api_path, button):
        with page.expect_response(lambda r: api_path in r.url and r.request.method == 'POST') as result:
            button.click()
        assert result.value.status in (200, 201), result.value.text()
        expect(page.get_by_role('status').filter(has_text=re.compile('سُجّلت العملية بنجاح|سُجّل المبيع'))).to_be_visible()

    try:
        store_ctx = context()
        shop = store_ctx.new_page()
        shop.goto(BASE)
        expect(shop.get_by_role('heading', name='مساحتك في العناية', exact=True)).to_be_visible()
        for label in ('دخول الزبون', 'دخول المتجر', 'دخول المدير'):
            expect(shop.get_by_role('link', name=label, exact=True)).to_be_visible()
        no_overflow(shop)
        central_before = shop.request.get(BASE + '/api/v1/catalog/products/').json()
        register(shop, 'store', STORE_USER, STORE_NAME)
        expect(shop.get_by_role('heading', name='حسابك بانتظار الموافقة', exact=True)).to_be_visible()
        assert shop.request.get(BASE+'/api/v1/auth/session/').json()['account_type'] == 'store'
        assert shop.request.get(BASE+'/api/v1/staff/inventory/').status == 403

        manager_ctx = context(1440)
        manager = manager_ctx.new_page()
        manager.goto(BASE + '/sign-in?type=manager')
        expect(manager.get_by_role('heading', name='تسجيل الدخول المدير', exact=True)).to_be_visible()
        expect(manager.get_by_role('button', name='ليس لديك حساب؟ أنشئ حسابًا', exact=True)).to_have_count(0)
        login(manager, 'manager', os.environ['LIVE_TEST_USERNAME'], os.environ['LIVE_TEST_PASSWORD'])
        expect(manager.get_by_role('heading', name='إدارة العناية', exact=True)).to_be_visible()
        row = manager.get_by_role('row').filter(has_text=STORE_NAME)
        expect(row).to_contain_text('بانتظار موافقة المدير')
        with manager.expect_response(lambda r: '/status/' in r.url and r.request.method == 'POST') as approval:
            row.get_by_role('button', name='تفعيل', exact=True).click()
        assert approval.value.status == 200
        expect(row).to_contain_text('مفعّل')
        expect(manager.get_by_role('link', name=re.compile('المستودع والطلبات'))).to_have_attribute('href', '/staff/')

        shop.get_by_role('button', name='تسجيل الخروج', exact=True).click()
        expect(shop.get_by_role('heading', name='مساحتك في العناية', exact=True)).to_be_visible()
        login(shop, 'store', STORE_USER, PASSWORD)
        expect(shop.get_by_role('heading', name=STORE_NAME, exact=True)).to_be_visible()
        shop.get_by_role('button', name='المنتجات والمخزون', exact=True).click()
        shop.get_by_label('رمز المنتج SKU', exact=True).fill(SKU)
        shop.get_by_label('اسم المنتج', exact=True).fill(PRODUCT_NAME)
        shop.get_by_label('سعر البيع (ل.س)', exact=True).fill('1000')
        shop.get_by_label('حد تنبيه المخزون', exact=True).fill('2')
        mutate(shop, '/store/products/', shop.get_by_role('button', name='حفظ الصنف', exact=True))
        expect(shop.get_by_role('cell', name=SKU, exact=True).first).to_be_visible()
        no_overflow(shop)

        shop.get_by_role('button', name='استلام مشتريات', exact=True).first.click()
        shop.get_by_label('مرجع فاتورة المورد', exact=True).fill('BROWSER-INVOICE-' + SUFFIX)
        shop.get_by_label('الصنف', exact=True).select_option(SKU)
        shop.get_by_label('رقم الدفعة', exact=True).fill('BROWSER-LOT-' + SUFFIX)
        shop.get_by_label('تاريخ انتهاء الصلاحية', exact=True).fill((date.today()+timedelta(days=180)).isoformat())
        shop.get_by_label('الكمية المستلمة', exact=True).fill('5')
        shop.get_by_label('تكلفة شراء العبوة (ل.س)', exact=True).fill('600')
        mutate(shop, '/store/receipts/', shop.get_by_role('button', name='تسجيل الفاتورة وزيادة المخزون', exact=True))
        pharmacy = shop.request.get(BASE + '/api/v1/store/dashboard/').json()
        assert pharmacy['products'][0]['available_quantity'] == 5

        shop.get_by_role('button', name='بيع نقدي', exact=True).click()
        shop.get_by_label('اختر الصنف', exact=True).select_option(SKU)
        shop.get_by_label('كمية البيع', exact=True).fill('2')
        shop.get_by_role('button', name='إضافة للإيصال', exact=True).click()
        mutate(shop, '/store/sales/', shop.get_by_role('button', name='تأكيد التحصيل النقدي وإصدار الإيصال', exact=True))
        expect(shop.get_by_role('heading', name='الإيصالات والمرتجعات', exact=True)).to_be_visible()
        pharmacy = shop.request.get(BASE + '/api/v1/store/dashboard/').json()
        assert pharmacy['products'][0]['available_quantity'] == 3
        assert pharmacy['summary']['sales_today_syp'] == 2000
        assert pharmacy['summary']['cost_today_syp'] == 1200
        assert pharmacy['summary']['gross_profit_today_syp'] == 800
        expect(shop.get_by_role('cell', name=pharmacy['sales'][0]['receipt_number'], exact=True)).to_be_visible()
        no_overflow(shop)
        shop.evaluate('window.print = () => { window.__receiptPrintRequested = true; }')
        shop.get_by_role('button', name='طباعة الإيصال', exact=True).click()
        print_receipt = shop.get_by_label('إيصال الزبون', exact=True)
        expect(print_receipt).to_have_count(1)
        shop.emulate_media(media='print')
        expect(print_receipt).to_be_visible()
        expect(print_receipt).to_contain_text(pharmacy['sales'][0]['receipt_number'])
        expect(print_receipt).to_contain_text(PRODUCT_NAME)
        assert 'التكلفة' not in print_receipt.inner_text() and 'الربح' not in print_receipt.inner_text()
        shop.emulate_media(media='screen')

        shop.once('dialog', lambda dialog: dialog.accept('مرتجع آلي لاختبار الحسابات'))
        mutate(shop, '/return/', shop.get_by_role('button', name='تسجيل مرتجع كامل', exact=True))
        expect(shop.get_by_text('مرتجع كامل', exact=True)).to_be_visible()
        pharmacy = shop.request.get(BASE + '/api/v1/store/dashboard/').json()
        assert pharmacy['products'][0]['available_quantity'] == 3
        assert sum(batch['quantity'] for batch in pharmacy['batches'] if batch['condition']=='QUARANTINE') == 2
        assert pharmacy['summary']['sales_today_syp'] == pharmacy['summary']['cost_today_syp'] == pharmacy['summary']['gross_profit_today_syp'] == 0
        central_after = shop.request.get(BASE + '/api/v1/catalog/products/').json()
        assert central_before == central_after, 'counter operations changed central online availability'

        customer_ctx = context()
        customer = customer_ctx.new_page()
        register(customer, 'customer', CUSTOMER_USER)
        expect(customer.get_by_role('heading', name='طلباتك في العناية', exact=True)).to_be_visible()
        expect(customer.get_by_role('heading', name='ابدأ طلبك الأول', exact=True)).to_be_visible()
        assert customer.request.get(BASE+'/api/v1/manager/stores/').status == 403
        assert customer.request.get(BASE+'/api/v1/store/dashboard/').status == 403
        customer.get_by_role('link', name='تصفّح المنتجات', exact=True).click()
        expect(customer.get_by_role('heading', name='تصفّح العناية', exact=True)).to_be_visible()
        customer.get_by_role('button', name='أضف سيروم تجريبي إلى السلة', exact=True).click()
        customer.get_by_role('button', name=re.compile('السلة')).first.click()
        customer.get_by_label('الاسم الكامل', exact=True).fill('عميل اختبار الحسابات')
        customer.get_by_label('رقم الهاتف', exact=True).fill('0991234567')
        customer.get_by_label('حي التوصيل', exact=True).select_option('homs-al-waer')
        customer.get_by_label('العنوان وأقرب نقطة علام', exact=True).fill('عنوان وهمي لاختبار حساب الزبون')
        with customer.expect_response(lambda r: '/checkout/submit/' in r.url and r.request.method=='POST') as checkout:
            customer.get_by_role('button', name='تأكيد الطلب والدفع عند الاستلام', exact=True).click()
        assert checkout.value.status == 201, checkout.value.text()
        receipt = checkout.value.json()
        expect(customer.get_by_role('heading', name='وصل طلبك إلى المتجر', exact=True)).to_be_visible()
        history = customer.request.get(BASE+'/api/v1/customer/orders/').json()['orders']
        assert [order['order_number'] for order in history] == [receipt['order_number']]
        customer.goto(BASE+'/account')
        expect(customer.get_by_text(receipt['order_number'], exact=True)).to_be_visible()
        no_overflow(customer)
        customer.get_by_role('button', name='تسجيل الخروج', exact=True).click()
        expect(customer.get_by_role('heading', name='مساحتك في العناية', exact=True)).to_be_visible()
        login(customer, 'customer', CUSTOMER_USER, PASSWORD)
        expect(customer.get_by_text(receipt['order_number'], exact=True)).to_be_visible()

        stranger_ctx = context()
        stranger = stranger_ctx.new_page()
        register(stranger, 'customer', 'browser_other_customer_' + SUFFIX)
        expect(stranger.get_by_role('heading', name='ابدأ طلبك الأول', exact=True)).to_be_visible()
        expect(stranger.get_by_text(receipt['order_number'], exact=True)).to_have_count(0)
        assert stranger.request.get(BASE+'/api/v1/customer/orders/').json()['orders'] == []
        no_overflow(stranger)
        assert not errors, errors
        print('PASS: three account entry points; pending store registration; manager approval; pharmacy inventory receipt, cash sale, frozen cost/profit and quarantine full return; central stock unchanged; customer registration, authenticated checkout and cross-session private history; account isolation; responsive pages; no browser errors.')
    finally:
        browser.close()
