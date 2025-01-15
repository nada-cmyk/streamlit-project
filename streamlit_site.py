import streamlit as st
import sqlite3
import pandas as pd
import datetime

def fetch_table_data(table_name):
    """Fetch all data from a given table."""
    conn = sqlite3.connect("trades.db")
    query = f"SELECT * FROM {table_name}"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def fetch_recent_trades(table_name, days=3):
    """Fetch recent N days of data from a given table."""
    conn = sqlite3.connect("trades.db")
    now = datetime.datetime.now()
    past_date = now - datetime.timedelta(days=days)
    query = f"""
        SELECT * FROM {table_name}
        WHERE timestamp >= '{past_date.isoformat()}'
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

def main():
    st.title("AI코인 투자기록")

    try:
        # 데이터 가져오기
        profit_data = fetch_table_data("profit_rates")
        ethprofit_data = fetch_table_data("ethprofit_rates")
        allprofit_data = fetch_table_data("allprofit_rates")
        btc_trades_recent = fetch_recent_trades("trades", days=3)  # 최근 3일 비트코인 거래내역
        eth_trades_recent = fetch_recent_trades("ethcoin_trades", days=3)  # 최근 3일 이더리움 거래내역
        btc_apology_data = fetch_table_data("btc_apology")
        eth_apology_data = fetch_table_data("eth_apology")
        btc_krw = fetch_table_data("global_state")
        eth_krw = fetch_table_data("ethcoin_global_state")

        # 전체 수익
        st.subheader("전체 수익") 
        st.dataframe(allprofit_data)

        # 비트코인 수익과 이더리움 수익 나란히 표시
        st.subheader("코인별 수익")
        col1, col2 = st.columns(2)  # 두 개의 열 생성

        with col1:
            st.markdown("**비트코인 수익**")
            st.dataframe(profit_data)

        with col2:
            st.markdown("**이더리움 수익**")
            st.dataframe(ethprofit_data)

        # 매수 가능한 금액 나란히 표시
        st.subheader("매수 가능한 금액")
        col1, col2 = st.columns(2)  # 두 개의 열 생성

        with col1:
            st.markdown("**비트코인 잔액**")
            st.dataframe(btc_krw)

        with col2:
            st.markdown("**이더리움 잔액**")
            st.dataframe(eth_krw)

        # 최근 3일 비트코인 거래내역
        st.subheader("비트코인 거래내역 (최근 3일)")
        st.dataframe(btc_trades_recent)

        # 최근 3일 이더리움 거래내역
        st.subheader("이더리움 거래내역 (최근 3일)")
        st.dataframe(eth_trades_recent)

        # 거래 평가
        st.subheader("비트코인 거래에 대한 평가 (매일 오후 8시에 업데이트)")
        st.dataframe(btc_apology_data)

        st.subheader("이더리움 거래에 대한 평가 (매일 오후 9시에 업데이트)")
        st.dataframe(eth_apology_data)

    except Exception as e:
        st.error(f"Error loading data: {e}")

    # Sidebar for navigation
    st.sidebar.title("Navigation")
    table_options = [ 
        "profit_rates",
        "allprofit_rates",
        "ethprofit_rates"  # 추가된 테이블
    ]
    selected_table = st.sidebar.selectbox(
        "Select a table to view", 
        table_options, 
        index=table_options.index("profit_rates")  # 기본값을 "profit_rates"로 설정
    )

    # Display selected table data
    if selected_table not in ["profit_rates", "ethprofit_rates", "allprofit_rates"]:
        st.subheader(f"Table: {selected_table}")
        try:
            data = fetch_table_data(selected_table)
            st.dataframe(data)
        except Exception as e:
            st.error(f"Error loading data: {e}")

    # Additional features: Display schema
    st.sidebar.title("Table Schema")
    if st.sidebar.button("Show Schema"):
        try:
            conn = sqlite3.connect("trades.db")
            query = f"PRAGMA table_info({selected_table})"
            schema = pd.read_sql_query(query, conn)
            conn.close()
            st.sidebar.write(schema)
        except Exception as e:
            st.sidebar.error(f"Error fetching schema: {e}")

if __name__ == "__main__":
    main()







