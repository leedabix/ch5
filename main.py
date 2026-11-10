
import re
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st
import requests

st.set_page_config(
    page_title="서울 지하철 데이터 대시보드",
    page_icon="🚇",
    layout="wide"
)

PAY_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/subway_pay.csv"
TIME_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/subway_time.csv"


# -----------------------------------
# 1. 데이터 불러오기
# -----------------------------------
@st.cache_data(ttl=3600)
def load_csv(url):
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    for encoding in ["utf-8-sig", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(
                BytesIO(response.content),
                encoding=encoding,
                low_memory=False
            )
        except UnicodeDecodeError:
            continue

    raise ValueError("CSV 파일의 인코딩을 확인해 주세요.")


def find_col(df, candidates):
    def normalize(s):
        return re.sub(r"\s+", "", str(s))

    columns = {normalize(c): c for c in df.columns}

    for candidate in candidates:
        if normalize(candidate) in columns:
            return columns[normalize(candidate)]

    raise ValueError(
        f"필요한 열을 찾지 못했습니다: {candidates}\n"
        f"실제 열: {list(df.columns)[:20]}"
    )


def numbers(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce"
    ).fillna(0)


def clean_station(series):
    return (
        series.astype(str)
        .str.replace(r"\([^)]*\)", "", regex=True)
        .str.strip()
    )


def clean_line(series):
    extracted = series.astype(str).str.extract(
        r"([1-9])호선"
    )[0]
    return extracted.map(
        lambda x: f"{x}호선" if pd.notna(x) else None
    )


def clean_month(series):
    text = (
        series.astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"[^0-9]", "", regex=True)
        .str[:6]
    )

    return pd.to_datetime(
        text + "01",
        format="%Y%m%d",
        errors="coerce"
    )


# -----------------------------------
# 2. 유무임 데이터 정리
# -----------------------------------
@st.cache_data(ttl=3600)
def load_pay_data():
    raw = load_csv(PAY_URL)

    month = find_col(raw, ["사용월"])
    line = find_col(raw, ["호선명", "호선"])
    station = find_col(raw, ["지하철역", "역명"])

    paid_in = find_col(raw, ["유임승차인원"])
    paid_out = find_col(raw, ["유임하차인원"])
    free_in = find_col(raw, ["무임승차인원"])
    free_out = find_col(raw, ["무임하차인원"])

    df = pd.DataFrame({
        "월": clean_month(raw[month]),
        "호선": clean_line(raw[line]),
        "역명": clean_station(raw[station]),
        "유임승차": numbers(raw[paid_in]),
        "유임하차": numbers(raw[paid_out]),
        "무임승차": numbers(raw[free_in]),
        "무임하차": numbers(raw[free_out])
    })

    df = df.dropna(subset=["월", "호선"])
    df = df[df["역명"] != ""]

    df["무임인원"] = df["무임승차"] + df["무임하차"]
    df["전체인원"] = (
        df["유임승차"] + df["유임하차"]
        + df["무임승차"] + df["무임하차"]
    )

    return df


