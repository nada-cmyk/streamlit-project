import os
from dotenv import load_dotenv
load_dotenv()

import pyupbit
import json
import requests
from ta.trend import SMAIndicator, MACD, EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from openai import OpenAI
import pandas_datareader.data as web
import datetime
import time
import sqlite3
import openai
import atexit
import schedule
import numpy as np
import threading
from threading import Lock

def create_upbit_client():
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    return pyupbit.Upbit(access, secret)

# --- 데이터베이스 초기화 ---
def init_db():
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()

    # ethcoin_trades 테이블 생성
    c.execute('''CREATE TABLE IF NOT EXISTS ethcoin_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    decision TEXT,
                    reason TEXT,
                    percentage REAL,
                    avg_buy_price REAL
                 )''')

    # ethcoin_predictions 테이블 생성
    c.execute('''CREATE TABLE IF NOT EXISTS ethcoin_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,  -- 고유 ID
                    timestamp TEXT,                        -- 예측이 저장된 시간
                    price_forecast_4h REAL,                -- 4시간 후 이더리움 가격 예측
                    price_forecast_12h REAL,               -- 12시간 후 이더리움 가격 예측
                    price_forecast_1d REAL,                -- 1일 후 이더리움 가격 예측
                    actual_price_4h REAL,                  -- 4시간 후 실제 이더리움 가격
                    actual_price_12h REAL,                 -- 12시간 후 실제 이더리움 가격
                    actual_price_1d REAL,                  -- 1일 후 실제 이더리움 가격
                    reflection_diary TEXT,                 -- 예측 결과에 대한 반성 기록
                    status TEXT DEFAULT 'pending',         -- 예측 상태
                    eth_accuracy_score REAL DEFAULT 0.0    -- 예측 정확도
                 )''')

    # eth_avg_accuracy_score 테이블 생성 (수정된 스키마)
    c.execute('''CREATE TABLE IF NOT EXISTS eth_avg_accuracy_score (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT,
                    avg_score REAL
                 )''')
    
    # ethcoin_global_state 테이블 생성 (available_budget 저장)
    c.execute('''CREATE TABLE IF NOT EXISTS ethcoin_global_state (
                    key TEXT PRIMARY KEY,
                    value REAL
                 )''')
    
    # ethprofit_rates 테이블 생성
    c.execute('''CREATE TABLE IF NOT EXISTS ethprofit_rates (
                    key TEXT PRIMARY KEY,
                    value TEXT
                 )''')
    
    # eth_apology 테이블 생성
    c.execute('''CREATE TABLE IF NOT EXISTS eth_apology (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                evaluation TEXT,
                summary TEXT
            )
        ''')
    
    # accuracy_score 열이 없으면 추가
    c.execute("PRAGMA table_info(ethcoin_predictions)")
    columns = [row[1] for row in c.fetchall()]
    if "accuracy_score" not in columns:
        c.execute("ALTER TABLE ethcoin_predictions ADD COLUMN accuracy_score REAL DEFAULT 0.0")

    # trades 테이블에 새로운 열 추가
    c.execute("PRAGMA table_info(ethcoin_trades)")
    columns = [row[1] for row in c.fetchall()]

    if "trade_amount" not in columns:
        c.execute("ALTER TABLE ethcoin_trades ADD COLUMN trade_amount REAL")  # 매수/매도 금액(KRW)
    if "profit" not in columns:
        c.execute("ALTER TABLE ethcoin_trades ADD COLUMN profit REAL")  # 수익

    # 초기값 설정
    initial_investment = 1000000  # 초기 투자 금액
    total_assets = initial_investment  # 초기에는 전액 현금 상태로 가정
    profit_rate = 0.0  # 초기 수익률은 0%

    c.execute("INSERT OR IGNORE INTO ethcoin_global_state (key, value) VALUES ('available_budget', 1000000)")
    c.execute("INSERT OR IGNORE INTO ethprofit_rates (key, value) VALUES ('initial_investment', ?)",
              (f"{initial_investment:,} KRW",))
    c.execute("INSERT OR IGNORE INTO ethprofit_rates (key, value) VALUES ('total_assets', ?)",
              (f"{total_assets:,.2f} KRW",))
    c.execute("INSERT OR IGNORE INTO ethprofit_rates (key, value) VALUES ('profit_rate', ?)",
              (f"{profit_rate:.2f}%",))    
    
    conn.commit()
    conn.close()

# --- 초기화 호출 ---
# 프로그램 실행 시 데이터베이스 초기화
init_db()

