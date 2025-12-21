import streamlit as st
from openai import OpenAI
import json
import os
import httpx
import re
import time
import glob
import shutil
from datetime import datetime
import difflib

# --- 常量定义 ---
HISTORY_DIR = "history"
CONFIG_DIR = "configs"
DEFAULT_CONFIG_NAME = "default_config.json"
OLD_CONFIG_V3 = "chat_config_v3.json"

DEFAULT_CONFIG = {
    "global": {
        "round_limit": 10,
        "context_limit": 10,
        "mod_interval": 5,
        "mod_enabled": True,
        "infinite_mode": False,
        "auto_speed": 1.5
    },
    "a_config": {
        "name": "角色 A", "model": "gpt-3.5-turbo", "base_url": "https://api.openai.com/v1",
        "api_key": "", "temp": 0.7, "use_thought": True,
        "system": "你是一个充满创意的艺术家，思维跳跃，喜欢用感性的语言描述世界。"
    },
    "b_config": {
        "name": "角色 B", "model": "gpt-3.5-turbo", "base_url": "https://api.openai.com/v1",
        "api_key": "", "temp": 0.9, "use_thought": True,
        "system": "你是一个严谨的程序员，逻辑缜密，喜欢用结构化的代码或伪代码来解释问题。"
    },
    "mod_config": {
        "name": "主持人", "model": "gpt-3.5-turbo", "base_url": "https://api.openai.com/v1",
        "api_key": "", "temp": 0.5,
        # 新增 role_mode 字段，默认为 "normal"
        "role_mode": "normal", 
        "system": "你是一个对话引导者。你需要总结当前的交流内容，并提出新的方向以加深讨论。"
    }
}

# --- 初始化目录 ---
if not os.path.exists(HISTORY_DIR): os.makedirs(HISTORY_DIR)
if not os.path.exists(CONFIG_DIR): os.makedirs(CONFIG_DIR)

# --- 配置管理 (略微精简，逻辑不变) ---
def migrate_old_config():
    if os.path.exists(OLD_CONFIG_V3):
        target = os.path.join(CONFIG_DIR, "migrated_config.json")
        if not os.path.exists(target):
            try: shutil.copy(OLD_CONFIG_V3, target)
            except: pass

