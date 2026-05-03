"""
任务聚合 Web 服务器 v5
======================
双面板布局 + 真实任务详情（步骤/截图/描述/评论）
数据来自赏帮赚 API，详情按需加载并缓存

启动: python web_server.py
访问: http://localhost:5000
"""

import sys, io, sqlite3, json, html as _html, uuid as _uuid, time as _time, os
import urllib.request, urllib.parse, threading, re as _re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from datetime import datetime

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DB = Path(__file__).parent / "tasks.db"
# ── 赏帮 API ──
API = "https://gateway.shangbangzhuan.com"
PHONE = "18681624624"
MD5 = "EBCBF97EC1D80C0388D39BF508039BAA"
# ── 趣闲赚 ──
QX_COOKIE = "tzb_user_cryptograph=16166236%3Apn69jwXEZLkJlGh8ydQK; tzb_session=qo5udfh67ngjlv4fq8ukmc0qs1n8k2kf; tzb_formhash_cookie=b6cdec79ffe46186d957eb828a573102"
QX_FORMHASH = "b6cdec79ffe46186d957eb828a573102"
QX_BASE = "https://wap.huayingrc.com"

CATS = {
    1000:("注册下载","#e74c3c","📱"), 1002:("扫码助力","#e67e22","🤝"),
    1003:("电商购物","#f39c12","🛒"), 1005:("体验试玩","#2ecc71","🎮"),
    1007:("问卷调查","#3498db","📋"), 2005:("金融理财","#9b59b6","💰"),
    2006:("保险相关","#1abc9c","🛡️"), 3000:("其他任务","#95a5a6","📌"),
}
TYPE_TAG = {
    1000:("赏","#e74c3c"), 1002:("扫","#e67e22"), 1003:("商","#f39c12"),
    1005:("玩","#2ecc71"), 1007:("卷","#3498db"), 2005:("金","#9b59b6"),
    2006:("保","#1abc9c"), 3000:("他","#95a5a6"),
}

def h(s): return _html.escape(str(s or ""))

def fmt_time(ts):
    """格式化时间：支持日期字符串和 Unix 时间戳"""
    if not ts:
        return ""
    ts = str(ts).strip()
    if not ts:
        return ""
    # Unix 时间戳（纯数字）
    if ts.isdigit():
        try:
            from datetime import datetime as _dt
            dt = _dt.fromtimestamp(int(ts))
            return dt.strftime("%m-%d %H:%M")
        except:
            return ts[:8]
    # 日期字符串
    if len(ts) >= 16:
        return ts[5:16]
    return ts

def db():
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    return c

def init_db():
    """Initialize database tables if they don't exist"""
    conn = db()
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

# Initialize database on startup
init_db()

# ── API Token 管理 ──
TOKEN = None
TOKEN_TIME = 0

def login():
    global TOKEN, TOKEN_TIME
    dev_id = str(_uuid.uuid4())
    url = f"{API}/user/loginForWeb?account={PHONE}&credentials={MD5}&appKey=000000&deviceId={dev_id}"
    headers = {"device":"ios","appKey":"000000","version":"2.01",
               "User-Agent":"Mozilla/5.0 (Linux; Android 6.0; Nexus 5) AppleWebKit/537.36",
               "Origin":"https://m.shangbangzhuan.com","Referer":"https://m.shangbangzhuan.com/"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        TOKEN = result["data"]["uuid"]
        TOKEN_TIME = _time.time()
        print(f"  ✅ 登录成功")

def api_headers():
    global TOKEN
    if not TOKEN or _time.time() - TOKEN_TIME > 3500:
        login()
    return {"device":"ios","appKey":"000000","version":"2.01","uuid":TOKEN,
            "User-Agent":"Mozilla/5.0 (Linux; Android 6.0; Nexus 5) AppleWebKit/537.36",
            "Origin":"https://m.shangbangzhuan.com","Referer":"https://m.shangbangzhuan.com/"}

def fetch_detail(task_id):
    """从 API 获取任务详情，返回 dict 或 None"""
    url = f"{API}/task/detail/{task_id}"
    try:
        req = urllib.request.Request(url, headers=api_headers())
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 200:
                return result["data"]
    except Exception as e:
        print(f"  ⚠️ 获取详情失败 {task_id}: {e}")
    return None

def save_detail(task_id, data):
    """将详情存入数据库"""
    conn = db()
    task = data.get("task", {})
    steps = data.get("steps", [])
    conn.execute("""UPDATE tasks SET remark=?, steps_json=?, audit_time=?, task_time=?,
                    task_count=?, max_stock=?, detail_fetched_at=?
                    WHERE source='shangbang' AND task_id=?""",
                 (task.get("remark",""), json.dumps(steps, ensure_ascii=False),
                  task.get("auditTime",0), task.get("taskTime",0),
                  task.get("taskCount",0), task.get("maxStock",0),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id))
    conn.commit()
    conn.close()

def prefetch_all():
    """后台线程：批量预抓取所有未缓存的任务详情（多线程加速）"""
    import time as _t
    from concurrent.futures import ThreadPoolExecutor, as_completed
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT task_id, source FROM tasks WHERE detail_fetched_at IS NULL").fetchall()
    total = len(rows)
    conn.close()
    if total == 0:
        print(f"  所有任务已有详情缓存")
        return
    print(f"  后台预抓取 {total} 个任务详情 (5线程)...")
    ok = 0
    done_count = 0

    def fetch_one(tid, src):
        if src == "quxian":
            data = fetch_quxian_detail(tid)
            if data:
                save_quxian_detail(tid, data)
                return True
        else:
            data = fetch_detail(tid)
            if data:
                save_detail(tid, data)
                return True
        return False

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_one, r["task_id"], r["source"]): r["task_id"] for r in rows}
        for f in as_completed(futures):
            done_count += 1
            if f.result():
                ok += 1
            if done_count % 50 == 0:
                print(f"  预抓取: [{done_count}/{total}] ✅{ok}")
    print(f"  预抓取完成: ✅{ok}/{total}")

