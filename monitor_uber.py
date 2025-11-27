import requests
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
import os
import json

# Configuration
UBER_BASE_URL = 'https://api.uber.com/v1'
UBER_TOKEN = os.getenv('UBER_TOKEN')  # Required: Your Bearer token
EMAIL_FROM = os.getenv('EMAIL_FROM', 'brumfidf@gmail.com')  # Default to your email
EMAIL_TO = 'david@jjtexas.com,hr@jjtexas.com'  # Fixed recipients
EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')  # Required: App password
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587

# Map external_store_id (UUID) to store names for readable alerts
STORE_NAMES = {
    'cb4a95b7-5c2a-4189-8db4-2155ae98d6ed': 'Grand Prairie',
    '2c4c9978-85a7-4f84-9335-070674f36c0b': 'Benbrook',
    'dd5f7384-5549-4255-800e-dd9614e43f3b': 'Abilene',
    '498ec771-b4bb-4422-8adc-f6a69e4eae9a': 'Azle',
    'bd152c15-b838-4353-8e28-3868dc34a672': 'Granbury',
    '0e3909c9-93a0-4984-bdfd-3610796933cc': 'Harlingen',
    'bc8d582d-5951-4a8c-a831-8cf3251a9e22': 'San Angelo',
    '363d3be2-7139-42fd-b4ea-7bb5a6d6b031': 'Weatherford',
    '0be4dcab-b3c4-4c1d-a490-c965fa163eab': 'Stephenville',
    # Add 10th if needed: 'uuid-here': 'Store Name',
}
STORE_IDS = list(STORE_NAMES.keys())  # List of your 9 UUIDs for looping

def get_store_name(store_id):
    return STORE_NAMES.get(store_id, store_id)  # Fallback to ID if unnamed

def send_email(subject, body):
    """Send alert email via SMTP to multiple recipients."""
    if not EMAIL_FROM or not EMAIL_PASSWORD:
        print(f"[ALERT - NO EMAIL CONFIG] {subject}: {body}")
        return
    
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_FROM
    msg['To'] = EMAIL_TO  # Handles comma-separated
    
    try:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_FROM, EMAIL_PASSWORD)
        server.sendmail(EMAIL_FROM, EMAIL_TO.split(','), msg.as_string())
        server.quit()
        print(f"Email sent successfully: {subject}")
    except Exception as e:
        print(f"Email failed: {e}")

def fetch_all_deliveries(token):
    """Fetch yesterday's deliveries per store using per-store API calls."""
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    start_dt = yesterday.isoformat() + 'T00:00:00Z'
    end_dt = yesterday.isoformat() + 'T23:59:59Z'
    
    headers = {'Authorization': f'Bearer {token}'}
    all_deliveries = []
    total_fetched = 0
    
    for store_id in STORE_IDS:
        params = {
            'start_dt': start_dt,
            'end_dt': end_dt
        }
        # Path matching your example: /v1/customers/{store_id}/deliveries (no /eats/)
        url = f'{UBER_BASE_URL}/customers/{store_id}/deliveries'
        try:
            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                print(f"API error for {get_store_name(store_id)}: {response.status_code} - {response.text[:200]}...")  # Truncate for logs
                continue
            data = response.json()
            deliveries = data.get('deliveries', [])  # Assuming 'deliveries' array in response
            # Tag each delivery with store_id for analysis
            tagged_deliveries = [{'store_id': store_id, **deliv} for deliv in deliveries]
            all_deliveries.extend(tagged_deliveries)
            total_fetched += len(deliveries)
            print(f"Fetched {len(deliveries)} deliveries for {get_store_name(store_id)} on {yesterday}")
        except Exception as e:
            print(f"Error fetching for {get_store_name(store_id)}: {e}")
    
    print(f"Total fetched: {total_fetched} across {len(STORE_IDS)} stores for {yesterday}")
    return all_deliveries

def analyze_overuse(deliveries):
    """Count deliveries per store and flag overuse."""
    store_counts = {}
    for deliv in deliveries:
        store_id = deliv.get('store_id')
        if store_id:
            store_counts[store_id] = store_counts.get(store_id, 0) + 1
    overusing_stores = [(store_id, count) for store_id, count in store_counts.items() if count >= 3]
    return overusing_stores

def check_early_cancellations(token, deliveries):
    """Check for early cancellations (before driver arrival at pickup)."""
    headers = {'Authorization': f'Bearer {token}'}
    early_cancels = {}
    
    for deliv in deliveries:
        order_id = deliv.get('order_id') or deliv.get('delivery_id')
        if not order_id:
            continue
        status = deliv.get('status', '').upper()
        if 'CANCEL' in status or status == 'FAILED':
            # Detail endpoint scoped to store: /v1/customers/{store_id}/deliveries/{order_id}
            store_id = deliv.get('store_id')
            detail_url = f'{UBER_BASE_URL}/customers/{store_id}/deliveries/{order_id}'
            try:
                detail_resp = requests.get(detail_url, headers=headers)
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    cancel_details = detail.get('cancellation_details', {})
                    last_status = cancel_details.get('last_known_delivery_status', '').upper()
                    if last_status in ['SCHEDULED', 'EN_ROUTE_TO_PICKUP']:
                        early_cancels[store_id] = early_cancels.get(store_id, 0) + 1
                else:
                    print(f"Detail fetch error for {order_id} in {get_store_name(store_id)}: {detail_resp.status_code} - {detail_resp.text[:100]}...")
            except Exception as e:
                print(f"Error fetching details for {order_id} in {get_store_name(store_id)}: {e}")
    
    return [(store_id, count) for store_id, count in early_cancels.items() if count > 0]

if __name__ == '__main__':
    if not UBER_TOKEN:
        print("Error: Set UBER_TOKEN env var")
        exit(1)
    
    print(f"Starting Uber Direct monitor for {datetime.now(timezone.utc).date()}")
    deliveries = fetch_all_deliveries(UBER_TOKEN)
    
    if not deliveries:
        print("No data fetched - check API/token or endpoints.")
        exit(0)
    
    # Analyze (handles all 9 stores automatically)
    overusing = analyze_overuse(deliveries)
    early_cancels = check_early_cancellations(UBER_TOKEN, deliveries)
    
    # Overuse Alert
    if overusing:
        store_list = [f"{get_store_name(sid)} ({count}x)" for sid, count in overusing]
        body = f"Stores overusing Uber Direct (≥3 deliveries) yesterday:\n\n" + "\n".join(store_list) + "\n\nAction needed: Review and limit if necessary."
        send_email("🚨 Uber Direct Overuse Alert", body)
    
    # Early Cancellation Alert
    if early_cancels:
        store_list = [f"{get_store_name(sid)} ({count}x)" for sid, count in early_cancels]
        body = f"Stores with early cancellations (before driver arrival) yesterday:\n\n" + "\n".join(store_list) + "\n\nAction needed: Train staff on proper usage."
        send_email("🚨 Uber Direct Early Cancellation Alert", body)
    
    if not overusing and not early_cancels:
        print("Daily check complete: No incidents.")
    else:
        print("Alerts sent for incidents.")