initial_investment = 1000000  # 초기 투자 금액
# --- 수익률 기록 ---
# 수익률 기록 함수
def record_profit_rate(initial_investment, available_budget, my_eth, current_price):
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()

        # 이더리움 보유량의 가치 계산
        eth_value = my_eth * current_price if current_price else 0.0

        # 총 자산 계산 (이더리움 가치 + 사용 가능한 예산)
        total_assets = available_budget + eth_value

        # 수익률 계산
        profit_rate = ((total_assets - initial_investment) / initial_investment) * 100

        # 초기 투자 금액 저장 또는 업데이트
        c.execute("""
            INSERT INTO ethprofit_rates (key, value)
            VALUES ('initial_investment', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"{initial_investment:,} KRW",))

        # 총 자산 저장 또는 업데이트
        c.execute("""
            INSERT INTO ethprofit_rates (key, value)
            VALUES ('total_assets', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"{total_assets:,.2f} KRW",))

        # 수익률 저장 또는 업데이트
        c.execute("""
            INSERT INTO ethprofit_rates (key, value)
            VALUES ('profit_rate', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (f"{profit_rate:.2f}%",))

        conn.commit()
        print(f"Profit rates updated: Initial={initial_investment:,} KRW, Total Assets={total_assets:,.2f} KRW, Profit Rate={profit_rate:.2f}%")
    except Exception as e:
        print(f"Error saving profit rates: {e}")
    finally:
        conn.close()

# --- 수익률 업데이트 스레드 ---
profit_rate_updater_running = False
profit_rate_thread = None

def start_profit_rate_updater(initial_investment):
    global profit_rate_updater_running, profit_rate_thread

    if profit_rate_updater_running:
        print("Profit rate updater is already running. Skipping new thread.")
        return

    profit_rate_updater_running = True

    def update_profit_rate():
        global profit_rate_updater_running, available_budget

        upbit = create_upbit_client()

        while profit_rate_updater_running:
            try:
                my_btc = upbit.get_balance("ETH")
                current_price = pyupbit.get_current_price("KRW-ETH")

                if current_price is not None:
                    record_profit_rate(initial_investment, available_budget, my_btc, current_price)
                else:
                    print("Failed to fetch current price for profit rate update.")

                # 1분 대기
                time.sleep(60)

            except Exception as e:
                print(f"Error in profit rate updater: {e}")
                break

        print("Profit rate updater thread has stopped.")

    profit_rate_thread = threading.Thread(target=update_profit_rate, daemon=True)
    profit_rate_thread.start()
    print("Profit rate updater thread started.")

def stop_profit_rate_updater():
    global profit_rate_updater_running

    if not profit_rate_updater_running:
        print("Profit rate updater is not running. Nothing to stop.")
        return

    profit_rate_updater_running = False
    print("Profit rate updater thread stop requested.")

# --- 투자 가능 금액 불러오기 ---
def load_available_budget():
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()
    c.execute("SELECT value FROM ethcoin_global_state WHERE key = 'available_budget'")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1000000  #기본값 100만 원

# --- 투자 가능 금액 저장 ---
def save_available_budget(budget):
    conn = sqlite3.connect("trades.db")
    c = conn.cursor()
    c.execute("UPDATE ethcoin_global_state SET value = ? WHERE key = 'available_budget'", (budget,))
    conn.commit()
    conn.close()

# --- 종료 시 사용 가능 금액 저장 ---
def on_exit_save_budget():
    global available_budget
    print(f"프로그램 종료 전 사용 가능 금액 저장: {available_budget:.2f} KRW")
    save_available_budget(available_budget)

# --- 글로벌 변수 ---
available_budget = load_available_budget()  # 데이터베이스에서 불러오기

def record_prediction(price_forecast_4h, price_forecast_12h, price_forecast_1d):
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        timestamp = datetime.datetime.now().isoformat()
        c.execute("INSERT INTO ethcoin_predictions (timestamp, price_forecast_4h, price_forecast_12h, price_forecast_1d) VALUES (?, ?, ?, ?)",
                  (timestamp, price_forecast_4h, price_forecast_12h, price_forecast_1d))
        conn.commit()
        print("Prediction successfully recorded to DB.")
    except Exception as e:
        print(f"Error saving prediction to DB: {e}")
    finally:
        conn.close()

def update_actual_prices():
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        c.execute("SELECT id, timestamp FROM ethcoin_predictions WHERE status = 'pending'")
        pending_predictions = c.fetchall()

        for pred_id, timestamp in pending_predictions:
            prediction_time = datetime.datetime.fromisoformat(timestamp)
            now = datetime.datetime.now()

            # Fetch actual prices after 4h, 12h, 1d
            price_actual_4h = price_actual_12h = price_actual_1d = None
            if now >= prediction_time + datetime.timedelta(hours=4):
                price_actual_4h = pyupbit.get_current_price("KRW-ETH")
            if now >= prediction_time + datetime.timedelta(hours=12):
                price_actual_12h = pyupbit.get_current_price("KRW-ETH")
            if now >= prediction_time + datetime.timedelta(days=1):
                price_actual_1d = pyupbit.get_current_price("KRW-ETH")

            # Update the prediction with actual prices
            c.execute('''UPDATE ethcoin_predictions SET actual_price_4h = ?, actual_price_12h = ?,
                         actual_price_1d = ?, status = CASE
                             WHEN actual_price_1d IS NOT NULL THEN 'completed'
                             ELSE 'pending' END
                         WHERE id = ?''',
                      (price_actual_4h, price_actual_12h, price_actual_1d, pred_id))
            conn.commit()
        print("Actual prices updated successfully.")
    except Exception as e:
        print(f"Error updating actual prices: {e}")
    finally:
        conn.close()

def calculate_accuracy_multiple(predicted_values, actual_values):
    # 예측 값과 실제 값이 모두 있는지 확인
    accuracies = []
    for predicted, actual in zip(predicted_values, actual_values):
        if predicted is not None and actual is not None:
            accuracy = max(0, 1 - abs((predicted - actual) / actual))
            accuracies.append(accuracy * 100)  # 백분율로 변환
    # 개별 정확도 평균 계산
    return sum(accuracies) / len(accuracies) if accuracies else 0.0

# --- 평균 accuracy_score 계산 및 업데이트 ---
def calculate_and_update_avg_accuracy():
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()

        # 모든 completed 상태의 eth_accuracy_score 값 가져오기 (특정 timestamp 제외)
        excluded_timestamp = "2025-01-09T14:30:06.266254"
        c.execute("""
            SELECT accuracy_score 
            FROM predictions 
            WHERE status = 'completed' AND timestamp != ?
        """, (excluded_timestamp,))
        accuracy_scores = c.fetchall()

        # 평균 계산
        avg_score = 0.0
        if accuracy_scores:
            avg_score = sum(score[0] for score in accuracy_scores) / len(accuracy_scores)

        # 평균 정확도 값을 eth_avg_accuracy_score 테이블에 삽입 또는 업데이트
        c.execute("""
            INSERT INTO eth_avg_accuracy_score (key, timestamp, avg_score)
            VALUES ('eth_avg_accuracy_score', ?, ?)
            ON CONFLICT(key) DO UPDATE SET avg_score = excluded.avg_score, timestamp = excluded.timestamp
        """, (datetime.datetime.now().isoformat(), avg_score))

        conn.commit()
        print(f"Average accuracy score updated in eth_avg_accuracy_score table: {avg_score:.2f}%")
    except Exception as e:
        print(f"Error updating average accuracy score: {e}")
    finally:
        conn.close()

# --- 정확도 업데이트 ---
def update_accuracy_scores():
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()

        # 'pending' 상태인 레코드 가져오기
        c.execute("""
            SELECT id, price_forecast_4h, price_forecast_12h, price_forecast_1d, 
                   actual_price_4h, actual_price_12h, actual_price_1d 
            FROM predictions 
            WHERE status = 'pending'
        """)
        rows = c.fetchall()

        for row in rows:
            pred_id, p4h, p12h, p1d, a4h, a12h, a1d = row

            # 모든 actual_price_* 값이 채워졌는지 확인
            predicted_values = [p4h, p12h, p1d]
            actual_values = [a4h, a12h, a1d]

            if all(actual_values):  # 모든 실제 값이 존재하면
                # 정확도 계산
                avg_accuracy = calculate_accuracy_multiple(predicted_values, actual_values)

                # 정확도와 상태 업데이트
                c.execute("""
                    UPDATE ethcoin_predictions 
                    SET accuracy_score = ?, status = 'completed' 
                    WHERE id = ?
                """, (avg_accuracy, pred_id))
            else:
                # 일부 값이 비어 있으면 정확도는 0으로 설정 (상태는 유지)
                c.execute("""
                    UPDATE ethcoin_predictions 
                    SET accuracy_score = 0.0 
                    WHERE id = ?
                """, (pred_id,))

        conn.commit()
        print("Prediction accuracy updated.")
        
        # 평균 accuracy_score 계산 및 업데이트
        calculate_and_update_avg_accuracy()

    except Exception as e:
        print(f"Error updating accuracy: {e}")
    finally:
        conn.close()                                           

def get_latest_accuracy():
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        # eth_avg_accuracy_score 테이블에서 'avg_accuracy_score' 키의 값을 가져오기
        c.execute("SELECT avg_score FROM eth_avg_accuracy_score WHERE key = 'eth_avg_accuracy_score'")
        row = c.fetchone()
        return row[0] if row else 100.0  # Default value is 100.0% (when no record exists)
    except Exception as e:
        print(f"Error retrieving average accuracy: {e}")
        return 100.0  # Default value in case of error
    finally:
        conn.close()


init_db()

#반성문 작성
#1. 최근 24시간 거래내용 가져오기
def get_recent_trades():
    """
    최근 24시간 동안의 ethcoin_trades 테이블 정보를 JSON 형식으로 반환
    """
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        
        # 현재 시간과 24시간 전 시간 계산
        now = datetime.datetime.now()
        time_24_hours_ago = now - datetime.timedelta(hours=24)
        
        # ethcoin_trades 테이블에서 최근 24시간 동안의 데이터 가져오기
        c.execute("""
            SELECT * FROM ethcoin_trades
            WHERE timestamp >= ?
        """, (time_24_hours_ago.isoformat(),))
        
        rows = c.fetchall()
        
        # 테이블의 열 이름 가져오기
        column_names = [desc[0] for desc in c.description]
        
        # 데이터를 JSON 형식으로 변환
        trades_data = [dict(zip(column_names, row)) for row in rows]
        
        return json.dumps(trades_data, indent=4, ensure_ascii=False)
    
    except Exception as e:
        print(f"Error retrieving recent trades: {e}")
        return json.dumps({"error": str(e)})
    
    finally:
        conn.close()

#2. 과거 데이터 수집
def analyze_timeframes(timeframes):
    """
    주어진 시간대에 대한 거래량, 변동성, 지지선/저항선, 주요 지표 값 계산
    """
    try:
        results = []
        for timestamp in timeframes:  # 시간만 받아 처리
            ohlcv = pyupbit.get_ohlcv("KRW-ETH", interval="minute60", count=24)  # 최근 하루 데이터
            
            if ohlcv is None or ohlcv.empty:
                raise ValueError("데이터를 가져오지 못했습니다.")

            # 해당 시간에 가장 가까운 데이터 추출
            target_row = ohlcv.loc[ohlcv.index <= timestamp].iloc[-1]

            # 거래량 및 변동성
            volume = target_row["volume"]
            volatility = target_row["high"] - target_row["low"]

            # 지지선/저항선 계산
            max_price = ohlcv["high"].max()
            min_price = ohlcv["low"].min()
            fib_levels = {
                "0%": min_price,
                "23.6%": max_price - (max_price - min_price) * 0.236,
                "38.2%": max_price - (max_price - min_price) * 0.382,
                "50%": max_price - (max_price - min_price) * 0.5,
                "61.8%": max_price - (max_price - min_price) * 0.618,
                "100%": max_price,
            }

            # 기술적 지표 계산
            close_prices = ohlcv["close"]
            rsi = RSIIndicator(close=close_prices, window=14).rsi().iloc[-1]
            macd = MACD(close=close_prices, window_slow=26, window_fast=12, window_sign=9)
            macd_value = macd.macd().iloc[-1]
            macd_signal = macd.macd_signal().iloc[-1]
            sma = SMAIndicator(close=close_prices, window=20).sma_indicator().iloc[-1]
            ema = EMAIndicator(close=close_prices, window=20).ema_indicator().iloc[-1]

            # 결과 저장
            results.append({
                "timestamp": timestamp.isoformat(),
                "volume": volume,
                "volatility": volatility,
                "support_resistance": fib_levels,
                "rsi": rsi,
                "macd": {"macd": macd_value, "signal": macd_signal},
                "sma": sma,
                "ema": ema
            })

        return json.dumps(results, indent=4, ensure_ascii=False)

    except Exception as e:
        print(f"Error analyzing timeframes: {e}")
        return json.dumps({"error": str(e)})
              
# 3. AI 반성문 작성
def evaluate_trading_decisions():
    try:
        # 최근 24시간 거래 내용 가져오기
        recent_trades_json = get_recent_trades()
        if not recent_trades_json:
            raise ValueError("get_recent_trades() returned None or empty string.")

        try:
            recent_trades = json.loads(recent_trades_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSONDecodeError while parsing recent_trades_json: {e}. Input: {recent_trades_json}")

        if "error" in recent_trades:
            raise ValueError(f"Error in get_recent_trades: {recent_trades['error']}")

        # 과거 데이터 분석
        timeframes = [
            datetime.datetime.now() - datetime.timedelta(hours=20),
            datetime.datetime.now() - datetime.timedelta(hours=16),
            datetime.datetime.now() - datetime.timedelta(hours=12),
            datetime.datetime.now() - datetime.timedelta(hours=8),
            datetime.datetime.now() - datetime.timedelta(hours=6),
            datetime.datetime.now() - datetime.timedelta(hours=2),
        ]
        analysis_results_json = analyze_timeframes(timeframes)
        if not analysis_results_json:
            raise ValueError("analyze_timeframes() returned None or empty string.")

        try:
            analysis_results = json.loads(analysis_results_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSONDecodeError while parsing analysis_results_json: {e}. Input: {analysis_results_json}")

        if "error" in analysis_results:
            raise ValueError(f"Error in analyze_timeframes: {analysis_results['error']}")

        # 데이터 결합
        combined_data = {
            "recent_trades": recent_trades,
            "analysis_results": analysis_results,
        }

        client = OpenAI()
        # AI 요청
        openai.api_key = os.getenv("OPENAI_API_KEY")
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a financial analyst specializing in Ethereum Coin trading. "
                        "Trading trades at regular hours of the day (3am, 7am, 11am, 3pm, 7pm and 11pm)."
                        "Your task is to evaluate recent trading decisions based on provided historical data "
                        "and suggest improvements for future trades. "
                        "The summary summarizes improvements to future trading"
                        "The response should be in JSON format, for example:"
                        "Do not include any additional text outside the JSON structure."
                        "{\n"
                        "  \"evaluation\": [\n"
                        "    {\"trade_id\": 1, \"decision\": \"buy\", \"evaluation\": \"...\", \"improvement\": \"...\"},\n"
                        "    {\"trade_id\": 2, \"decision\": \"sell\", \"evaluation\": \"...\", \"improvement\": \"...\"}\n"
                        "  ],\n"
                        "  \"summary\": \"...\"\n"
                        
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(combined_data, indent=4),
                },
            ],
        )

        try:
            ai_response = response.choices[0].message.content  # 수정된 부분
            parsed_response = json.loads(ai_response)  # JSON 파싱
            print("AI의 분석 결과 (JSON):")
            print(json.dumps(parsed_response, indent=4, ensure_ascii=False))

            # btc_apology 테이블에 저장
            save_to_apology_table(parsed_response)

            return parsed_response
        except json.JSONDecodeError as e:
            print(f"AI 응답을 JSON으로 파싱할 수 없습니다: {e}")
            print("원시 응답:")
            print(ai_response)
            return {"error": "Failed to parse AI response as JSON", "raw_response": ai_response}
        except Exception as e:
            print(f"Error evaluating trading decisions: {e}")
            return {"error": str(e)}
    except Exception as e:
        print(f"evaluate_trading_decisions() encountered an error: {e}")
        raise
    
#4. 반성문 저장
def save_to_apology_table(ai_response):
    """
    AI 응답을 eth_apology 테이블에 저장
    """
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()

        # 현재 시간
        timestamp = datetime.datetime.now().isoformat()

        # AI 응답 데이터를 JSON에서 문자열로 변환
        evaluation = json.dumps(ai_response.get("evaluation", []), ensure_ascii=False)
        summary = ai_response.get("summary", "")

        # 데이터 삽입
        c.execute('''
            INSERT INTO eth_apology (timestamp, evaluation, summary)
            VALUES (?, ?, ?)
        ''', (timestamp, evaluation, summary))
        
        conn.commit()
        print("AI 응답이 eth_apology 테이블에 저장되었습니다.")
    except Exception as e:
        print(f"Error saving to eth_apology table: {e}")
    finally:
        conn.close()

def record_trade(decision, reason, percentage, order_result, avg_buy_price, trade_amount=0, profit=0):
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        timestamp = datetime.datetime.now().isoformat()

        # Convert order_result to JSON string or handle None
        order_result_str = json.dumps(order_result) if order_result else "N/A"

        # Debugging output
        print("Recording to DB:")
        print(f"Timestamp: {timestamp}, Decision: {decision}, Reason: {reason}, "
              f"Percentage: {percentage}, Order Result: {order_result_str}, "
              f"Avg Buy Price: {avg_buy_price}, Trade Amount: {trade_amount}, Profit: {profit}")

        # Insert into trades table
        c.execute("""
            INSERT INTO ethcoin_trades (timestamp, decision, reason, percentage, avg_buy_price, trade_amount, profit)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, decision, reason, percentage, avg_buy_price, trade_amount, profit))
        
        conn.commit()
        print("Trade record successfully saved to DB.")
    except Exception as e:
        print(f"Error saving trade record to DB: {e}")
    finally:
        conn.close()

