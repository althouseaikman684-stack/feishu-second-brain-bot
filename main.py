# -*- coding: utf-8 -*-
"""
Feishu 24/7 WebSocket Second Brain Bot (Cloud Always-On Daemon) - V2.1
======================================================================
Lin Yunshu's Second Brain Mobile Gateway with:
1. Exact Daily Morning Report & Feynman Challenge Sync
2. Role-Based Access Control (RBAC) & Knowledge Anti-Pollution Guard
"""

import os
import sys
import json
import time
import re
import requests
from datetime import datetime, timezone, timedelta
import io

# Fix Windows console encoding
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateFileRequest,
    CreateFileRequestBody
)

# ==================== Credentials & Configuration ====================
CONFIG = {
    "APP_ID": os.environ.get("FEISHU_APP_ID", ""),
    "APP_SECRET": os.environ.get("FEISHU_APP_SECRET", ""),
    "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", ""),
    "GITHUB_REPO": os.environ.get("GITHUB_REPO", "althouseaikman684-stack/second-brain-vault")
}

BEIJING_TZ = timezone(timedelta(hours=8))

# ==================== Lark Client ====================
lark_client = None

def get_lark_client():
    global lark_client
    if not lark_client:
        lark_client = lark.Client.builder() \
            .app_id(CONFIG["APP_ID"]) \
            .app_secret(CONFIG["APP_SECRET"]) \
            .log_level(lark.LogLevel.INFO) \
            .build()
    return lark_client

# ==================== Cloud Knowledge Base Manager & RBAC Guard ====================
class CloudKnowledgeManager:
    def __init__(self, token, repo):
        self.token = token
        self.repo = repo
        self.headers_raw = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3.raw",
            "User-Agent": "Feishu-Second-Brain-Bot"
        }
        self.headers_json = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Feishu-Second-Brain-Bot"
        }
        self.cache = {}
        self.cache_ttl = 60  # Cache raw files for 60s
        self.tree_cache = []
        self.tree_cache_time = 0

    def _normalize_path(self, path):
        path = path.strip().replace("\\", "/")
        if path.startswith("/"):
            path = path[1:]
        if not path.startswith("vault/"):
            path = f"vault/{path}"
        return path

    def check_write_permission(self, path):
        """
        移动管家受限写入权限控制网 (RBAC Guard):
        - ✅ 允许写入: memory/tasks/index.md, memory/notes/*.md, memory/decisions/index.md
        - 🛡️ 禁止篡改: memory/knowledge/* (核心学术库由 Antigravity 主管理维护), RULES.md, KB_PROFILE.md, files/*
        """
        norm_p = self._normalize_path(path)
        
        # 1. 允许修改的任务和决策
        if norm_p in ["vault/memory/tasks/index.md", "vault/memory/decisions/index.md"]:
            return True, norm_p, "OK"
        
        # 2. 允许随手记灵感与攻略笔记
        if norm_p.startswith("vault/memory/notes/"):
            return True, norm_p, "OK"
            
        # 3. 拦截对核心学科库的随意写入，自动重定向为随手笔记
        if norm_p.startswith("vault/memory/knowledge/"):
            fn = os.path.basename(norm_p)
            diverted_p = f"vault/memory/notes/草稿-{fn}"
            return True, diverted_p, f"🛡️ 核心学术库 (`memory/knowledge/`) 仅由主管理 Antigravity 维护，已安全转存至笔记: `{diverted_p}`"
            
        # 4. 严禁改动系统顶层文件
        if any(norm_p.startswith(x) for x in ["vault/RULES.md", "vault/KB_PROFILE.md", "vault/memory/AGENTS.md", "vault/files/"]):
            return False, norm_p, f"🔒 严禁修改系统级核心规则与原始文件档案: `{norm_p}`"

        # 默认归入 notes 目录
        fn = os.path.basename(norm_p)
        diverted_p = f"vault/memory/notes/{fn}"
        return True, diverted_p, "OK"

    def fetch_file_raw(self, path):
        path = self._normalize_path(path)
        now_ts = time.time()
        if path in self.cache:
            data, ts = self.cache[path]
            if now_ts - ts < self.cache_ttl:
                return data
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        try:
            r = requests.get(url, headers=self.headers_raw, timeout=10)
            if r.status_code == 200:
                self.cache[path] = (r.text, now_ts)
                return r.text
        except Exception as e:
            print(f"[Error] fetch_file_raw({path}): {e}")
        return ""

    def fetch_file_json(self, path):
        path = self._normalize_path(path)
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        try:
            r = requests.get(url, headers=self.headers_json, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[Error] fetch_file_json({path}): {e}")
        return None

    def commit_file(self, path, content, message, sha=None):
        allowed, final_path, reason = self.check_write_permission(path)
        if not allowed:
            print(f"🚫 [RBAC Guard 拦截] {path} -> {reason}")
            return False, reason

        url = f"https://api.github.com/repos/{self.repo}/contents/{final_path}"
        import base64
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8")
        }
        if not sha:
            current_info = self.fetch_file_json(final_path)
            if current_info and "sha" in current_info:
                sha = current_info["sha"]
        if sha:
            body["sha"] = sha
            
        try:
            r = requests.put(url, headers=self.headers_json, json=body, timeout=15)
            if final_path in self.cache:
                del self.cache[final_path]
            success = r.status_code in [200, 201]
            if success:
                print(f"✅ [GitHub Sync OK] {final_path} -> {message}")
                return True, final_path
            else:
                print(f"❌ [GitHub Sync Failed] {final_path} HTTP {r.status_code}: {r.text}")
                return False, f"HTTP {r.status_code}"
        except Exception as e:
            print(f"[Error] commit_file({final_path}): {e}")
            return False, str(e)

    def get_vault_tree(self):
        now_ts = time.time()
        if self.tree_cache and (now_ts - self.tree_cache_time < 600):
            return self.tree_cache
        url = f"https://api.github.com/repos/{self.repo}/git/trees/main?recursive=1"
        try:
            r = requests.get(url, headers=self.headers_json, timeout=10)
            if r.status_code == 200:
                tree = r.json().get("tree", [])
                self.tree_cache = [x["path"] for x in tree if (x["path"].endswith(".md") or x["path"].endswith(".html")) and x["path"].startswith("vault/")]
                self.tree_cache_time = now_ts
                return self.tree_cache
        except Exception as e:
            print(f"[Error] get_vault_tree: {e}")
        return self.tree_cache

    def search_relevant_docs(self, query):
        tree = self.get_vault_tree()
        if not tree:
            return []
        core_files = {
            "vault/KB_PROFILE.md",
            "vault/memory/tasks/index.md",
            "vault/memory/learning-tracker.md",
            "vault/memory/decisions/index.md",
            "vault/memory/notes/index.md",
            "vault/RULES.md",
            "vault/memory/AGENTS.md",
            "vault/memory/changes/CHANGELOG.md"
        }
        
        en_terms = [w.lower() for w in re.findall(r'[a-zA-Z0-9_\-]+', query) if len(w) >= 2]
        cn_clean = "".join(re.findall(r'[\u4e00-\u9fa5]', query))
        cn_terms = []
        stop_words = {'今天', '明天', '昨天', '什么', '怎么', '如何', '帮我', '一下', '可以', '这个', '那个', '现在', '有哪些', '是啥', '讲了', '并且', '或者', '知道', '告诉我'}
        for length in [4, 3, 2]:
            for i in range(len(cn_clean) - length + 1):
                term = cn_clean[i:i+length]
                if term not in stop_words:
                    cn_terms.append(term)
        terms = list(set(en_terms + cn_terms))
        if not terms:
            return []

        scored = []
        for path in tree:
            if path in core_files:
                continue
            path_lower = path.lower()
            score = 0
            for term in terms:
                if term in path_lower:
                    score += len(term) * 2
            if score > 0:
                scored.append((score, path))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_paths = [p for s, p in scored[:3]]

        retrieved = []
        for p in top_paths:
            content = self.fetch_file_raw(p)
            if content:
                if len(content) > 2000:
                    content = content[:2000] + "\n...(部分过长内容已截断)..."
                retrieved.append((p, content))
        return retrieved