# -----------------------------------
# 3. 시간대별 데이터 정리
# -----------------------------------
@st.cache_data(ttl=3600)
def load_time_data():
    raw = load_csv(TIME_URL)

    month = find_col(raw, ["사용월"])
    line = find_col(raw, ["호선명", "호선"])
    station = find_col(raw, ["지하철역", "역명"])

    base = pd.DataFrame({
        "월": clean_month(raw[month]),
        "호선": clean_line(raw[line]),
        "역명": clean_station(raw[station])
    })

    base = base.dropna(subset=["월", "호선"])
    valid_index = base.index

    # 시간대별 승차/하차 열을 자동 탐색
    # 형식 예: 04시-05시 승차인원
    # 또는 04:00:00~04:59:59 / Unnamed: 5
    groups = {}

    for i, col in enumerate(raw.columns):
        name = str(col)

        match = re.search(r"(\d{1,2})시", name)
        if not match:
            match = re.search(r"(\d{1,2}):\d{2}", name)

        if not match:
            continue

        hour = int(match.group(1)) % 24
        groups.setdefault(hour, {})

        if "승차" in name:
            groups[hour]["승차"] = col
        elif "하차" in name:
            groups[hour]["하차"] = col
        elif i + 1 < len(raw.columns):
            # 시간 열 다음에 하차 열이 있는 구조
            groups[hour]["승차"] = col
            groups[hour]["하차"] = raw.columns[i + 1]

    frames = []

    for hour, cols in sorted(groups.items()):
        if "승차" not in cols or "하차" not in cols:
            continue

        part = base.copy()
        part["시간"] = hour
        part["승차"] = numbers(
            raw.loc[valid_index, cols["승차"]]
        )
        part["하차"] = numbers(
            raw.loc[valid_index, cols["하차"]]
        )
        frames.append(part)

    if not frames:
        raise ValueError(
            "시간대별 승하차 열을 찾지 못했습니다. "
            f"실제 열 이름: {list(raw.columns)[:20]}"
        )

    return pd.concat(frames, ignore_index=True)


# -----------------------------------
# 4. 대시보드 시작
# -----------------------------------
st.title("🚇 서울 지하철 데이터 대시보드")
st.write(
    "서울 지하철 1~9호선의 무임 비율, "
    "시간대별 승하차 1위, 10년간 이용량 변화를 살펴봅니다."
)

try:
    with st.spinner("데이터를 불러오는 중입니다..."):
        pay = load_pay_data()
        time_df = load_time_data()
except Exception as e:
    st.error("데이터를 처리하는 중 오류가 발생했습니다.")
    st.exception(e)
    st.stop()

if pay.empty or time_df.empty:
    st.error("분석할 데이터가 없습니다.")
    st.stop()

latest_pay_month = pay["월"].max()
latest_time_month = time_df["월"].max()

tab1, tab2, tab3 = st.tabs([
    "💳 무임 비율 순위",
    "⏰ 시간대별 1위",
    "📈 10년 변화"
])


# -----------------------------------
# 5. 첫 번째 탭: 무임 비율 순위
# -----------------------------------
with tab1:
    st.subheader("무임 비율이 높은 지하철역")
    st.caption(f"기준: {latest_pay_month:%Y년 %m월}")

    top_n = st.slider(
        "상위 몇 개 역을 볼까요?",
        min_value=5,
        max_value=30,
        value=10
    )

    merge_station = st.checkbox(
        "환승역 합치기",
        value=True,
        help="같은 이름의 역은 호선이 달라도 하나로 합산합니다."
    )

    latest = pay[pay["월"] == latest_pay_month].copy()

    group_cols = (
        ["역명"] if merge_station
        else ["호선", "역명"]
    )

    ranking = (
        latest.groupby(group_cols, as_index=False)[
            ["무임인원", "전체인원"]
        ].sum()
    )

    ranking = ranking[ranking["전체인원"] > 0].copy()
    ranking["무임비율"] = (
        ranking["무임인원"] / ranking["전체인원"] * 100
    )

    if merge_station:
        ranking["역"] = ranking["역명"]
    else:
        ranking["역"] = (
            ranking["역명"] + " (" + ranking["호선"] + ")"
        )

    ranking = ranking.sort_values(
        ["무임비율", "무임인원"],
        ascending=[False, False]
    ).head(top_n)

    fig = px.bar(
        ranking.sort_values("무임비율"),
        x="무임비율",
        y="역",
        orientation="h",
        text="무임비율",
        title=f"무임 비율 상위 {len(ranking)}개 역",
        labels={"무임비율": "무임 비율 (%)"}
    )
    fig.update_traces(
        texttemplate="%{text:.1f}%",
        textposition="outside"
    )
    fig.update_layout(
        height=max(450, len(ranking) * 35 + 120)
    )
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        ranking[["역", "무임인원", "전체인원", "무임비율"]],
        hide_index=True,
        use_container_width=True
    )

    st.caption(
        "무임 비율 = 무임 승하차 인원 ÷ 전체 승하차 인원 × 100"
    )


