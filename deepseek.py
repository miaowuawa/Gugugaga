# -*- coding: utf-8 -*-
"""DeepSeek 自动答题（OpenAI 兼容接口）。

模型 deepseek-v4-flash，禁用思考模式（thinking.type=disabled）使 temperature=0 生效，
返回 1|3|2|4 形式的选项编号。
"""
import json
import re

import requests

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def solve_questions(api_key: str, materials: str, questions: list,
                    base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL,
                    timeout: int = 120) -> dict:
    """自动作答。

    questions: [{question_id, question_name, options:[{answer_id, answer_name}]}]
    返回: {ok, raw_text, numbers, answers, error}
      numbers: 1-indexed 选项编号列表
      answers: [{question_id, answer_id}]
    """
    if not api_key:
        return {"ok": False, "error": "DeepSeek API Key 未配置"}
    if not questions:
        return {"ok": False, "error": "题目为空"}

    q_lines = []
    for i, q in enumerate(questions):
        q_lines.append(f"第{i + 1}题: {q.get('question_name', '')}")
        for j, opt in enumerate(q.get("options", [])):
            q_lines.append(f"  {j + 1}. {opt.get('answer_name', '')}")
        q_lines.append("")
    q_text = "\n".join(q_lines)

    prompt = (
        "你是一个答题助手。根据下面提供的材料回答以下题目。\n\n"
        f"【材料】\n{materials}\n\n"
        f"【题目】\n{q_text}\n"
        "【作答要求】\n"
        "1. 只需返回每题所选选项的编号（从1开始计数），不要解释。\n"
        "2. 多题用 | 分隔，例如: 1|3|2|4\n"
        "3. 必须按题目顺序返回，数量与题目数一致。\n"
        "4. 不要包含任何 markdown 标记或额外文字。"
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "thinking": {"type": "disabled"},
        "max_tokens": 2048,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        resp = requests.post(base_url.rstrip("/") + "/chat/completions",
                             headers=headers, json=body, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": f"DeepSeek API 调用异常: {e}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"DeepSeek API HTTP {resp.status_code}: {resp.text[:500]}"}
    try:
        data = resp.json()
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    except Exception as e:
        return {"ok": False, "error": f"DeepSeek 响应解析失败: {e}"}

    numbers = _parse_numbers(content, len(questions))
    if len(numbers) < len(questions):
        return {"ok": False, "raw_text": content, "numbers": numbers,
                "error": f"DeepSeek 返回的答案数量({len(numbers)})少于题目数量({len(questions)})"}

    answers = []
    for i, num in enumerate(numbers):
        opts = questions[i].get("options", [])
        if num < 1 or num > len(opts):
            return {"ok": False, "raw_text": content, "numbers": numbers,
                    "error": f"第{i + 1}题的选项编号 {num} 超出范围(1-{len(opts)})"}
        answers.append({"question_id": questions[i]["question_id"],
                        "answer_id": opts[num - 1]["answer_id"]})
    return {"ok": True, "raw_text": content, "numbers": numbers, "answers": answers}


def _parse_numbers(text: str, n: int) -> list:
    """鲁棒解析 "1|3|2|4"，取最后一个数字|数字模式。"""
    out = []
    cleaned = text.strip().replace("```", "")
    matches = re.findall(r"\d+\s*(?:\|\s*\d+\s*)+", cleaned)
    if matches:
        cleaned = matches[-1]
    for p in cleaned.split("|"):
        m = re.search(r"\d+", p.strip())
        if m:
            out.append(int(m.group(0)))
    return out[:n]
