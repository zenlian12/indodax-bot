import ccxt
import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
DRY_RUN = False
STATE_FILE = 'bot_state.json'
MIN_BTC_ORDER = 0.000001
REPORT_INTERVAL = 14  # Days between reports
LAST_REPORT_FILE = 'last_report.txt'

# Email settings
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# Initialize exchange
indodax = ccxt.indodax({
    'apiKey': os.getenv("INDODAX_API_KEY"),
    'secret': os.getenv("INDODAX_SECRET_KEY"),
    'enableRateLimit': True,
    'options': {'adjustForTimeDifference': True}
})

# --- UI Functions ---
def print_header():
    print("\n" + "=" * 40)
    print(f" Indodax Trading Bot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 40)

def print_balance(balance):
    print("[Account] Connected Successfully!")
    print(f"  IDR Balance: {balance['IDR']['total']:,.0f}")
    print(f"  BTC Balance: {balance['BTC']['total']:.6f}")
    print("-" * 40)

# --- Email Reporting ---
def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

def calculate_pnl(initial_idr, current_idr, current_btc, btc_price):
    current_value = current_idr + (current_btc * btc_price)
    pnl_idr = current_value - initial_idr
    pnl_percent = (pnl_idr / initial_idr) * 100
    return pnl_idr, pnl_percent

def check_report_due():
    try:
        with open(LAST_REPORT_FILE, 'r') as f:
            last_report = datetime.fromisoformat(f.read().strip())
    except FileNotFoundError:
        last_report = datetime.now() - timedelta(days=REPORT_INTERVAL + 1)
    
    if (datetime.now() - last_report).days >= REPORT_INTERVAL:
        return True
    return False

def generate_report(balance, btc_price):
    state = load_state()
    initial_idr = state.get('original_strategy_budget', balance['IDR']['total'] / 0.7 * 0.3)
    current_idr = balance['IDR']['total']
    current_btc = balance['BTC']['total']
    
    pnl_idr, pnl_percent = calculate_pnl(initial_idr, current_idr, current_btc, btc_price)
    
    report = f"""
    Biweekly Trading Report
    =======================
    - Initial IDR: {initial_idr:,.0f}
    - Current IDR: {current_idr:,.0f}
    - BTC Holdings: {current_btc:.6f} (Value: {current_btc * btc_price:,.0f} IDR)
    - P&L (IDR): {pnl_idr:+,.0f}
    - P&L (%): {pnl_percent:+.2f}%
    """
    return report

# --- State Management ---
def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            'original_strategy_budget': None,
            'remaining_budget': None,
            'last_purchase_price': None,
            'highest_price': None,
            'trailing_active': False
        }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    # Commit state to GitHub (required for GitHub Actions)
    os.system('git config --global user.email "actions@github.com"')
    os.system('git config --global user.name "GitHub Actions"')
    os.system('git add bot_state.json')
    os.system('git commit -m "Update bot state"')
    os.system('git push')

# --- Trading Logic ---
def execute_strategy():
    print_header()
    try:
        balance = indodax.fetch_balance()
        print_balance(balance)
        state = load_state()

        # Initialize strategy budget
        if state['original_strategy_budget'] is None:
            idr_total = balance['IDR']['total']
            state['original_strategy_budget'] = idr_total * 0.7
            state['remaining_budget'] = state['original_strategy_budget'] * 0.5
            state['last_purchase_price'] = None
            state['highest_price'] = None
            state['trailing_active'] = False
            save_state(state)
            print("[STATUS] Strategy initialized!")

        # Fetch price and execute logic
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = ticker['last']
        print(f"[PRICE] Current BTC/IDR: {current_price:,.0f}")

        # Buy/Sell logic (same as previous code)
        # ... [Insert your buy/sell logic here] ...

        # Send biweekly report
        if check_report_due():
            report = generate_report(balance, current_price)
            send_email("Indodax Biweekly Report", report)
            with open(LAST_REPORT_FILE, 'w') as f:
                f.write(datetime.now().isoformat())

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        
def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    # Commit and push using PAT for authentication
    os.system('git config --global user.email "actions@github.com"')
    os.system('git config --global user.name "GitHub Actions"')
    os.system(f'git add {STATE_FILE}')
    os.system('git commit -m "Update bot state"')
    os.system('git push https://${{ secrets.PAT }}@github.com/${{ github.repository }}.git HEAD:main')
    
if __name__ == "__main__":
    execute_strategy()