def get_latest_eth_apology_summary():
    """
    데이터베이스에서 eth_apology 테이블의 가장 최근 summary를 가져옵니다.
    """
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        
        # 가장 최근의 summary 가져오기
        c.execute("""
            SELECT summary 
            FROM eth_apology
            ORDER BY timestamp DESC
            LIMIT 1
        """)
        row = c.fetchone()
        
        if row:
            return row[0]  # summary 반환
        else:
            return "No summary available."  # 데이터가 없을 경우 기본 메시지
        
    except Exception as e:
        print(f"Error fetching latest eth_apology summary: {e}")
        return f"Error: {e}"  # 오류 메시지 반환
    
    finally:
        conn.close()

# 뉴스 데이터를 가져오는 함수 추가
def fetch_ethereum_news():
    API_KEY = "5aeb3b8472c00d9e845cac14a211bcac48fadba96a4f1a7c551369cddcbbd770"
    BASE_URL = "https://serpapi.com/search.json"

    params = {
        "engine": "google_news",
        "q": "cointelegraph ethereum price news when:1d",
        "gl": "us",
        "hl": "en",
        "api_key": API_KEY
    }

    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()
        news_results = data.get("news_results", [])

        # 최근 5개의 뉴스 정보 반환
        recent_news = []
        for news in news_results[:5]:
            recent_news.append({
                "title": news.get("title", "No Title"),
                "date": news.get("date", "No Date")
            })
        return recent_news

    except requests.exceptions.RequestException as e:
        print(f"Error fetching news: {e}")
        return []
    
