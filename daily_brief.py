# coding=utf-8
"""
每日极简投资简报 - 钉钉推送版
基于 TrendRadar 数据 + DeepSeek AI 生成

工作流程：
1. 从 newsnow API 拉取热榜数据
2. 主数据源失败时，使用 DeepSeek 联网搜索作为备用
3. 读取 feedback.json 确定轮动赛道
4. 调用 DeepSeek 生成简报（使用自定义Prompt）
5. 字数检查与截断
6. 通过钉钉 Webhook 推送
"""

import json
import os
import random
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

# ── 配置 ──────────────────────────────────────────────────────
CONFIG = {
    # NewsNow API
    "NEWSNOW_API_URL": "https://newsnow.busiyi.world/api/s",

    # 备用搜索关键词（当主数据源失败时使用）
    "FALLBACK_SEARCH_KEYWORDS": "今日美股 中概股 A股热点 国内政策 国际新闻",

    # DeepSeek API
    "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
    "DEEPSEEK_MODEL": "deepseek-chat",  # DeepSeek V4 模型

    # 钉钉
    "DINGTALK_WEBHOOK": os.environ.get("DINGTALK_WEBHOOK", ""),

    # 各板块最大字数
    "SECTION_MAX_WORDS": 300,

    # 简报总字数上限
    "TOTAL_MAX_WORDS": 1500,

    # 轮动赛道池
    "SECTORS": ["半导体", "储能", "低空经济", "消费电子", "有色"],

    # 小白股票课知识库（按日期轮换）
    "KNOWLEDGE_BASE": [
        {
            "id": 1,
            "name": "北向资金",
            "content": "北向资金指通过沪港通、深港通从香港市场流入A股的资金，被视作\"聪明钱\"。连续大幅流入通常预示市场看多情绪。",
            "slogan": "北向资金连续3日净流入，说明外资在真金白银地投票"
        },
        {
            "id": 2,
            "name": "PE/PB",
            "content": "PE（市盈率）= 股价 ÷ 每股收益，反映市场愿意为每1元利润支付多少价格。PB（市净率）= 股价 ÷ 每股净资产，用于衡量资产估值。",
            "slogan": "PE看盈利预期，PB看资产底牌，两者结合才不踩雷"
        },
        {
            "id": 3,
            "name": "集合竞价",
            "content": "集合竞价是开盘前（9:15-9:25）的集中撮合阶段，所有买卖单统一按最大成交量价格成交。9:20前可撤单，9:20后不可撤单。",
            "slogan": "9:20前的撤单是烟雾弹，9:20后的挂单才是真金白银"
        },
        {
            "id": 4,
            "name": "十字星",
            "content": "十字星K线形态：开盘价与收盘价几乎相等，上下影线较长。高位十字星可能见顶，低位十字星可能见底，是趋势反转信号。",
            "slogan": "高位十字星别追高，低位十字星别割肉"
        },
        {
            "id": 5,
            "name": "高抛低吸",
            "content": "核心逻辑：在价格低位买入、高位卖出。难点在于判断\"高低\"——可参考PE历史分位、均线偏离度、RSI等指标辅助判断。",
            "slogan": "别人恐惧我贪婪的前提是：你手里还有子弹"
        },
        {
            "id": 6,
            "name": "均线",
            "content": "均线（MA）是过去N日收盘价的平均值。常用MA5/MA10/MA20/MA60。金叉（短线上穿长线）= 买入信号，死叉（短线下穿长线）= 卖出信号。",
            "slogan": "多头排列（短>长）不做空，空头排列（长>短）不抄底"
        },
        {
            "id": 7,
            "name": "换手率",
            "content": "换手率 = 当日成交量 ÷ 流通股数，反映股票的交易活跃度。换手率＞5%算活跃，＞10%为极高。突然放量往往是变盘前兆。",
            "slogan": "底部放量是启动，高位放量是出货"
        },
        {
            "id": 8,
            "name": "涨停板",
            "content": "A股涨停板制度：普通股票±10%，ST股±5%，科创板/创业板±20%。涨停买不到，跌停卖不出。封单量越大，次日延续概率越高。",
            "slogan": "早盘封板看封单，尾盘炸板快跑路"
        },
    ],
}


