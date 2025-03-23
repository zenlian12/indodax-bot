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
    # Handle None values gracefully
    initial_idr = state['original_strategy_budget'] or 0
    current_idr = balance.get('IDR', {}).get('total', 0) or 0
    current_btc = balance.get('BTC', {}).get('total', 0) or 0
    current_value = current_idr + (current_btc * btc_price)

    # Realized P&L
    realized_pnl = state['realized_pnl'] or 0
    realized_pnl_percent = (realized_pnl / initial_idr * 100) if initial_idr else 0

    # Unrealized P&L
    total_spent = state['total_idr_spent'] or 0
    unrealized_pnl = (current_btc * btc_price) - (total_spent - realized_pnl)
    unrealized_pnl_percent = (unrealized_pnl / initial_idr * 100) if initial_idr else 0

    # Win Rate
    total_trades = state['total_trades'] or 0
    win_rate = (state['winning_trades'] / total_trades * 100) if total_trades > 0 else 0

    # Trade History
    recent_trades = "\n".join(
        [f"- {trade.get('date', '')[:10]}: {trade.get('type', '').upper()} "
         f"{trade.get('amount', 0):.5f} BTC @ {trade.get('price', 0):,.0f} IDR"
         for trade in state['trade_history'][-3:]]
    ) if state['trade_history'] else "No recent trades"

    # Next Buy Trigger
    last_price = state['last_purchase_price'] or 0
    next_buy_trigger = last_price * 0.9 if last_price else "N/A (first buy pending)"
    
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
    - Max Drawdown: {state.get('max_drawdown', 0):.1f}%

    💹 Market Overview
    -----------------
    - Current BTC Price: {btc_price:,.0f} IDR
    - Next Buy Trigger: {next_buy_trigger if isinstance(next_buy_trigger, str) else f'{next_buy_trigger:,.0f} IDR (-10%)'}

    📆 Recent Activity
    -----------------
    {recent_trades}
    """

def check_report_due():
    try:
        # Pull latest state first
        os.system('git pull origin main > /dev/null 2>&1')
        with open(LAST_REPORT_FILE, 'r') as f:
            last_report = datetime.fromisoformat(f.read().strip())
    except (FileNotFoundError, ValueError):
        last_report = datetime.now() - timedelta(days=REPORT_INTERVAL + 1)
    
    # Exact 14-day check using seconds (14*24*3600 = 1,209,600)
    return (datetime.now() - last_report).total_seconds() >= 1209600

# --- Core Trading Logic ---
def execute_strategy():
    print("\n" + "=" * 40)
    print(f" Indodax Trading Bot - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 40)
    
    try:
        # Fetch data
        balance = indodax.fetch_balance()
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = ticker.get('last', 0)
        
        state = load_state()

        # Initialize strategy
        if state['original_strategy_budget'] is None:
            state['original_strategy_budget'] = balance.get('IDR', {}).get('total', 0) * 0.7
            state['remaining_budget'] = state['original_strategy_budget'] * 0.5
            state['last_purchase_price'] = None
            state['equity_peak'] = state['original_strategy_budget']
            save_state(state)
            print("[STATUS] Strategy initialized!")

        # Update equity tracking
        current_equity = balance.get('IDR', {}).get('total', 0) + (balance.get('BTC', {}).get('total', 0) * current_price)
        state['equity_peak'] = max(state.get('equity_peak', 0), current_equity)
        drawdown = ((state['equity_peak'] - current_equity) / state['equity_peak']) * 100 if state['equity_peak'] else 0
        state['max_drawdown'] = max(state.get('max_drawdown', 0), drawdown)

        # --- Trading Strategy ---
        # Initial Buy (50% of strategy budget)
        if state['last_purchase_price'] is None and state['remaining_budget'] > 0:
            buy_amount = state['remaining_budget']
            btc_amount = buy_amount / current_price
            if btc_amount >= MIN_BTC_ORDER:
                print(f"[BUY] Purchasing {btc_amount:.6f} BTC ({buy_amount:,.0f} IDR)")
                if not DRY_RUN:
                    indodax.create_order(
                        symbol='BTC/IDR',
                        type='market',
                        side='buy',
                        amount=None,
                        price=current_price,
                        params={'idr': buy_amount}
                    )
                state.update({
                    'last_purchase_price': current_price,
                    'total_idr_spent': buy_amount,
                    'total_trades': state['total_trades'] + 1,
                    'trade_history': state['trade_history'] + [{
                        'date': datetime.now().isoformat(),
                        'type': 'buy',
                        'amount': btc_amount,
                        'price': current_price
                    }]
                })
        else:
            # DCA Buy (10% price drop)
            price_drop = (state['last_purchase_price'] - current_price) / state['last_purchase_price']
            if price_drop >= 0.1 and state['remaining_budget'] > 0:
                buy_amount = state['remaining_budget'] * 0.5
                btc_amount = buy_amount / current_price
                if btc_amount >= MIN_BTC_ORDER:
                    print(f"[DCA BUY] Purchasing {btc_amount:.6f} BTC ({buy_amount:,.0f} IDR)")
                    if not DRY_RUN:
                        indodax.create_order(
                            symbol='BTC/IDR',
                            type='market',
                            side='buy',
                            amount=None,
                            price=current_price,
                            params={'idr': buy_amount}
                        )
                    state.update({
                        'remaining_budget': state['remaining_budget'] * 0.5,
                        'last_purchase_price': current_price,
                        'total_idr_spent': state['total_idr_spent'] + buy_amount,
                        'total_trades': state['total_trades'] + 1,
                        'trade_history': state['trade_history'] + [{
                            'date': datetime.now().isoformat(),
                            'type': 'buy',
                            'amount': btc_amount,
                            'price': current_price
                        }]
                    })

            # Trailing Stop-Loss
            if state['trailing_active']:
                state['highest_price'] = max(state.get('highest_price', 0), current_price)
                trailing_stop = state['highest_price'] * 0.97
                if current_price <= trailing_stop:
                    btc_balance = balance.get('BTC', {}).get('total', 0)
                    if btc_balance >= MIN_BTC_ORDER:
                        print(f"[SELL] Selling {btc_balance:.6f} BTC")
                        if not DRY_RUN:
                            indodax.create_order(
                                symbol='BTC/IDR',
                                type='market',
                                side='sell',
                                amount=btc_balance,
                                price=current_price,
                                params={'btc': btc_balance}
                            )
                        realized_profit = (btc_balance * current_price) - state['total_idr_spent']
                        state.update({
                            'realized_pnl': state['realized_pnl'] + realized_profit,
                            'total_trades': state['total_trades'] + 1,
                            'winning_trades': state['winning_trades'] + (1 if realized_profit > 0 else 0),
                            'trade_history': state['trade_history'] + [{
                                'date': datetime.now().isoformat(),
                                'type': 'sell',
                                'amount': btc_balance,
                                'price': current_price
                            }],
                            'remaining_budget': None,
                            'last_purchase_price': None,
                            'trailing_active': False
                        })
            elif current_price >= state['last_purchase_price'] * 1.08:
                state['trailing_active'] = True
                state['highest_price'] = current_price

        # Send report & save state
        if check_report_due():
            report = generate_report(balance, current_price, state)
            send_email("Indodax Biweekly Report", report)
            with open(LAST_REPORT_FILE, 'w') as f:
                f.write(datetime.now().isoformat())
            # Update to commit both files
            os.system(f'git add {STATE_FILE} {LAST_REPORT_FILE}')
            save_state(state)
        else:
            save_state(state)
            
        print("Operation completed successfully")

    except Exception as e:
        print(f"[CRITICAL ERROR] {str(e)}")
        raise

if __name__ == "__main__":
    execute_strategy()