# --- 글로벌 변수 추가 ---
initial_budget = 1000000  # 초기 예산
available_budget = initial_budget  # 현재 사용 가능 금액
suspend_compare_and_buy = False  # compare_and_buy 동작 여부를 결정하는 플래그

# AI 매매하기
def ai_trading(current_hour, current_minute):
    global available_budget, ai_trading_in_progress

    with lock:
        ai_trading_in_progress = True  # ai_trading 시작

    print(f"AI 트레이딩 실행: {current_hour}:{current_minute}")
    
    with lock:
        ai_trading_in_progress = False  # ai_trading 종료

    # 1. 업비트 차트 가져오기
    df = pyupbit.get_ohlcv("KRW-ETH", count=30, interval="day")
    df_24h = pyupbit.get_ohlcv("KRW-ETH", count=24, interval="minute60")
    df_26w = pyupbit.get_ohlcv("KRW-ETH", count=26, interval="week")

    # 컬럼명 변경 (대문자)
    df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    df_24h = df_24h.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    df_26w = df_26w.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})

    # 지표 계산 (일봉)
    df["SMA20"] = SMAIndicator(close=df["Close"], window=20).sma_indicator()
    macd = MACD(close=df["Close"], window_slow=26, window_fast=12, window_sign=9)
    df["MACD"] = macd.macd()
    df["MACD_signal"] = macd.macd_signal()
    df["MACD_hist"] = macd.macd_diff()
    rsi = RSIIndicator(close=df["Close"], window=14)
    df["RSI"] = rsi.rsi()
    bb = BollingerBands(close=df["Close"], window=20, window_dev=2)
    df['BB_mavg'] = bb.bollinger_mavg()
    df['BB_hband'] = bb.bollinger_hband()
    df['BB_lband'] = bb.bollinger_lband()
    df['BB_pband'] = bb.bollinger_pband()
    df['BB_wband'] = bb.bollinger_wband()

    # 지표 계산 (1시간 봉)
    df_24h["SMA20"] = SMAIndicator(close=df_24h["Close"], window=20).sma_indicator()
    macd_24 = MACD(close=df_24h["Close"], window_slow=26, window_fast=12, window_sign=9)
    df_24h["MACD"] = macd_24.macd()
    df_24h["MACD_signal"] = macd_24.macd_signal()
    df_24h["MACD_hist"] = macd_24.macd_diff()
    rsi_24 = RSIIndicator(close=df_24h["Close"], window=14)
    df_24h["RSI"] = rsi_24.rsi()
    bb_24 = BollingerBands(close=df_24h["Close"], window=20, window_dev=2)
    df_24h['BB_mavg'] = bb_24.bollinger_mavg()
    df_24h['BB_hband'] = bb_24.bollinger_hband()
    df_24h['BB_lband'] = bb_24.bollinger_lband()
    df_24h['BB_pband'] = bb_24.bollinger_pband()
    df_24h['BB_wband'] = bb_24.bollinger_wband()

    # 지표 계산 (26주 봉)
    df_26w["SMA20"] = SMAIndicator(close=df_26w["Close"], window=20).sma_indicator()
    macd_26w = MACD(close=df_26w["Close"], window_slow=26, window_fast=12, window_sign=9)
    df_26w["MACD"] = macd_26w.macd()
    df_26w["MACD_signal"] = macd_26w.macd_signal()
    df_26w["MACD_hist"] = macd_26w.macd_diff()
    rsi_26w = RSIIndicator(close=df_26w["Close"], window=14)
    df_26w["RSI"] = rsi_26w.rsi()
    bb_26w = BollingerBands(close=df_26w["Close"], window=20, window_dev=2)
    df_26w['BB_mavg'] = bb_26w.bollinger_mavg()
    df_26w['BB_hband'] = bb_26w.bollinger_hband()
    df_26w['BB_lband'] = bb_26w.bollinger_lband()
    df_26w['BB_pband'] = bb_26w.bollinger_pband()
    df_26w['BB_wband'] = bb_26w.bollinger_wband()

    # 피보나치 되돌림 (일봉 기준)
    max_price = df["High"].max()
    min_price = df["Low"].min()
    fib_ratios = [0,0.236,0.382,0.5,0.618,0.786,1]
    fib_levels = {f"FIB_{int(r*100)}%": max_price - (max_price - min_price)*r for r in fib_ratios}

    # 업비트 잔액 확인
    access = os.getenv("UPBIT_ACCESS_KEY")
    secret = os.getenv("UPBIT_SECRET_KEY")
    upbit = pyupbit.Upbit(access, secret)
    my_eth = upbit.get_balance("KRW-ETH")
    orderbook = pyupbit.get_orderbook("KRW-ETH")

    # eth_avg_accuracy_score 가져오기
    latest_accuracy = 100.0  # 기본값 설정
    try:
        conn = sqlite3.connect("trades.db")
        c = conn.cursor()
        # eth_avg_accuracy_score 테이블에서 'avg_accuracy_score' 키의 값을 가져오기
        c.execute("SELECT value FROM eth_avg_accuracy_score WHERE key = 'avg_accuracy_score'")
        row = c.fetchone()
        if row:
            latest_accuracy = row[0]
        conn.close()
    except Exception as e:
        print(f"Error retrieving eth_avg_accuracy_score: {e}")

    print(f"가장 최근의 eth_avg_accuracy_score: {latest_accuracy:.2f}%")

    # balances에서 ETH의 avg_buy_price 가져오기
    balances = upbit.get_balances()
    avg_buy_price = 0.0
    for b in balances:
        if b['currency'] == 'ETH':
            avg_buy_price = float(b['avg_buy_price'])
            break

    # 공포탐욕지수 (30일)
    fg_response = requests.get("https://api.alternative.me/fng/?limit=30")
    if fg_response.status_code == 200:
        fear_greed_data = fg_response.json()
    else:
        fear_greed_data = {"error": "Failed to fetch Fear & Greed data"}

    # 환율 데이터 (30일)
    end_date = datetime.datetime.now()
    start_date = end_date - datetime.timedelta(days=30)
    usd_df = web.DataReader("DEXKOUS", 'fred', start_date, end_date)

    # 최신 btc_apology summary 가져오기
    latest_summary = get_latest_eth_apology_summary()    

    # 뉴스는 오전 10시 30분에만 가져오기
    recent_news = fetch_ethereum_news() if current_hour == 10 and current_minute == 30 else []

    data_payload = {
        "30d_ohlcv": df.to_dict(orient="records"),
        "24h_ohlcv": df_24h.to_dict(orient="records"),
        "26w_ohlcv": df_26w.to_dict(orient="records"),
        "orderbook": orderbook,
        "fib_levels": fib_levels,
        "balances": {
            "available_budget": available_budget,  # 사용 가능한 금액
            "ETH": my_eth
        },
        "fear_and_greed_30d": fear_greed_data,
        "usd_30d": usd_df.to_dict(orient="records"),
        "latest_accuracy_score": latest_accuracy,  # avg_accuracy_score 추가
        "latest_summary": latest_summary,  # 최신 summary 추가
        "recent_news": recent_news  # 뉴스 데이터 추가함
    }

    client = OpenAI()
    #시스템 메세지
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are a Ethereum investor who trades every 4hours. My goal is to continue increasing the available budget through Ethereum. "
                    "The available budget increases when you make a profit in Ethereum. But on the other hand, if you lose money, the available budget decreases."
                    "The initial available budget was 1,000,000 krw"
                    f"Your price prediction accuracy so far is {latest_accuracy:.2f}%. "
                    "First, take the data below and predict the price of Ethereum in 4 hours, 12 hours, and 1 day. "
                    "Then, based on the expected results, my available_budget, ETH balance, and your prediction accuracy, "
                    "Please make the best choice (buy, sell, hold) according to my goal. Consider lowering the average unit price by making additional purchases. also If you're expecting a price increase, and you're currently in the midst of a profit, it may be a good option to maximize your profit by buying more. (Note: If ETH balance (my_eth) is 0, you cannot choose 'sell'. If available_budget is less than 5,000: you cannot choose 'buy'. )"
                    "Also, if your judgment is buy or sell, decide what percentage of available_budget or sell my ETH. "
                    f"In addition, here is a reflection on your recent trading performance: \"{latest_summary}\". "
                    "Please take this feedback and proceed with the transaction."
                    "The response should be in JSON format, for example:\n"
                    "{\"price_forecast_4h\": 31000000, \"price_forecast_12h\": 31200000, \"price_forecast_1d\": 31500000, "
                    "\"decision\": \"buy\", \"reason\": \"some technical reason\", \"percentage\": 70}"
                )
            },
            {
                "role": "user",
                "content": json.dumps(data_payload)
            }
        ],
        response_format={"type": "json_object"},
    )

    try:
        result = response.choices[0].message.content
        result = json.loads(result)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"Error decoding AI response: {e}")
        print("Response content:", response)
        result = {}

    # 예측 값이 누락되었는지 확인 (4시간, 12시간, 1일)
    if "price_forecast_4h" not in result:
        print("Warning: 4-hour forecast is missing in the AI response.")
    
    if "price_forecast_12h" not in result:
        print("Warning: 12-hour forecast is missing in the AI response.")
    
    if "price_forecast_1d" not in result:
        print("Warning: 1-day forecast is missing in the AI response.")

    # 결과 데이터에서 값 가져오기
    price_forecast_4h = result.get("price_forecast_4h", "N/A")
    price_forecast_12h = result.get("price_forecast_12h", "N/A")
    price_forecast_1d = result.get("price_forecast_1d", "N/A")
    decision = result.get("decision", "hold")
    reason = result.get("reason", "No reason provided")
    percentage = result.get("percentage", 0)

    # 예측 값 저장 (4시간, 12시간, 1일)
    record_prediction(price_forecast_4h, price_forecast_12h, price_forecast_1d)
    print("AI 예측 값이 ethcoin_predictions 테이블에 저장되었습니다.")

    # 결과 출력
    print(f"AI Price Forecast (4 hours): {price_forecast_4h}")
    print(f"AI Price Forecast (12 hours): {price_forecast_12h}")
    print(f"AI Price Forecast (1 day): {price_forecast_1d}")
    print(f"AI Decision: {decision}")
    print(f"Reason: {reason}")
    print(f"Percentage: {percentage}%")


    # 매매 로직 및 DB 기록
    try:
        if decision == "buy":
            # Adjusted percentage로 매수 금액 계산
            amount_to_buy = available_budget * (percentage / 100.0) * 0.9995

            if amount_to_buy > 5000:  # 최소 매수 금액 검증
                order_result = upbit.buy_market_order("KRW-ETH", amount_to_buy)
                if order_result:  # 매수 성공 시
                    available_budget -= amount_to_buy  # 사용 가능 금액 업데이트
                    record_trade("buy", reason, percentage, order_result, avg_buy_price, trade_amount=amount_to_buy)
                    print(f"매수 진행: {amount_to_buy:.2f} KRW, 남은 예산: {available_budget:.2f} KRW")
                    save_available_budget(available_budget)  # 저장
                else:
                    print("매수 실패: 업비트 API 호출 문제 또는 기타 이유")
                    record_trade("매수 실패: 업비트 API 호출 문제 또는 기타 이유", reason, percentage, "N/A", avg_buy_price)
            else:
                print("잔액 부족으로 매수하지 않음")
                record_trade("잔액 부족으로 매수하지 않음", reason, percentage, "N/A", avg_buy_price)

        elif decision == "sell":
            my_eth = upbit.get_balance("KRW-ETH")
            eth_to_sell = my_eth * (percentage / 100.0)
            current_price = pyupbit.get_orderbook(ticker="KRW-ETH")["orderbook_units"][0]["ask_price"]

            if eth_to_sell * current_price > 5000:
                order_result = upbit.sell_market_order("KRW-ETH", eth_to_sell)

                # 수익 계산 및 예산 업데이트
                sell_amount = eth_to_sell * current_price
                avg_buy_cost = avg_buy_price * eth_to_sell
                profit = sell_amount - avg_buy_cost
                available_budget += sell_amount  # 수익 추가
                record_trade("sell", reason, percentage, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit)
                print(f"매도 진행: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                save_available_budget(available_budget)  # 저장
            else:
                print("최소거래량 미달")
                record_trade("sell", reason, percentage, "N/A", avg_buy_price)

        else:  # hold인 경우
            print("Hold Decision")
            record_trade("hold", reason, percentage, "N/A", avg_buy_price)
    except Exception as e:
        print(f"Error during trade execution: {e}")
        record_trade("error", f"Exception: {e}", 0, "N/A", avg_buy_price)
 
    # 실제 가격 업데이트
    update_actual_prices()
    # 정확도 업데이트
    update_accuracy_scores()
    #사용가능 금액 업데이트
    save_available_budget(available_budget)

ai_trading_in_progress = False  # ai_trading 동작 여부를 나타내는 플래그
buy_count = 0  # 연속 매수 횟수 추적

def compare_and_buy():
    global available_budget, suspend_compare_and_buy, buy_count, ai_trading_in_progress, last_update_time
    upbit = create_upbit_client()

    while True:
        with lock:
            if ai_trading_in_progress:
                print("compare_and_buy가 ai_trading 실행 중이므로 대기 중입니다.")
                time.sleep(10)
                continue
        try:
            # 현재 이더리움 가격 가져오기
            current_price = pyupbit.get_current_price("KRW-ETH")
            if current_price is None or not isinstance(current_price, (int, float)):
                raise ValueError("현재 이더리움 가격 데이터를 가져오지 못했습니다.")

            balances = upbit.get_balances()
            avg_buy_price = next(
                (float(b['avg_buy_price']) for b in balances if b['currency'] == 'ETH'),
                0.0
            )
            my_eth = upbit.get_balance("KRW-ETH")
            ethprofit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100

            if avg_buy_price > 0:
                ethprofit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100
                # 수익률 업데이트 스레드 시작
                if not profit_rate_updater_running:
                    start_profit_rate_updater(initial_investment)
            elif avg_buy_price == 0 and profit_rate_updater_running:
                # 이더리움 미보유 시 스레드 종료
                stop_profit_rate_updater()
                ethprofit_rate = 0  # 기본값 설정    
                
                # 추가 조건: 보유한 이더리움 금액이 5만 원 이하일 경우 전체 매도
            if ethprofit_rate >= 2 and (my_eth * current_price) <= 50000:
                print("수익률이 2% 이상이며 보유한 이더리움 금액이 5만 원 이하입니다. 전체 매도 진행합니다.")
                eth_to_sell = my_eth
                order_result = upbit.sell_market_order("KRW-ETH", eth_to_sell)
                if order_result:
                    sell_amount = eth_to_sell * current_price
                    avg_buy_cost = avg_buy_price * eth_to_sell
                    profit = sell_amount - avg_buy_cost
                    available_budget += sell_amount  # 수익 추가
                    save_available_budget(available_budget)  # 저장
                    print(f"전체 매도 진행: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                    record_trade("sell", "수익률 2% 이상 및 보유한 이더리움 금액이 5만 원 이하로 전체 매도", 100, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )
                continue

            if ethprofit_rate >= 2:
                print("수익률이 2% 이상입니다. 보유한 이더리움 중 30%를 매도합니다.")
                eth_to_sell = my_eth * 0.3
                if eth_to_sell * current_price > 5000:
                    order_result = upbit.sell_market_order("KRW-ETH", eth_to_sell)
                    if order_result:
                        sell_amount = eth_to_sell * current_price
                        avg_buy_cost = avg_buy_price * eth_to_sell
                        profit = sell_amount - avg_buy_cost
                        available_budget += sell_amount  # 수익 추가
                        save_available_budget(available_budget)  # 저장
                        print(f"수익률2% 이상으로 매도: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                        record_trade("sell", "수익률 2% 이상으로 매도", 30, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )

                        print("1시간 대기 시작")
                        start_wait_time = datetime.datetime.now()
                        while (datetime.datetime.now() - start_wait_time).total_seconds() < 3600:
                            remaining_time = 3600 - (datetime.datetime.now() - start_wait_time).total_seconds()
                            time.sleep(20)
                            current_price = pyupbit.get_current_price("KRW-ETH")
                            ethprofit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100
                            print(f"대기 중- 남은 시간: {int(remaining_time // 60)}분 {int(remaining_time % 60)}초- 현재 이더리움 수익률: {ethprofit_rate:.2f}%")

                            # 수익률 5% 이상일 경우 추가 매도
                            if ethprofit_rate >= 5 and ethprofit_rate < 10:
                                eth_to_sell = my_eth * 0.6
                                if eth_to_sell * current_price > 5000:
                                    order_result = upbit.sell_market_order("KRW-ETH", eth_to_sell)
                                    if order_result:
                                        sell_amount = eth_to_sell * current_price
                                        avg_buy_cost = avg_buy_price * eth_to_sell
                                        profit = sell_amount - avg_buy_cost
                                        available_budget += sell_amount  # 수익 추가
                                        save_available_budget(available_budget)  # 저장
                                        print(f"수익률 5% 이상으로 추가 매도: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                                        record_trade("sell", "수익률 5% 이상으로 추가 매도", 60, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )
                                        break

                            # 수익률 10% 이상일 경우 전체 매도
                            if ethprofit_rate >= 10:
                                eth_to_sell = my_eth
                                if eth_to_sell * current_price > 5000:
                                    order_result = upbit.sell_market_order("KRW-ETH", eth_to_sell)
                                    if order_result:
                                        sell_amount = eth_to_sell * current_price
                                        avg_buy_cost = avg_buy_price * eth_to_sell
                                        profit = sell_amount - avg_buy_cost
                                        available_budget += sell_amount  # 수익 추가
                                        save_available_budget(available_budget)  # 저장
                                        print(f"수익률 10% 이상으로 전체체 매도: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                                        record_trade("sell", "수익률 10% 이상으로 전체 매도", 100, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )
                                        break

                        print("1시간 대기 종료")

            elif ethprofit_rate < -2.5:
                print("손실률이 -2.5% 이상입니다. 매수를 진행합니다.")
                amount_to_buy = available_budget * 0.2 * 0.9995
                if amount_to_buy > 5000:
                    print(f"매수 진행: {amount_to_buy:.2f} KRW")
                    order_result = upbit.buy_market_order("KRW-ETH", amount_to_buy)
                    if order_result:
                        available_budget -= amount_to_buy
                        print(f"남은 사용 가능 금액: {available_budget:.2f} KRW")
                        # 사용 가능 금액 저장
                        save_available_budget(available_budget)
                        record_trade("buy", "수익률 -2.5% 보다 낮음으로 매수", 20, order_result, avg_buy_price, trade_amount=amount_to_buy)
                            
                        # buy_count를 글로벌 변수로 사용
                        buy_count += 1
                        print(f"연속 매수 횟수: {buy_count}")

                        if buy_count >= 2:
                            print("연속으로 두 번 매수가 발생했습니다. 4시간 대기 시작.")
                            start_wait_time = datetime.datetime.now()
                            while (datetime.datetime.now() - start_wait_time).total_seconds() < 14400:
                                remaining_time = 14400 - (datetime.datetime.now() - start_wait_time).total_seconds()                                    
                                time.sleep(20)
                                current_price = pyupbit.get_current_price("KRW-ETH")
                                ethprofit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100
                                print(f"대기 중- 남은 시간: {int(remaining_time // 60)}분 {int(remaining_time % 60)}초 - 현재 이더리움 수익률: {ethprofit_rate:.2f}%")
                                    
                                # 대기 중 손실률이 -7% 이상이면 전부 매도
                                if ethprofit_rate <= -7:
                                    print("대기 중 손실률이 -7% 이상입니다. 보유한 이더리움을 전부 매도합니다.")
                                    order_result = upbit.sell_market_order("KRW-ETH", my_eth)
                                    if order_result:
                                        sell_amount = eth_to_sell * current_price
                                        avg_buy_cost = avg_buy_price * eth_to_sell
                                        profit = sell_amount - avg_buy_cost
                                        available_budget += sell_amount  # 수익 추가
                                        save_available_budget(available_budget)  # 저장
                                        print(f"-7% 손실로 전부 매도: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                                        record_trade("sell", "-7% 손실로 전부 매도", 100, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )
                                        break
                            print("4시간 대기 종료. 매수 횟수 초기화.")
                            buy_count = 0

                        print("1시간 대기 시작")
                        start_wait_time = datetime.datetime.now()
                        while (datetime.datetime.now() - start_wait_time).total_seconds() < 3600:
                            remaining_time = 3600 - (datetime.datetime.now() - start_wait_time).total_seconds()
                            time.sleep(20)
                            current_price = pyupbit.get_current_price("KRW-ETH")
                            ethprofit_rate = ((current_price - avg_buy_price) / avg_buy_price) * 100
                            print(f"대기 중- 남은 시간: {int(remaining_time // 60)}분 {int(remaining_time % 60)}초- 현재 이더리움 수익률: {ethprofit_rate:.2f}%")
                                
                            # 대기 중 손실률이 -7% 이상이면 전부 매도
                            if ethprofit_rate <= -7:
                                print("대기 중 손실률이 -7% 이상입니다. 보유한 이더리움을 전부 매도합니다.")
                                order_result = upbit.sell_market_order("KRW-ETH", my_eth)
                                if order_result:
                                        sell_amount = eth_to_sell * current_price
                                        avg_buy_cost = avg_buy_price * eth_to_sell
                                        profit = sell_amount - avg_buy_cost
                                        available_budget += sell_amount  # 수익 추가
                                        save_available_budget(available_budget)  # 저장
                                        print(f"-7% 손실로 전부 매도: {eth_to_sell:.6f} ETH, 매도 금액: {sell_amount:.2f} KRW, 남은 예산: {available_budget:.2f} KRW, 수익: {profit:.2f} KRW")
                                        record_trade("sell", "-7% 손실로 전부 매도", 100, order_result, avg_buy_price, trade_amount=sell_amount, profit=profit )
                                break
                        print("1시간 대기 종료")
            else:
                time.sleep(20)

        except ZeroDivisionError:
            ethprofit_rate = 0  # 기본값 설정
        except Exception as e:
            print(f"이더리움compare_and_buy 실행 중 오류 발생: {e}")
            import traceback
            traceback.print_exc()
        finally:
            time.sleep(20)            

# --- schedule_trading 수정 ---
def schedule_trading():
    run_times = [
        {"hour": 3, "minute": 00},  # 오전 3시
        {"hour": 7, "minute": 00},  # 오전 7시
        {"hour": 11, "minute": 00}, # 오전 11시
        {"hour": 15, "minute": 00}, # 오후 3시
        {"hour": 19, "minute": 00}, # 오후 7시       
        {"hour": 23, "minute": 00}  # 오후 11시
    ]

    while True:
        now = datetime.datetime.now()
        next_run = None

        # 다음 실행 시간을 찾음
        for run_time in run_times:
            scheduled_time = now.replace(
                hour=run_time["hour"], minute=run_time["minute"], second=0, microsecond=0
            )
            if now < scheduled_time:
                next_run = scheduled_time
                break

        # 오늘의 시간이 모두 지났다면 다음날 첫 번째 시간으로 설정
        if not next_run:
            first_time = run_times[0]
            next_run = (now + datetime.timedelta(days=1)).replace(
                hour=first_time["hour"], minute=first_time["minute"], second=0, microsecond=0
            )

        # 다음 실행 시간까지 대기
        sleep_seconds = (next_run - now).total_seconds()
        print(f"다음 실행 시간: {next_run}")
        time.sleep(sleep_seconds)

        # 현재 시간을 ai_trading에 전달
        current_hour = next_run.hour
        current_minute = next_run.minute
        ai_trading(current_hour, current_minute)

# --- 멀티스레드 락 추가 ---
lock = threading.Lock()

# --- 멀티스레드 실행 ---
ai_trading_thread = threading.Thread(target=schedule_trading)
compare_and_buy_thread = threading.Thread(target=compare_and_buy)

# 글로벌 변수 초기화
available_budget = load_available_budget()  # 데이터베이스에서 사용 가능 금액 로드

# 프로그램 종료 시 사용 가능 금액 저장
atexit.register(on_exit_save_budget)

# 스레드 시작
ai_trading_thread.start()
compare_and_buy_thread.start()

def run_evaluate_trading_decisions():
    print("평가 실행 중...")
    evaluate_trading_decisions()

# 매일 오후 9시에 실행되도록 스케줄 설정
schedule.every().day.at("21:00").do(run_evaluate_trading_decisions)

def schedule_runner():
    while True:
        schedule.run_pending()
        time.sleep(1)

# 스케줄러를 별도 스레드에서 실행
scheduler_thread = threading.Thread(target=schedule_runner, daemon=True)
scheduler_thread.start()

# 프로그램이 실행 중임을 유지
print("스케줄러가 설정되었습니다. 프로그램을 종료하지 마세요.")

while True:
    time.sleep(60)