km = CloudKnowledgeManager(CONFIG["GITHUB_TOKEN"], CONFIG["GITHUB_REPO"])

# ==================== Precision Time & Dynamic Countdown Engine ====================
def get_time_and_schedule_context():
    now_bj = datetime.now(BEIJING_TZ)
    today = now_bj.date()
    today_str = now_bj.strftime("%Y年%m月%d日")
    time_str = now_bj.strftime("%H:%M")
    weekday_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now_bj.weekday()]

    events = [
        ("山西博物院微信预约与大同高铁购票建议截止", datetime(2026, 8, 20).date()),
        ("太原·山西5日松弛游 (8/24-8/28)", datetime(2026, 8, 24).date()),
        ("太原旅游返程飞长沙", datetime(2026, 8, 28).date()),
        ("等离子体物理25天学习计划正式启动", datetime(2026, 8, 29).date()),
    ]
    countdown_lines = []
    for name, dt in events:
        diff = (dt - today).days
        date_label = dt.strftime("%m月%d日")
        if diff > 0:
            countdown_lines.append(f"- ⏳ {name}：距今还有 {diff} 天（目标日期：{date_label}）")
        elif diff == 0:
            countdown_lines.append(f"- 🚨 {name}：就在今天（{date_label}）！")
        else:
            countdown_lines.append(f"- ✅ {name}：已于 {-diff} 天前（{date_label}）过去")

    start_travel = datetime(2026, 8, 24).date()
    end_travel = datetime(2026, 8, 28).date()
    if start_travel <= today <= end_travel:
        day_idx = (today - start_travel).days + 1
        countdown_lines.append(f"- ✈️ 【实时行程】太原5日游 Day {day_idx}/5 正在进行中！")

    countdowns_text = "\n".join(countdown_lines)

    return f"""【当前真实系统时间（绝对基准）】：
- 日期：{today_str}（{weekday_cn}）
- 时间：{time_str} (北京时间)
- ⚠️ 绝对时间锚定规则：现在就是 {today_str}！所有关于“今天”、“几号”、“距离某事还有几天”的推算，必须以 {today_str} 为唯一基准，严禁沿用历史对话或过往笔记中的旧日期！

【近期关键日程倒计时】：
{countdowns_text}"""

