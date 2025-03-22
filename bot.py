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

# --- State Management ---
def load_state():
    default_state = {
        'original_strategy_budget': None,
        'remaining_budget': None,
        'last_purchase_price': None,
        'highest_price': None,
        'trailing_active': False,
        'total_trades': 0,
        'winning_trades': 0,
        'max_drawdown': 0.0,
        'trade_history': [],
        'total_idr_spent': 0.0,
        'realized_pnl': 0.0,
        'equity_peak': None
    }
    try:
        with open(STATE_FILE, 'r') as f:
            saved_state = json.load(f)
            return {**default_state, **saved_state}
    except (FileNotFoundError, json.JSONDecodeError):
        return default_state

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    repo_url = f"https://{os.environ['PAT']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"
    os.system('git config --global user.email "actions@github.com"')
    os.system('git config --global user.name "GitHub Actions"')
    os.system(f'git add {STATE_FILE}')
    os.system('git commit -m "Update bot state"')
    os.system(f'git push {repo_url} HEAD:main')

# --- Email Reporting ---
def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

def generate_report(balance, btc_price, state):
    initial_idr = state['original_strategy_budget'] or 0
    current_idr = balance['IDR']['total'] or 0
    current_btc = balance['BTC']['total'] or 0
    current_value = current_idr + (current_btc * btc_price)

    # Realized P&L
    realized_pnl = state['realized_pnl'] or 0
    realized_pnl_percent = (realized_pnl / initial_idr * 100) if initial_idr else 0

    # Unrealized P&L
    unrealized_pnl = (current_btc * btc_price) - (state['total_idr_spent'] - realized_pnl)
    unrealized_pnl_percent = (unrealized_pnl / initial_idr * 100) if initial_idr else 0

    # Win Rate
    total_trades = state['total_trades'] or 0
    win_rate = (state['winning_trades'] / total_trades * 100) if total_trades > 0 else 0

    # Trade History
    recent_trades = "\n".join(
        [f"- {trade['date'][:10]}: {trade['type'].upper()} {trade['amount']:.5f} BTC @ {trade['price']:,.0f} IDR"
         for trade in state['trade_history'][-3:]]
    ) if state['trade_history'] else "No recent trades"

    return f"""
    📈 Biweekly Trading Report
    =========================
    - Initial Balance: {initial_idr:,.0f} IDR
    - Current Value: {current_value:,.0f} IDR
    - Realized P&L: {realized_pnl:+,.0f} IDR ({realized_pnl_percent:+.1f}%)
    - Unrealized P&L: {unrealized_pnl:+,.0f} IDR ({unrealized_pnl_percent:+.1f}%)

    🔍 Performance Metrics
    ---------------------
    - Total Trades: {total_trades}
    - Win Rate: {win_rate:.1f}%
    - Max Drawdown: {state['max_drawdown']:.1f}%

    💹 Market Overview
    -----------------
    - Current BTC Price: {btc_price:,.0f} IDR
    - Next Buy Trigger: {state['last_purchase_price'] * 0.9:,.0f} IDR (if applicable)

    📆 Recent Activity
    -----------------
    {recent_trades}
    """

def check_report_due():
    try:
        with open(LAST_REPORT_FILE, 'r') as f:
            last_report = datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        last_report = datetime.now() - timedelta(days=REPORT_INTERVAL + 1)
    return (datetime.now() - last_report).days >= REPORT_INTERVAL

# --- Core Trading Logic ---
def execute_strategy():
    print("\n" + "=" * 40)
    print(f" Indodax Trading Bot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 40)
    
    try:
        # Fetch market data
        balance = indodax.fetch_balance()
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = ticker['last']
        
        state = load_state()

        # Initialize strategy
        if state['original_strategy_budget'] is None:
            state['original_strategy_budget'] = balance['IDR']['total'] * 0.7
            state['remaining_budget'] = state['original_strategy_budget'] * 0.5
            state['equity_peak'] = state['original_strategy_budget']
            save_state(state)
            print("[STATUS] Strategy initialized!")

        # Update equity tracking
        current_equity = balance['IDR']['total'] + (balance['BTC']['total'] * current_price)
        state['equity_peak'] = max(state['equity_peak'] or 0, current_equity)
        drawdown = ((state['equity_peak'] - current_equity) / state['equity_peak']) * 100
        state['max_drawdown'] = max(state['max_drawdown'], drawdown)

        # --- Buy/Sell Logic ---
        # [Your existing trading strategy implementation here]

        # Send biweekly report
        if check_report_due():
            report = generate_report(balance, current_price, state)
            send_email("Indodax Biweekly Trading Report", report)
            with open(LAST_REPORT_FILE, 'w') as f:
                f.write(datetime.now().isoformat())

        save_state(state)
        print("Operation completed successfully")

    except Exception as e:
        print(f"[CRITICAL ERROR] {str(e)}")
        raise

if __name__ == "__main__":
    execute_strategy()
