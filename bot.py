import ccxt
import os
import json
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from dotenv import load_dotenv
import logging
from decimal import Decimal, getcontext

load_dotenv()
getcontext().prec = 8  # For precise BTC calculations

# --- Configuration ---
DRY_RUN = False
TAKE_PROFIT = 0.06
DCA_DROP = 0.10
STATE_FILE = 'bot_state.json'
MIN_BTC_ORDER = Decimal('0.000001')
REPORT_INTERVAL = 14  # Days
LOG_FILE = 'bot.log'

# Security: Removed git operations and use local state management
# Added logging configuration
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Initialize exchange with timeout
indodax = ccxt.indodax({
    'apiKey': os.getenv("INDODAX_API_KEY"),
    'secret': os.getenv("INDODAX_SECRET_KEY"),
    'enableRateLimit': True,
    'timeout': 30000,
    'options': {'adjustForTimeDifference': True}
})

# --- State Management ---
def load_state():
    default_state = {
        'original_budget': None,
        'remaining_budget': None,
        'total_btc': Decimal('0'),
        'total_idr_spent': Decimal('0'),
        'purchase_prices': [],
        'trade_history': [],
        'realized_pnl': Decimal('0'),
        'last_report': None
    }
    
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            saved_state = json.load(f)
            # Convert numeric values to Decimal
            return {
                **default_state,
                'original_budget': Decimal(str(saved_state.get('original_budget', 0))),
                'remaining_budget': Decimal(str(saved_state.get('remaining_budget', 0))),
                'total_btc': Decimal(str(saved_state.get('total_btc', 0))),
                'total_idr_spent': Decimal(str(saved_state.get('total_idr_spent', 0))),
                'purchase_prices': saved_state.get('purchase_prices', []),
                'trade_history': saved_state.get('trade_history', []),
                'realized_pnl': Decimal(str(saved_state.get('realized_pnl', 0))),
                'last_report': saved_state.get('last_report')
            }
    return default_state

def save_state(state):
    serializable_state = {
        'original_budget': str(state['original_budget']),
        'remaining_budget': str(state['remaining_budget']),
        'total_btc': str(state['total_btc']),
        'total_idr_spent': str(state['total_idr_spent']),
        'purchase_prices': state['purchase_prices'],
        'trade_history': state['trade_history'],
        'realized_pnl': str(state['realized_pnl']),
        'last_report': state['last_report']
    }
    
    with open(STATE_FILE, 'w') as f:
        json.dump(serializable_state, f, indent=2)

# --- Core Trading Logic ---
def execute_strategy():
    logging.info("Starting execution cycle")
    try:
        balance = indodax.fetch_balance()
        ticker = indodax.fetch_ticker('BTC/IDR')
        current_price = Decimal(str(ticker['last']))
        state = load_state()

        # Initialize strategy
        if not state['purchase_prices'] and state['remaining_budget'] is None:
            idr_balance = Decimal(str(balance['IDR']['total']))
            state['original_budget'] = idr_balance * Decimal('0.7')
            state['remaining_budget'] = state['original_budget'] * Decimal('0.5')
            save_state(state)
            logging.info(f"Initialized with budget: {state['remaining_budget']} IDR")

        # Calculate correct average price
        avg_price = (state['total_idr_spent'] / state['total_btc']) if state['total_btc'] else Decimal('0')
        
        # Buy/Sell logic
        handle_initial_buy(balance, current_price, state)
        handle_take_profit(current_price, avg_price, balance, state)
        handle_dca_buy(current_price, state, balance)
        
        # Reporting
        handle_reporting(balance, current_price, state)

        logging.info("Execution completed successfully")
        
    except ccxt.NetworkError as e:
        logging.error(f"Network error: {str(e)}")
    except ccxt.ExchangeError as e:
        logging.error(f"Exchange error: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error: {str(e)}", exc_info=True)
    finally:
        if 'state' in locals():
            save_state(state)

def handle_initial_buy(balance, current_price, state):
    if not state['purchase_prices'] and state['remaining_budget'] > 0:
        idr_available = Decimal(str(balance['IDR']['free']))
        buy_amount = min(state['remaining_budget'], idr_available)
        btc_amount = buy_amount / current_price
        
        if btc_amount >= MIN_BTC_ORDER:
            logging.info(f"Executing initial buy: {btc_amount:.8f} BTC @ {current_price}")
            if not DRY_RUN:
                order = indodax.create_market_buy_order(
                    'BTC/IDR',
                    float(btc_amount),
                    params={'cost': float(buy_amount)}
                )
                # Verify order execution
                if order['status'] != 'closed':
                    logging.warning("Order not filled completely")
                    return
            
            state['purchase_prices'].append(float(current_price))
            state['total_btc'] += btc_amount
            state['total_idr_spent'] += buy_amount
            state['remaining_budget'] -= buy_amount
            state['trade_history'].append({
                'type': 'buy',
                'amount': float(btc_amount),
                'price': float(current_price),
                'timestamp': datetime.now().isoformat()
            })
            logging.info(f"Updated state: {state}")

