# -*- coding: utf-8 -*-
"""
Feishu 24/7 WebSocket Second Brain Bot (Cloud Always-On Daemon)
==============================================================
Lin Yunshu's Second Brain Mobile Gateway
"""

import os
import sys
import json
import time
import re
import requests
from datetime import datetime, timezone, timedelta

# Fix Windows console encoding
if sys.platform == "win32":
    import io
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
import io

# ==================== Credentials & Configuration ====================
CONFIG = {
    "APP_ID": os.environ.get("FEISHU_APP_ID", "cli_aa09bb45ebf89bda"),
    "APP_SECRET": os.environ.get("FEISHU_APP_SECRET", "V02XmqKk5HXUQw43XEx6Gz1hJ0Zd5SNV"),
    "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", "sk-52386a6bd06742c4900b9413923b8010"),
    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", "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"),
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

# ==================== Cloud Knowledge Base Manager ====================
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

    def fetch_file_raw(self, path):
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
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        try:
            r = requests.get(url, headers=self.headers_json, timeout=10)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print(f"[Error] fetch_file_json({path}): {e}")
        return None

    def commit_file(self, path, content, message, sha=None):
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        import base64
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8")
        }
        if not sha:
            current_info = self.fetch_file_json(path)
            if current_info and "sha" in current_info:
                sha = current_info["sha"]
        if sha:
            body["sha"] = sha
            
        try:
            r = requests.put(url, headers=self.headers_json, json=body, timeout=15)
            # Invalidate cache
            if path in self.cache:
                del self.cache[path]
            return r.status_code in [200, 201]
        except Exception as e:
            print(f"[Error] commit_file({path}): {e}")
            return False

    def get_vault_tree(self):
        now_ts = time.time()
        if self.tree_cache and (now_ts - self.tree_cache_time < 600):
            return self.tree_cache
        url = f"https://api.github.com/repos/{self.repo}/git/trees/main?recursive=1"
        try:
            r = requests.get(url, headers=self.headers_json, timeout=10)
            if r.status_code == 200:
                tree = r.json().get("tree", [])
                self.tree_cache = [x["path"] for x in tree if x["path"].endswith(".md") and x["path"].startswith("vault/")]
                self.tree_cache_time = now_ts
                return self.tree_cache
        except Exception as e:
            print(f"[Error] get_vault_tree: {e}")
        return self.tree_cache

    def get_all_md_files(self):
        return self.get_vault_tree()

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
            "vault/memory/AGENTS.md"
        }
        
        # Extract English terms and Chinese n-grams
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
        top_paths = [p for s, p in scored[:2]]

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

# ==================== DeepSeek AI Brain Reasoning ====================
def query_ai_brain(chat_id, user_text):
    # Support reset memory commands
    if user_text.strip() in ["清空对话", "重置记忆", "新话题", "reset", "/reset"]:
        reset_session(chat_id)
        return "🧠 对话上下文记忆已重置完毕！我们开启一个崭新的话题吧。"

    time_ctx = get_time_and_schedule_context()
    
    # 1. Fetch core knowledge files
    kb_profile = km.fetch_file_raw("vault/KB_PROFILE.md") or "暂无个人档案"
    current_tasks = km.fetch_file_raw("vault/memory/tasks/index.md") or "暂无任务清单"
    learning_tracker = km.fetch_file_raw("vault/memory/learning-tracker.md") or "暂无学习追踪"

    # 2. Dynamic RAG retrieval for specific academic/project/literature queries
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
{rag_context}
【回答规则】：
1. 语言亲切生动、极具专业深度，针对物理/数学/科研问题给出精确推导与物理图像（支持 Markdown 与 LaTeX 公式排版）。
2. 你具备连续多轮对话记忆能力，请紧密结合前文聊过的内容进行连贯回答。
3. 严格遵循当前真实时间锚定，牢记今天就是系统指定日期。
4. 如果用户要求修改待办、打勾完成、新增任务，请在回复末尾附带：
   <<<UPDATE_TASK: [替换后的整个 memory/tasks/index.md 内容]>>>
5. 如果用户要求记录想法/灵感/随手记，请在回复末尾附带：
   <<<NEW_NOTE: [简短英文或拼音或中文文件名.md] | [笔记Markdown完整内容]>>>