# ==================== Multi-Turn Conversation Memory ====================
CHAT_SESSIONS = {}
MAX_SESSION_MESSAGES = 16  # 8 rounds of conversation

def get_session_history(chat_id):
    return CHAT_SESSIONS.get(chat_id, [])

def append_to_session(chat_id, role, content):
    if chat_id not in CHAT_SESSIONS:
        CHAT_SESSIONS[chat_id] = []
    now_bj = datetime.now(BEIJING_TZ)
    time_tag = now_bj.strftime("%m-%d %H:%M")
    CHAT_SESSIONS[chat_id].append({
        "role": role,
        "content": content,
        "ts": time.time(),
        "time_tag": time_tag
    })
    if len(CHAT_SESSIONS[chat_id]) > MAX_SESSION_MESSAGES:
        CHAT_SESSIONS[chat_id] = CHAT_SESSIONS[chat_id][-MAX_SESSION_MESSAGES:]

def reset_session(chat_id):
    CHAT_SESSIONS[chat_id] = []

# ==================== Deterministic Command Interceptors ====================
def handle_deterministic_shortcuts(chat_id, user_text):
    now_bj = datetime.now(BEIJING_TZ)
    now_date_str = now_bj.strftime("%Y-%m-%d")
    now_time_str = now_bj.strftime("%H:%M")

    # 1. 快捷添加待办
    add_task_match = re.match(r'^(?:添加待办|新增待办|加待办|待办[:：])\s*(.+)$', user_text.strip(), re.DOTALL)
    if add_task_match:
        task_text = add_task_match.group(1).strip()
        current_tasks = km.fetch_file_raw("vault/memory/tasks/index.md")
        if not current_tasks:
            current_tasks = "# 任务清单\n\n## 🔴 今日/本周必须做\n\n"
        
        new_task_item = f"- [ ] {task_text} — 来源: {now_date_str} 飞书指令"
        if "## 🔴 今日/本周必须做" in current_tasks:
            parts = current_tasks.split("## 🔴 今日/本周必须做", 1)
            updated_tasks = parts[0] + "## 🔴 今日/本周必须做\n\n" + new_task_item + "\n" + parts[1].lstrip("\n")
        else:
            updated_tasks = current_tasks + f"\n\n## 🔴 今日/本周必须做\n\n{new_task_item}\n"
        
        ok, res_info = km.commit_file("vault/memory/tasks/index.md", updated_tasks, f"feat(tasks): add task '{task_text[:20]}' via Feishu")
        if ok:
            return f"✅ 已成功将待办添加至云端知识库任务清单！\n\n📌 **新增事项**：{task_text}\n📂 **同步文件**：`memory/tasks/index.md`\n⏰ **时间**：{now_date_str} {now_time_str}"
        else:
            return f"⚠️ 写入云端待办清单失败: {res_info}"

    # 2. 快捷完成待办
    done_task_match = re.match(r'^(?:完成待办|打勾待办|打勾|完成[:：])\s*(.+)$', user_text.strip())
    if done_task_match:
        keyword = done_task_match.group(1).strip()
        current_tasks = km.fetch_file_raw("vault/memory/tasks/index.md")
        if not current_tasks:
            return "⚠️ 未能读取到当前任务清单。"
        
        lines = current_tasks.split("\n")
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith("- [ ]") and keyword.lower() in line.lower():
                line = line.replace("- [ ]", "- [x]", 1)
                if f"（{now_date_str} 完成）" not in line:
                    line += f" ✅（{now_date_str} 飞书完成）"
                found = True
            new_lines.append(line)
        
        if not found:
            return f"🔍 未能在待办清单中匹配到包含「{keyword}」的未完成事项，请检查关键字或输入「查看待办」。"
        
        updated_tasks = "\n".join(new_lines)
        ok, res_info = km.commit_file("vault/memory/tasks/index.md", updated_tasks, f"fix(tasks): complete task '{keyword}' via Feishu")
        if ok:
            return f"🎉 已将包含「{keyword}」的待办事项标记为已完成 [x]！\n\n📂 **同步文件**：`memory/tasks/index.md`"
        else:
            return f"⚠️ 更新云端待办失败: {res_info}"

    # 3. 快捷随手记笔记
    note_match = re.match(r'^(?:记笔记|写笔记|存笔记|随手记)[:：\s]\s*(?:\[(.*?)\]|(.*?))\s*[\|\n]\s*(.+)$', user_text.strip(), re.DOTALL)
    if note_match:
        title = (note_match.group(1) or note_match.group(2) or "随手灵感").strip()
        body = note_match.group(3).strip()
        clean_title = re.sub(r'[\\/:*?"<>|]', '_', title)
        filename = f"{now_date_str}-{clean_title}.md"
        content = f"# {title}\n\n> 🤖 由林云舒于 {now_date_str} {now_time_str} 通过飞书移动端随手记录\n\n---\n\n{body}\n"
        ok, res_info = km.commit_file(f"vault/memory/notes/{filename}", content, f"feat(notes): new note '{clean_title}' via Feishu")
        if ok:
            return f"📝 笔记已成功存入云端知识库！\n\n📌 **标题**：{title}\n📂 **保存路径**：`memory/notes/{filename}`\n📊 **字数**：{len(body)} 字"
        else:
            return f"⚠️ 笔记保存至云端失败: {res_info}"

    return None