# ── AI Prompt ──────────────────────────────────────────────────
SYSTEM_PROMPT = """你是一位极度克制、注重效率的投资分析师。你的任务是：基于提供的新闻数据，生成一份极简深度简报，每条内容不超过300字。

输出格式严格如下（Markdown）：

## 🌍 隔夜海外脉搏（≤300字）
- 美股三大指数涨跌 + 核心原因（1句）
- 中概股异动（如有）
- 🔗 数据来源：[雅虎财经](https://finance.yahoo.com) 或 [华尔街见闻](https://wallstreetcn.com)

## 🇨🇳 A股盘前风向标（≤300字）
- 今日预判方向（1句）
- 关键参考指标（如北向资金昨日流向）
- 置信度标注（如：置信度 65%）
- 🔗 来源：[财联社](https://cls.cn)

## 🔄 今日轮动赛道（≤300字）
- 从以下赛道中选1个：{sector_name}
- 该赛道过去24小时关键变化（如报价异动）
- 🔗 来源：[生意社](https://www.100ppi.com) 或公司公告链接

## 🔗 跨行业推演（≤300字）
- 选1条国际新闻 + 1条国内新闻，建立逻辑关联
- 推演结论（1-2句）
- 标注：推演置信度 ±20%
- 🔗 来源：路透社快讯 / 新华社

## 📚 小白股票课（≤300字）
- 今日知识点：{knowledge_name}
- {knowledge_content}
- 实操口诀：{knowledge_slogan}
- 知识库轮换：[1.北向资金 2.PE/PB 3.集合竞价 4.十字星 5.高抛低吸 6.均线 7.换手率 8.涨停板]"""

USER_PROMPT_TEMPLATE = """请基于以下新闻数据，生成一份极简深度投资简报。

当前日期：{date}

【今日轮动赛道】
系统已选中：{sector_name}

【新闻原始数据】
{news_data}

注意：
1. 总字数严格控制在1500字以内
2. 每个板块不超过300字
3. 每个板块结束必须附上对应的数据源链接
4. 用专业但通俗的语言，方便新手理解
5. 必须包含置信度标注"""


# ── 工具函数 ──────────────────────────────────────────────────

def get_today_date() -> str:
    """获取今日日期字符串（北京时间）"""
    from datetime import timezone, timedelta
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%d")


def get_knowledge_for_today() -> dict:
    """
    根据日期轮换返回今日的小白股票课知识点
    基于当年的第几天循环知识库
    """
    tz_tz = __import__("datetime").timezone(__import__("datetime").timedelta(hours=8))
    day_of_year = datetime.now(tz_tz).timetuple().tm_yday
    idx = (day_of_year - 1) % len(CONFIG["KNOWLEDGE_BASE"])
    return CONFIG["KNOWLEDGE_BASE"][idx]


