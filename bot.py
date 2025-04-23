import ccxt
import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
DRY_RUN = False  # Set to True for testing
TAKE_PROFIT = 0.06  # 6% take-profit
DCA_DROP = 0.10     # 10% drop for DCA buys
STATE_FILE = 'bot_state.json'
MIN_BTC_ORDER = 0.000001
REPORT_INTERVAL = 14
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
        'purchase_prices': [],
        'total_btc': 0.0,
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
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
        # Git operations for state persistence [[8]]
        repo_url = f"https://{os.environ['PAT']}@github.com/{os.environ['GITHUB_REPOSITORY']}.git"
        os.system('git config --global user.email "actions@github.com"')
        os.system('git config --global user.name "GitHub Actions"')
        os.system(f'git add {STATE_FILE}')
        os.system('git commit -m "Update bot state"')
        os.system(f'git push {repo_url} HEAD:main')
    except Exception as e:
        print(f"State save failed: {str(e)}")
        raise

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
    if not state['purchase_prices']:
        return "No active trades to report"
        
    avg_price = sum(state['purchase_prices']) / len(state['purchase_prices'])
    current_idr = balance.get('IDR', {}).get('total', 0) or 0
    current_btc = balance.get('BTC', {}).get('total', 0) or 0
    current_value = current_idr + (current_btc * btc_price)

    realized_pnl = state['realized_pnl'] or 0
    unrealized_pnl = (current_btc * btc_price) - (state['total_idr_spent'] - realized_pnl)

    return f"""
    📈 Biweekly Trading Report
    =========================
    - Strategy Budget: {state['original_strategy_budget']:,.0f} IDR
    - Current Value: {current_value:,.0f} IDR
    - Avg Purchase Price: {avg_price:,.0f} IDR
    - Realized P&L: {realized_pnl:+,.0f} IDR
    - Unrealized P&L: {unrealized_pnl:+,.0f} IDR
    - Total Trades: {state['total_trades']}
    - Next Buy Trigger: {state['purchase_prices'][-1]*0.9 if state['purchase_prices'] else 'N/A':,.0f} IDR
    """

def check_report_due():
    try:
        os.system('git pull origin main > /dev/null 2>&1')
        with open(LAST_REPORT_FILE, 'r') as f:
            last_report = datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        last_report = datetime.now() - timedelta(days=REPORT_INTERVAL + 1)
    return (datetime.now() - last_report).total_seconds() >= 1209600

# --- Core Trading Logic ---
def execute_strategy():
    print("\n" + "=" * 40)
    print(f" Indodax Trading Bot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 40)
    
    try:
        balance = indodax.fetch_balance()
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = ticker.get('last', 0)
        state = load_state()

        # Initialize strategy (first run or after sell)
        if not state['purchase_prices'] and state['remaining_budget'] is None:
            idr_balance = balance['IDR']['total']
            strategy_budget = idr_balance * 0.7
            state.update({
                'original_strategy_budget': strategy_budget,
                'remaining_budget': strategy_budget * 0.5,
                'total_idr_spent': 0.0,
                'total_btc': 0.0
            })
            save_state(state)  # Immediate state save

        # Initial Buy (first purchase in cycle)
        if not state['purchase_prices'] and state['remaining_budget'] > 0:
            buy_amount = min(state['remaining_budget'], balance['IDR']['free'])
            btc_amount = buy_amount / current_price
            
            if btc_amount >= MIN_BTC_ORDER:
                print(f"[INITIAL BUY] Buying {btc_amount:.6f} BTC @ {current_price:,.0f} IDR")
                if not DRY_RUN:
                    # FIX: Added price parameter for market buy [[1]][[8]]
                    order = indodax.create_order(
                        symbol='BTC/IDR',
                        type='market',
                        side='buy',
                        amount=None,
                        price=current_price,  # Required for cost calculation
                        params={'idr': buy_amount}
                    )
                    filled_price = order['average']
                    state['purchase_prices'].append(filled_price)
                else:
                    state['purchase_prices'].append(current_price)
                    
                state['total_btc'] += btc_amount
                state['total_idr_spent'] += buy_amount
                state['remaining_budget'] -= buy_amount
                state['total_trades'] += 1
                save_state(state)  # Ensure state saved

        # --- Take Profit Check (6% from average) ---
        if state['purchase_prices']:
            avg_price = sum(state['purchase_prices']) / len(state['purchase_prices'])
            if current_price >= avg_price * (1 + TAKE_PROFIT):
                print(f"[SELL] 6% profit reached (Avg: {avg_price:,.0f} IDR, Current: {current_price:,.0f} IDR)")
                if not DRY_RUN:
                    # Market sell without price parameter [[8]]
                    indodax.create_order(
                        symbol='BTC/IDR',
                        type='market',
                        side='sell',
                        amount=state['total_btc']
                    )
                profit = (current_price * state['total_btc']) - state['total_idr_spent']
                new_balance = indodax.fetch_balance()
                new_idr_balance = new_balance['IDR']['total']
                state.update({
                    'realized_pnl': state['realized_pnl'] + profit,
                    'total_idr_spent': 0.0,
                    'winning_trades': state['winning_trades'] + (1 if profit > 0 else 0),
                    'total_trades': state['total_trades'] + 1,
                    'trade_history': state['trade_history'] + [{
                        'date': datetime.now().isoformat(),
                        'type': 'sell',
                        'amount': state['total_btc'],
                        'price': current_price
                    }],
                    'purchase_prices': [],
                    'total_btc': 0.0,
                    'original_strategy_budget': new_idr_balance * 0.7,
                    'remaining_budget': new_idr_balance * 0.7 * 0.5
                })
                save_state(state)
                print(f"[RE-ENTRY] New budget: {state['remaining_budget']:,.0f} IDR")

        # --- DCA Buy Logic (10% drop) ---
        elif state['purchase_prices']:
            last_price = state['purchase_prices'][-1]
            if (last_price - current_price) / last_price >= DCA_DROP:
                buy_amount = min(state['remaining_budget'] * 0.5, balance['IDR']['free'])
                btc_amount = buy_amount / current_price
                
                if btc_amount >= MIN_BTC_ORDER:
                    print(f"[DCA BUY] Buying {btc_amount:.6f} BTC @ {current_price:,.0f} IDR")
                    if not DRY_RUN:
                        order = indodax.create_order(
                            symbol='BTC/IDR',
                            type='market',
                            side='buy',
                            amount=None,
                            price=current_price,  # Required for cost calculation [[8]]
                            params={'idr': buy_amount}
                        )
                        filled_price = order['average']
                        state['purchase_prices'].append(filled_price)
                    else:
                        state['purchase_prices'].append(current_price)
                        
                    state['total_btc'] += btc_amount
                    state['total_idr_spent'] += buy_amount
                    state['remaining_budget'] -= buy_amount
                    state['total_trades'] += 1
                    save_state(state)

        # Reporting
        if check_report_due():
            report = generate_report(balance, current_price, state)
            send_email("Indodax Biweekly Report", report)
            with open(LAST_REPORT_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
            save_state(state)

        print("Operation completed successfully")

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        raise

if __name__ == "__main__":
    execute_strategy()