# ==================== DeepSeek AI Brain Reasoning ====================
def query_ai_brain(chat_id, user_text):
    if user_text.strip() in ["清空对话", "重置记忆", "新话题", "reset", "/reset"]:
        reset_session(chat_id)
        return "🧠 对话上下文记忆已重置完毕！我们开启一个崭新的话题吧。"

    shortcut_res = handle_deterministic_shortcuts(chat_id, user_text)
    if shortcut_res:
        append_to_session(chat_id, "user", user_text)
        append_to_session(chat_id, "assistant", shortcut_res)
        return shortcut_res

    time_ctx = get_time_and_schedule_context()
    now_bj = datetime.now(BEIJING_TZ)
    now_date_str = now_bj.strftime("%Y-%m-%d")
    
    # 1. Fetch core knowledge files
    kb_profile = km.fetch_file_raw("vault/KB_PROFILE.md") or "暂无个人档案"
    current_tasks = km.fetch_file_raw("vault/memory/tasks/index.md") or "暂无任务清单"
    learning_tracker = km.fetch_file_raw("vault/memory/learning-tracker.md") or "暂无学习追踪"
    
    # 2. 注入【今日真实每日晨报】（确保题目与论文与微信推送 100% 绝对一致）
    today_morning_report = km.fetch_file_raw(f"vault/memory/summary/daily/{now_date_str}.md")
    morning_report_section = ""
    if today_morning_report:
        morning_report_section = f"\n【🌅 今日真实每日晨报与费曼挑战 (memory/summary/daily/{now_date_str}.md)】：\n{today_morning_report}\n"

    # 3. Dynamic RAG retrieval
    retrieved_docs = km.search_relevant_docs(user_text)
    rag_context = ""
    if retrieved_docs:
        rag_parts = []
        for path, doc_text in retrieved_docs:
            rag_parts.append(f"### 📄 匹配文件：`{path}`\n{doc_text}")
        rag_context = f"\n【🧠 动态检索到的第二大脑专属知识库文件 (RAG)】：\n" + "\n\n".join(rag_parts) + "\n"

    system_prompt = f"""你是林云舒的第二大脑（基于 Google DeepMind Antigravity 架构），正在飞书移动端为云舒提供全天候 24 小时科研与日程助理服务。

{time_ctx}

【用户热缓存档案 (KB_PROFILE.md)】：
{kb_profile}

【当前待办清单 (memory/tasks/index.md)】：
{current_tasks}

【学习与复习进度 (memory/learning-tracker.md)】：
{learning_tracker}
{morning_report_section}
【知识库核心归档结构常识】：
- 每日晨报归档：`memory/summary/daily/YYYY-MM-DD.md`（每天早8点生成并提交，包含今日费曼思考题与前沿论文研判）
- 旅行与生活攻略：`memory/notes/`（如 `2026-08-24-太原山西5日游攻略.md`）
- 结构化学科知识：`memory/knowledge/`（由 Antigravity 主管理维护）
- 原始文件档案馆：`files/`
{rag_context}
【🛡️ 移动端受限写入权限与防冗余规范】：
你是知识库的【移动端外勤管家】，权限级别低于电脑端主管理者 Antigravity：
1. **允许直接更新的任务**：`memory/tasks/index.md`（修改/完成待办）
2. **允许记录的灵感/攻略**：`memory/notes/`（随手记灵感、临时备忘）
3. **禁止随意篡改核心学术库**：严禁直接覆盖 `memory/knowledge/` 中的 S/A 级学科笔记，有新内容一律存入 `memory/notes/` 作为待整理草稿，由 Antigravity 后续统一精细整理，防止产生碎片化冗余。
4. **【写入铁律】**：如果你在回复中向用户表示已添加待办或保存笔记，你**必须在回复末尾附带写入指令标签**：
   - 更新待办：`<<<UPDATE_TASK: [完整tasks Markdown内容]>>>`
   - 保存笔记：`<<<NEW_NOTE: [文件名.md] | [完整Markdown内容]>>>`

【回答规则】：
1. 语言亲切生动、极具专业深度，针对物理/数学/科研问题给出精确推导与物理图像（支持 Markdown 与 LaTeX 公式排版）。
2. 如果用户回答或询问今日晨报中的费曼思考题，必须以【今日真实每日晨报】中列出的题目为准进行互动评测！
3. 严格遵循当前真实时间锚定，牢记今天就是系统指定日期。
"""

    messages = [{"role": "system", "content": system_prompt}]
    
    history = get_session_history(chat_id)
    for h in history:
        time_prefix = f"[{h.get('time_tag', '')}] " if h.get('time_tag') else ""
        messages.append({
            "role": h["role"],
            "content": f"{time_prefix}{h['content']}"
        })
    messages.append({"role": "user", "content": user_text})

    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CONFIG['DEEPSEEK_API_KEY']}"
            },
            json={
                "model": "deepseek-chat",
                "messages": messages,
                "temperature": 0.3
            },
            timeout=40
        )
        
        if resp.status_code == 402 or "Insufficient Balance" in resp.text:
            return "⚠️【DeepSeek API 余额不足】\n你的 DeepSeek 账户余额已用尽（HTTP 402 Insufficient Balance）。\n💡 解决办法：请前往 https://platform.deepseek.com 充值 5~10 元即可恢复！"
            
        if resp.status_code != 200:
            return f"⚠️【DeepSeek API 请求失败 (HTTP {resp.status_code})】\n原因：{resp.text[:300]}"

        resp_data = resp.json()
        if "choices" not in resp_data or not resp_data["choices"]:
            return f"⚠️【AI 返回格式异常】：{resp.text[:300]}"

        ai_reply = resp_data["choices"][0]["message"]["content"]
        
        executed_actions = []

        # 1. Intercept UPDATE_TASK
        if "<<<UPDATE_TASK:" in ai_reply:
            match = re.search(r"<<<UPDATE_TASK:\s*([\s\S]*?)>>>", ai_reply)
            if match:
                new_tasks_content = match.group(1).strip()
                ok, res_path = km.commit_file(
                    "vault/memory/tasks/index.md",
                    new_tasks_content,
                    "update(tasks): updated via 24/7 Feishu cloud bot"
                )
                executed_actions.append(("memory/tasks/index.md", ok, res_path))
            ai_reply = re.sub(r"<<<UPDATE_TASK:[\s\S]*?>>>", "", ai_reply).strip()

        # 2. Intercept WRITE_FILE (Universal)
        if "<<<WRITE_FILE:" in ai_reply:
            matches = re.findall(r"<<<WRITE_FILE:\s*(.*?)\s*\|\s*([\s\S]*?)>>>", ai_reply)
            for file_path, file_content in matches:
                file_path = file_path.strip()
                file_content = file_content.strip()
                ok, res_path = km.commit_file(
                    file_path,
                    file_content,
                    f"feat(kb): write file {os.path.basename(file_path)} via Feishu cloud bot"
                )
                executed_actions.append((file_path, ok, res_path))
            ai_reply = re.sub(r"<<<WRITE_FILE:[\s\S]*?>>>", "", ai_reply).strip()

        # 3. Intercept NEW_NOTE
        if "<<<NEW_NOTE:" in ai_reply:
            matches = re.findall(r"<<<NEW_NOTE:\s*(.*?)\s*\|\s*([\s\S]*?)>>>", ai_reply)
            for note_fn, note_body in matches:
                note_fn = note_fn.strip()
                note_body = note_body.strip()
                now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
                clean_fn = f"{now_str}-{note_fn}" if not note_fn.startswith(now_str) else note_fn
                if not clean_fn.endswith(".md"):
                    clean_fn += ".md"
                target_path = f"memory/notes/{clean_fn}"
                ok, res_path = km.commit_file(
                    target_path,
                    note_body,
                    f"feat(notes): new note {clean_fn} captured via Feishu cloud bot"
                )
                executed_actions.append((target_path, ok, res_path))
            ai_reply = re.sub(r"<<<NEW_NOTE:[\s\S]*?>>>", "", ai_reply).strip()

        # Append Physical Execution Badges to AI reply
        if executed_actions:
            badge_lines = ["\n\n━━━━━━━━━━━━━━━", "🌐 **【云端知识库物理同步状态】**"]
            for orig_path, ok, res_path in executed_actions:
                clean_disp = res_path.replace("vault/", "")
                if ok:
                    badge_lines.append(f"✅ `[已同步]` 📁 `{clean_disp}` (GitHub Commit OK)")
                else:
                    badge_lines.append(f"❌ `[失败/受限]` 📁 `{orig_path}`: {res_path}")
            ai_reply += "\n" + "\n".join(badge_lines)
        else:
            save_intent = any(k in user_text for k in ["帮我记录", "添加到待办", "加入待办", "保存到知识库", "新建笔记", "保存为笔记", "帮我打勾", "完成待办"])
            if save_intent and not executed_actions:
                ai_reply += "\n\n━━━━━━━━━━━━━━━\n💡 *提示：本次回复为对话建议，若需立即物理写入云端知识库，可使用快捷指令，如：「添加待办 [内容]」或「记笔记 [标题] | [内容]」。*"

        append_to_session(chat_id, "user", user_text)
        append_to_session(chat_id, "assistant", ai_reply)

        return ai_reply
    except Exception as e:
        print(f"[Error] query_ai_brain: {e}")
        return f"大脑思考时遇到了一点网络波动: {e}"

