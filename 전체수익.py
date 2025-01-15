import os
from dotenv import load_dotenv
load_dotenv()

import pyupbit
import sqlite3
import threading
import schedule
import time

# 글로벌 변수 추가
total_assets_update_active = True

def create_upbit_client():
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    return pyupbit.Upbit(access, secret)

def init_db():
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    # allprofit_rates 테이블 생성
    c.execute('''CREATE TABLE IF NOT EXISTS allprofit_rates (
                    key TEXT PRIMARY KEY,
                    value TEXT
                 )''')

    # 초기값 설정
    initial_investment = 1500000  # 초기 투자 금액
    total_assets = initial_investment  # 초기에는 전액 현금 상태로 가정
    profit_rate = 0.0  # 초기 수익률은 0%

    c.execute("INSERT OR IGNORE INTO allprofit_rates (key, value) VALUES ('initial_investment', ?)",
              (f"{initial_investment:,} KRW",))
    c.execute("INSERT OR IGNORE INTO allprofit_rates (key, value) VALUES ('total_assets', ?)",
              (f"{total_assets:,.2f} KRW",))
    c.execute("INSERT OR IGNORE INTO allprofit_rates (key, value) VALUES ('profit_rate', ?)",
              (f"{profit_rate:.2f}%",))

    conn.commit()
    conn.close()

# --- 초기화 호출 ---
# 프로그램 실행 시 데이터베이스 초기화
init_db()

def calculate_total_assets():
    global total_assets_update_active

    # 업비트 클라이언트 생성
    upbit = create_upbit_client()

    # 1. KRW 잔고 가져오기
    try:
        krw_balance = upbit.get_balance("KRW")  # KRW 잔액
        if krw_balance is None:
            krw_balance = 0.0
    except Exception as e:
        print(f"Error fetching KRW balance: {e}")
        krw_balance = 0.0

    # 2. 비트코인(BTC) 잔고 가져오기
    try:
        btc_balance = upbit.get_balance("BTC")  # BTC 잔액
        if btc_balance is None:
            btc_balance = 0.0
    except Exception as e:
        print(f"Error fetching BTC balance: {e}")
        btc_balance = 0.0

    # 3. 현재 비트코인(BTC) 가격 가져오기
    try:
        btc_price = pyupbit.get_current_price("KRW-BTC")  # BTC 현재 가격
        if btc_price is None:
            btc_price = 0.0
    except Exception as e:
        print(f"Error fetching BTC price: {e}")
        btc_price = 0.0

    # 4. 이더리움(ETH) 잔고 가져오기
    try:
        eth_balance = upbit.get_balance("ETH")  # ETH 잔액
        if eth_balance is None:
            eth_balance = 0.0
    except Exception as e:
        print(f"Error fetching ETH balance: {e}")
        eth_balance = 0.0

    # 5. 현재 이더리움(ETH) 가격 가져오기
    try:
        eth_price = pyupbit.get_current_price("KRW-ETH")  # ETH 현재 가격
        if eth_price is None:
            eth_price = 0.0
    except Exception as e:
        print(f"Error fetching ETH price: {e}")
        eth_price = 0.0

    # BTC와 ETH가 모두 없는 경우
    if btc_balance == 0 and eth_balance == 0:
        print("No BTC or ETH holdings. Updates will remain inactive until assets are available.")
        return None

    # 보유 자산 계산
    total_assets = krw_balance + (btc_balance * btc_price) + (eth_balance * eth_price)

    # 결과 출력 및 반환    
    return total_assets

def update_total_assets_and_profit_rate():
    global total_assets_update_active

    total_assets = calculate_total_assets()
    if total_assets is not None:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()

        # Total Assets 업데이트
        c.execute("""
            INSERT INTO allprofit_rates (key, value)
            VALUES ('total_assets', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"{total_assets:,.2f} KRW",))

        # 초기 투자 금액 가져오기
        c.execute("SELECT value FROM allprofit_rates WHERE key = 'initial_investment'")
        initial_investment_row = c.fetchone()
        initial_investment = float(initial_investment_row[0].replace(",", "").replace(" KRW", "")) if initial_investment_row else 0.0

        # Profit Rate 계산
        profit_rate = ((total_assets - initial_investment) / initial_investment) * 100 if initial_investment > 0 else 0.0

        # Profit Rate 업데이트
        c.execute("""
            INSERT INTO allprofit_rates (key, value)
            VALUES ('profit_rate', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"{profit_rate:.2f}%",))

        conn.commit()
        conn.close()

        print(f"Total Assets Updated in DB: {total_assets:,.2f} KRW")
        print(f"Profit Rate Updated in DB: {profit_rate:.2f}%")

def schedule_total_assets_and_profit_rate_update():
    global total_assets_update_active

    def check_and_activate_updates():
        global total_assets_update_active
        # BTC 또는 ETH 보유 여부 확인
        upbit = create_upbit_client()
        btc_balance = upbit.get_balance("BTC") or 0.0
        eth_balance = upbit.get_balance("ETH") or 0.0

        if btc_balance > 0 or eth_balance > 0:
            total_assets_update_active = True
            print("BTC or ETH detected. Updates reactivated.")
        else:
            total_assets_update_active = False

    # 5분마다 update_total_assets_and_profit_rate 함수 실행
    schedule.every(5).minutes.do(update_total_assets_and_profit_rate)
    # 5분마다 자산 상태 확인 및 활성화
    schedule.every(5).minutes.do(check_and_activate_updates)

    print("Total Assets and Profit Rate Update Scheduled Every 5 Minutes.")

    while True:
        schedule.run_pending()  # 예약된 작업 실행
        time.sleep(1)  # CPU 과부하를 방지하기 위한 sleep

# 스레드에서 스케줄 실행
update_thread = threading.Thread(target=schedule_total_assets_and_profit_rate_update)
update_thread.daemon = True  # 메인 스레드가 종료되면 이 스레드도 종료
update_thread.start()

# 프로그램이 종료되지 않도록 유지
while True:
    try:
        time.sleep(1)
    except KeyboardInterrupt:
        print("Program stopped by user.")
        break



