
import re
from io import BytesIO

import pandas as pd
import plotly.express as px
import streamlit as st

st.set_page_config(
    page_title="서울 지하철 데이터 대시보드",
    page_icon="🚇",
    layout="wide",
)

PAY_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/subway_pay.csv"
TIME_URL = "https://raw.githubusercontent.com/greatsong/modudata/main/data/subway_time.csv"


# 1. 데이터 읽기
@st.cache_data(ttl=3600)
def load_csv(url):
    import requests

    response = requests.get(url, timeout=90)
    response.raise_for_status()

    for encoding in ["utf-8-sig", "cp949", "euc-kr"]:
        try:
            return pd.read_csv(
                BytesIO(response.content),
                encoding=encoding,
                low_memory=False,
            )
        except UnicodeDecodeError:
            continue

    raise ValueError("CSV 파일의 문자 인코딩을 확인해 주세요.")


def find_column(df, names):
    normalized = {
        re.sub(r"\s+", "", str(col)): col
        for col in df.columns
    }
    for name in names:
        key = re.sub(r"\s+", "", name)
        if key in normalized:
            return normalized[key]
    raise ValueError(
        f"필요한 열을 찾을 수 없습니다: {names}\n"
        f"실제 열 이름: {list(df.columns)[:15]}"
    )


def to_number(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0)


def clean_station(series):
    return (
        series.astype(str)
        .str.replace(r"\([^)]*\)", "", regex=True)
        .str.strip()
    )


def clean_line(series):
    # '9호선2~3단계' 등은 9호선으로 통합
    numbers = series.astype(str).str.extract(r"([1-9])호선")[0]
    return numbers.map(
        lambda x: f"{x}호선" if pd.notna(x) else None
    )


def clean_month(series):
    text = (
        series.astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"[^0-9]", "", regex=True)
    )
    return pd.to_datetime(
        text.str.slice(0, 6) + "01",
        format="%Y%m%d",
        errors="coerce",
    )


# 2. 유무임 데이터 정리
@st.cache_data(ttl=3600)
def prepare_pay():
    raw = load_csv(PAY_URL)

    month_col = find_column(raw, ["사용월", "사용일자", "날짜"])
    line_col = find_column(raw, ["호선명", "호선"])
    station_col = find_column(raw, ["지하철역", "역명"])

    free_in = find_column(raw, ["무임승차인원", "무임승차"])
    free_out = find_column(raw, ["무임하차인원", "무임하차"])
    paid_in = find_column(raw, ["유임승차인원", "유임승차"])
    paid_out = find_column(raw, ["유임하차인원", "유임하차"])

    df = pd.DataFrame({
        "월": clean_month(raw[month_col]),
        "호선": clean_line(raw[line_col]),
        "역명": clean_station(raw[station_col]),
        "무임승차": to_number(raw[free_in]),
        "무임하차": to_number(raw[free_out]),
        "유임승차": to_number(raw[paid_in]),
        "유임하차": to_number(raw[paid_out]),
    })

    df = df.dropna(subset=["월", "호선"])
    df["무임인원"] = df["무임승차"] + df["무임하차"]
    df["전체인원"] = (
        df["무임인원"] + df["유임승차"] + df["유임하차"]
    )
    return df