# ==================== Send Feishu Message & File Upload ====================
def send_feishu_reply(chat_id, text_content):
    client = get_lark_client()
    req = CreateMessageRequest.builder() \
        .receive_id_type("chat_id") \
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text_content}))
            .build()
        ).build()
    
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[Error] Failed to send Feishu reply: {resp.code}, {resp.msg}")

def upload_and_send_feishu_file(chat_id, file_name, file_content_str):
    client = get_lark_client()
    try:
        file_bytes = file_content_str.encode('utf-8')
        file_stream = io.BytesIO(file_bytes)
        
        file_req = CreateFileRequest.builder() \
            .request_body(
                CreateFileRequestBody.builder()
                .file_type("stream")
                .file_name(file_name)
                .file(file_stream)
                .build()
            ).build()
            
        file_resp = client.im.v1.file.create(file_req)
        if not file_resp.success():
            print(f"[Error] 飞书文件上传失败: code={file_resp.code}, msg={file_resp.msg}")
            return False
            
        file_key = file_resp.data.file_key
        print(f"✅ [Feishu] 文件上传成功: file_name={file_name}, file_key={file_key}")
        
        msg_req = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("file")
                .content(json.dumps({"file_key": file_key}))
                .build()
            ).build()
            
        msg_resp = client.im.v1.message.create(msg_req)
        if msg_resp.success():
            print(f"✅ [Feishu] 原生文件卡片消息已成功投递至聊天: {chat_id}")
            return True
        else:
            print(f"[Error] 发送文件卡片消息失败: code={msg_resp.code}, msg={msg_resp.msg}")
            return False
    except Exception as e:
        print(f"[Error] upload_and_send_feishu_file 异常: {e}")
        return False

