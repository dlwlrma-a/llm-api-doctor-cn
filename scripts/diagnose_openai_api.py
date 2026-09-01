#!/usr/bin/env python3
"""Minimal, secret-safe diagnostics for OpenAI-compatible HTTP APIs."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib import error, parse, request


PRESETS = {
    "qixuai": {
        "base_url": "https://token.qixuai.com/v1",
        "api_key_env": "QIXUAI_API_KEY",
        "console": "https://token.qixuai.com/",
    }
}

SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(api[-_ ]?key\s*[:=]\s*)[\"']?[^\s,;\"']+"),
    re.compile(r"\b(?:sk|sd)-[A-Za-z0-9_-]{8,}\b"),
)


@dataclass
class ProbeResult:
    probe: str
    status: str
    http_status: Optional[int]
    elapsed_ms: Optional[int]
    code: str
    summary: str
    evidence: str
    repair: str
    model_ids: Optional[List[str]] = None


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Refuse redirects so Authorization is never forwarded to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        if pattern.groups:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
        else:
            redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).lower() in {
                "authorization",
                "api_key",
                "apikey",
                "access_token",
                "refresh_token",
                "cookie",
                "set-cookie",
            }:
                result[key] = "[REDACTED]"
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute http:// or https:// URL")
    if parsed.username or parsed.password:
        raise ValueError("credentials are not allowed in the base URL")

    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/models"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    normalized = parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path.rstrip("/"), "", "")
    )
    return normalized.rstrip("/")


def endpoint(base_url: str, resource: str) -> str:
    return f"{base_url.rstrip('/')}/{resource.lstrip('/')}"


def decode_json(raw: bytes) -> Tuple[Optional[Any], str]:
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        return None, text


def error_message(payload: Optional[Any], raw_text: str) -> str:
    if isinstance(payload, dict):
        nested = payload.get("error")
        if isinstance(nested, dict):
            for key in ("message", "detail", "type", "code"):
                if nested.get(key):
                    return str(nested[key])
        for key in ("message", "detail", "error_description", "error"):
            if payload.get(key) and not isinstance(payload[key], (dict, list)):
                return str(payload[key])
    compact = re.sub(r"\s+", " ", raw_text).strip()
    return compact[:500] if compact else "No response body"


def classify_http(status: int, message: str) -> Tuple[str, str, str]:
    lower = message.lower()
    if 300 <= status < 400:
        return (
            "redirect_blocked",
            "API 请求发生重定向；为防止密钥跨域泄露，脚本已阻止携带鉴权的重定向。",
            "改用供应商最终 API Base URL，并确认请求始终停留在预期域名。",
        )
    if status == 400:
        if "reasoning_content" in lower:
            return (
                "reasoning_replay_contract",
                "供应商拒绝了推理模型的历史消息回放。",
                "保留供应商要求的推理字段，或用一轮全新的最小对话验证。",
            )
        if "tool" in lower or "function" in lower:
            return (
                "tool_schema_invalid",
                "供应商拒绝了工具定义或工具消息顺序。",
                "先测试一个无参数函数并核对 tool_call_id 顺序，再恢复完整工具 Schema。",
            )
        return (
            "bad_request",
            "端点已收到请求，但拒绝了参数或消息结构。",
            "使用最小聊天请求重试，再逐个恢复可选字段。",
        )
    if status == 401:
        return (
            "authentication_failed",
            "API Key 缺失、无效、过期、已吊销，或属于其他供应商。",
            "检查环境变量、删除首尾空白，并确认 Key 与当前端点匹配。",
        )
    if status == 403:
        return (
            "authorization_denied",
            "鉴权信息已被识别，但账户、模型、IP 或地区没有访问权限。",
            "修改请求前先检查模型授权、账户策略和网络策略。",
        )
    if status == 404:
        return (
            "endpoint_or_model_not_found",
            "未找到资源路径或模型 ID。",
            "检查最终 URL，确认只有一个 /v1，并从 /models 复制当前精确模型 ID。",
        )
    if status in {408, 504}:
        return (
            "upstream_timeout",
            "网关或上游模型响应超时。",
            "用极小诊断提示词和受限输出重试一次，并保留 Request ID 供支持排查。",
        )
    if status == 413:
        return (
            "payload_too_large",
            "提示词、图片或编码后的请求超过网关限制。",
            "缩小或分块请求，并检查编码后的实际字节数。",
        )
    if status == 415:
        return (
            "unsupported_media_type",
            "端点拒绝了请求媒体类型。",
            "聊天请求使用 application/json，媒体输入按供应商文档格式发送。",
        )
    if status == 422:
        return (
            "schema_validation_failed",
            "JSON 解析成功，但字段类型或约束校验失败。",
            "逐项比对请求 Schema，重点检查 content 数组和工具参数。",
        )
    if status == 429:
        quota = any(term in lower for term in ("quota", "balance", "credit", "insufficient"))
        return (
            "quota_exhausted" if quota else "rate_limited",
            "账户额度不足或请求速率受限。"
            if quota
            else "请求速率或并发数受限。",
            "检查账户余额和额度。"
            if quota
            else "遵循 Retry-After 等响应头，并降低请求速率或并发。",
        )
    if status >= 500:
        return (
            "provider_or_gateway_failure",
            "网关或上游供应商处理有效 HTTP 请求时发生故障。",
            "保留供应商 Request ID，用最小请求重试一次后再升级处理。",
        )
    return (
        "unexpected_http_status",
        f"端点返回了非预期 HTTP 状态码 {status}。",
        "重试前检查已脱敏响应和供应商文档。",
    )


def build_opener() -> request.OpenerDirector:
    return request.build_opener(NoRedirectHandler())


def http_call(
    url: str,
    api_key: Optional[str],
    timeout: float,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, str], bytes, int]:
    headers = {"Accept": "application/json"}
    data = None
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    started = time.monotonic()
    try:
        with build_opener().open(req, timeout=timeout) as response:
            raw = response.read(2_000_000)
            elapsed = round((time.monotonic() - started) * 1000)
            return response.status, dict(response.headers.items()), raw, elapsed
    except error.HTTPError as exc:
        raw = exc.read(2_000_000)
        elapsed = round((time.monotonic() - started) * 1000)
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, raw, elapsed


def network_failure(probe: str, exc: BaseException) -> ProbeResult:
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    if isinstance(reason, socket.gaierror):
        code = "dns_failure"
        summary = "无法解析 API 主机名。"
        repair = "核对主机名、DNS 解析器和网络策略。"
    elif isinstance(reason, ssl.SSLError):
        code = "tls_failure"
        summary = "TLS 证书或握手校验失败。"
        repair = "检查证书链和系统时间，不要关闭 TLS 校验。"
    elif isinstance(reason, (socket.timeout, TimeoutError)):
        code = "network_timeout"
        summary = "在收到可用 API 响应前请求已超时。"
        repair = "检查路由和代理设置，再用相同最小探针重试一次。"
    elif isinstance(reason, ConnectionRefusedError):
        code = "connection_refused"
        summary = "目标主机拒绝连接。"
        repair = "检查服务监听状态、端口、防火墙和绑定地址。"
    else:
        code = "network_failure"
        summary = "请求在获得 HTTP 响应前失败。"
        repair = "检查 DNS、TLS、代理、防火墙和端点可达性。"
    return ProbeResult(
        probe=probe,
        status="failed",
        http_status=None,
        elapsed_ms=None,
        code=code,
        summary=summary,
        evidence=redact_text(str(reason))[:500],
        repair=repair,
    )


def failed_http(probe: str, status: int, raw: bytes, elapsed: int) -> ProbeResult:
    payload, raw_text = decode_json(raw)
    message = redact_text(error_message(redact(payload), redact_text(raw_text)))
    code, summary, repair = classify_http(status, message)
    return ProbeResult(
        probe=probe,
        status="failed",
        http_status=status,
        elapsed_ms=elapsed,
        code=code,
        summary=summary,
        evidence=message,
        repair=repair,
    )


def probe_models(base_url: str, api_key: Optional[str], timeout: float) -> ProbeResult:
    name = "models"
    try:
        status, _, raw, elapsed = http_call(endpoint(base_url, "models"), api_key, timeout)
    except (error.URLError, OSError) as exc:
        return network_failure(name, exc)
    if not 200 <= status < 300:
        return failed_http(name, status, raw, elapsed)

    payload, raw_text = decode_json(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return ProbeResult(
            name,
            "degraded",
            status,
            elapsed,
            "models_shape_incompatible",
            "端点已响应，但模型列表不符合常见 OpenAI 兼容结构。",
            redact_text(re.sub(r"\s+", " ", raw_text)[:500]),
            "确认供应商是否支持 GET /models，或配置文档给出的精确模型 ID。",
        )

    ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, dict) and item.get("id")]
    return ProbeResult(
        name,
        "healthy",
        status,
        elapsed,
        "models_ok",
        f"鉴权和模型发现成功，共返回 {len(ids)} 个模型 ID。",
        "响应包含 data 数组。",
        "下一项能力探测使用返回的精确模型 ID。",
        model_ids=ids[:100],
    )


def chat_body(model: str, stream: bool = False) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 16,
        "temperature": 0,
        "stream": stream,
    }


def probe_chat(base_url: str, api_key: Optional[str], timeout: float, model: str) -> ProbeResult:
    name = "chat"
    try:
        status, _, raw, elapsed = http_call(
            endpoint(base_url, "chat/completions"), api_key, timeout, chat_body(model)
        )
    except (error.URLError, OSError) as exc:
        return network_failure(name, exc)
    if not 200 <= status < 300:
        return failed_http(name, status, raw, elapsed)

    payload, raw_text = decode_json(raw)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices:
        return ProbeResult(
            name,
            "degraded",
            status,
            elapsed,
            "chat_shape_incompatible",
            "端点返回 2xx，但未找到 Chat Completions 的 choices 数组。",
            redact_text(re.sub(r"\s+", " ", raw_text)[:500]),
            "确认客户端和端点使用同一种 Chat Completions 契约。",
        )
    return ProbeResult(
        name,
        "healthy",
        status,
        elapsed,
        "chat_ok",
        "最小非流式聊天请求成功。",
        "响应至少包含一个 choice。",
        "只把发生故障的可选能力加回请求进行验证。",
    )


def probe_stream(base_url: str, api_key: Optional[str], timeout: float, model: str) -> ProbeResult:
    name = "stream"
    try:
        status, headers, raw, elapsed = http_call(
            endpoint(base_url, "chat/completions"), api_key, timeout, chat_body(model, stream=True)
        )
    except (error.URLError, OSError) as exc:
        return network_failure(name, exc)
    if not 200 <= status < 300:
        return failed_http(name, status, raw, elapsed)

    text = raw.decode("utf-8", errors="replace")
    data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    json_events = 0
    for line in data_lines:
        if line == "[DONE]":
            continue
        try:
            json.loads(line)
            json_events += 1
        except json.JSONDecodeError:
            pass
    if not data_lines or json_events == 0:
        content_type = headers.get("Content-Type", "unknown")
        return ProbeResult(
            name,
            "degraded",
            status,
            elapsed,
            "stream_shape_incompatible",
            "端点返回 2xx，但未找到可用的 SSE JSON 事件。",
            f"Content-Type={content_type}; data lines={len(data_lines)}",
            "检查代理缓冲，并确认供应商的流式格式使用 data: JSON 事件。",
        )
    return ProbeResult(
        name,
        "healthy",
        status,
        elapsed,
        "stream_ok",
        "流式请求返回了可用的 Server-Sent Events。",
        f"成功解析 {json_events} 个 JSON 事件。",
        "继续确认生产客户端能正确消费增量 delta 字段。",
    )


def probe_tools(base_url: str, api_key: Optional[str], timeout: float, model: str) -> ProbeResult:
    name = "tools"
    body = chat_body(model)
    body["messages"] = [{"role": "user", "content": "Call get_current_time now. Do not answer in text."}]
    body["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "get_current_time",
                "description": "Return the current time from the host application.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]
    body["tool_choice"] = "auto"
    try:
        status, _, raw, elapsed = http_call(
            endpoint(base_url, "chat/completions"), api_key, timeout, body
        )
    except (error.URLError, OSError) as exc:
        return network_failure(name, exc)
    if not 200 <= status < 300:
        return failed_http(name, status, raw, elapsed)

    payload, raw_text = decode_json(raw)
    try:
        tool_calls = payload["choices"][0]["message"].get("tool_calls")
    except (KeyError, IndexError, TypeError, AttributeError):
        tool_calls = None
    if not isinstance(tool_calls, list) or not tool_calls:
        return ProbeResult(
            name,
            "degraded",
            status,
            elapsed,
            "tool_call_not_emitted",
            "请求成功，但模型没有输出结构化工具调用。",
            redact_text(re.sub(r"\s+", " ", raw_text)[:500]),
            "确认所选模型支持工具调用；如供应商支持，再测试强制指定工具。",
        )
    return ProbeResult(
        name,
        "healthy",
        status,
        elapsed,
        "tools_ok",
        "模型输出了结构化工具调用。",
        f"共收到 {len(tool_calls)} 个工具调用。",
        "下一步在目标客户端验证匹配 tool_call_id 的工具结果回放。",
    )


def overall_status(results: Sequence[ProbeResult]) -> str:
    if any(item.status == "failed" for item in results):
        return "failed"
    if any(item.status == "degraded" for item in results):
        return "degraded"
    return "healthy"


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        "国产模型 API 诊断医生",
        f"状态: {report['status']}",
        f"Base URL: {report['base_url']}",
        f"鉴权: {report['authentication']}",
    ]
    if report.get("model"):
        lines.append(f"模型: {report['model']}")
    for item in report["results"]:
        http_status = item["http_status"] if item["http_status"] is not None else "n/a"
        elapsed = f"{item['elapsed_ms']} ms" if item["elapsed_ms"] is not None else "n/a"
        lines.extend(
            [
                "",
                f"[{item['status'].upper()}] {item['probe']} ({item['code']})",
                f"HTTP: {http_status}; 耗时: {elapsed}",
                f"结论: {item['summary']}",
                f"证据: {item['evidence']}",
                f"修复: {item['repair']}",
            ]
        )
        if item.get("model_ids") is not None:
            lines.append("模型 IDs: " + ", ".join(item["model_ids"]))
    if report.get("console"):
        lines.extend(["", f"预设控制台: {report['console']}"])
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对 OpenAI 兼容 API 执行最小化兼容性探测。"
    )
    parser.add_argument("--base-url", help="API 根地址，通常以 /v1 结尾")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="可选且透明声明的供应商预设")
    parser.add_argument("--api-key-env", help="保存 API Key 的环境变量名")
    parser.add_argument("--no-auth", action="store_true", help="不发送 Authorization 请求头")
    parser.add_argument("--model", help="chat、stream 和 tools 探针需要的精确模型 ID")
    parser.add_argument(
        "--probe",
        action="append",
        choices=("models", "chat", "stream", "tools"),
        help="要执行的探针；可重复指定多个（默认：models）",
    )
    parser.add_argument("--all", action="store_true", help="执行全部四个探针")
    parser.add_argument("--timeout", type=float, default=20.0, help="单个请求超时秒数")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--confirm-live-probe",
        action="store_true",
        help="确认端点会收到 Key 和可能计费的最小测试请求",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if not args.confirm_live_probe:
        print(
            "缺少 --confirm-live-probe，已拒绝实时请求。请先确认目标端点、测试 Key 和可能产生的费用。",
            file=sys.stderr,
        )
        return 2
    if args.timeout <= 0 or args.timeout > 120:
        print("--timeout 必须大于 0 且不超过 120 秒", file=sys.stderr)
        return 2

    preset = PRESETS.get(args.preset or "", {})
    raw_base_url = args.base_url or preset.get("base_url")
    if not raw_base_url:
        print("请提供 --base-url 或 --preset", file=sys.stderr)
        return 2
    try:
        base_url = normalize_base_url(raw_base_url)
    except ValueError as exc:
        print(f"Base URL 无效: {exc}", file=sys.stderr)
        return 2

    probes = ["models", "chat", "stream", "tools"] if args.all else (args.probe or ["models"])
    probes = list(dict.fromkeys(probes))
    if any(name in {"chat", "stream", "tools"} for name in probes) and not args.model:
        print("chat、stream 和 tools 探针必须提供 --model", file=sys.stderr)
        return 2

    api_key: Optional[str] = None
    key_env = args.api_key_env or preset.get("api_key_env") or "OPENAI_API_KEY"
    if not args.no_auth:
        api_key = os.environ.get(key_env)
        if not api_key or not api_key.strip():
            print(f"API Key 环境变量缺失或为空: {key_env}", file=sys.stderr)
            return 2
        api_key = api_key.strip()

    results: List[ProbeResult] = []
    for name in probes:
        if name == "models":
            results.append(probe_models(base_url, api_key, args.timeout))
        elif name == "chat":
            results.append(probe_chat(base_url, api_key, args.timeout, args.model))
        elif name == "stream":
            results.append(probe_stream(base_url, api_key, args.timeout, args.model))
        elif name == "tools":
            results.append(probe_tools(base_url, api_key, args.timeout, args.model))

    report = redact(
        {
            "status": overall_status(results),
            "base_url": base_url,
            "authentication": "none" if args.no_auth else f"environment:{key_env}",
            "model": args.model,
            "results": [asdict(item) for item in results],
            "console": preset.get("console"),
        }
    )
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