# 3. 시간대별 데이터 정리
@st.cache_data(ttl=3600)
def prepare_time():
    raw = load_csv(TIME_URL)

    month_col = find_column(raw, ["사용월", "사용일자", "날짜"])
    line_col = find_column(raw, ["호선명", "호선"])
    station_col = find_column(raw, ["지하철역", "역명"])

    base = pd.DataFrame({
        "월": clean_month(raw[month_col]),
        "호선": clean_line(raw[line_col]),
        "역명": clean_station(raw[station_col]),
    })

    # 시간 열을 원본의 등장 순서대로 수집
    # 예: 04:00:00~04:59:59, Unnamed: 5
    # 첫 번째는 승차, 두 번째는 하차
    columns = list(raw.columns)
    time_columns = []

    for i, col in enumerate(columns):
        match = re.search(r"(\d{1,2}):\d{2}", str(col))
        if match and i + 1 < len(columns):
            hour = int(match.group(1)) % 24
            time_columns.append((hour, col, columns[i + 1]))

    if not time_columns:
        # '04시-05시 승차인원'처럼 단일 헤더인 경우
        for col in columns:
            name = str(col)
            match = re.search(r"(\d{1,2})시", name)
            if match and ("승차" in name or "하차" in name):
                hour = int(match.group(1)) % 24
                kind = "승차" if "승차" in name else "하차"
                time_columns.append((hour, kind, col))

        if not time_columns:
            raise ValueError(
                "시간대별 열을 찾을 수 없습니다. "
                f"실제 열 이름: {columns[:15]}"
            )

        frames = []
        for hour in sorted(set(x[0] for x in time_columns)):
            part = base.copy()
            part["시간"] = hour
            part["승차"] = 0
            part["하차"] = 0
            for h, kind, col in time_columns:
                if h == hour:
                    part[kind] = to_number(raw[col])
            frames.append(part)
    else:
        frames = []
        for hour, in_col, out_col in time_columns:
            part = base.copy()
            part["시간"] = hour
            part["승차"] = to_number(raw[in_col])
            part["하차"] = to_number(raw[out_col])
            frames.append(part)

    df = pd.concat(frames, ignore_index=True)
    return df.dropna(subset=["월", "호선"])


# 4. 화면 시작
st.title("🚇 서울 지하철 데이터 대시보드")
st.write("서울 지하철 1~9호선의 무임 승하차, 시간대별 이용량, 월별 유동 인구를 분석합니다.")

try:
    with st.spinner("지하철 데이터를 불러오는 중입니다..."):
        pay = prepare_pay()
        time_df = prepare_time()
except Exception as e:
    st.error("데이터를 불러오거나 정리하는 중 오류가 발생했습니다.")
    st.exception(e)
    st.stop()

if pay.empty or time_df.empty:
    st.error("분석할 데이터가 없습니다.")
    st.stop()

pay_month = pay["월"].max()
time_month = time_df["월"].max()

pay_latest = pay[pay["월"] == pay_month].copy()
time_latest = time_df[time_df["월"] == time_month].copy()

tab1, tab2, tab3 = st.tabs([
    "💳 무임 비율 순위",
    "⏰ 시간대별 승하차",
    "📈 월별 유동 인구",
])