def get_cached_detail(task_id, source=None):
    """从缓存获取详情"""
    conn = db()
    if source:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=? AND source=?", (task_id, source)).fetchone()
    else:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    if t and t["detail_fetched_at"]:
        return t
    return None

def fetch_quxian_detail(reward_id):
    """从趣闲赚获取任务详情（HTML 解析 Vue SSR 数据）"""
    url = f"{QX_BASE}/reward/{reward_id}/"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            "Cookie": QX_COOKIE,
            "Accept": "text/html,*/*",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        # Extract reward_info
        m = _re.search(r'reward_info\s*:\s*(\{.*?\})\s*,\s*\n', html, _re.DOTALL)
        if not m:
            return None
        info = json.loads(m.group(1))
        # Extract steps (variable name is 'steps', not 'step_list')
        steps = []
        sm = _re.search(r'steps\s*:\s*(\[\{"step_id".*?\])\s*[,}]', html, _re.DOTALL)
        if sm:
            try:
                steps = json.loads(sm.group(1))
            except json.JSONDecodeError:
                pass
        return {"info": info, "steps": steps}
    except Exception as e:
        print(f"  ⚠️ 趣闲详情获取失败 {reward_id}: {e}")
    return None

def save_quxian_detail(reward_id, data):
    """将趣闲赚详情存入数据库"""
    info = data.get("info", {})
    steps = data.get("steps", [])
    # Convert steps to shangbang-compatible format
    compat_steps = []
    for s in steps:
        compat_steps.append({
            "title": s.get("title", ""),
            "content": s.get("url", ""),
            "type": 2 if s.get("url") else 3,
        })
    conn = db()
    conn.execute("""UPDATE tasks SET remark=?, steps_json=?, detail_fetched_at=?
                    WHERE source='quxian' AND task_id=?""",
                 (info.get("contents", ""), json.dumps(compat_steps, ensure_ascii=False),
                  datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reward_id))
    conn.commit()
    conn.close()

def get_task(task_id, source=None):
    conn = db()
    if source:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=? AND source=?", (task_id, source)).fetchone()
    else:
        t = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    conn.close()
    return t

def get_tasks(cat=None, kw=None, block=None, mn=0, mx=9999, sort="money", order="desc", source=None):
    conn = db()
    wh, pr = ["1=1"], []
    if source: wh.append("source=?"); pr.append(source)
    if cat: wh.append("category_id=?"); pr.append(cat)
    if kw:
        parts = [p.strip() for p in kw.split(",") if p.strip()]
        if parts:
            conds = []
            for p in parts:
                conds.append("(title LIKE ? OR advertiser LIKE ?)")
                pr += [f"%{p}%", f"%{p}%"]
            wh.append("(" + " OR ".join(conds) + ")")
    if block:
        for p in [p.strip() for p in block.split(",") if p.strip()]:
            wh.append("title NOT LIKE ?"); pr.append(f"%{p}%")
    if mn > 0: wh.append("money >= ?"); pr.append(mn)
    if mx < 9999: wh.append("money <= ?"); pr.append(mx)
    w = " AND ".join(wh)
    sc = {"money":"money","stock":"current_stock","success":"success_count",
          "time":"fetched_at","repeat":"success_count"}.get(sort,"money")
    od = "DESC" if order=="desc" else "ASC"
    rows = conn.execute(f"SELECT * FROM tasks WHERE {w} ORDER BY {sc} {od}", pr).fetchall()
    total = len(rows)
    conn.close()
    return rows, total