def load_feedback() -> Dict[str, int]:
    """
    读取 feedback.json，返回赛道权重字典
    如果文件不存在，初始化为全1
    """
    feedback_path = Path("feedback.json")
    if feedback_path.exists():
        try:
            with open(feedback_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 确保所有赛道都有权重
            for sector in CONFIG["SECTORS"]:
                if sector not in data:
                    data[sector] = 1
            return data
        except (json.JSONDecodeError, Exception) as e:
            print(f"[反馈] 读取 feedback.json 失败: {e}，使用默认权重")

    # 初始化为全1
    return {sector: 1 for sector in CONFIG["SECTORS"]}


def save_feedback(feedback: Dict[str, int]) -> None:
    """保存 feedback.json"""
    with open("feedback.json", "w", encoding="utf-8") as f:
        json.dump(feedback, f, ensure_ascii=False, indent=2)
    print(f"[反馈] feedback.json 已保存")


def select_top_sector(feedback: Dict[str, int]) -> str:
    """从 feedback 中选择权重最高的赛道"""
    if not feedback:
        return random.choice(CONFIG["SECTORS"])

    # 按权重降序排列
    sorted_sectors = sorted(feedback.items(), key=lambda x: x[1], reverse=True)
    top_sector = sorted_sectors[0][0]
    print(f"[赛道] 选中: {top_sector} (权重: {sorted_sectors[0][1]})")
    return top_sector


# ── 数据获取 ──────────────────────────────────────────────────

def fetch_newsnow_data() -> Optional[List[Dict]]:
    """
    从 newsnow.busiyi.world API 获取热榜数据
    返回平台列表，失败时返回 None
    """
    platforms = [
        ("toutiao", "今日头条"),
        ("baidu", "百度热搜"),
        ("wallstreetcn-hot", "华尔街见闻"),
        ("thepaper", "澎湃新闻"),
        ("weibo", "微博"),
        ("zhihu", "知乎"),
        ("douyin", "抖音"),
        ("cls-hot", "财联社热门"),
    ]

    all_results = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
    }

    success_count = 0
    for platform_id, platform_name in platforms:
        try:
            url = f"{CONFIG['NEWSNOW_API_URL']}?id={platform_id}&latest"
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") in ("success", "cache"):
                items = data.get("items", [])
                titles = []
                for item in items[:30]:  # 取前30条
                    title = item.get("title", "")
                    if title and isinstance(title, str) and title.strip():
                        titles.append({
                            "title": title.strip(),
                            "url": item.get("url", ""),
                        })

                if titles:
                    all_results.append({
                        "platform": platform_name,
                        "id": platform_id,
                        "items": titles,
                    })
                    success_count += 1
                    print(f"[数据] {platform_name}: 获取 {len(titles)} 条成功")

            time.sleep(1.5)  # 请求间隔

        except Exception as e:
            print(f"[数据] {platform_name} 获取失败: {type(e).__name__}: {e}")

    if success_count == 0:
        print("[数据] 所有平台获取失败，主数据源不可用")
        return None

    print(f"[数据] 共获取 {success_count}/{len(platforms)} 个平台数据")
    return all_results


def deepseek_search(query: str, max_results: int = 20) -> List[Dict]:
    """
    使用 DeepSeek 联网搜索获取新闻数据（备用数据源）
    """
    print(f"[备用] 使用 DeepSeek 联网搜索: {query}")

    api_key = CONFIG["DEEPSEEK_API_KEY"]
    if not api_key:
        print("[备用] DeepSeek API Key 未配置！")
        return []

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )

        response = client.chat.completions.create(
            model=CONFIG["DEEPSEEK_MODEL"],
            messages=[
                {
                    "role": "system",
                    "content": "你是一个联网搜索助手。请搜索以下关键词，返回搜索结果列表（标题+来源+摘要）。只返回JSON格式，不要额外说明。格式：[{\"title\": \"xxx\", \"source\": \"xxx\", \"summary\": \"xxx\"}]",
                },
                {
                    "role": "user",
                    "content": f"请联网搜索以下关键词的最新新闻（24小时内）：{query}。返回{max_results}条左右结果。",
                },
            ],
            stream=False,
            temperature=0.3,
            max_tokens=4000,
            extra_body={
                "enable_search": True,
            },
        )

        content = response.choices[0].message.content.strip()

        # 尝试解析 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            results = json.loads(content)
            if isinstance(results, list):
                print(f"[备用] 获取到 {len(results)} 条搜索结果")
                return results
        except json.JSONDecodeError:
            print("[备用] 搜索结果非JSON格式，包装为文本")
            return [{"title": content[:500], "source": "DeepSeek搜索", "summary": ""}]

    except ImportError:
        print("[备用] openai 库未安装，尝试直接调用 API")
        # 使用 requests 直接调用
        try:
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": CONFIG["DEEPSEEK_MODEL"],
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是一个联网搜索助手。请搜索以下关键词，返回搜索结果列表。",
                        },
                        {
                            "role": "user",
                            "content": f"请联网搜索以下关键词的最新新闻：{query}",
                        },
                    ],
                    "temperature": 0.3,
                    "max_tokens": 4000,
                    "enable_search": True,
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return [{"title": content[:1000], "source": "DeepSeek搜索", "summary": ""}]
        except Exception as e:
            print(f"[备用] API 调用失败: {e}")
            return []

    except Exception as e:
        print(f"[备用] 搜索失败: {e}")
        return []

    return []


