"""
任务雷达 - 自动爬虫脚本
=======================
运行赏帮+趣闲爬虫，检查 web 服务器状态
由 cron 定时任务每30分钟调用一次
"""
import sys, io, subprocess, socket, time, sqlite3, json, os, urllib.request
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT = Path(__file__).parent
DB = PROJECT / "tasks.db"
WEB_PORT = int(os.environ.get("PORT", 5000))
SHANGBANG = PROJECT / "shangbang_spider.py"
QUXIAN = PROJECT / "quxian_spider.py"
LOG_FILE = PROJECT / "spider_log.txt"

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def is_port_open(port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except:
        return False

def run_spider(script_path, name, timeout=300):
    """运行爬虫脚本，返回 (成功, 输出)"""
    for attempt in range(2):
        try:
            log(f"  启动 {name}{'  (重试)' if attempt else ''}...")
            result = subprocess.run(
                [sys.executable, "-u", str(script_path)],
                capture_output=True, text=True, timeout=timeout,
                cwd=str(PROJECT), encoding="utf-8", errors="replace"
            )
            output = (result.stdout or "") + (result.stderr or "")
            lines = output.strip().split("\n")
            summary = "\n".join(lines[-5:])
            if result.returncode == 0:
                log(f"  ✅ {name} 完成")
                for line in lines[-3:]:
                    log(f"    {line}")
                return True, output
            else:
                if attempt == 0:
                    log(f"  ⚠️ {name} 失败，3秒后重试...")
                    time.sleep(3)
                    continue
                log(f"  ⚠️ {name} 返回码 {result.returncode}")
                log(f"    {summary[:200]}")
                return False, output
        except subprocess.TimeoutExpired:
            if attempt == 0:
                log(f"  ⚠️ {name} 超时，重试...")
                continue
            log(f"  ❌ {name} 超时 ({timeout}s)")
            return False, "timeout"
        except Exception as e:
            log(f"  ❌ {name} 异常: {e}")
            return False, str(e)
    return False, "max retries"

def ensure_web_server():
    """确保 web 服务器在运行"""
    if is_port_open(WEB_PORT):
        log(f"  ✅ Web 服务器已在运行 (port {WEB_PORT})")
        return True
    
    log("  ⚠️ Web 服务器未运行，正在启动...")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-u", str(PROJECT / "web_server.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(PROJECT)
        )
        time.sleep(3)
        if is_port_open(WEB_PORT):
            log(f"  ✅ Web 服务器已启动 (PID: {proc.pid})")
            return True
        else:
            log(f"  ❌ Web 服务器启动失败")
            return False
    except Exception as e:
        log(f"  ❌ 启动失败: {e}")
        return False

def init_db():
    """Initialize database tables if they don't exist"""
    conn = sqlite3.connect(str(DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        source TEXT, task_id TEXT, title TEXT, money REAL,
        advertiser TEXT, avatar TEXT, current_stock INTEGER,
        success_count INTEGER, category_id INTEGER,
        category_name TEXT DEFAULT '', vip_level INTEGER DEFAULT 0,
        remark TEXT, steps_json TEXT, fetched_at TEXT,
        expire_time TEXT, cancel_home_time TEXT DEFAULT '',
        audit_time INTEGER DEFAULT 0, task_time INTEGER DEFAULT 0,
        max_stock INTEGER DEFAULT 0, task_count INTEGER DEFAULT 0,
        task_type TEXT DEFAULT '', apply_limit INTEGER DEFAULT 0,
        avatar_url TEXT DEFAULT '', detail_fetched_at TEXT,
        PRIMARY KEY (source, task_id))""")
    conn.commit()
    conn.close()

def get_db_stats():
    """获取数据库统计"""
    try:
        conn = sqlite3.connect(str(DB))
        stats = {}
        for src in ["shangbang", "quxian"]:
            total = conn.execute("SELECT COUNT(*) FROM tasks WHERE source=?", (src,)).fetchone()[0]
            cached = conn.execute("SELECT COUNT(*) FROM tasks WHERE source=? AND detail_fetched_at IS NOT NULL", (src,)).fetchone()[0]
            stats[src] = {"total": total, "cached": cached}
        conn.close()
        return stats
    except Exception as e:
        return {"error": str(e)}

def keep_alive():
    """Ping web service to prevent Render.com free tier from sleeping"""
    web_url = os.environ.get("WEB_URL", "https://xuanshangjiankong.onrender.com")
    try:
        req = urllib.request.Request(web_url, headers={"User-Agent": "KeepAlive/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            log(f"  ✅ Keep-alive ping OK ({resp.status})")
    except Exception as e:
        log(f"  ⚠️ Keep-alive ping failed: {e}")

# ════════════════════════════════════════
# 主流程
# ════════════════════════════════════════
if __name__ == "__main__":
    # Initialize database
    init_db()
    
    log("=" * 50)
    log("🕷️ 任务雷达自动爬虫启动")
    
    # 1. 运行赏帮爬虫
    log("▶ 赏帮赚爬虫")
    sb_ok, sb_out = run_spider(SHANGBANG, "赏帮", timeout=120)
    
    # 2. 运行趣闲赚爬虫
    log("▶ 趣闲赚爬虫")
    qx_ok, qx_out = run_spider(QUXIAN, "趣闲", timeout=120)
    
    # 3. 检查 web 服务器
    log("▶ Web 服务器检查")
    web_ok = ensure_web_server()
    
    # 4. Keep-alive ping (prevent Render.com free tier from sleeping)
    log("▶ Keep-alive Ping")
    keep_alive()
    
    # 4. 数据库统计
    stats = get_db_stats()
    log(f"▶ 数据库统计: {json.dumps(stats, ensure_ascii=False)}")
    
    # 总结
    results = []
    if sb_ok: results.append("赏帮✅")
    else: results.append("赏帮❌")
    if qx_ok: results.append("趣闲✅")
    else: results.append("趣闲❌")
    if web_ok: results.append("Web✅")
    else: results.append("Web❌")
    
    log(f"🏁 完成: {' | '.join(results)}")
    log("=" * 50)
