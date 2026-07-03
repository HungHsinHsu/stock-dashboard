"""追蹤清單體質掃描：把回檔承接法(＋趨勢健康關)套在『使用者追蹤清單』每一檔，
依『離進場多近』由近到遠排序，每檔標進場／觀望／避開，推到 Telegram 並存 DB
供網頁/機器人直接讀。

跟 jobs/screen 的差別：universe 是追蹤清單(不是成交前 150 大)，且外資查不到
不剔除、保留成觀望(自己的清單本來就要全部看得到)。
"""
from core.data import fetch_daily, fetch_foreign_flow
from core.screener import scan
from core.watchlist import all_tracked_stocks
from core.config import DASHBOARD_URL
from core.tz import now_tw
from core.barstore import dump_bars
import core.telegram as tg

STATE_KEY = "watch:latest"


def _store_bars(db, bars):
    """把各追蹤股日線存進 DB（key＝bars:代號），網頁讀這份、不用自己去打證交所。"""
    n = 0
    for c, dfc in bars.items():
        try:
            db.set_state(f"bars:{c}", dump_bars(dfc))
            n += 1
        except Exception as e:
            print(f"存日線失敗 {c}：", e)
    if n:
        print(f"[watch] 已存 {n} 檔日線供網頁讀")


def _line(x, names):
    nm = names.get(x["code"], x["code"])
    where = x.get("at_batch") or x["kind"]
    trend = x.get("trend", "")
    trend_txt = f"〔{trend}〕" if trend else ""
    return f"・[{x['signal']}] {nm} ({x['code']}){trend_txt}：{where}｜{x['reason']}"


def _digest(date, cands, names):
    stocks = [x for x in cands if x.get("kind") != "ETF"]
    etfs = [x for x in cands if x.get("kind") == "ETF"]
    lines = [
        f"⭐ 追蹤清單體質掃描（依『離進場多近』排序）— {date}",
        "📏 排最前＝最接近進場：進場>觀望>避開 ＞ 回檔到支撐 ＞ 收盤站穩 ＞ 量縮 ＞ 離均線近。",
        "",
        "📈 個股：",
    ]
    lines += [_line(x, names) for x in stocks] or ["・（清單裡沒有個股）"]
    if etfs:
        lines += ["", "📦 ETF（趨勢參考）："]
        lines += [_line(x, names) for x in etfs]
    lines += ["", "（進場＝四關到位可接；觀望＝趨勢沒破在等，排越前越接近；避開＝跌破季線）",
              "🕒 收盤快照、盤中不更新；「等站穩」＝隔日回到支撐、收盤站穩再分批接。",
              f"🔗 {DASHBOARD_URL}"]
    return "\n".join(lines)


def run(notify=True, fetch=None, stocks=None, limit=50, pause=0.05):
    from core import db
    db.migrate_owner_data()
    date = str(now_tw().date())
    stocks = all_tracked_stocks() if stocks is None else stocks
    names = {cfg["code"]: name for name, cfg in stocks.items()}
    codes = list(names.keys())
    got = {"ok": 0}

    bars = {}

    def _f(c):
        df = (fetch or (lambda x: fetch_daily(x, months=12, workers=2)))(c)
        if df is not None and not getattr(df, "empty", True):
            got["ok"] += 1
            bars[str(c)] = df                 # 順手留一份日線，等下存 DB 給網頁讀
        return df

    cands = scan(codes, fetch=_f, foreign_lookup=fetch_foreign_flow,
                 limit=limit, etf_limit=limit, pause=pause,
                 drop_incomplete=False) if codes else []
    result = {"date": date, "n": len(codes), "fetched_n": got["ok"],
              "names": names, "cands": cands}
    if codes:
        try:
            db.set_state(STATE_KEY, result)
        except Exception as e:
            print("存追蹤掃描結果失敗：", e)
        _store_bars(db, bars)                 # 存各追蹤股日線，網頁改讀 DB、不再自己抓證交所
        if fetch is None:                     # 真的在跑(非測試注入)才順手存大盤指數日線
            try:
                from core.data import fetch_index
                idx = fetch_index(months=12)
                if idx is not None and not getattr(idx, "empty", True):
                    db.set_state("bars:_index", dump_bars(idx))
                    print("[watch] 已存大盤指數日線")
            except Exception as e:
                print("存指數日線失敗：", e)
    print(f"[watch] date={date} 清單={len(codes)} 讀取成功={got['ok']} 排出={len(cands)}")
    for x in cands:
        print(f"[watch] {names.get(x['code'], x['code'])} ({x['code']}) "
              f"[{x['signal']}] {x.get('trend', '')} | {x.get('at_batch') or x['kind']}")
    if notify:
        if cands:
            tg.send(_digest(date, cands, names))
        elif not codes:
            tg.send("⭐ 追蹤清單是空的——先用 /add 代號 或網頁『管理追蹤』加股票，再掃。")
        else:
            tg.send(f"⭐ 追蹤清單掃描：{len(codes)} 檔都抓不到資料（TWSE 沒回應），稍後再試。")
    return result


if __name__ == "__main__":
    run()