def get_stats():
    conn = db()
    total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    last = conn.execute("SELECT MAX(fetched_at) FROM tasks").fetchone()[0]
    conn.close()
    return total, last or "未知"

# ════════════════════════════════════════
# 列表页
# ════════════════════════════════════════
def page_list(kw="", block="", cat=None, mn=0, mx=9999, sort="money", order="desc", source=None):
    total_count, last_upd = get_stats()
    rows, total = get_tasks(cat, kw or None, block or None, mn, mx, sort, order, source)

    # 平台统计
    conn = db()
    sb_count = conn.execute("SELECT COUNT(*) FROM tasks WHERE source='shangbang'").fetchone()[0]
    qx_count = conn.execute("SELECT COUNT(*) FROM tasks WHERE source='quxian'").fetchone()[0]
    conn.close()

    # 平台 tab
    src_tabs = [(None, "全部", total_count), ("shangbang", "赏帮", sb_count), ("quxian", "趣闲", qx_count)]
    src_html = ""
    for val, label, cnt in src_tabs:
        cls = "active" if source == val else ""
        src_html += f'<div class="type-btn {cls}" onclick="filterSource(\'{val or ""}\')">{label} ({cnt})</div>'

    type_list = [("赏帮","1"),("可乐","3"),("众人","4"),("闲趣","5"),
                 ("微客","6"),("六六","7"),("秒单","8"),("宝盒","9")]
    type_to_cat = {'1':1000,'3':1002,'4':1003,'5':1005,'6':1007,'7':2005,'8':2006,'9':3000}
    type_html = ""
    for name, tv in type_list:
        cls = "active" if cat==type_to_cat.get(tv) else ""
        type_html += f'<div class="type-btn {cls}" onclick="filterType(\'{tv}\')">{name}</div>'

    repeat_cls = f"sort-{order}" if sort=="repeat" else ""
    rows_html = ""
    for t in rows:
        tag, color = TYPE_TAG.get(t["category_id"], ("他","#95a5a6"))
        # 趣闲赚用 category_name 做 tag
        if t["source"] == "quxian":
            tag = (t["category_name"] or "任务")[:2]
            color = "#ff6700"
        rc = t["success_count"]
        rep = "<span class='first'>首发</span>" if rc<=1 else str(rc)
        ft = fmt_time(t["expire_time"])
        src_badge = "" if t["source"]=="shangbang" else "<span style='background:#ff6700;color:#fff;padding:1px 4px;border-radius:2px;font-size:10px;margin-left:4px'>趣闲</span>"
        rows_html += f'''<tr onclick="selectRow(this)" data-id="{t['task_id']}" data-source="{t['source']}">
            <td><div class="tt"><span class="tg" style="background:{color}">{tag}</span>{h(t['title'])}{src_badge} <span style="color:#aaa;font-size:11px">· {h(t['advertiser'] or '')}</span></div></td>
            <td class="price">{t['money']:.2f}</td>
            <td class="stock-col" style="display:none">{t['current_stock']}</td>
            <td class="upstock-col" style="display:none">{t['current_stock']}</td>
            <td class="rep">{rep}</td>
            <td class="time">{ft}</td></tr>'''
    if not rows_html:
        rows_html = '<tr><td colspan="6" style="text-align:center;padding:40px;color:#999">没有找到匹配的任务</td></tr>'

    source_label = {"shangbang":"赏帮赚","quxian":"趣闲赚"}.get(source, "全部平台")

    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>任务雷达</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Microsoft YaHei",sans-serif;background:#f0f2f5;padding:8px}}