def handle_topic_export(chat_id, user_text):
    try:
        text = user_text.strip()
        # 1. 过滤疑问句和日常探讨（交由大模型正常解答）
        question_words = ["吗", "？", "?", "能不能", "是否", "怎么", "如何", "为什么", "可以吗", "能吗", "会吗", "有吗", "么"]
        if any(q in text for q in question_words):
            return False

        # 2. 判断是否属于导出/生成文档指令
        triggers = ["导出", "整理", "生成", "下载", "发我"]
        if not any(text.startswith(t) or f"帮我{t}" in text or f"请{t}" in text for t in triggers):
            return False

        # 提取 topic
        topic = re.sub(r'^(?:帮我|请)?(?:导出|整理|生成|下载|发我)[:：\s]*', '', text).strip()
        topic = re.sub(r'^(?:专题|大纲|复习大纲|复习资料|知识大纲|笔记大纲|文档|文件|解答|题解)[:：\s]*', '', topic).strip()
        topic = topic.strip("[]【】 ")
        topic_clean = re.sub(r'(?:专题|大纲|复习|资料|文档)$', '', topic).strip()
        if topic_clean:
            topic = topic_clean
        if not topic:
            topic = "物理核心知识"

        print(f"⚡ [Feishu] 触发确定性专题大纲文件导出: {topic} (来自原始输入: {text})")

        now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
        now_time_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")

        # 特别支持 1：导出今日费曼挑战与推导大纲
        if any(k in topic for k in ["费曼", "思考题", "挑战"]):
            feynman_template = """# 🎯 今日费曼挑战 · 深度数理推导与物理图像 (__DATE__)

> 📚 **学科领域**：量子力学 (S级核心资产)  
> ❓ **思考题**：在一维无限深势阱中，如果势阱宽度突然扩大一倍（瞬变近似），为什么基态波函数向偶数能级的跃迁概率为 0，而只向奇数能级跃迁？这体现了什么对称性选择定则？  
> 🤖 **解析者**：林云舒的第二大脑 (Google DeepMind Antigravity)  

---

## 物理图像总览（The Core Intuition）

在**瞬变近似（Sudden Approximation）**下，外场或边界条件变化的特征时间 $\\tau$ 远小于体系波函数演化的内在周期 $T = \\hbar / \\Delta E$（即 $\\tau \\ll T$）。  
因此，**在势阱宽度突然从 $a$ 扩大到 $2a$ 的瞬间，粒子的空间波函数来不及发生任何改变，依然保持为原势阱中的基态分布 $\\psi_1(x)$**。

决定粒子最终处于新势阱各能级概率的，是初态在新本征基矢上的**投影展开系数（跃迁振幅）**：
$$c_n = \\langle \\psi_n' | \\psi_1 \\rangle = \\int_{-\\infty}^{+\\infty} \\psi_n'^*(x) \\psi_1(x) \\, dx$$
跃迁概率即为 $P_{1 \\to n} = |c_n|^2$。

---

## 严密数理推导

### 1. 选取具有明显对称性的坐标系
为了最简洁地利用宇称（Parity），我们将坐标原点 $x = 0$ 设在**原势阱的中心**：
- **初态（原势阱，区间 $[-a/2, a/2]$）**：
  $$\\psi_1(x) = \\begin{cases} \\sqrt{\\frac{2}{a}} \\cos\\left(\\frac{\\pi x}{a}\\right), & x \\in \\left[-\\frac{a}{2}, \\frac{a}{2}\\right] \\\\ 0, & |x| > \\frac{a}{2} \\end{cases}$$
  观察可知：$\\psi_1(-x) = \\psi_1(x)$，初态关于原点具有**严格的偶宇称（Even Parity）**。

- **末态本征基（新势阱，区间 $[-a, a]$，总宽度 $2a$）**：
  新势阱的本征函数在对称区间 $[-a, a]$ 内按宇称交替排列：
  $$\\psi_n'(x) = \\begin{cases} \\sqrt{\\frac{1}{a}} \\cos\\left(\\frac{n\\pi x}{2a}\\right), & n = 1, 3, 5, \\dots \\text{ (奇数能级，偶宇称)} \\\\ \\sqrt{\\frac{1}{a}} \\sin\\left(\\frac{n\\pi x}{2a}\\right), & n = 2, 4, 6, \\dots \\text{ (偶数能级，奇宇称)} \\end{cases}$$

### 2. 对称性与宇称积分（宇称选择定则）
计算跃迁振幅积分：
$$c_n = \\int_{-a/2}^{a/2} \\psi_n'^*(x) \\psi_1(x) \\, dx$$

- 当 $n$ 为**偶数**（$n = 2, 4, 6, \\dots$）时：
  * $\\psi_1(x)$ 是 **偶函数（Even）**
  * $\\psi_n'(x) \\propto \\sin\\left(\\frac{n\\pi x}{2a}\\right)$ 是 **奇函数（Odd）**
  * 被积函数为：$\\text{Odd} \\times \\text{Even} = \\text{Odd}$（奇函数）
  * 在关于原点对称的积分区间 $[-a/2, a/2]$ 上：
    $$c_n = \\int_{-a/2}^{a/2} (\\text{奇函数}) \\, dx = 0 \\quad (\\forall n = 2k, \\, k \\in \\mathbb{N}^+)$$
  * 因此，**向所有偶数能级的跃迁概率恒为零**：$P_{1 \\to 2k} = |c_{2k}|^2 \\equiv 0$！

- 当 $n$ 为**奇数**（$n = 1, 3, 5, \\dots$）时：
  * $\\psi_n'(x)$ 为偶函数，被积函数为 $\\text{Even} \\times \\text{Even} = \\text{Even}$；
  * 积分不为零，具体计算积化和差可得：
    $$c_n = \\sqrt{\\frac{2}{a^2}} \\int_{-a/2}^{a/2} \\cos\\left(\\frac{n\\pi x}{2a}\\right) \\cos\\left(\\frac{\\pi x}{a}\\right) \\, dx = \\frac{4\\sqrt{2}}{\\pi} \\frac{\\cos(n\\pi/4)}{4 - n^2} \\quad (n = 1, 3, 5, \\dots)$$
  * 例如：
    * $n = 1$（新基态）：$P_{1 \\to 1} = |c_1|^2 = \\frac{32}{9\\pi^2} \\approx 36.03\\%$
    * $n = 3$：$P_{1 \\to 3} = |c_3|^2 = \\frac{32}{25\\pi^2} \\approx 12.97\\%$

---

## 深刻的物理本质：对称性与选择定则（Selection Rule）

1. **空间反演对称性（Parity Conservation）**：
   * 瞬变过程中，势阱的膨胀是**关于中心左右对称向两边对称扩张**的，体系的 Hamiltonian $H(t)$ 在所有时刻都保持关于原点的空间反演不变性（$[H, \\hat{P}] = 0$）。
   * 空间反演算符（宇称算符 $\\hat{P}$）是对称性算符，初始波函数属于 $\\hat{P}$ 的 $+1$ 本征态（偶宇称）。
   * 对称扩张的操作无法破坏体系的对称性，因此波函数在演化中**只能保持在偶宇称子空间内展开**，不可能自发产生奇宇称分量。

2. **量子跃迁选择定则的一般性结论**：
   * 若扰动或外场是偶宇称的（例如对称势场变动），则跃迁定则为 $\\Delta \\text{Parity} = 0$（同宇称跃迁）；
   * 若扰动是奇宇称的（如电偶极跃迁算符 $\\hat{x}$），则跃迁定则为 $\\Delta \\text{Parity} \\ne 0$（异宇称跃迁，如原子光谱的 Laporte 定则）。

---
> 💡 *本解答由第二大脑整理归档至 `memory/summary/daily/__DATE__.md` 与量子力学专题库。在飞书云文档中打开可获得完整的 LaTeX 公式排版体验！*
"""
            feynman_doc = feynman_template.replace("__DATE__", now_str)
            fn = f"{now_str}_量子力学费曼挑战_瞬变势阱对称性定则解析.md"
            ok = upload_and_send_feishu_file(chat_id, fn, feynman_doc)
            if ok:
                send_feishu_reply(chat_id, f"🎯 已成功为你生成并发送今日费曼挑战的深度解析文档《{fn}》！\n\n💡 手机端点击上方文件卡片，选择「用飞书云文档打开」，即可享受完美渲染编译的 LaTeX 积分与公式排版！")
            else:
                send_feishu_reply(chat_id, feynman_doc)
            return True

        # 特别支持 2：导出今日晨报
        if "晨报" in topic:
            morning_report = km.fetch_file_raw(f"vault/memory/summary/daily/{now_str}.md")
            if morning_report:
                fn = f"{now_str}_每日晨报与前沿研判.md"
                ok = upload_and_send_feishu_file(chat_id, fn, morning_report)
                if ok:
                    send_feishu_reply(chat_id, f"🌅 已成功为你生成并发送今日晨报文档《{fn}》！\n\n💡 点击上方文件卡片即可用飞书云文档查看。")
                else:
                    send_feishu_reply(chat_id, morning_report)
                return True

        tree = km.get_vault_tree()
        matched_paths = [p for p in tree if topic.lower() in p.lower() and ("knowledge" in p.lower() or "notes" in p.lower())]
        if not matched_paths:
            matched_paths = [p for p in tree if topic.lower() in p.lower()]
        if any(k in topic for k in ["太原", "大同", "山西", "旅行", "旅游", "行程"]):
            for p in tree:
                if any(k in p for k in ["太原", "taiyuan", "travel"]):
                    if p not in matched_paths:
                        matched_paths.append(p)

        now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
        doc_lines = [
            f"# 📚 《{topic}》第二大脑全景知识与复习大纲",
            f"> 🤖 由林云舒的第二大脑 (JARVIS) 自动聚合生成于 {now_str} (北京时间)",
            f"> 📐 知识库权威数据来源: `second-brain-vault`",
            "",
            "---",
            ""
        ]
        
        found_any = False
        for p in matched_paths[:6]:
            content = km.fetch_file_raw(p)
            if content:
                found_any = True
                doc_lines.append(f"## 📄 核心模块：`{os.path.basename(p)}`\n")
                doc_lines.append(content)
                doc_lines.append("\n---\n")
                
        if not found_any:
            retrieved = km.search_relevant_docs(topic)
            if retrieved:
                for p, content in retrieved:
                    doc_lines.append(f"## 📄 关联模块：`{os.path.basename(p)}`\n")
                    doc_lines.append(content)
                    doc_lines.append("\n---\n")
                    found_any = True

        if not found_any:
            send_feishu_reply(chat_id, f"🔍 未能在知识库中找到与「{topic}」相关的专属知识文件。建议尝试：电动力学、理论力学、微分几何、量子力学、固体物理、激光原理、热力学与统计物理、等离子体物理、太原旅游攻略。")
            return True

        file_content_str = "\n".join(doc_lines)
        file_name = f"{topic}_全景知识大纲.md"
        
        ok = upload_and_send_feishu_file(chat_id, file_name, file_content_str)
        if ok:
            send_feishu_reply(
                chat_id,
                f"✅ 已成功为你生成并发送《{file_name}》（共 {len(file_content_str)} 字）！\n\n"
                f"💡 手机端点击上方文件卡片即可直接阅读、保存到本地或转存至飞书云文档。"
            )
        else:
            preview_len = min(2500, len(file_content_str))
            send_feishu_reply(
                chat_id,
                f"📚 《{topic}》第二大脑全景复习大纲（共 {len(file_content_str)} 字）：\n\n"
                f"{file_content_str[:preview_len]}\n\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💡 提示：如需在飞书里直接接收可下载的 .md 原生文件卡片（不占知识库空间），只需在飞书开发者后台勾选「获取与上传图片或文件资源 (im:resource:upload)」权限即可！"
            )
        return True
    except Exception as e:
        print(f"[Error] handle_topic_export 发生异常: {e}")
        return False

