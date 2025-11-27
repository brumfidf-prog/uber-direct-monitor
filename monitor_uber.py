import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
import os
import json

# Configuration
UBER_BASE_URL = 'https://api.uber.com/v1'
UBER_TOKEN = os.getenv('UBER_TOKEN')  # Required: Bearer token with deliveries.read scope
EMAIL_FROM = os.getenv('EMAIL_FROM', 'brumfidf@gmail.com')
EMAIL_TO = 'david@jjtexas.com,hr@jjtexas.com'
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587

# 9 LLC Customer IDs (loop over these to fetch per LLC)
CUSTOMER_IDS = [
    'cb4a95b7-5c2a-4189-8db4-2155ae98d6ed',  # Grand Prairie (or shared)
    '2c4c9978-85a7-4f84-9335-070674f36c0b',  # Benbrook
    'dd5f7384-5549-4255-800e-dd9614e43f3b',  # Abilene
    '498ec771-b4bb-4422-8adc-f6a69e4eae9a',  # Azle
    'bd152c15-b838-4353-8e28-3868dc34a672',  # Granbury
    '0e3909c9-93a0-4984-bdfd-3610796933cc',  # Harlingen
    'bc8d582d-5951-4a8c-a831-8cf3251a9e22',  # San Angelo
    '363d3be2-7139-42fd-b4ea-7bb5a6d6b031',  # Weatherford
    '0be4dcab-b3c4-4c1d-a490-c965fa163eab',  # Stephenville (or shared)
]

# Map external_store_id (per-store UUID) to store names (add all 10 if known)
STORE_NAMES = {
    'cb4a95b7-5c2a-4189-8db4-2155ae98d6ed': 'Grand Prairie',  # Example; replace with actual external_store_id if different
    # Add the other 9 external_store_ids here, e.g.:
    # 'store-uuid-for-benbrook': 'Benbrook',
    # ... (for shared LLC, two entries under one customer_id)
    # If external_store_id = customer_id for single-store LLCs, it works as-is
}

def get_store_name(store_id):
    return STORE_NAMES.get(store_id, store_id)

def send_email(subject, body):
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print(f"[ALERT - NO EMAIL CONFIG] {subject}: {body}")
        return
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(','), msg.as_string())
        server.quit()
        print(f"Email sent: {subject}")
    except Exception as e:
        print(f"Email failed: {e}")

def fetch_all_deliveries(token):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_dt = yesterday.isoformat() + 'T00:00:00Z'
    end_dt = yesterday.isoformat() + 'T23:59:59Z'
    headers = {'Authorization': f'Bearer {token}'}
    all_deliveries = []
    total_fetched = 0
    
    for customer_id in CUSTOMER_IDS:
        url = f'{UBER_BASE_URL}/customers/{customer_id}/deliveries'
        params = {
            'start_dt': start_dt,
            'end_dt': end_dt,
            'limit': 100
        }
        while url:
            response = requests.get(url, headers=headers, params=params if url == f'{UBER_BASE_URL}/customers/{customer_id}/deliveries' else None)
            if response.status_code != 200:
                print(f"API error for LLC {customer_id[:8]}...: {response.status_code} - {response.text[:200]}...")
                break
            data = response.json()
            deliveries = data.get('data', [])
            all_deliveries.extend(deliveries)
            total_fetched += len(deliveries)
            print(f"Fetched {len(deliveries)} deliveries for LLC {customer_id[:8]}... on {yesterday}")
            url = data.get('next_href')  # Spec pagination
            params = None
    print(f"Total fetched: {total_fetched} deliveries across 9 LLCs for {yesterday}")
    return all_deliveries

def analyze_overuse(deliveries):
    store_counts = {}
    for deliv in deliveries:
        store_id = deliv.get('external_store_id')
        if store_id:
            store_counts[store_id] = store_counts.get(store_id, 0) + 1
    overusing_stores = [(store_id, count) for store_id, count in store_counts.items() if count >= 3]
    return overusing_stores

def check_early_cancellations(token, deliveries):
    headers = {'Authorization': f'Bearer {token}'}
    early_cancels = {}
    for deliv in deliveries:
        if deliv.get('status') == 'canceled':
            delivery_id = deliv.get('id')
            # Fetch details (assume customer_id from deliv if available, or loop; for simplicity, use first CUSTOMER_IDS[0]—adjust if needed)
            customer_id = CUSTOMER_IDS[0]  # Fallback; optimize by storing per-LLC if multi
            detail_url = f'{UBER_BASE_URL}/customers/{customer_id}/deliveries/{delivery_id}'
            try:
                detail_resp = requests.get(detail_url, headers=headers)
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    cancel_details = detail.get('cancellation_details', {})
                    last_status = cancel_details.get('last_known_delivery_status', '').upper()
                    if last_status in ['SCHEDULED', 'EN_ROUTE_TO_PICKUP']:
                        store_id = deliv.get('external_store_id')
                        if store_id:
                            early_cancels[store_id] = early_cancels.get(store_id, 0) + 1
            except Exception as e:
                print(f"Detail error for {delivery_id}: {e}")
    return [(store_id, count) for store_id, count in early_cancels.items() if count > 0]

if __name__ == '__main__':
    if not UBER_TOKEN:
        print("Error: Set UBER_TOKEN env var")
        exit(1)
    print(f"Starting Uber Direct monitor for {datetime.now(timezone.utc).date()}")
    deliveries = fetch_all_deliveries(UBER_TOKEN)
    if not deliveries:
        print("No data fetched - check token or customer_ids.")
        exit(0)
    overusing = analyze_overuse(deliveries)
    early_cancels = check_early_cancellations(UBER_TOKEN, deliveries)
    if overusing:
        store_list = [f"{get_store_name(sid)} ({count}x)" for sid, count in overusing]
        body = f"Stores overusing Uber Direct (≥3 deliveries) yesterday:\n\n" + "\n".join(store_list) + "\n\nAction needed: Review and limit if necessary."
        send_email("🚨 Uber Direct Overuse Alert", body)
    if early_cancels:
        store_list = [f"{get_store_name(sid)} ({count}x)" for sid, count in early_cancels]
        body = f"Stores with early cancellations (before driver arrival) yesterday:\n\n" + "\n".join(store_list) + "\n\nAction needed: Train staff on proper usage."
        send_email("🚨 Uber Direct Early Cancellation Alert", body)
    if not overusing and not early_cancels:
        print("Daily check complete: No incidents.")
    else:
        print("Alerts sent for incidents.")
