"""
Feishu 24/7 WebSocket Second Brain Bot
======================================
Cloud Always-On Daemon for Lin Yunshu
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

# Windows console encoding fix
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    P2ImMessageReceiveV1,
    CreateMessageRequest,
    CreateMessageRequestBody
)

CONFIG = {
    "APP_ID": os.environ.get("FEISHU_APP_ID", "cli_aa09bb45ebf89bda"),
    "APP_SECRET": os.environ.get("FEISHU_APP_SECRET", "V02XmqKk5HXUQw43XEx6Gz1hJ0Zd5SNV"),
    "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", "sk-52386a6bd06742c4900b9413923b8010"),
    "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN", "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"),
    "GITHUB_REPO": os.environ.get("GITHUB_REPO", "althouseaikman684-stack/second-brain-vault")
}

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

# ==================== Cloud Knowledge Base Read / Write ====================
def fetch_github_file(path):
    try:
        url = f"https://api.github.com/repos/{CONFIG['GITHUB_REPO']}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {CONFIG['GITHUB_TOKEN']}",
            "User-Agent": "Feishu-Second-Brain-Bot",
            "Accept": "application/vnd.github.v3+json"
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            import base64
            content = base64.b64decode(resp.json()["content"]).decode("utf-8")
            return {"content": content, "sha": resp.json()["sha"]}
    except Exception as e:
        print(f"[Error] fetch_github_file: {e}")
    return None

def commit_github_file(path, content, message, sha=None):
    try:
        url = f"https://api.github.com/repos/{CONFIG['GITHUB_REPO']}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {CONFIG['GITHUB_TOKEN']}",
            "User-Agent": "Feishu-Second-Brain-Bot",
            "Accept": "application/vnd.github.v3+json"
        }
        import base64
        body = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8")
        }
        if sha:
            body["sha"] = sha
        resp = requests.put(url, headers=headers, json=body, timeout=10)
        return resp.status_code in [200, 201]
    except Exception as e:
        print(f"[Error] commit_github_file: {e}")
        return False

# ==================== DeepSeek AI Reasoning ====================
def query_ai_brain(user_text):
    tasks_data = fetch_github_file("vault/memory/tasks/index.md")
    current_tasks = tasks_data["content"] if tasks_data else "暂无任务清单"
    
    system_prompt = f"""你是林云舒的第二大脑（基于 Google DeepMind Antigravity 架构），正在飞书移动端为云舒提供全天候 24 小时科研与日程助理服务。

【用户档案】：
- 姓名：林云舒（湖南大学应用物理学本科，已保研至中科大/合肥物质院等离子体所张伟组，研究方向：磁约束核聚变 ICRF 波加热与托卡马克物理）
- 近期关键日程：2026年8月24日-28日 太原5日游（海友酒店、云冈石窟、晋祠）；8月29日正式启动等离子体物理25天学习计划（双轨体系：Chen导论 + 武松涛托卡马克工程）。

【当前待办清单 (tasks/index.md)】：
{current_tasks}

【回答规则】：
1. 语言亲切生动、极具专业深度，针对物理问题给出精确推导与物理图像（支持 Markdown 与 LaTeX 公式）。
2. 如果用户要求修改待办或标记完成，请在回复末尾附带：
   <<<UPDATE_TASK: [替换后的整个tasks/index.md内容]>>>
3. 如果用户要求记录想法/灵感/随手记，请在回复末尾附带：
   <<<NEW_NOTE: [文件名.md] | [笔记Markdown内容]>>>
"""
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {CONFIG['DEEPSEEK_API_KEY']}"
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                "temperature": 0.3
            },
            timeout=30
        )
        ai_reply = resp.json()["choices"][0]["message"]["content"]
        
        # Intercept task update
        if "<<<UPDATE_TASK:" in ai_reply:
            import re
            match = re.search(r"<<<UPDATE_TASK:\s*([\s\S]*?)>>>", ai_reply)
            if match and tasks_data:
                commit_github_file(
                    "vault/memory/tasks/index.md",
                    match.group(1).strip(),
                    "update(tasks): updated via 24/7 Feishu cloud bot",
                    tasks_data["sha"]
                )
            ai_reply = re.sub(r"<<<UPDATE_TASK:[\s\S]*?>>>", "", ai_reply).strip()

        # Intercept note creation
        if "<<<NEW_NOTE:" in ai_reply:
            import re
            match = re.search(r"<<<NEW_NOTE:\s*(.*?)\s*\|\s*([\s\S]*?)>>>", ai_reply)
            if match:
                now_str = datetime.now().strftime("%Y-%m-%d")
                fn = f"{now_str}-{match.group(1).strip()}"
                if not fn.endswith(".md"):
                    fn += ".md"
                commit_github_file(
                    f"vault/memory/notes/{fn}",
                    match.group(2).strip(),
                    f"feat(notes): new note {fn} captured via Feishu cloud bot"
                )
            ai_reply = re.sub(r"<<<NEW_NOTE:[\s\S]*?>>>", "", ai_reply).strip()

        return ai_reply
    except Exception as e:
        return f"大脑思考时遇到了一点网络波动: {e}"

# ==================== Send Feishu Message ====================
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

# ==================== Event Handler ====================
PROCESSED_MESSAGE_IDS = set()

def do_p2_im_message_receive_v1(data: P2ImMessageReceiveV1) -> None:
    event = data.event
    message = event.message
    message_id = message.message_id
    
    # 严格去重防重发
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
            print(f"📩 [Feishu 24/7] 收到用户消息 (msg_id: {message_id}): {user_text}")
            
            ai_reply = query_ai_brain(user_text)
            print(f"🤖 [Feishu 24/7] AI 回复生成完毕，正在发送...")
            send_feishu_reply(chat_id, ai_reply)
        except Exception as e:
            print(f"[Error] 处理消息异常: {e}")

# ==================== Main Entry ====================
def main():
    print("=" * 60)
    print("  ☁️ 林云舒的第二大脑 · 飞书 24/7 云端全天候移动管家 正在启动...")
    print(f"  📌 App ID: {CONFIG['APP_ID']}")
    print("  🔌 模式: 飞书官方 WebSocket 长连接 (24/7 永不掉线)")
    print("=" * 60)

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