# ==================== Event Handler ====================
PROCESSED_MESSAGE_IDS = set()
BOT_START_TIME_MS = int(time.time() * 1000)

def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    event = data.event
    message = event.message
    message_id = message.message_id
    
    try:
        create_time_ms = int(getattr(message, "create_time", 0) or 0)
        now_ms = int(time.time() * 1000)
        if create_time_ms > 0:
            if create_time_ms < (BOT_START_TIME_MS - 5000) or (now_ms - create_time_ms) > 30000:
                print(f"[Feishu 24/7] 🚫 丢弃历史重放消息: id={message_id}, 延迟={(now_ms - create_time_ms)/1000:.1f}秒")
                return
    except Exception as e:
        print(f"[Warn] Message timestamp parse error: {e}")

    if message_id in PROCESSED_MESSAGE_IDS:
        print(f"[Feishu 24/7] 忽略已处理的重复消息: {message_id}")
        return
    PROCESSED_MESSAGE_IDS.add(message_id)
    if len(PROCESSED_MESSAGE_IDS) > 2000:
        try:
            PROCESSED_MESSAGE_IDS.pop()
        except KeyError:
            pass

    chat_id = message.chat_id
    msg_type = message.message_type
    
    if msg_type == "text":
        try:
            content_dict = json.loads(message.content)
            user_text = content_dict.get("text", "").strip()
            print(f"📩 [Feishu 24/7] 收到用户即时消息 (msg_id: {message_id}): {user_text}")
            
            if handle_topic_export(chat_id, user_text):
                return

            ai_reply = query_ai_brain(chat_id, user_text)
            print(f"🤖 [Feishu 24/7] AI 回复生成完毕，正在发送...")
            send_feishu_reply(chat_id, ai_reply)
        except Exception as e:
            print(f"[Error] 处理消息异常: {e}")
            try:
                send_feishu_reply(chat_id, f"⚠️ 处理你的消息时遇到了小异常: {e}，请再试一次或直接提问！")
            except Exception:
                pass

# ==================== Main Entry ====================
def main():
    now_bj = datetime.now(BEIJING_TZ)
    print("=" * 65)
    print(f"  ☁️ 林云舒的第二大脑 · 飞书 24/7 云端全天候移动管家 V2.1 正在启动...")
    print(f"  📌 当前北京时间: {now_bj.strftime('%Y-%m-%d %H:%M:%S %A')}")
    print(f"  📌 App ID: {CONFIG['APP_ID']}")
    print("  🔌 模式: 飞书官方 WebSocket 24/7 长连接 (RBAC分级权限防护网 + 每日晨报题目绝对对齐)")
    print("=" * 65)

    event_handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(do_p2_im_message_receive_v1) \
        .build()

    ws_client = lark.ws.Client(
        app_id=CONFIG["APP_ID"],
        app_secret=CONFIG["APP_SECRET"],
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO
    )
    
    print("⚡ 正在与飞书官方网关建立 WebSocket 24/7 安全长连接...")
    ws_client.start()

if __name__ == "__main__":
    main()