def prepare_news_data() -> Tuple[str, str]:
    """
    准备新闻数据，返回 (格式化后的新闻文本, 数据源说明)
    优先使用 newsnow API，失敗时使用 DeepSeek 备用搜索
    """
    # 尝试主数据源
    print("=" * 60)
    print("开始获取新闻数据...")
    print("=" * 60)

    platforms_data = fetch_newsnow_data()

    if platforms_data:
        # 主数据源成功
        sections = []
        all_titles = []

        for p in platforms_data:
            platform_name = p["platform"]
            items = p["items"]
            section = f"【{platform_name}】\n"
            for i, item in enumerate(items[:20], 1):
                title = item["title"]
                url = item.get("url", "")
                section += f"{i}. {title}\n"
                if url:
                    section += f"   链接: {url}\n"
                all_titles.append(title)
            sections.append(section)

        news_text = "\n".join(sections)
        source_note = f"数据来源: {', '.join([p['platform'] for p in platforms_data])}"

        print(f"[数据] 主数据源成功，共 {len(all_titles)} 条新闻")
        return news_text, source_note

    # 主数据源失败，使用备用搜索
    print("[数据] 主数据源失败，切换到 DeepSeek 联网搜索备用方案...")

    search_results = deepseek_search(CONFIG["FALLBACK_SEARCH_KEYWORDS"])

    if search_results:
        sections = ["【联网搜索结果】"]
        all_titles = []

        for i, item in enumerate(search_results, 1):
            title = item.get("title", "")
            source = item.get("source", "")
            summary = item.get("summary", "")
            line = f"{i}. {title}"
            if source:
                line += f" (来源: {source})"
            if summary:
                line += f"\n   {summary}"
            sections.append(line)
            all_titles.append(title)

        news_text = "\n".join(sections)
        source_note = "数据来源: DeepSeek 联网搜索（备用方案）"

        print(f"[数据] 备用搜索成功，获取 {len(search_results)} 条")
        return news_text, source_note

    print("[数据] 所有数据源均失败！")
    return "", ""


# ── AI 简报生成 ───────────────────────────────────────────────