def handle_take_profit(current_price, avg_price, balance, state):
    if state['total_btc'] > 0 and current_price >= avg_price * (1 + Decimal(str(TAKE_PROFIT))):
        btc_balance = Decimal(str(balance['BTC']['free']))
        sell_amount = min(state['total_btc'], btc_balance)
        
        if sell_amount >= MIN_BTC_ORDER:
            logging.info(f"Take profit triggered: Selling {sell_amount:.8f} BTC @ {current_price}")
            if not DRY_RUN:
                order = indodax.create_market_sell_order(
                    'BTC/IDR',
                    float(sell_amount)
                )
                # Calculate actual proceeds
                proceeds = Decimal(str(order['cost']))
            
            realized_profit = proceeds - state['total_idr_spent']
            state['realized_pnl'] += realized_profit
            state['remaining_budget'] = Decimal(str(balance['IDR']['total'])) * Decimal('0.7') * Decimal('0.5')
            state['purchase_prices'] = []
            state['total_btc'] = Decimal('0')
            state['total_idr_spent'] = Decimal('0')
            state['trade_history'].append({
                'type': 'sell',
                'amount': float(sell_amount),
                'price': float(current_price),
                'profit': float(realized_profit),
                'timestamp': datetime.now().isoformat()
            })
            logging.info(f"Reset state for new cycle. New budget: {state['remaining_budget']}")

def handle_dca_buy(current_price, state, balance):
    if state['purchase_prices']:
        last_price = Decimal(str(state['purchase_prices'][-1]))
        price_drop = (last_price - current_price) / last_price
        
        if price_drop >= DCA_DROP:
            idr_available = Decimal(str(balance['IDR']['free']))
            buy_amount = min(state['remaining_budget'] * Decimal('0.5'), idr_available)
            btc_amount = buy_amount / current_price
            
            if btc_amount >= MIN_BTC_ORDER:
                logging.info(f"DCA buy triggered: {btc_amount:.8f} BTC @ {current_price}")
                if not DRY_RUN:
                    order = indodax.create_market_buy_order(
                        'BTC/IDR',
                        float(btc_amount),
                        params={'cost': float(buy_amount)}
                    )
                    # Verify execution
                    if order['status'] != 'closed':
                        logging.warning("DCA order not filled completely")
                        return
                
                state['purchase_prices'].append(float(current_price))
                state['total_btc'] += btc_amount
                state['total_idr_spent'] += buy_amount
                state['remaining_budget'] -= buy_amount
                state['trade_history'].append({
                    'type': 'dca_buy',
                    'amount': float(btc_amount),
                    'price': float(current_price),
                    'timestamp': datetime.now().isoformat()
                })

def handle_reporting(balance, current_price, state):
    last_report = state['last_report']
    if not last_report or (datetime.now() - datetime.fromisoformat(last_report)).days >= REPORT_INTERVAL:
        report = generate_report(balance, current_price, state)
        send_email("Indodax Biweekly Report", report)
        state['last_report'] = datetime.now().isoformat()
        logging.info("Report sent successfully")

def generate_report(balance, current_price, state):
    avg_price = (state['total_idr_spent'] / state['total_btc']) if state['total_btc'] else 0
    current_btc = Decimal(str(balance['BTC']['total']))
    current_value = current_btc * current_price + Decimal(str(balance['IDR']['total']))
    unrealized_pnl = current_btc * current_price - state['total_idr_spent']
    
    return f"""
    📈 Biweekly Report - {datetime.now().strftime('%Y-%m-%d')}
    ==================================
    - Strategy Budget: {state['original_budget']:.0f} IDR
    - Current Value: {current_value:.0f} IDR
    - Avg Purchase Price: {avg_price:.0f} IDR
    - Realized P&L: {state['realized_pnl']:+.0f} IDR
    - Unrealized P&L: {unrealized_pnl:+.0f} IDR
    - Total Trades: {len(state['trade_history'])}
    - Next Buy Trigger: {(Decimal(str(state['purchase_prices'][-1])) * 0.9):.0f} IDR
    - 30D Price Change: {((current_price - avg_price)/avg_price * 100):+.2f}%
    """

def send_email(subject, body):
    msg = MIMEText(body)
    msg['Subject'] = subject
    msg['From'] = os.getenv("EMAIL_SENDER")
    msg['To'] = os.getenv("EMAIL_RECEIVER")
    
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(os.getenv("EMAIL_SENDER"), os.getenv("EMAIL_PASSWORD"))
            server.sendmail(
                os.getenv("EMAIL_SENDER"),
                [os.getenv("EMAIL_RECEIVER")],
                msg.as_string()
            )
        logging.info("Email report sent successfully")
    except Exception as e:
        logging.error(f"Failed to send email: {str(e)}")

if __name__ == "__main__":
    execute_strategy()