"""

    messages = [{"role": "system", "content": system_prompt}]
    
    # Build history with timestamps
    history = get_session_history(chat_id)
    now_ts = time.time()
    for h in history:
        # If message was from > 4 hours ago, prepend a temporal note
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
            timeout=35
        )
        
        if resp.status_code == 402 or "Insufficient Balance" in resp.text:
            return "⚠️【DeepSeek API 余额不足】\n你的 DeepSeek 账户余额已用尽（HTTP 402 Insufficient Balance）。\n💡 解决办法：请前往 https://platform.deepseek.com 充值 5~10 元，或生成新 Key 发给电脑端的 Antigravity，即可秒级恢复！"
            
        if resp.status_code != 200:
            return f"⚠️【DeepSeek API 请求失败 (HTTP {resp.status_code})】\n原因：{resp.text[:300]}"

        resp_data = resp.json()
        if "choices" not in resp_data or not resp_data["choices"]:
            return f"⚠️【AI 返回格式异常】：{resp.text[:300]}"

        ai_reply = resp_data["choices"][0]["message"]["content"]
        
        # Intercept task updates
        if "<<<UPDATE_TASK:" in ai_reply:
            match = re.search(r"<<<UPDATE_TASK:\s*([\s\S]*?)>>>", ai_reply)
            if match:
                new_tasks_content = match.group(1).strip()
                km.commit_file(
                    "vault/memory/tasks/index.md",
                    new_tasks_content,
                    "update(tasks): updated via 24/7 Feishu cloud bot"
                )
            ai_reply = re.sub(r"<<<UPDATE_TASK:[\s\S]*?>>>", "", ai_reply).strip()

        # Intercept new notes
        if "<<<NEW_NOTE:" in ai_reply:
            match = re.search(r"<<<NEW_NOTE:\s*(.*?)\s*\|\s*([\s\S]*?)>>>", ai_reply)
            if match:
                note_fn = match.group(1).strip()
                note_body = match.group(2).strip()
                now_str = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d")
                clean_fn = f"{now_str}-{note_fn}"
                if not clean_fn.endswith(".md"):
                    clean_fn += ".md"
                km.commit_file(
                    f"vault/memory/notes/{clean_fn}",
                    note_body,
                    f"feat(notes): new note {clean_fn} captured via Feishu cloud bot"
                )
            ai_reply = re.sub(r"<<<NEW_NOTE:[\s\S]*?>>>", "", ai_reply).strip()

        # Store in session memory
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
    """
    通过飞书官方开放平台 API 上传文件流，并以原生文件卡片形式发送到聊天窗口
    用户在手机飞书上可直接点击阅读、保存到本地或转存为飞书云文档
    """
    client = get_lark_client()
    try:
        file_bytes = file_content_str.encode('utf-8')
        file_stream = io.BytesIO(file_bytes)
        
        # 1. 上传文件到飞书
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
        
        # 2. 发送文件卡片消息
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
    """
    智能拦截大纲与专题导出指令，生成完整 Markdown 文档并上传为飞书原生文件卡片
    """
    try:
        # 判断是否为导出大纲/文档指令
        is_export = any(k in user_text for k in ["导出大纲", "导出专题", "整理大纲", "生成大纲", "导出复习", "导出笔记", "大纲文档"])
        if not is_export and not user_text.strip().startswith("导出"):
            return False

        # 提取关键字
        topic = user_text
        for prefix in ["帮我导出", "导出专题", "导出大纲", "导出复习资料", "整理专题", "整理大纲", "生成大纲", "导出"]:
            if prefix in topic:
                topic = topic.replace(prefix, "")
        topic = topic.replace("大纲", "").replace("专题", "").replace("复习", "").replace("资料", "").replace("文档", "").strip()
        if not topic:
            topic = "物理核心知识"

        print(f"⚡ [Feishu] 触发专题大纲文件导出: {topic}")
        
        # 检索相关知识文件
        tree = km.get_vault_tree()
        matched_paths = [p for p in tree if topic.lower() in p.lower() and "knowledge" in p.lower()]
        if not matched_paths:
            matched_paths = [p for p in tree if topic.lower() in p.lower()]

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
            send_feishu_reply(chat_id, f"🔍 未能在知识库中找到与「{topic}」相关的专属知识文件。建议尝试：电动力学、理论力学、微分几何、量子力学、固体物理、激光原理、热力学与统计物理、等离子体物理。")
            return True

        file_content_str = "\n".join(doc_lines)
        file_name = f"{topic}_全景知识大纲.md"
        
        # 尝试上传飞书原生文件卡片
        ok = upload_and_send_feishu_file(chat_id, file_name, file_content_str)
        if ok:
            send_feishu_reply(chat_id, f"✅ 已成功为你生成并发送《{file_name}》（共约 {len(file_content_str)} 字）！\n💡 提示：在手机飞书上点击上方文件卡片即可直接阅读、保存到本地或转存至飞书云文档。")
        else:
            # 优雅降级为精美富文本大纲发送
            preview_len = min(2000, len(file_content_str))
            send_feishu_reply(
                chat_id,
                f"📚 已为你生成《{topic}》第二大脑全景复习大纲（共约 {len(file_content_str)} 字）：\n\n"
                f"{file_content_str[:preview_len]}\n\n"
                f"💡 提示：如需在飞书里直接接收可下载的 .md 原生文件卡片，可前往飞书开发者后台给应用开通「获取与上传图片或文件资源 (im:resource:upload)」权限即可一键直达！"
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
    
    # 1. Filter out stale / replayed messages (> 30 seconds old or before start)
    try:
        create_time_ms = int(getattr(message, "create_time", 0) or 0)
        now_ms = int(time.time() * 1000)
        if create_time_ms > 0:
            if create_time_ms < (BOT_START_TIME_MS - 5000) or (now_ms - create_time_ms) > 30000:
                print(f"[Feishu 24/7] 🚫 丢弃历史重放消息: id={message_id}, 延迟={(now_ms - create_time_ms)/1000:.1f}秒")
                return
    except Exception as e:
        print(f"[Warn] Message timestamp parse error: {e}")

    # 2. Strict deduplication
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
            
            # 优先检查是否为大纲/专题导出指令
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
    print(f"  ☁️ 林云舒的第二大脑 · 飞书 24/7 云端全天候移动管家 正在启动...")
    print(f"  📌 当前北京时间: {now_bj.strftime('%Y-%m-%d %H:%M:%S %A')}")
    print(f"  📌 App ID: {CONFIG['APP_ID']}")
    print("  🔌 模式: 飞书官方 WebSocket 24/7 长连接 (实时时钟锚定 + 动态 RAG 检索)")
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