def get_config_files():
    files = glob.glob(os.path.join(CONFIG_DIR, "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    return files

def load_config_data(filepath=None):
    data = DEFAULT_CONFIG.copy()
    loaded_content = None
    if filepath and os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f: loaded_content = json.load(f)
        except: pass
    if not loaded_content:
        files = get_config_files()
        if files:
            try:
                with open(files[0], "r", encoding="utf-8") as f: loaded_content = json.load(f)
            except: pass
            
    if loaded_content:
        for section, settings in DEFAULT_CONFIG.items():
            if section not in loaded_content: loaded_content[section] = settings
            elif isinstance(settings, dict):
                for k, v in settings.items():
                    if k not in loaded_content[section]: loaded_content[section][k] = v
        return loaded_content
    return DEFAULT_CONFIG

def save_config_to_file(config_data, filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(CONFIG_DIR, filename)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)
        return True, filepath
    except Exception as e: return False, str(e)

migrate_old_config()

# --- 历史记录 ---
def get_history_files():
    files = glob.glob(os.path.join(HISTORY_DIR, "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    return files

def save_current_session(filename):
    if not filename.endswith(".json"): filename += ".json"
    filepath = os.path.join(HISTORY_DIR, filename)
    data_to_save = {
        "messages": st.session_state.messages,
        "turn_count": st.session_state.turn_count,
        "long_term_memory": st.session_state.long_term_memory, # 保存长期记忆
        "refiner_state": st.session_state.refiner_state, # 保存提炼员状态
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        return True, filepath
    except Exception as e: return False, str(e)

def load_session_from_file(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f: data = json.load(f)
        if isinstance(data, list):
            st.session_state.messages = data
            b_name = st.session_state.app_config["b_config"]["name"]
            st.session_state.turn_count = sum(1 for m in data if m.get("role") == b_name)
            st.session_state.long_term_memory = "" # 旧版没有记忆
        else:
            st.session_state.messages = data.get("messages", [])
            st.session_state.turn_count = data.get("turn_count", 0)
            st.session_state.long_term_memory = data.get("long_term_memory", "")
            # 加载提炼员状态（如果存在）
            if "refiner_state" in data:
                st.session_state.refiner_state = data["refiner_state"]
            else:
                # 如果没有保存提炼员状态，则初始化
                st.session_state.refiner_state = {'last_summary': "", 'refine_turn': 0, 'MAX_REFINE_TURNS': 3}
        
        # 【安全气囊】修复加载后可能出现的连续User消息问题
        i = 0
        while i < len(st.session_state.messages):
            if (i < len(st.session_state.messages) - 1 and 
                st.session_state.messages[i]["role"] != st.session_state.app_config["a_config"]["name"] and
                st.session_state.messages[i]["role"] != st.session_state.app_config["b_config"]["name"] and
                st.session_state.messages[i]["role"] != st.session_state.app_config["mod_config"]["name"] and
                st.session_state.messages[i+1]["role"] != st.session_state.app_config["a_config"]["name"] and
                st.session_state.messages[i+1]["role"] != st.session_state.app_config["b_config"]["name"] and
                st.session_state.messages[i+1]["role"] != st.session_state.app_config["mod_config"]["name"]):
                print(f"⚠️ 检测到连续的 User 消息，正在自动修复... 移除角色'{st.session_state.messages[i]['role']}'的消息")
                st.session_state.messages.pop(i)
            else:
                i += 1
        
        st.session_state.auto_playing = False
        return True
    except Exception as e: return False

def convert_to_markdown(messages, title="对话记录"):
    """
    将对话历史转换为 Markdown 格式字符串
    """
    md_lines = [f"# 🎭 {title}", f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "---"]
    
    for msg in messages:
        # 跳过隐藏消息 (如 System_Log)
        if msg.get("hidden"): continue
        
        role = msg["role"]
        content = msg["content"]
        thought = msg.get("thought")
        
        # 根据角色选择 Emoji (这里简单判断，也可以从 config 读取)
        # 你可以使用 st.session_state.app_config 来获取准确的名字
        a_name = st.session_state.app_config["a_config"]["name"]
        b_name = st.session_state.app_config["b_config"]["name"]
        mod_name = st.session_state.app_config["mod_config"]["name"]
        
        icon = "👤"
        if role == a_name: icon = "👨‍🎨"
        elif role == b_name: icon = "👨‍💻"
        elif role == mod_name: icon = "⚖️"
        
        # 1. 角色标题
        md_lines.append(f"### {icon} {role}")
        
        # 2. 如果有思维链，用引用块或折叠块显示
        if thought:
            md_lines.append(f"> **🧠 思考过程**:\n> {thought.replace('\n', '\n> ')}\n")
        
        # 3. 正文内容
        md_lines.append(f"{content}\n")
        
        # 4. 分隔线
        md_lines.append("---\n")
        
    # 如果有长期记忆，附在最后
    if st.session_state.long_term_memory:
        md_lines.append("## 🧠 最终长期记忆 (Summary)")
        
        # === 【修改点】导出时也清洗 ===
        clean_mem = remove_think_tags(st.session_state.long_term_memory)
        md_lines.append(f"```text\n{clean_mem}\n```")
        
    return "\n".join(md_lines)

# --- Session State ---
if "app_config" not in st.session_state: st.session_state.app_config = load_config_data()
if "messages" not in st.session_state: st.session_state.messages = []
if "turn_count" not in st.session_state: st.session_state.turn_count = 0
if "auto_playing" not in st.session_state: st.session_state.auto_playing = False
# 新增：长期记忆存储变量
if "long_term_memory" not in st.session_state: st.session_state.long_term_memory = ""
# 新增：提炼员状态跟踪变量
if "refiner_state" not in st.session_state: st.session_state.refiner_state = {'last_summary': "", 'refine_turn': 0, 'MAX_REFINE_TURNS': 3}

# === 【新增】初始化提炼游标 ===
if "last_sum_idx" not in st.session_state: st.session_state.last_sum_idx = 0

# --- LLM 逻辑 ---
def get_client(api_key, base_url):
    if not api_key: api_key = "EMPTY" 
    http_client = httpx.Client(trust_env=False)
    return OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def remove_think_tags(text):
    """
    清洗工具：移除文本中的 <think> 标签及其内容
    """
    if not text: return ""
    # === 【修正】正则改为匹配 <think> 标签 ===
    pattern = r"<think>.*?</think>"
    cleaned_text = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
    return cleaned_text.strip()

def parse_response(text):
    if not text: return None, ""
    pattern = r"<think>(.*?)</think>"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        thought = match.group(1).strip()
        reply = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        return thought, reply
    return None, text

def generate_reply(agent_config, global_messages, is_moderator=False, override_system=None):
    """
    通用的回复生成函数，适用于任何角色（A, B, Moderator）。
    """
    client = get_client(agent_config['api_key'], agent_config['base_url'])
    
    # 1. 确定 System Prompt (人设)
    # 无论我是谁，我只加载我配置里的 system 字段，或者覆盖的指令
    sys_content = override_system if override_system else agent_config['system']
    
    # 注入长期记忆 (如果配置了且不是主持人)
    if not is_moderator and st.session_state.long_term_memory:
        sys_content += f"\n\n【前情提要】\n{st.session_state.long_term_memory}"

    # 2. 构建 API 消息列表 (视角转换)
    # 目标格式：[System, User, Assistant, User, Assistant...]
    api_messages = []
    
    current_agent_name = agent_config['name']
    
    for msg in global_messages:
        # 跳过隐藏消息
        if msg.get("hidden"): continue

        role_name = msg["role"]
        content = msg["content"]

        if role_name == current_agent_name:
            # === 如果这是"我"以前说的话 ===
            # 映射为 assistant，让模型知道这是我自己的历史
            api_messages.append({"role": "assistant", "content": content})
        else:
            # === 如果这是"别人"说的话 ===
            # 映射为 user。
            # 【关键】为了不让模型搞混"别人"是谁，我们在内容里带上名字标签
            # 例如："[角色B]: 你的观点不对"
            formatted_content = f"[{role_name}]: {content}"
            api_messages.append({"role": "user", "content": formatted_content})

    # 3. [兼容性修正] 确保符合 User -> Assistant -> User 的交替规则
    # 许多本地模型 (LM Studio/Llama3) 强制要求对话历史必须以 User 开头
    
    sanitized_messages = []
    
    # 3.1 处理空历史的情况
    if not api_messages:
        # 如果没有历史，添加一个默认的引导
        sanitized_messages.append({"role": "user", "content": "（对话开始）"})
    else:
        # 3.2 检查第一条是不是 Assistant
        if api_messages[0]["role"] == "assistant":
            # 如果第一条就是我自己说的话，必须在前面插一个 User 占位符
            # 这比直接丢弃消息更好，因为保留了上下文
            sanitized_messages.append({"role": "user", "content": "（回顾之前的对话...）"})
            sanitized_messages.extend(api_messages)
        else:
            sanitized_messages = api_messages

    # 3.3 (可选) 合并连续的 User 消息 
    # 有些模型不喜欢连续两个 User，但带上名字标签后通常没问题，为了保险可以不合并，
    # 只要保证开头不是 Assistant 即可。

    # 4. 最终组装
    final_payload = [{"role": "system", "content": sys_content}] + sanitized_messages

    # 5. 发送请求
    try:
        response = client.chat.completions.create(
            model=agent_config['model'],
            messages=final_payload,
            temperature=agent_config['temp']
        )
        
        # === 【核心修改开始】 ===
        message = response.choices[0].message
        content = message.content
        
        # 1. 尝试获取 DeepSeek 官方/兼容 API 的原生思考字段
        # 不同的 API 库可能存在 message.reasoning_content 属性，或者在 extra_fields 里
        reasoning = getattr(message, 'reasoning_content', None)
        
        # 2. 如果标准属性里没有，尝试从 model_extra (Pydantic models) 或 dict 中找
        if not reasoning:
            # 某些库版本把额外字段藏在 dict 里
            try:
                # 针对不同的 openai 库版本进行防御性编程
                if hasattr(message, 'model_dump'):
                    msg_dict = message.model_dump()
                elif hasattr(message, 'to_dict'):
                    msg_dict = message.to_dict()
                else:
                    msg_dict = message.__dict__
                
                reasoning = msg_dict.get('reasoning_content')
            except:
                pass

        # 3. 如果抓到了原生思考内容，手动把它拼回去！
        # 这样你的 parse_response 函数（依赖于 <think> 标签）才能正常工作
        if reasoning:
            content = f"<think>{reasoning}</think>\n{content}"

        return content
    except Exception as e:
        return f"Error: {str(e)}"

# --- UI 渲染 ---
st.set_page_config(page_title="AI 对话系统", layout="wide", page_icon="🤖")
st.title("🤖 AI 对话系统 (AI Chat System)")

# === 侧边栏 ===
with st.sidebar:
    with st.expander("🛠️ 配置方案", expanded=False):
        cfg_name = st.text_input("配置另存为", value="我的自定义配置")
        if st.button("💾 保存配置", use_container_width=True):
            s, p = save_config_to_file(st.session_state.app_config, cfg_name)
            if s: st.toast(f"已保存: {os.path.basename(p)}", icon="✅")
        
        st.divider()
        cfg_files = get_config_files()
        if cfg_files:
            c_opts = {f: os.path.basename(f) for f in cfg_files}
            sel_cfg = st.selectbox("选择配置", options=cfg_files, format_func=lambda x: c_opts[x])
            if st.button("📂 加载配置", use_container_width=True):
                st.session_state.app_config = load_config_data(sel_cfg)
                st.toast("配置已重载！", icon="🔄")
                time.sleep(0.5)
                st.rerun()

    with st.expander("💾 对话历史", expanded=True):
        col_new, col_save = st.columns([1, 1])
        if col_new.button("🆕 新对话", use_container_width=True):
            st.session_state.messages = []
            st.session_state.turn_count = 0
            st.session_state.long_term_memory = "" # 清空记忆
            st.session_state.auto_playing = False
            # 重置提炼员状态
            st.session_state.refiner_state = {'last_summary': "", 'refine_turn': 0, 'MAX_REFINE_TURNS': 3}
            
            # === 【新增】重置游标 ===
            st.session_state.last_sum_idx = 0
            # =======================
            
            st.rerun()
            
        save_name = st.text_input("历史文件名", value=f"历史对话_{datetime.now().strftime('%m%d_%H%M')}")
        if col_save.button("💾 存档", use_container_width=True):
            if st.session_state.messages:
                success, msg = save_current_session(save_name)
                if success: st.toast(f"已存档: {os.path.basename(msg)}", icon="✅")

        # === 【需要恢复的代码：Markdown 下载按钮】 ===
        # 实时生成 Markdown 内容
        if st.session_state.messages:
            # 调用你已经写好的函数
            md_content = convert_to_markdown(st.session_state.messages, title=save_name)
            
            # 显示下载按钮
            st.download_button(
                label="📝 导出为 Markdown",
                data=md_content,
                file_name=f"{save_name}.md",
                mime="text/markdown",
                use_container_width=True
            )
        # ========================================

        st.divider()
        history_files = get_history_files()
        if history_files:
            file_options = {f: os.path.basename(f) for f in history_files}
            selected_file = st.selectbox("选择历史记录", options=history_files, format_func=lambda x: file_options[x])
            if st.button("📖 读取历史", use_container_width=True):
                if load_session_from_file(selected_file):
                    st.toast("历史加载成功！", icon="📖")
                    time.sleep(0.5)
                    st.rerun()

    st.divider()
    cfg = st.session_state.app_config
    
    with st.expander("🌍 规则设置", expanded=False):
        cfg["global"]["mod_enabled"] = st.toggle("启用主持人/提炼员", value=cfg["global"].get("mod_enabled", True))
        if cfg["global"]["mod_enabled"]:
            cfg["global"]["mod_interval"] = st.number_input("介入/提炼频率", value=cfg["global"].get("mod_interval", 5), min_value=1)
        cfg["global"]["infinite_mode"] = st.toggle("♾️ 无限模式", value=cfg["global"].get("infinite_mode", False))
        if not cfg["global"]["infinite_mode"]:
            cfg["global"]["round_limit"] = st.number_input("最大轮数", value=cfg["global"].get("round_limit", 10))
        cfg["global"]["context_limit"] = st.number_input("上下文记忆数", value=cfg["global"].get("context_limit", 10))
        cfg["global"]["auto_speed"] = st.slider("自动播放速度", 0.5, 10.0, cfg["global"].get("auto_speed", 1.5))

    def render_agent_config(title, conf_key, icon):
        with st.expander(f"{icon} {title} 设置", expanded=False):
            c = cfg[conf_key]
            c["name"] = st.text_input("名称", c.get("name", title), key=f"{conf_key}_name")
            
            # 特殊逻辑：如果是主持人，增加模式选择
            if conf_key == "mod_config":
                mode_options = {"normal": "📢 显式主持人 (参与对话)", "summarizer": "🕵️ 幕后提炼员 (生成摘要)"}
                
                # 修复开始
                # 1. 获取所有选项的 key 列表
                opt_keys = list(mode_options.keys())
                # 2. 获取当前配置中的值，如果没有则默认为 normal
                current_val = c.get("role_mode", "normal")
                # 3. 计算这个值在列表中的索引位置 (如果是无效值则默认为0)
                try:
                    default_idx = opt_keys.index(current_val)
                except ValueError:
                    default_idx = 0
                
                c["role_mode"] = st.selectbox(
                    "职能模式", 
                    options=opt_keys, 
                    format_func=lambda x: mode_options[x], 
                    index=default_idx,  # 关键修复：显式指定默认选中项
                    key="mod_mode_select"
                )
                # 修复结束
            else:
                c["use_thought"] = st.toggle("🧠 解析思考标签", value=c.get("use_thought", True), key=f"{conf_key}_thought")
            
            c["model"] = st.text_input("模型", c.get("model", "gpt-3.5-turbo"), key=f"{conf_key}_model")
            c["base_url"] = st.text_input("Base URL", c.get("base_url", ""), key=f"{conf_key}_url")
            c["api_key"] = st.text_input("API Key", c.get("api_key", ""), type="password", key=f"{conf_key}_key")
            c["temp"] = st.slider("温度", 0.0, 2.0, c.get("temp", 0.7), key=f"{conf_key}_temp")
            c["system"] = st.text_area("人设 / 提炼指令", c.get("system", ""), height=100, key=f"{conf_key}_sys")

    render_agent_config("角色 A", "a_config", "👨‍🎨")
    render_agent_config("角色 B", "b_config", "👨‍💻")
    if cfg["global"]["mod_enabled"]:
        render_agent_config("主持人/提炼员", "mod_config", "⚖️")

# === 逻辑执行区 ===
def step_logic():
    if not cfg["global"]["infinite_mode"]:
        if st.session_state.turn_count >= cfg["global"]["round_limit"]:
            st.session_state.auto_playing = False
            st.toast("结束。", icon="🛑")
            return False

    current_turns = st.session_state.turn_count
    if not st.session_state.messages: return False
    
    last_msg = st.session_state.messages[-1]
    last_role = last_msg["role"]
    
    conf_a = cfg["a_config"]
    conf_b = cfg["b_config"]
    conf_mod = cfg["mod_config"]
    
    # --- 主持人/提炼员 介入判断 ---
    # 锚定 Role B：只有在 B 说完话（回合结束）时才触发
    mod_trigger = (
        cfg["global"]["mod_enabled"] 
        and current_turns > 0 
        and current_turns % cfg["global"]["mod_interval"] == 0 
        and last_role == conf_b["name"]  # 只锁定 Role B
    )
    
    if mod_trigger:
        # 获取模式：默认为 normal
        mode = conf_mod.get("role_mode", "normal")
        
        # 准备历史记录 (传给 LLM 进行总结或发言)
        # 提取最近 20 条，或者全部，取决于你想给提炼员看多少
        history_content = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.messages[-20:]])
        
        if mode == "summarizer":
            # === 幕后提炼模式 (增量更新版) ===
            refiner = st.session_state.refiner_state
            
            # 1. 获取增量对话 (只获取上次提炼后新产生的对话)
            start_idx = st.session_state.last_sum_idx
            new_msgs = st.session_state.messages[start_idx:]
            
            # 如果没有新消息 (防止重复提炼)
            if not new_msgs:
                st.session_state.refiner_state['refine_turn'] = 0
                st.session_state.messages.append({"role": "System_Log", "content": "No new messages to refine", "hidden": True})
                return True

            # 转换新消息为文本
            new_history_text = "\n".join([f"[{m['role']}]: {m['content']}" for m in new_msgs if not m.get("hidden")])
            
            # 获取当前的旧记忆
            current_memory = st.session_state.long_term_memory if st.session_state.long_term_memory else "（暂无初期记忆）"

            # 检查最大轮数限制
            if refiner['refine_turn'] >= refiner['MAX_REFINE_TURNS']:
                print(f"\n{"="*50}")
                print("⚠️ [系统警告] 达到最大提炼轮数限制，停止提炼。")
                print(f"{"="*50}\n")
                st.toast("达到最大提炼轮数限制，停止提炼。", icon="🛑")
                refiner['refine_turn'] = 0
                st.session_state.messages.append({"role": "System_Log", "content": "Refine Limit Hit", "hidden": True})
                return True
            
            with st.spinner("🕵️ 提炼员正在执行【增量记忆更新】..."):
                # === 构造增量更新 Prompt ===
                summary_prompt = (
                    f"【系统指令】\n{conf_mod['system']}\n\n"
                    "【当前任务】\n"
                    "请基于【现有长期记忆】和【新增对话剧情】，合并生成一份更新后的长期记忆。\n"
                    "你的目标是维护一份连贯的剧情大纲，不要遗漏之前的关键设定，同时加入最新的进展。\n\n"
                    "⚠️ 严格约束：\n"
                    "1. 严禁生成对话、剧本或括号内的动作描写。\n"
                    "2. 必须以客观的第三人称叙述（如：'角色A做了什么...'）。\n"
                    "3. 篇幅控制在 300-500 字以内，剔除无关的寒暄。\n\n"
                    f"📜 【现有长期记忆】:\n{current_memory}\n\n"
                    f"➕ 【新增对话剧情】:\n{new_history_text}\n\n"
                    "【最终输出】\n"
                    "请直接输出更新后的完整长期记忆摘要："
                )
                
                # 调用模型 (传入空列表，因为内容都在 Prompt 里了)
                raw_summary = generate_reply(conf_mod, [], is_moderator=True, override_system=summary_prompt)
                
                # === 【修正】直接复用或者修正正则 ===
                def clean_thought_content(text):
                    if not text: return ""
                    # 修正正则为 <think>
                    pattern = r"<think>.*?</think>"
                    cleaned = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
                    return cleaned.strip()

                # 获取纯净的摘要正文
                final_summary = clean_thought_content(raw_summary)
                # ========================================
                
                # 检查摘要是否为空 (检查清洗后的)
                if not final_summary:
                    print(f"\n{"="*50}")
                    print("⚠️ [系统警告] 摘要为空，停止提炼。")
                    print(f"{"="*50}\n")
                    st.toast("摘要为空，停止提炼。", icon="⚠️")
                    refiner['refine_turn'] = 0  # 重置计数器
                    st.session_state.messages.append({"role": "System_Log", "content": "Empty Summary", "hidden": True})
                    return True

                # 检查内容收敛（相似度检测）
                # 注意：增量更新时，相似度可能会比较低（因为加了新东西），
                # 但如果剧情没推进，相似度会高，所以保留这个检查是合理的。
                # 相似度检查 (使用清洗后的对比)
                similarity = difflib.SequenceMatcher(None, refiner['last_summary'], final_summary).ratio()
                if similarity > 0.95:
                    print(f"\n{"="*50}")
                    print(f"✅ [系统] 内容已收敛 (相似度 {similarity:.2%})，停止无意义的重复生成。")
                    print(f"{"="*50}\n")
                    st.toast(f"内容已收敛，停止提炼。", icon="✅")
                    refiner['last_summary'] = final_summary  # 更新最后状态
                    refiner['refine_turn'] = 0  # 重置计数器
                    # 更新 Session State 中的长期记忆
                    st.session_state.long_term_memory = final_summary
                    # 插入系统日志消息以改变last_role，防止死循环
                    st.session_state.messages.append({"role": "System_Log", "content": "Memory Converged", "hidden": True})
                    return True
                
                # === 成功提炼后的状态更新 ===
                refiner['last_summary'] = final_summary  # 存入纯净版
                refiner['refine_turn'] += 1
                
                # 【关键】存入长期记忆的必须是纯净版
                st.session_state.long_term_memory = final_summary
                
                # 【关键】更新游标：下次提炼从这里开始
                st.session_state.last_sum_idx = len(st.session_state.messages)
                
                # 打印日志
                print("\n" + "="*50)
                print(f"🕵️ [提炼员] 增量更新成功 (处理了 {len(new_msgs)} 条新消息):")
                # 使用黄色高亮 (ANSI Escape Code)
                print(f"\033[93m{final_summary}\033[0m")
                print("="*50 + "\n")
                
                # UI 反馈 (仅 Toast)
                st.toast(f"记忆已更新 (处理了 {len(new_msgs)} 条新消息)", icon="🧠")
                
                # 插入 Log 切换回合
                st.session_state.messages.append({"role": "System_Log", "content": "Memory Updated", "hidden": True})
                
                return True

        else:
            # === 显式主持人模式 (原有逻辑) ===
            with st.chat_message(conf_mod["name"], avatar="⚖️"):
                with st.spinner("主持人正在发言..."):
                    raw_resp = generate_reply(conf_mod, st.session_state.messages, is_moderator=True)
                    st.write(raw_resp)
                    st.session_state.messages.append({"role": conf_mod["name"], "content": raw_resp})
                    if st.session_state.auto_playing:
                        st.session_state.auto_playing = False
                        st.toast("主持人已介入，暂停。", icon="⏸️")
                    return True

    # --- 角色发言逻辑 ---
    if last_role == conf_b["name"] or last_role == "User" or last_role == conf_mod["name"] or last_role == "System_Log":
        next_agent = conf_a
        avatar = "👨‍🎨" 
    else:
        next_agent = conf_b
        avatar = "👨‍💻"

    with st.chat_message(next_agent["name"], avatar=avatar):
        with st.spinner(f"{next_agent['name']} 正在思考..."):
            # === 【核心修改：实施短期记忆切片】 ===
            # 1. 获取配置中的限制数
            limit = cfg["global"]["context_limit"]
            
            # 2. 对历史消息进行切片 (只取最近的 N 条)
            # 注意：如果历史总数小于 limit，Python 的切片会自动处理，不会报错
            short_term_history = st.session_state.messages[-limit:]
            
            # 3. 把切片后的短历史传给 LLM
            # 注意：generate_reply 内部会自动把 System (含长期记忆) 拼在最前面
            raw_resp = generate_reply(next_agent, short_term_history)
            # ==================================
            
            thought = None
            reply = raw_resp
            
            # 解析思考
            use_thought = cfg[ "a_config" if next_agent == conf_a else "b_config" ]["use_thought"]
            if use_thought:
                thought, reply = parse_response(raw_resp)
                if thought:
                    with st.expander("💭 思维链"): st.markdown(thought)
            
            st.write(reply)
            st.session_state.messages.append({
                "role": next_agent["name"], 
                "content": reply, 
                "thought": thought
            })
            
            # === 【核心修改：在这里重置计数器】 ===
            # 只要 A 或 B 任何一个人正常把话说出来了，
            # 就说明之前的"提炼循环"已经成功打破，计数器归零。
            st.session_state.refiner_state['refine_turn'] = 0
            # ====================================
            
            if next_agent == conf_b:
                st.session_state.turn_count += 1
            return True

# === 界面渲染 ===
# 显示长期记忆监视器 (可选，方便调试，也可以注释掉)
if st.session_state.long_term_memory:
    with st.expander("🧠 当前长期记忆 (Long-term Memory Status)", expanded=False):
        # === 【修改点】在这里调用清洗函数 ===
        # 即使后台变量里混入了思考过程，展示给用户时也会被过滤掉
        clean_memory_display = remove_think_tags(st.session_state.long_term_memory)
        st.info(clean_memory_display)

for msg in st.session_state.messages:
    # 不渲染隐藏消息
    if msg.get("hidden"): continue
    
    r = msg["role"]
    ava = "👤"
    if r == cfg["a_config"]["name"]: ava = "👨‍🎨"
    elif r == cfg["b_config"]["name"]: ava = "👨‍💻"
    elif r == cfg["mod_config"]["name"]: ava = "⚖️"

    with st.chat_message(r, avatar=ava):
        if msg.get("thought"):
            with st.expander(f"💭 {r} 的思考"): st.markdown(msg['thought'])
        st.write(msg["content"])

placeholder = st.empty()

if len(st.session_state.messages) == 0:
    start_topic = st.chat_input("输入话题开启对话...")
    if start_topic:
        # 【安全气囊】确保初始消息添加前的状态是干净的
        if st.session_state.messages and st.session_state.messages[-1]["role"] == "User":
            print("⚠️ 检测到连续的 User 消息，正在自动修复...")
            st.session_state.messages.pop()
        st.session_state.messages.append({"role": "User", "content": start_topic})
        st.rerun()

elif st.session_state.auto_playing:
    with placeholder.container():
        st.info(f"🔄 自动对话... (轮数: {st.session_state.turn_count})")
        if st.button("⏹️ 暂停", type="primary", use_container_width=True):
            st.session_state.auto_playing = False
            st.rerun()
    time.sleep(cfg["global"]["auto_speed"])
    step_logic()
    st.rerun()
else:
    with placeholder.container():
        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st.button("▶️ 单步", use_container_width=True):
                step_logic()
                st.rerun()
        with c2:
            if st.button("⏩ 自动", use_container_width=True):
                st.session_state.auto_playing = True
                st.rerun()
        with c3:
            if not cfg["global"]["infinite_mode"]:
                remain = cfg["global"]["round_limit"] - st.session_state.turn_count
                st.caption(f"🏁 剩余: {max(0, remain)}")
            else:
                st.caption("♾️ 无限模式")