# 5. 첫 번째 탭: 무임 비율
with tab1:
    st.subheader("무임 승하차 비율이 높은 역")
    st.caption(f"분석 기준: {pay_month:%Y년 %m월}")

    top_n = st.slider(
        "상위 몇 개 역을 볼까요?",
        min_value=5,
        max_value=30,
        value=10,
        step=1,
    )

    merge = st.checkbox(
        "환승역을 하나로 합치기",
        value=True,
        help="같은 이름의 역은 호선이 달라도 합산합니다.",
    )

    group_cols = ["역명"] if merge else ["호선", "역명"]

    ranking = (
        pay_latest.groupby(group_cols, as_index=False)[
            ["무임인원", "전체인원"]
        ]
        .sum()
    )

    ranking = ranking[ranking["전체인원"] > 0].copy()
    ranking["무임비율"] = (
        ranking["무임인원"] / ranking["전체인원"] * 100
    )

    if merge:
        ranking["표시역"] = ranking["역명"]
    else:
        ranking["표시역"] = (
            ranking["역명"] + " (" + ranking["호선"] + ")"
        )

    ranking = ranking.sort_values(
        ["무임비율", "무임인원"],
        ascending=[False, False],
    ).head(top_n)

    fig = px.bar(
        ranking.sort_values("무임비율"),
        x="무임비율",
        y="표시역",
        orientation="h",
        text="무임비율",
        labels={"무임비율": "무임 비율 (%)", "표시역": "역"},
        title=f"무임 비율 상위 {len(ranking)}개 역",
    )
    fig.update_traces(texttemplate="%{text:.1f}%", textposition="outside")
    fig.update_layout(height=max(450, len(ranking) * 35 + 120))
    st.plotly_chart(fig, use_container_width=True)

    st.dataframe(
        ranking[["표시역", "무임인원", "전체인원", "무임비율"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "무임인원": st.column_config.NumberColumn(format="%d명"),
            "전체인원": st.column_config.NumberColumn(format="%d명"),
            "무임비율": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
    st.caption("무임 비율 = (무임 승차 + 무임 하차) ÷ 전체 승하차 × 100")


# 6. 두 번째 탭: 시간대별 승하차
with tab2:
    st.subheader("시간대별 승차·하차 1위 역")
    st.caption(f"분석 기준: {time_month:%Y년 %m월}")

    available_hours = sorted(time_latest["시간"].unique().astype(int))
    selected_hour = st.select_slider(
        "분석할 시간대를 선택하세요.",
        options=available_hours,
        value=8 if 8 in available_hours else available_hours[0],
        format_func=lambda h: f"{h:02d}:00~{(h + 1) % 24:02d}:00",
    )

    selected = time_latest[
        time_latest["시간"] == selected_hour
    ].copy()

    hourly = selected.groupby("역명", as_index=False)[
        ["승차", "하차"]
    ].sum()

    top_in = hourly.sort_values("승차", ascending=False).iloc[0]
    top_out = hourly.sort_values("하차", ascending=False).iloc[0]

    col1, col2 = st.columns(2)
    with col1:
        st.metric("🚉 승차 1위", top_in["역명"], f"{int(top_in['승차']):,}명")
    with col2:
        st.metric("🚉 하차 1위", top_out["역명"], f"{int(top_out['하차']):,}명")

    top_stations = set(
        hourly.nlargest(10, "승차")["역명"]
    ) | set(
        hourly.nlargest(10, "하차")["역명"]
    )

    chart_df = hourly[
        hourly["역명"].isin(top_stations)
    ].melt(
        id_vars="역명",
        value_vars=["승차", "하차"],
        var_name="구분",
        value_name="인원",
    )

    fig = px.bar(
        chart_df,
        x="역명",
        y="인원",
        color="구분",
        barmode="group",
        title=f"{selected_hour:02d}시대 주요 역 승하차 비교",
        labels={"인원": "인원(명)"},
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption("해당 시간대의 한 달 승하차 인원을 역 이름 기준으로 합산했습니다.")


# 7. 세 번째 탭: 월별 유동 인구
with tab3:
    st.subheader("역별 월간 유동 인구 변화")

    station_list = sorted(pay["역명"].unique().tolist())
    default_station = (
        station_list.index("서울역")
        if "서울역" in station_list else 0
    )

    selected_station = st.selectbox(
        "분석할 역을 선택하세요.",
        station_list,
        index=default_station,
    )

    station_df = pay[
        pay["역명"] == selected_station
    ].copy()

    monthly = (
        station_df.groupby("월", as_index=False)["전체인원"]
        .sum()
        .sort_values("월")
    )

    fig = px.line(
        monthly,
        x="월",
        y="전체인원",
        markers=True,
        title=f"{selected_station} 월별 유동 인구",
        labels={"월": "월", "전체인원": "승하차 인원(명)"},
    )
    fig.update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    if not monthly.empty:
        st.metric(
            "최근 집계 월의 승하차 인원",
            f"{int(monthly.iloc[-1]['전체인원']):,}명",
        )

    st.caption(
        "유동 인구 = 유임 승하차 + 무임 승하차. "
        "실제 고유 이용자 수가 아니라 승하차 건수의 합계입니다."
    )