# -----------------------------------
# 6. 두 번째 탭: 시간대별 1위
# -----------------------------------
with tab2:
    st.subheader("시간대별 승차·하차 1위 역")
    st.caption(f"기준: {latest_time_month:%Y년 %m월}")

    latest_time = time_df[
        time_df["월"] == latest_time_month
    ].copy()

    hours = sorted(
        latest_time["시간"].unique().astype(int)
    )

    selected_hour = st.select_slider(
        "시간대를 선택하세요.",
        options=hours,
        value=8 if 8 in hours else hours[0],
        format_func=lambda h: (
            f"{h:02d}:00~{(h + 1) % 24:02d}:00"
        )
    )

    selected = latest_time[
        latest_time["시간"] == selected_hour
    ]

    hourly = (
        selected.groupby("역명", as_index=False)[
            ["승차", "하차"]
        ].sum()
    )

    top_in = hourly.loc[hourly["승차"].idxmax()]
    top_out = hourly.loc[hourly["하차"].idxmax()]

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "🚉 승차 1위",
            top_in["역명"],
            f"{int(top_in['승차']):,}명"
        )

    with col2:
        st.metric(
            "🚉 하차 1위",
            top_out["역명"],
            f"{int(top_out['하차']):,}명"
        )

    top_names = set(
        hourly.nlargest(10, "승차")["역명"]
    ) | set(
        hourly.nlargest(10, "하차")["역명"]
    )

    chart_data = hourly[
        hourly["역명"].isin(top_names)
    ].melt(
        id_vars="역명",
        value_vars=["승차", "하차"],
        var_name="구분",
        value_name="인원"
    )

    fig = px.bar(
        chart_data,
        x="역명",
        y="인원",
        color="구분",
        barmode="group",
        title=f"{selected_hour:02d}시대 주요 역 승하차 비교"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(
        "선택한 시간대의 한 달 승하차 인원을 "
        "역 이름 기준으로 합산한 결과입니다."
    )


# -----------------------------------
# 7. 세 번째 탭: 10년 변화
# -----------------------------------
with tab3:
    st.subheader("지하철역의 10년간 이용량 변화")

    station_list = sorted(pay["역명"].unique())

    default_index = (
        station_list.index("서울역")
        if "서울역" in station_list else 0
    )

    selected_station = st.selectbox(
        "분석할 역을 선택하세요.",
        station_list,
        index=default_index
    )

    # 최신 월을 포함한 최근 120개월
    end_month = pay["월"].max()
    start_month = end_month - pd.DateOffset(months=119)

    station_data = pay[
        (pay["역명"] == selected_station)
        & (pay["월"] >= start_month)
        & (pay["월"] <= end_month)
    ]

    monthly = (
        station_data.groupby("월", as_index=False)["전체인원"]
        .sum()
        .sort_values("월")
    )

    if monthly.empty:
        st.warning("선택한 역의 데이터가 없습니다.")
    else:
        fig = px.line(
            monthly,
            x="월",
            y="전체인원",
            markers=True,
            title=f"{selected_station}의 월별 유동 인구 변화",
            labels={
                "월": "연월",
                "전체인원": "승하차 인원(명)"
            }
        )

        fig.update_layout(hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        col1, col2 = st.columns(2)

        with col1:
            st.metric(
                "최근 월 승하차 인원",
                f"{int(monthly.iloc[-1]['전체인원']):,}명"
            )

        with col2:
            st.metric(
                "데이터가 있는 월",
                f"{len(monthly)}개월"
            )

        st.caption(
            f"표시 범위: {start_month:%Y년 %m월} ~ "
            f"{end_month:%Y년 %m월}. "
            "유동 인구는 유임·무임 승하차 인원을 합한 값이며, "
            "실제 고유 이용자 수와는 다릅니다."
        )
