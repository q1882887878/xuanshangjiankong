"""
趣闲赚 (QuXianZhuan) Spider - vwap.huayingrc.com
任务列表 API: POST /reward/list/ (form-urlencoded)
任务详情: GET /reward/{id}/ (HTML embedded Vue SSR data)
"""
import sys, io, urllib.request, urllib.parse, json, re, sqlite3, time, uuid
from pathlib import Path
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── Config ──
BASE_URL = "https://wap.huayingrc.com"
DB_PATH = Path(__file__).parent / "tasks.db"
PAGE_SIZE = 20
MAX_PAGES = 50
SOURCE_NAME = "quxian"  # 趣闲赚

# ── Cookie (user-provided, needs refresh periodically) ──
COOKIE = "tzb_user_cryptograph=16166236%3Apn69jwXEZLkJlGh8ydQK; tzb_session=ljkelkmadi3hmrnmbvfav02o478205rt; tzb_formhash_cookie=486be00a214d3fa85d6751174d6f0439"
FORMHASH = "486be00a214d3fa85d6751174d6f0439"

# ── Category mapping ──
CATEGORY_MAP = {
    "zcxz": "注册下载",
    "ggqf": "高价付费",
    "gzdz": "关注点赞",
    "rzbk": "日报快看",
    "tdrw": "团队任务",
    "ftzf": "分摊支付",
    "tppl": "投票评论",
    "dsxg": "电商相关",
    "eshs": "二手回收",
    "yysd": "应用商店",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
    "Cookie": COOKIE,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"{BASE_URL}/reward/list/",
    "Origin": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# ── Database ──
def db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        source TEXT, task_id TEXT, title TEXT, money REAL,
        advertiser_name TEXT, avatar_url TEXT,
        current_stock INTEGER, success_count INTEGER,
        category_id INTEGER, category_name TEXT,
        audit_fast INTEGER, vip_level INTEGER,
        fetched_at TEXT, cancel_home_time TEXT,
        remark TEXT, steps_json TEXT,
        audit_time TEXT, task_time TEXT,
        task_count INTEGER, max_stock INTEGER,
        detail_fetched_at TEXT,
        PRIMARY KEY (source, task_id))""")
    return conn

def save_task(conn, task):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("""
        INSERT INTO tasks (source, task_id, title, money, advertiser, avatar,
            current_stock, success_count, category_id, category_name,
            vip_level, fetched_at, expire_time, max_stock)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, task_id) DO UPDATE SET
            title=excluded.title, money=excluded.money,
            advertiser=excluded.advertiser, avatar=excluded.avatar,
            current_stock=excluded.current_stock, success_count=excluded.success_count,
            category_name=excluded.category_name, vip_level=excluded.vip_level,
            fetched_at=excluded.fetched_at, max_stock=excluded.max_stock
    """, (
        SOURCE_NAME,
        task.get("reward_id"),
        task.get("reward_title"),
        float(task.get("apply_price", 0)),
        task.get("tags_name", ""),
        task.get("avatar", ""),
        int(task.get("surplus_votes", 0)),
        int(task.get("finish_votes", 0)),
        0,  # category_id (趣闲用 cat_code 不是数字)
        task.get("cat_name", ""),
        int(task.get("vip_id") or 0),
        now,
        # Convert Unix timestamp to formatted date string
        _tet = task.get("top_end_time") or ""
        _tet_str = ""
        if _tet:
            try:
                from datetime import datetime as _dt
                _tet_str = _dt.fromtimestamp(int(_tet)).strftime("%Y-%m-%d %H:%M:%S")
            except:
                _tet_str = str(_tet)
        _tet_str,
        int(task.get("total_votes") or 0),
    ))

def fetch_task_list(page=1, cat_id="0", search=""):
    """获取任务列表"""
    send_data = {
        "page": str(page),
        "cat_id": cat_id,
        "type": "0",
        "rand": f"0.{uuid.uuid4().hex[:10]}",
        "limit": str(PAGE_SIZE),
        "search": search,
        "search_type": "top",
        "level": "0",
        "refresh_page": str(page),
        "tags_id": "0",
        "exclusive": "0",
        "integral_id": "",
        "integral_type": "top",
        "is_game2": "0",
        "formhash": FORMHASH,
    }
    data = urllib.parse.urlencode(send_data).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/reward/list/", data=data, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        result = json.loads(body)
        if result.get("state") == 1:
            return result.get("reward_list", [])
    return []

def fetch_task_detail(reward_id):
    """获取任务详情（从 HTML 解析 Vue SSR 数据）"""
    url = f"{BASE_URL}/reward/{reward_id}/"
    req = urllib.request.Request(url, headers={
        "User-Agent": HEADERS["User-Agent"],
        "Cookie": COOKIE,
        "Accept": "text/html,*/*",
        "Referer": f"{BASE_URL}/reward/list/",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    
    # Extract reward_info JSON
    m = re.search(r'reward_info\s*:\s*(\{.*?\})\s*,\s*\n', html, re.DOTALL)
    if not m:
        return None
    
    try:
        reward_info = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    
    # Extract steps JSON array
    steps = []
    steps_match = re.search(r'step_list\s*:\s*(\[.*?\])\s*,\s*\n', html, re.DOTALL)
    if steps_match:
        try:
            steps = json.loads(steps_match.group(1))
        except json.JSONDecodeError:
            pass
    
    return {
        "info": reward_info,
        "steps": steps,
    }

# ── Main ──
if __name__ == "__main__":
    conn = db()
    total = 0
    
    print(f"趣闲赚爬虫启动")
    print(f"开始抓取任务列表...")
    
    for page in range(1, MAX_PAGES + 1):
        tasks = fetch_task_list(page)
        if not tasks:
            print(f"  第{page}页: 无数据，停止翻页")
            break
        
        for task in tasks:
            save_task(conn, task)
            total += 1
        
        print(f"  第{page}页: {len(tasks)}条, 累计{total}条")
        
        if len(tasks) < PAGE_SIZE:
            print(f"  最后一页，停止翻页")
            break
        
        time.sleep(0.5)
    
    conn.commit()
    
    # Show stats
    row = conn.execute(f"SELECT COUNT(*) FROM tasks WHERE source='{SOURCE_NAME}'").fetchone()
    print(f"\n完成！数据库中共 {row[0]} 条趣闲赚任务")
    
    # Show category distribution
    rows = conn.execute(f"SELECT category_name, COUNT(*) FROM tasks WHERE source='{SOURCE_NAME}' GROUP BY category_name ORDER BY COUNT(*) DESC").fetchall()
    for cat, cnt in rows:
        print(f"  {cat or '未分类'}: {cnt}")
    
    conn.close()