.ct{{width:1200px;margin:0 auto;display:flex;gap:10px;min-height:80vh}}
/* 左侧列表 */
.lt{{width:595px;display:flex;flex-direction:column;gap:8px;max-height:100vh}}
.lt .gc{{flex:1;min-height:900px;max-height:100vh;overflow-y:auto;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.05)}}
.lt .gc::-webkit-scrollbar{{width:6px}}.lt .gc::-webkit-scrollbar-thumb{{background:#c1c1c1;border-radius:3px}}
/* 右侧详情 */
.rt{{width:595px;background:#fff;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.05);min-height:900px;max-height:100vh;overflow-y:auto}}
.rt::-webkit-scrollbar{{width:6px}}.rt::-webkit-scrollbar-thumb{{background:#c1c1c1;border-radius:3px}}
/* 搜索 */
.sc{{background:#fff;padding:8px;border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.1);margin-bottom:8px}}
.ty{{margin-bottom:8px;display:flex;gap:6px;flex-wrap:wrap}}
.ty .type-btn{{padding:6px 8px;background:#fff;border:1px solid #e5e7eb;border-radius:4px;color:#666;cursor:pointer;font-size:13px;transition:all .2s}}
.ty .type-btn:hover{{border-color:#1890ff;color:#1890ff}}
.ty .type-btn.active{{background:#1890ff;border-color:#1890ff;color:#fff}}
.sf{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
.si{{flex:1;min-width:160px;padding:8px 12px;border:1px solid #e5e7eb;border-radius:4px;font-size:13px;outline:none}}
.si:focus{{border-color:#1890ff}}
.sb{{padding:8px 20px;background:#1890ff;color:#fff;border:none;border-radius:4px;font-size:14px;cursor:pointer}}
.sb:hover{{background:#40a9ff}}
/* 表格 */
.gc{{background:#fff;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.05);overflow-x:hidden}}
.gv{{width:100%;border-collapse:collapse;table-layout:fixed}}
.gv th{{background:#f1f5f9;padding:10px 6px;font-weight:600;text-align:left;color:#4a5568;font-size:13px;border-bottom:1px solid #e2e8f0;cursor:pointer}}
.gv th:hover{{background:#e8f0fe}}
.gv td{{padding:8px 6px;border-bottom:1px solid #e2e8f0;font-size:13px;color:#2c3e50}}
.gv tr{{cursor:pointer;transition:background .2s}}
.gv tr:hover{{background:#f8fafc}}
.gv tr.sel{{background:#e6f7ff;border-left:3px solid #1890ff}}
.gv td:first-child{{width:55%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.gv td.price{{width:60px;text-align:right}}
.gv td.rep{{width:60px;text-align:center}}
.gv td.time{{width:100px;text-align:center}}
.gv th:nth-child(2){{text-align:right;width:60px}}
.gv th:nth-child(5){{width:60px;text-align:center;font-weight:bold;color:#ff4d4f;position:relative;padding-right:20px}}
.gv th:nth-child(5)::after{{content:'↕';position:absolute;right:8px;opacity:.6}}
.gv th:nth-child(5).sort-asc::after{{content:'↑'}}.gv th:nth-child(5).sort-desc::after{{content:'↓'}}
.tt{{display:flex;align-items:center;gap:4px}}
.tg{{display:inline-block;padding:1px 5px;border-radius:3px;color:#fff;font-size:11px;font-weight:600;flex-shrink:0}}
.first{{color:#ff4d4f;font-weight:600}}
.st{{margin-top:5px;padding:5px;border:1px solid #e0e0e0;border-radius:4px;font-size:12px;color:#666;background:#fff}}
footer{{margin-top:5px;text-align:center;padding:10px;background:#fff;border-radius:6px;font-size:12px;color:#999}}
@media(max-width:768px){{body{{padding:0}}.ct{{flex-direction:column;width:100%}}.lt,.rt{{width:100%;max-width:100%}}.lt .gc{{max-height:none;min-height:auto}}}}
</style></head><body>
<div class="ct">
<div class="lt">
    <div class="sc">
        <div class="ty">{src_html}</div>
        <div class="ty">{type_html}</div>
        <form class="sf" method="get" id="sf">
            <input name="keyword" class="si" placeholder="搜索任务名称或广告主" value="{h(kw)}">
            <input name="block" class="si" placeholder="屏蔽关键词" value="{h(block)}" style="max-width:150px">
            <input type="number" name="min_price" class="si" placeholder="最低" value="{'' if mn<=0 else mn}" step="0.01" style="width:70px;flex:none;min-width:70px">
            <span style="color:#ccc">~</span>
            <input type="number" name="max_price" class="si" placeholder="最高" value="{'' if mx>=9999 else mx}" step="0.01" style="width:70px;flex:none;min-width:70px">
            <input type="hidden" name="category" id="ci" value="{cat or ''}">
            <input type="hidden" name="sort" id="si" value="{sort}">
            <input type="hidden" name="order" id="oi" value="{order}">
            <input type="hidden" name="source" id="srci" value="{source or ''}">
            <button type="submit" class="sb">搜索</button>
        </form>
    </div>
    <div class="st">{source_label} · 共 {total_count} 条 · 搜索 {total} 条 · 更新 {h(last_upd)} · <a href="/run-spider" style="color:#667eea;font-weight:600" onclick="this.innerText='启动中...';return true">🔄 立即抓取</a></div>
    <div class="gc"><table class="gv">
        <tr><th onclick="sc(0)">标题</th><th onclick="sc(1)">价格</th>
        <th class="stock-col" onclick="sc(2)" style="display:none">数</th>
        <th class="upstock-col" onclick="sc(3)" style="display:none">差</th>
        <th class="{repeat_cls}" onclick="sc(4)">重复</th><th onclick="sc(5)">时间</th></tr>
        {rows_html}
    </table></div>
</div>
<div class="rt" id="rt">
    <div style="text-align:center;color:#999;padding:80px 20px">
        <div style="font-size:48px;margin-bottom:16px">👈</div>
        <div style="font-size:16px">点击左侧任务查看详情</div>
    </div>
</div>
</div>
<footer>任务雷达 · 赏帮 {sb_count} 条 + 趣闲 {qx_count} 条 = {sb_count+qx_count} 条 · 本地数据</footer>
<script>
var cm={{'1':'1000','3':'1002','4':'1003','5':'1005','6':'1007','7':'2005','8':'2006','9':'3000'}};
function selectRow(row){{
    var id=row.getAttribute('data-id');
    var src=row.getAttribute('data-source')||'';
    document.querySelectorAll('.gv tr.sel').forEach(function(t){{t.classList.remove('sel')}});
    row.classList.add('sel');
    var rt=document.getElementById('rt');
    rt.innerHTML='<div style="text-align:center;color:#999;padding:80px 20px"><div style="font-size:32px;margin-bottom:12px">⏳</div>加载中...</div>';
    fetch('/detail?id='+id+'&source='+src).then(function(r){{return r.text()}}).then(function(html){{
        rt.innerHTML=html;rt.scrollTop=0;
    }}).catch(function(){{
        rt.innerHTML='<div style="text-align:center;color:#e74c3c;padding:80px 20px">加载失败</div>';
    }});
}}
function filterSource(s){{document.getElementById('srci').value=s;document.getElementById('sf').submit()}}
function filterType(t){{var i=document.getElementById('ci');i.value=i.value===cm[t]?'':cm[t];document.getElementById('sf').submit()}}
var cs=-1,os=[0,0,0,0,0,0];
function sc(c){{
    if(cs===c)os[c]=1-os[c];else{{cs=c;os[c]=0}}
    var sm={{0:'money',1:'money',2:'stock',3:'stock',4:'repeat',5:'time'}};
    document.querySelectorAll('.gv th').forEach(function(th,i){{th.classList.remove('sort-asc','sort-desc');if(i===c)th.classList.add(os[c]===0?'sort-desc':'sort-asc')}});
    document.getElementById('si').value=sm[c]||'money';
    document.getElementById('oi').value=os[c]===0?'desc':'asc';
    document.getElementById('sf').submit();
}}
</script></body></html>'''


# ════════════════════════════════════════
# 详情页（AJAX 内容）
# ════════════════════════════════════════
def page_detail(tid, source=None):
    # 先查缓存
    cached = get_cached_detail(tid, source)
    if not cached:
        # 缓存没有，从 API 获取
        if source == "quxian":
            data = fetch_quxian_detail(tid)
            if data:
                save_quxian_detail(tid, data)
                cached = get_task(tid, source)
        else:
            data = fetch_detail(tid)
            if data:
                save_detail(tid, data)
                cached = get_task(tid, source)
        if not cached:
            cached = get_task(tid, source)
    
    if not cached:
        return '<div style="text-align:center;padding:60px;color:#999;font-size:16px">❌ 任务不存在</div>'

    # 趣闲赚用 category_name，赏帮用 category_id
    if cached["source"] == "quxian":
        cn = cached["category_name"] or "其他"
        cc = "#ff6700"
        ci = "🍊"
        source_name = "趣闲赚"
    else:
        cn, cc, ci = CATS.get(cached["category_id"], ("其他","#95a5a6","📌"))
        source_name = "赏帮赚"
    stk = cached["current_stock"]
    if stk > 50: st = "库存充足"; sc_color = "#52c41a"
    elif stk > 0: st = f"剩余 {stk} 个"; sc_color = "#faad14"
    else: st = "已抢光"; sc_color = "#ff4d4f"

    audit = "⚡ 秒审" if cached["audit_fast"] else "普通审核"
    vip = f"VIP {cached['vip_level']}" if cached["vip_level"] else "无限制"
    expire = fmt_time(cached["expire_time"]) or "长期有效"
    total_slots = cached["success_count"] + cached["current_stock"]
    pct = min(100, round(cached["success_count"] / max(1, total_slots) * 100)) if total_slots > 0 else 0

    remark = cached["remark"] or ""
    steps_json = cached["steps_json"] or "[]"
    try: steps = json.loads(steps_json)
    except: steps = []
    audit_time = cached["audit_time"] or 0
    task_time = cached["task_time"] or 0

    # 步骤 HTML
    steps_html = ""
    if steps:
        for i, s in enumerate(steps):
            title = h(s.get("title",""))
            content = s.get("content","")
            stype = s.get("type", 0)
            # type: 2=图片, 3=文字+图片
            img_html = ""
            if content and (content.startswith("http") or content.startswith("//")):
                img_url = content if content.startswith("http") else "https:" + content
                img_html = f'<img src="{h(img_url)}" style="max-width:100%;border-radius:8px;margin-top:8px;cursor:pointer" onclick="window.open(this.src)" loading="lazy" onerror="this.style.display=\'none\'">'
            steps_html += f'''
            <div style="background:#fff;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06);border-left:4px solid {cc}">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
                    <span style="background:{cc};color:#fff;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;flex-shrink:0">{i+1}</span>
                    <span style="font-weight:600;font-size:14px;color:#333">步骤 {i+1}</span>
                </div>
                <div style="font-size:14px;color:#333;line-height:1.8;white-space:pre-wrap">{title}</div>
                {img_html}
            </div>'''
    else:
        steps_html = '<div style="text-align:center;padding:30px;color:#bbb">暂无步骤详情</div>'

    # 描述
    remark_html = ""
    if remark:
        remark_html = f'''
        <div style="background:#fff;border-radius:10px;padding:16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
            <div style="font-weight:600;font-size:14px;color:#333;margin-bottom:8px">📝 任务描述</div>
            <div style="font-size:13px;color:#666;line-height:1.8;white-space:pre-wrap">{h(remark)}</div>
        </div>'''

    # 信息行
    def irow(label, value):
        return f'<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f5f5f5"><span style="color:#999;font-size:13px">{label}</span><span style="font-size:13px;font-weight:600;color:#333">{value}</span></div>'

    info_html = f'''
    <div style="background:#fff;border-radius:10px;padding:4px 16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
        {irow("📢 广告主", h(cached["advertiser"] or "未知"))}
        {irow("🏷 分类", f'{ci} {cn}')}
        {irow("📦 库存", f'<span style="color:{sc_color}">{st}</span>')}
        {irow("🔍 审核", audit)}
        {irow("⏱ 审核时限", f'{audit_time}小时' if audit_time else '未知')}
        {irow("⏰ 任务时限", f'{task_time}小时' if task_time else '未知')}
        {irow("👑 VIP", vip)}
        {irow("📅 过期时间", h(str(expire)))}
    </div>'''

    return f'''
<div style="padding:16px;font-family:'Microsoft YaHei',sans-serif">
    <!-- 标题区 -->
    <div style="background:linear-gradient(135deg,{cc}dd,{cc});color:#fff;padding:12px 16px;border-radius:10px;margin-bottom:12px">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
            <span style="background:rgba(255,255,255,.2);padding:2px 8px;border-radius:10px;font-size:11px">{ci} {cn}</span>
            {"<span style='background:rgba(255,255,255,.2);padding:2px 8px;border-radius:10px;font-size:11px'>⚡秒审</span>" if cached["audit_fast"] else ""}
            <span style="margin-left:auto;font-size:24px;font-weight:900;line-height:1">¥{cached["money"]:.2f}</span>
        </div>
        <div style="font-size:15px;font-weight:700;line-height:1.4">{h(cached["title"])}</div>
        <div style="font-size:12px;opacity:.75;margin-top:4px">📢 {h(cached["advertiser"] or "未知")}</div>
    </div>

    <!-- 数据卡 -->
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px">
        <div style="background:#fff;border-radius:8px;padding:10px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06)">
            <div style="font-size:18px;font-weight:700;color:#1890ff">{cached["success_count"]}</div>
            <div style="font-size:11px;color:#999;margin-top:2px">已完成</div>
        </div>
        <div style="background:#fff;border-radius:8px;padding:10px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06)">
            <div style="font-size:18px;font-weight:700;color:{sc_color}">{stk}</div>
            <div style="font-size:11px;color:#999;margin-top:2px">剩余库存</div>
        </div>
        <div style="background:#fff;border-radius:8px;padding:10px;text-align:center;box-shadow:0 1px 4px rgba(0,0,0,.06)">
            <div style="font-size:18px;font-weight:700;color:#722ed1">{pct}%</div>
            <div style="font-size:11px;color:#999;margin-top:2px">完成率</div>
        </div>
    </div>

    <!-- 进度条 -->
    <div style="background:#fff;border-radius:8px;padding:10px 16px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06)">
        <div style="width:100%;height:8px;background:#f0f0f0;border-radius:4px;overflow:hidden">
            <div style="width:{pct}%;height:100%;background:linear-gradient(90deg,#1890ff,#722ed1);border-radius:4px"></div>
        </div>
    </div>

    {remark_html}

    <!-- 步骤 -->
    <div style="margin-bottom:12px">
        <div style="font-weight:600;font-size:15px;color:#333;margin-bottom:12px;padding-left:4px">📋 任务步骤</div>
        {steps_html}
    </div>

    {info_html}

    <div style="text-align:center;padding:12px;color:#ccc;font-size:11px">
        ID: {cached["task_id"]} · 来源：{source_name} · {"详情已缓存" if cached["detail_fetched_at"] else "基础数据"}
    </div>
</div>'''


# ════════════════════════════════════════
# HTTP Handler
# ════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            qs = parse_qs(parsed.query)
            src = qs.get("source",[""])[0] or None
            body = page_list(
                qs.get("keyword",[""])[0], qs.get("block",[""])[0],
                int(qs["category"][0]) if qs.get("category") else None,
                float(qs.get("min_price",[0])[0] or 0),
                float(qs.get("max_price",[9999])[0] or 9999),
                qs.get("sort",["money"])[0], qs.get("order",["desc"])[0],
                src
            ).encode("utf-8")
        elif parsed.path == "/detail":
            qs = parse_qs(parsed.query)
            tid = int(qs.get("id",[0])[0] or 0)
            src = qs.get("source",[""])[0] or None
            body = page_detail(tid, src).encode("utf-8")
        elif parsed.path == "/run-spider":
            # Trigger spider in background
            import subprocess
            subprocess.Popen(
                [sys.executable, "-u", str(DB.parent / "auto_spider.py")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(DB.parent)
            )
            body = b"<html><body><h2>Spider started!</h2><p>Wait 1-2 minutes, then <a href='/'>refresh</a>.</p></body></html>"
        elif parsed.path == "/crawl":
            # Run spiders inline and return results
            results = []
            # Shangbang spider
            try:
                import uuid as _uuid2
                _token = None
                _dev = str(_uuid2.uuid4())
                _url = f"{API}/user/loginForWeb?account={PHONE}&credentials={MD5}&appKey=000000&deviceId={_dev}"
                _hdrs = {"device":"ios","appKey":"000000","version":"2.01","User-Agent":"Mozilla/5.0 (Linux; Android 6.0; Nexus 5) AppleWebKit/537.36","Origin":"https://m.shangbangzhuan.com","Referer":"https://m.shangbangzhuan.com/"}
                _req = urllib.request.Request(_url, headers=_hdrs)
                with urllib.request.urlopen(_req, timeout=15) as _r:
                    _d = json.loads(_r.read())
                    _token = _d["data"]["uuid"]
                conn2 = db()
                _now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _total = 0
                for _pg in range(1, 21):
                    _purl = f"{API}/task/search?current={_pg}&size=100"
                    _preq = urllib.request.Request(_purl, headers={**_hdrs, "uuid": _token})
                    with urllib.request.urlopen(_preq, timeout=15) as _pr:
                        _tasks = json.loads(_pr.read()).get("data", [])
                    if not _tasks: break
                    for _t in _tasks:
                        conn2.execute("""INSERT INTO tasks (source,task_id,title,money,advertiser,avatar,current_stock,success_count,category_id,vip_level,expire_time,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(source,task_id) DO UPDATE SET title=excluded.title,money=excluded.money,current_stock=excluded.current_stock,success_count=excluded.success_count,fetched_at=excluded.fetched_at""",
                            ("shangbang",_t.get("id"),_t.get("title",""),_t.get("money",0),_t.get("name",""),_t.get("avatar",""),_t.get("currentStock",0),_t.get("success",0),_t.get("categoryId"),_t.get("vipLevel",0),_t.get("cancelHomeTime",""),_now))
                    _total += len(_tasks)
                    if len(_tasks) < 100: break
                conn2.commit()
                conn2.close()
                results.append(f"✅ 赏帮: {_total}条")
            except Exception as e:
                results.append(f"❌ 赏帮: {e}")
            # Quxian spider
            try:
                import uuid as _uuid3
                conn3 = db()
                _now2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _qxtotal = 0
                for _qpg in range(1, 51):
                    _qdata = urllib.parse.urlencode({"page":str(_qpg),"cat_id":"0","type":"0","rand":f"0.{_uuid3.uuid4().hex[:10]}","limit":"20","search":"","search_type":"top","level":"0","formhash":QX_FORMHASH}).encode()
                    _qreq = urllib.request.Request(f"{QX_BASE}/reward/list/",data=_qdata,headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15","Cookie":QX_COOKIE,"Content-Type":"application/x-www-form-urlencoded; charset=UTF-8","X-Requested-With":"XMLHttpRequest","Referer":f"{QX_BASE}/reward/list/"})
                    with urllib.request.urlopen(_qreq, timeout=15) as _qr:
                        _qresult = json.loads(_qr.read())
                    _qtasks = _qresult.get("reward_list",[]) if _qresult.get("state")==1 else []
                    if not _qtasks: break
                    for _qt in _qtasks:
                        _tet = _qt.get("top_end_time","")
                        _tet_str = ""
                        if _tet:
                            try: _tet_str = datetime.fromtimestamp(int(_tet)).strftime("%Y-%m-%d %H:%M:%S")
                            except: _tet_str = str(_tet)
                        conn3.execute("""INSERT INTO tasks (source,task_id,title,money,advertiser,avatar,current_stock,success_count,category_id,category_name,vip_level,fetched_at,expire_time,max_stock) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(source,task_id) DO UPDATE SET title=excluded.title,money=excluded.money,current_stock=excluded.current_stock,success_count=excluded.success_count,category_name=excluded.category_name,fetched_at=excluded.fetched_at""",
                            ("quxian",_qt.get("reward_id"),_qt.get("reward_title"),float(_qt.get("apply_price",0)),_qt.get("tags_name",""),_qt.get("avatar",""),int(_qt.get("surplus_votes",0)),int(_qt.get("finish_votes",0)),0,_qt.get("cat_name",""),int(_qt.get("vip_id") or 0),_now2,_tet_str,int(_qt.get("total_votes") or 0)))
                    _qxtotal += len(_qtasks)
                    if len(_qtasks) < 20: break
                conn3.commit()
                conn3.close()
                results.append(f"✅ 趣闲: {_qxtotal}条")
            except Exception as e:
                results.append(f"❌ 趣闲: {e}")
            _st, _lt = get_stats()
            body = f"<html><body><h2>爬取完成</h2><pre>{'<br>'.join(results)}</pre><p>数据库: {_st}条, 更新: {_lt}</p><a href='/'>返回首页</a></body></html>".encode()
        elif parsed.path == "/test-api":
            # Test API connectivity
            results = []
            # Test shangbang
            try:
                import uuid as _uuid
                phone = "18681624624"
                md5_pwd = "EBCBF97EC1D80C0388D39BF508039BAA"
                url = f"https://gateway.shangbangzhuan.com/user/loginForWeb?account={phone}&credentials={md5_pwd}&appKey=000000&deviceId={_uuid.uuid4()}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                    results.append(f"✅ 赏帮 API: code={data.get('code')}, msg={data.get('msg')}")
            except Exception as e:
                results.append(f"❌ 赏帮 API: {e}")
            # Test quxian
            try:
                url = "https://wap.huayingrc.com/reward/list/"
                data = urllib.parse.urlencode({"page": "1", "cat_id": "0", "type": "0", "rand": "0", "limit": "20", "search": "", "level": "0", "formhash": QX_FORMHASH}).encode()
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15", "Cookie": QX_COOKIE, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest", "Referer": "https://wap.huayingrc.com/reward/list/"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                    results.append(f"✅ 趣闲 API: state={result.get('state')}, tasks={len(result.get('reward_list', []))}")
            except Exception as e:
                results.append(f"❌ 趣闲 API: {e}")
            body = f"<html><body><h2>API Test</h2><pre>{'<br>'.join(results)}</pre><a href='/'>Back</a></body></html>".encode()
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass

    def do_POST(self):
        """接受本地爬虫推送的任务数据"""
        if self.path == "/api/sync":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = json.loads(body)
                conn = db()
                count = 0
                for task in data.get("tasks", []):
                    conn.execute("""INSERT INTO tasks (source, task_id, title, money, advertiser, avatar,
                        current_stock, success_count, category_id, category_name,
                        vip_level, fetched_at, expire_time, max_stock)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source, task_id) DO UPDATE SET
                        title=excluded.title, money=excluded.money,
                        advertiser=excluded.advertiser, avatar=excluded.avatar,
                        current_stock=excluded.current_stock, success_count=excluded.success_count,
                        category_name=excluded.category_name, vip_level=excluded.vip_level,
                        fetched_at=excluded.fetched_at, max_stock=excluded.max_stock""",
                        (task.get("source"), task.get("task_id"), task.get("title"),
                         task.get("money", 0), task.get("advertiser", ""), task.get("avatar", ""),
                         task.get("current_stock", 0), task.get("success_count", 0),
                         task.get("category_id", 0), task.get("category_name", ""),
                         task.get("vip_level", 0), task.get("fetched_at", ""),
                         task.get("expire_time", ""), task.get("max_stock", 0)))
                    count += 1
                conn.commit()
                conn.close()
                resp = json.dumps({"ok": True, "synced": count}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            except Exception as e:
                resp = json.dumps({"ok": False, "error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
        else:
            self.send_error(404)

if __name__ == "__main__":
    total, last = get_stats()
    print(f"任务数: {total} | 更新: {last}")
    print(f"正在登录赏帮赚...")
    login()
    # 后台预抓取未缓存的任务详情
    t = threading.Thread(target=prefetch_all, daemon=True)
    t.start()
    # Port from environment variable
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 任务聚合平台启动: http://0.0.0.0:{port}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()
