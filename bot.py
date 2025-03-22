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
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
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
            'equity_peak': 0.0  # Track for drawdown calculation
        }

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)
    # Push state to GitHub
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
    initial_idr = state['original_strategy_budget']
    current_idr = balance['IDR']['total']
    current_btc = balance['BTC']['total']
    current_value = current_idr + (current_btc * btc_price)
    
    # Realized P&L
    realized_pnl = state['realized_pnl']
    realized_pnl_percent = (realized_pnl / initial_idr) * 100 if initial_idr != 0 else 0
    
    # Unrealized P&L
    unrealized_pnl = (current_btc * btc_price) - (state['total_idr_spent'] - state['realized_pnl'])
    unrealized_pnl_percent = (unrealized_pnl / initial_idr) * 100 if initial_idr != 0 else 0
    
    # Win Rate
    win_rate = (state['winning_trades'] / state['total_trades']) * 100 if state['total_trades'] > 0 else 0
    
    # Trade History (last 5 trades)
    recent_trades = "\n".join(
        [f"- {trade['date']}: {trade['type'].upper()} {trade['amount']:.5f} BTC @ {trade['price']:,.0f} IDR" 
         for trade in state['trade_history'][-5:]]
    )

    report = f"""
    📈 Biweekly Trading Report
    =========================
    - Initial Balance: {initial_idr:,.0f} IDR
    - Current Balance: {current_value:,.0f} IDR
    - Realized P&L: {realized_pnl:+,.0f} IDR ({realized_pnl_percent:+.2f}%)
    - Unrealized P&L: {unrealized_pnl:+,.0f} IDR ({unrealized_pnl_percent:+.2f}%)

    🔍 Key Metrics
    -------------
    - Total Trades: {state['total_trades']}
    - Win Rate: {win_rate:.1f}%
    - Max Drawdown: {state['max_drawdown']:.1f}%

    📉 Market Snapshot
    -----------------
    - BTC Price: {btc_price:,.0f} IDR
    - Next Buy Trigger: {state['last_purchase_price'] * 0.9:,.0f} IDR (-10%)

    📅 Recent Trades
    ----------------
    {recent_trades if recent_trades else "No trades this period."}
    """
    return report

# --- Trading Logic ---
def execute_strategy():
    print("\n" + "=" * 40)
    print(f" Indodax Trading Bot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 40)
    
    try:
        balance = indodax.fetch_balance()
        state = load_state()
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = ticker['last']

        # Initialize strategy budget
        if state['original_strategy_budget'] is None:
            state['original_strategy_budget'] = balance['IDR']['total'] * 0.7
            state['remaining_budget'] = state['original_strategy_budget'] * 0.5
            state['equity_peak'] = state['original_strategy_budget']
            save_state(state)
            print("[STATUS] Strategy initialized!")

        # --- Buy/Sell Logic (simplified) ---
        # ... (your existing buy/sell logic here)
        # After each trade, update state:
        # state['total_trades'] += 1
        # state['trade_history'].append({'date': datetime.now().isoformat(), 'type': 'buy/sell', 'amount': ..., 'price': ...})
        
        # Calculate drawdown
        current_equity = balance['IDR']['total'] + (balance['BTC']['total'] * current_price)
        drawdown = ((state['equity_peak'] - current_equity) / state['equity_peak']) * 100
        state['max_drawdown'] = max(state['max_drawdown'], drawdown)
        state['equity_peak'] = max(state['equity_peak'], current_equity)

        # Send biweekly report
        if check_report_due():
            report = generate_report(balance, current_price, state)
            send_email("Indodax Biweekly Report", report)
            with open(LAST_REPORT_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
        
        save_state(state)

    except Exception as e:
        print(f"[ERROR] {str(e)}")

def check_report_due():
    try:
        with open(LAST_REPORT_FILE, 'r') as f:
            last_report = datetime.fromisoformat(f.read().strip())
    except FileNotFoundError:
        last_report = datetime.now() - timedelta(days=REPORT_INTERVAL + 1)
    return (datetime.now() - last_report).days >= REPORT_INTERVAL

if __name__ == "__main__":
    execute_strategy()
