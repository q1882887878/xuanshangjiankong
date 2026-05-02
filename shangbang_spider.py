"""
赏帮赚任务爬虫
=============
API: https://gateway.shangbangzhuan.com/task/search
功能: 定时抓取赏帮赚任务列表，存入 SQLite 数据库

使用方法:
  1. 修改下方 ACCOUNT / CREDENTIALS 为你的手机号和密码MD5
  2. python shangbang_spider.py          # 单次运行
  3. python shangbang_spider.py --loop   # 循环模式（每60秒一轮）
"""

import sys, io, os, json, time, hashlib, sqlite3, logging, urllib.request, urllib.parse
from datetime import datetime
from pathlib import Path

# ── Windows 编码修复 ──
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# ── 配置 ──
ACCOUNT = "18681624624"           # 赏帮赚手机号
CREDENTIALS = "EBCBF97EC1D80C0388D39BF508039BAA"  # 密码的 MD5
APP_KEY = "000000"
DEVICE_ID = str(int(time.time() * 1000)) + "802"

BASE_URL = "https://gateway.shangbangzhuan.com"
DB_PATH = Path(__file__).parent / "tasks.db"
PAGE_SIZE = 100
MAX_PAGES = 20       # 最多抓几页（每页100条，最多2000条/轮）
LOOP_INTERVAL = 60    # 循环间隔（秒）

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("shangbang")

# ── HTTP 工具 ──
COMMON_HEADERS = {
    "device": "ios",
    "deviceinfo": "Linux; Android 6.0; Nexus 5 Build/MRA58N",
    "appKey": APP_KEY,
    "version": "2.01",
    "Origin": "https://m.shangbangzhuan.com",
    "Referer": "https://m.shangbangzhuan.com/",
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
                  "AppleWebKit/537.36 Chrome/147.0.0.0 Mobile Safari/537.36",
}


def api_get(path: str, params: dict = None, token: str = None) -> dict:
    """发起 GET 请求，返回解析后的 JSON"""
    url = f"{BASE_URL}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {**COMMON_HEADERS}
    if token:
        headers["uuid"] = token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 数据库 ──
def init_db():
    """初始化 SQLite 数据库"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            source       TEXT NOT NULL DEFAULT 'shangbang',
            task_id      INTEGER NOT NULL,
            title        TEXT,
            advertiser   TEXT,
            money        REAL,
            current_stock INTEGER,
            success_count INTEGER,
            category_id  INTEGER,
            audit_fast   INTEGER,
            vip_level    INTEGER,
            user_id      INTEGER,
            recommend    INTEGER,
            expire_time  TEXT,
            avatar       TEXT,
            fetched_at   TEXT NOT NULL,
            PRIMARY KEY (source, task_id)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_money ON tasks(money DESC)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_fetched ON tasks(fetched_at DESC)
    """)
    conn.commit()
    return conn


def upsert_tasks(conn, tasks: list) -> int:
    """插入或更新任务列表，返回受影响行数"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for t in tasks:
        rows.append((
            "shangbang",
            t.get("id"),
            t.get("title", ""),
            t.get("name", ""),
            t.get("money", 0),
            t.get("currentStock", 0),
            t.get("success", 0),
            t.get("categoryId"),
            t.get("auditFast", 0),
            t.get("vipLevel", 0),
            t.get("userId"),
            1 if t.get("recommend") else 0,
            t.get("cancelHomeTime", ""),
            t.get("avatar", ""),
            now,
        ))
    conn.executemany("""
        INSERT INTO tasks (source, task_id, title, advertiser, money,
            current_stock, success_count, category_id, audit_fast,
            vip_level, user_id, recommend, expire_time, avatar, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, task_id) DO UPDATE SET
            title=excluded.title,
            money=excluded.money,
            current_stock=excluded.current_stock,
            success_count=excluded.success_count,
            audit_fast=excluded.audit_fast,
            expire_time=excluded.expire_time,
            fetched_at=excluded.fetched_at
    """, rows)
    conn.commit()
    return len(rows)


# ── 登录 ──
def login() -> str:
    """登录并返回 JWT token (uuid)"""
    params = {
        "account": ACCOUNT,
        "credentials": CREDENTIALS,
        "appKey": APP_KEY,
        "deviceId": DEVICE_ID,
    }
    data = api_get("user/loginForWeb", params)
    if data.get("code") != 200:
        raise RuntimeError(f"登录失败: {data.get('message')}")
    token = data["data"]["uuid"]
    log.info(f"登录成功: {data['data'].get('nickName', '?')} (ID:{data['data'].get('id')})")
    return token


# ── 抓取 ──
def fetch_tasks(token: str, page: int = 1) -> list:
    """抓取指定页的任务列表"""
    params = {"current": str(page), "size": str(PAGE_SIZE)}
    data = api_get("task/search", params, token=token)
    if data.get("code") != 200:
        log.warning(f"task/search 返回异常: {data.get('code')} {data.get('message')}")
        return []
    return data.get("data", [])


def fetch_all(token: str, conn) -> int:
    """抓取所有页面，返回总入库条数"""
    total = 0
    for page in range(1, MAX_PAGES + 1):
        tasks = fetch_tasks(token, page)
        if not tasks:
            log.info(f"第{page}页无数据，停止翻页")
            break
        count = upsert_tasks(conn, tasks)
        total += count
        log.info(f"第{page}页: {len(tasks)}条任务，入库{count}条")
        if len(tasks) < PAGE_SIZE:
            break
        time.sleep(0.5)  # 礼貌间隔
    return total


# ── 主流程 ──
def run_once(token: str, conn) -> int:
    """执行一轮抓取"""
    start = time.time()
    total = fetch_all(token, conn)
    elapsed = time.time() - start
    log.info(f"本轮完成: {total}条任务入库, 耗时{elapsed:.1f}s")
    return total


def main():
    loop_mode = "--loop" in sys.argv

    # 初始化数据库
    conn = init_db()
    log.info(f"数据库: {DB_PATH}")

    # 登录
    token = login()

    if loop_mode:
        log.info(f"循环模式启动, 间隔{LOOP_INTERVAL}秒")
        while True:
            try:
                run_once(token, conn)
            except Exception as e:
                log.error(f"抓取异常: {e}")
                # 尝试重新登录
                try:
                    token = login()
                except Exception as e2:
                    log.error(f"重新登录失败: {e2}")
            time.sleep(LOOP_INTERVAL)
    else:
        run_once(token, conn)

    conn.close()


if __name__ == "__main__":
    main()