def generate_briefing(news_text: str, sector: str, knowledge: dict) -> Optional[str]:
    """
    调用 DeepSeek 生成投资简报

    Args:
        news_text: 新闻数据文本
        sector: 选中的轮动赛道
        knowledge: 今日股票知识

    Returns:
        生成的简报文本，失败时返回 None
    """
    api_key = CONFIG["DEEPSEEK_API_KEY"]
    if not api_key:
        print("[AI] 错误: DeepSeek API Key 未配置！")
        return None

    print("[AI] 正在调用 DeepSeek 生成简报...")
    print(f"[AI] 模型: {CONFIG['DEEPSEEK_MODEL']}")
    print(f"[AI] 今日赛道: {sector}")
    print(f"[AI] 今日知识: {knowledge['name']}")

    try:
        # 构建系统提示词（注入变量）
        system_prompt = SYSTEM_PROMPT.format(
            sector_name=sector,
            knowledge_name=knowledge["name"],
            knowledge_content=knowledge["content"],
            knowledge_slogan=knowledge["slogan"],
        )

        # 构建用户提示词
        user_prompt = USER_PROMPT_TEMPLATE.format(
            date=get_today_date(),
            sector_name=sector,
            news_data=news_text,
        )

        # 调用 DeepSeek API
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=api_key,
                base_url="https://api.deepseek.com",
            )

            response = client.chat.completions.create(
                model=CONFIG["DEEPSEEK_MODEL"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
                temperature=0.7,
                max_tokens=4000,
                extra_body={
                    "enable_search": True,
                },
            )

            content = response.choices[0].message.content.strip()

            if not content:
                print("[AI] 返回内容为空")
                return None

            print(f"[AI] 简报生成完成，长度: {len(content)} 字符")
            return content

        except ImportError:
            # 使用 requests 直接调用
            print("[AI] 使用 requests 直接调用 DeepSeek API")
            resp = requests.post(
                "https://api.deepseek.com/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": CONFIG["DEEPSEEK_MODEL"],
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 4000,
                    "enable_search": True,
                },
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()

            if not content:
                print("[AI] 返回内容为空")
                return None

            print(f"[AI] 简报生成完成，长度: {len(content)} 字符")
            return content

    except Exception as e:
        print(f"[AI] 生成失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None


# ── 字数控制 ──────────────────────────────────────────────────

def count_chinese_words(text: str) -> int:
    """
    统计中文字数（不计空格和Markdown符号）
    - 中文字符：按实际字数字
    - 英文单词：按空格分隔计数
    - Markdown 符号不计入
    """
    import re

    # 去除 Markdown 链接 [text](url) → text
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)

    # 去除标题符号 ##
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    # 去除加粗 **
    text = re.sub(r'\*\*', '', text)

    count = 0
    for char in text:
        if '一' <= char <= '鿿' or '㐀' <= char <= '䶿':
            count += 1  # 中文字符
        elif char.isalpha():
            continue  # 英文字母，在单词级统计
        elif char.isdigit():
            count += 1  # 数字算1个字

    # 统计英文单词（粗估：每单词算2个字）
    english_words = re.findall(r'[a-zA-Z]+', text)
    count += len(english_words) * 2

    # 统计数字（连续数字算1个字）
    numbers = re.findall(r'\d+', text)
    count += len(numbers)

    return count


def truncate_by_sections(content: str) -> str:
    """
    如果内容超长，按板块从后往前截断
    最后添加截断提示
    """
    total_words = count_chinese_words(content)

    if total_words <= CONFIG["TOTAL_MAX_WORDS"]:
        return content

    print(f"[字数] 简报字数: {total_words}，超限，开始截断...")

    # 按板块分割
    import re

    # 找到所有 ## 开头的板块标题
    sections = re.split(r'(?=^##\s)', content, flags=re.MULTILINE)

    # 如果分割不当，尝试另一种方式
    if len(sections) <= 1:
        lines = content.split("\n")
        sections = []
        current_section = []
        for line in lines:
            if line.startswith("## "):
                if current_section:
                    sections.append("\n".join(current_section))
                current_section = [line]
            else:
                current_section.append(line)
        if current_section:
            sections.append("\n".join(current_section))

    # 保留前4个板块，删除最后一个（小白股票课优先被截断）
    while len(sections) > 1 and total_words > CONFIG["TOTAL_MAX_WORDS"]:
        removed = sections.pop()
        total_words = count_chinese_words("\n".join(sections))
        print(f"[字数] 已删除板块，当前字数: {total_words}")

    result = "\n".join(sections).strip()
    result += "\n\n> ⚠️ 内容过长已精简"

    return result


# ── 钉钉推送 ──────────────────────────────────────────────────

def push_to_dingtalk(content: str) -> bool:
    """
    通过钉钉群机器人 Webhook 推送消息
    """
    webhook = CONFIG["DINGTALK_WEBHOOK"]
    if not webhook:
        print("[推送] 错误: 钉钉 Webhook 未配置！")
        return False

    date_str = get_today_date()
    title = f"📈 {date_str} 极简投资脉搏"

    # 构建完整消息
    full_message = f"# {title}\n\n{content}"

    # 添加反馈通道
    full_message += """

---
💬 **反馈通道**
如果你希望调整明日赛道权重，请直接在此条消息下回复"好/中/差"（针对当日赛道），或手动修改仓库中的 `feedback.json`（文件路径：`/feedback.json`，格式：`{"赛道名": 权重数字}`）。系统将在每次运行时读取该文件，选中权重最高的赛道作为次日轮动焦点。
"""

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": full_message,
        },
    }

    headers = {"Content-Type": "application/json"}

    try:
        print(f"[推送] 发送钉钉消息...")
        print(f"[推送] 标题: {title}")
        print(f"[推送] 消息长度: {len(full_message)} 字符")

        resp = requests.post(
            webhook,
            headers=headers,
            json=payload,
            timeout=30,
        )

        result = resp.json()

        if resp.status_code == 200 and result.get("errcode") == 0:
            print(f"[推送] ✅ 钉钉推送成功!")
            return True
        else:
            print(f"[推送] ❌ 推送失败: {result.get('errmsg', '未知错误')} (errcode: {result.get('errcode')})")
            return False

    except requests.exceptions.Timeout:
        print("[推送] ❌ 请求超时")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"[推送] ❌ 连接错误: {e}")
        return False
    except Exception as e:
        print(f"[推送] ❌ 推送异常: {type(e).__name__}: {e}")
        return False


