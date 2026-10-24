"""麦当劳 MCP 的最小可用客户端（纯标准库，零依赖）。

只做四件事：initialize 握手 / tools/list / tools/call / 结果解包。
限流默认 150ms 一次请求，远低于官方 600 次/分钟上限。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = "https://mcp.mcd.cn"
DEFAULT_PROTOCOL = "2025-06-18"


class McpError(RuntimeError):
    pass


class McdMCP:
    def __init__(
        self,
        token: str | None = None,
        base: str = BASE_URL,
        timeout: int = 40,
        min_interval: float = 0.15,
    ) -> None:
        self.token = (token or os.environ.get("MCD_MCP_TOKEN", "")).strip()
        if not self.token:
            raise McpError("缺少 MCD_MCP_TOKEN 环境变量（或显式传入 token）")
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.min_interval = min_interval
        self.session_id: str | None = None
        self._last_call = 0.0
        self._seq = 0
        self.calls = 0

    # ---------- 底层传输 ----------

    def _headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _throttle(self) -> None:
        gap = time.time() - self._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last_call = time.time()

    @staticmethod
    def _parse(raw: bytes) -> dict | None:
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return None
        if text.startswith(("event:", "data:", "id:", ":")):
            payloads: list[dict] = []
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    payloads.append(json.loads(chunk))
                except json.JSONDecodeError:
                    continue
            return payloads[-1] if payloads else None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}

    def _post(self, payload: dict) -> dict | None:
        self._throttle()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base, data=body, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                return self._parse(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise McpError(f"HTTP {exc.code} {self.base} -> {detail}") from None
        except urllib.error.URLError as exc:
            raise McpError(f"网络不可达：{exc.reason}") from None

    # ---------- 协议层 ----------

    def request(self, method: str, params: dict | None = None) -> dict:
        self._seq += 1
        self.calls += 1
        resp = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._seq,
                "method": method,
                "params": params or {},
            }
        )
        if not isinstance(resp, dict):
            raise McpError(f"{method} 返回为空")
        if "error" in resp and resp["error"]:
            raise McpError(f"{method} 失败：{resp['error']}")
        return resp.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self.calls += 1
        self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self, protocol: str = DEFAULT_PROTOCOL) -> dict:
        result = self.request(
            "initialize",
            {
                "protocolVersion": protocol,
                "capabilities": {},
                "clientInfo": {"name": "mealhack", "version": "0.1.0"},
            },
        )
        try:
            self.notify("notifications/initialized")
        except McpError:
            pass
        return result

    def list_tools(self) -> list[dict]:
        return self.request("tools/list").get("tools", [])

    def call_raw(self, name: str, arguments: dict | None = None) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    # ---------- 业务层：自动解包 content ----------

    def call(self, name: str, arguments: dict | None = None) -> object:
        """调用工具并返回解析后的结果。

        注意：麦当劳 MCP 的 content[].text 常常是
        "给 LLM 看的展示规则说明 + 原始 JSON" 拼在一起的混合文本，
        直接 json.loads 会失败，必须先把其中的 JSON 负载抠出来。
        """
        result = self.call_raw(name, arguments)
        if result.get("isError"):
            raise McpError(f"{name} 返回错误：{_flatten(result)}")
        text = _flatten(result)
        if not text:
            return None
        parsed = extract_json(text)
        return parsed if parsed is not None else text


def extract_json(text: str) -> object | None:
    """从混合文本中抠出最大的合法 JSON 负载。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    best: object | None = None
    best_len = 0
    length = len(text)
    for start, ch in enumerate(text):
        if ch not in "{[":
            continue
        stack: list[str] = []
        for end in range(start, length):
            cur = text[end]
            if cur in "{[":
                stack.append(cur)
            elif cur in "}]":
                if not stack:
                    break
                opener = stack.pop()
                if (opener == "{" and cur != "}") or (opener == "[" and cur != "]"):
                    break
                if stack:
                    continue
                chunk = text[start : end + 1]
                if len(chunk) <= best_len:
                    break
                try:
                    parsed = json.loads(chunk)
                except json.JSONDecodeError:
                    break
                best, best_len = parsed, len(chunk)
                break
    return best


def _flatten(result: dict) -> str:
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict):
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif "text" in item:
                parts.append(str(item["text"]))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(p for p in parts if p)


def connect() -> McdMCP:
    """一步连接：握手 + 返回可用客户端。"""
    client = McdMCP()
    client.initialize()
    return client


if __name__ == "__main__":
    cli = connect()
    tools = cli.list_tools()
    print(f"OK  session={cli.session_id}  tools={len(tools)}")
    for t in tools:
        desc = (t.get("description") or "").splitlines()[0][:60]
        print(f"  - {t.get('name'):28} {desc}")
