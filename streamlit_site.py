import os
import subprocess
import streamlit as st
import sqlite3
import pandas as pd
import datetime


def push_to_github():
    """Push the trades.db file to GitHub."""
    try:
        repo_path = "C:\\Users\\Public\\gptcoin"  # 로컬 저장소 경로
        os.chdir(repo_path)
        subprocess.run(["git", "add", "trades.db"], check=True)
        subprocess.run(["git", "commit", "-m", "Update database"], check=True)
        subprocess.run(["git", "push"], check=True)
        return "데이터베이스가 성공적으로 GitHub에 업데이트되었습니다!"
    except subprocess.CalledProcessError as e:
        return f"GitHub 업데이트 실패: {e}"


def fetch_table_data(table_name):
    """Fetch data from a given table."""
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
    st.title("AI코인 투자기록 (5분마다 업데이트)")

    # GitHub에 데이터베이스 업데이트 버튼
    if st.button("GitHub에 업데이트"):
        result = push_to_github()
        if "성공" in result:
            st.success(result)
        else:
            st.error(result)

    try:
        # 데이터 가져오기
        profit_data = fetch_table_data("profit_rates")
        ethprofit_data = fetch_table_data("ethprofit_rates")
        allprofit_data = fetch_table_data("allprofit_rates")
        btc_trades_recent = fetch_recent_trades("trades", days=3)
        eth_trades_recent = fetch_recent_trades("ethcoin_trades", days=3)
        btc_apology_data = fetch_table_data("btc_apology")
        eth_apology_data = fetch_table_data("eth_apology")
        btc_krw = fetch_table_data("global_state")
        eth_krw = fetch_table_data("ethcoin_global_state")

        # 전체 수익
        st.subheader("전체 수익")
        st.dataframe(allprofit_data)

        # 비트코인 수익과 이더리움 수익 나란히 표시
        st.subheader("코인별 수익")
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**비트코인 수익**")
            st.dataframe(profit_data)

        with col2:
            st.markdown("**이더리움 수익**")
            st.dataframe(ethprofit_data)

        # 매수 가능한 금액 나란히 표시
        st.subheader("매수 가능한 금액")
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**비트코인 잔액**")
            st.dataframe(btc_krw)

        with col2:
            st.markdown("**이더리움 잔액**")
            st.dataframe(eth_krw)

        # 거래내역
        st.subheader("비트코인 거래내역(최근 3일)")
        st.dataframe(btc_trades_recent)

        st.subheader("이더리움 거래내역(최근 3일)")
        st.dataframe(eth_trades_recent)

        # 거래 평가
        st.subheader("비트코인 거래에 대한 평가 (매일 오후 8시에 업데이트)")
        st.dataframe(btc_apology_data)

        st.subheader("이더리움 거래에 대한 평가 (매일 오후 9시에 업데이트)")
        st.dataframe(eth_apology_data)

    except Exception as e:
        st.error(f"Error loading data: {e}")


if __name__ == "__main__":
    main()