# ── 主流程 ──────────────────────────────────────────────────

def main():
    """主函数"""
    print("=" * 60)
    print("  📈 每日极简投资脉搏 - 简报生成系统")
    print(f"  日期: {get_today_date()}")
    print("=" * 60)
    print()

    # 检查密钥
    if not CONFIG["DEEPSEEK_API_KEY"]:
        print("❌ 错误: DEEPSEEK_API_KEY 环境变量未设置")
        sys.exit(1)

    if not CONFIG["DINGTALK_WEBHOOK"]:
        print("❌ 错误: DINGTALK_WEBHOOK 环境变量未设置")
        sys.exit(1)

    # Step 1: 读取反馈 → 确定轮动赛道
    print("=" * 60)
    print("[Step 1/5] 读取 feedback 确定轮动赛道")
    print("=" * 60)

    feedback = load_feedback()
    print(f"[反馈] 当前权重: {json.dumps(feedback, ensure_ascii=False)}")

    sector = select_top_sector(feedback)

    # Step 2: 获取今日股票知识
    print()
    print("=" * 60)
    print("[Step 2/5] 获取今日股票知识")
    print("=" * 60)

    knowledge = get_knowledge_for_today()
    print(f"[知识] 今日知识点: {knowledge['name']}")

    # Step 3: 获取新闻数据（主/备切换）
    print()
    print("=" * 60)
    print("[Step 3/5] 获取新闻数据")
    print("=" * 60)

    news_text, source_note = prepare_news_data()

    if not news_text:
        print("❌ 所有数据源获取失败，无法生成简报")
        sys.exit(1)

    # Step 4: 生成简报
    print()
    print("=" * 60)
    print("[Step 4/5] AI 生成简报")
    print("=" * 60)

    briefing = generate_briefing(news_text, sector, knowledge)

    if not briefing:
        print("❌ 简报生成失败")
        sys.exit(1)

    # Step 5: 字数检查与推送
    print()
    print("=" * 60)
    print("[Step 5/5] 字数检查 & 推送")
    print("=" * 60)

    # 字数统计
    word_count = count_chinese_words(briefing)
    print(f"[字数] 简报字数: {word_count}")

    if word_count > CONFIG["TOTAL_MAX_WORDS"]:
        print(f"[字数] 超限 {word_count - CONFIG['TOTAL_MAX_WORDS']} 字，截断中...")
        briefing = truncate_by_sections(briefing)
        final_count = count_chinese_words(briefing)
        print(f"[字数] 截断后字数: {final_count}")

    # 添加数据源说明
    briefing += f"\n\n---\n📊 {source_note}"

    # 推送
    print()
    success = push_to_dingtalk(briefing)

    if success:
        print()
        print("=" * 60)
        print("  ✅ 简报推送完成！")
        print("=" * 60)
    else:
        print()
        print("=" * 60)
        print("  ❌ 简报推送失败，请检查日志")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
