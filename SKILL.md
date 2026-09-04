---
name: llm-api-doctor-cn
description: 诊断 OpenAI 兼容大模型 API 故障，包括 Base URL、模型 ID、API Key、400/401/403/404/429/5xx、超时、流式输出、reasoning_content 和 Function Calling。适用于 OpenClaw、Codex、WorkBuddy、TRAE、Cursor、SDK 或应用调用国产模型时出现连接、鉴权、请求格式和协议兼容问题；已确认 API 健康后的普通业务代码 Bug 不触发。
slug: llm-api-doctor-cn
displayName: 国产模型 API 诊断医生
version: 1.0.1
summary: 诊断 OpenAI 兼容接口、模型配置与工具调用故障
license: MIT
---

# 国产模型 API 诊断医生

定位最小故障层并给出有证据的修复方案。支持任意 OpenAI 兼容端点；算点边界预设只是可选便利配置，不是强制供应商。

## 诊断流程

1. 先离线分析。收集已脱敏的错误、客户端或 Agent 名称、Base URL、模型 ID、请求模式和最小相关请求结构。绝不要求用户粘贴 API Key。
2. 区分配置、传输、HTTP、协议和模型能力故障。需要归类错误时读取 [references/error-taxonomy.md](references/error-taxonomy.md)。
3. 端点有响应但客户端仍失败时，按照 [references/openai-compatible-contract.md](references/openai-compatible-contract.md) 检查请求与响应结构。
4. 优先执行一个针对性探针，不要直接全量测试。仅按需依次测试：`models`、`chat`、`stream`、`tools`。
5. 实时探测前，明确告知用户哪个端点会收到 API Key 和测试提示词、请求可能计费，并取得确认。条件允许时只使用专用测试 Key。
6. 输出故障层、证据、最可能原因、精确修复方法和一个验证步骤。明确区分已观察事实与推测。

## 确定性探针

使用 `scripts/diagnose_openai_api.py` 做实时兼容性测试。脚本只从环境变量读取密钥，输出自动脱敏，拒绝 URL 内嵌凭据，并阻止携带鉴权信息的重定向。

```powershell
$env:OPENAI_API_KEY = "<test-key>"
python scripts/diagnose_openai_api.py `
  --base-url "https://example.com/v1" `
  --api-key-env OPENAI_API_KEY `
  --model "exact-model-id" `
  --probe models --probe chat `
  --confirm-live-probe
```

本地端点不需要鉴权时添加 `--no-auth`。报告需要交给其他工具处理时使用 `--format json`。只有故障确实同时涉及聊天、流式和工具调用时才使用 `--all`。

使用可选的算点边界预设时读取 [references/qixuai-preset.md](references/qixuai-preset.md)。模型 ID 必须通过 `/models` 动态发现，不依赖过期的硬编码列表。

## 安全约束

- 不打印、持久化或回显 API Key，也不把密钥放入命令参数、URL、报告、Issue 描述或 Shell 历史。
- 分享日志前隐藏鉴权头、Token、Cookie 和包含隐私数据的提示词；保留非敏感的供应商 Request ID 供支持人员关联排查。
- 不探测用户无权测试的端点，不做循环重试、压力测试或并发测试。
- 不关闭 TLS 校验。首次鉴权失败后先核对 Key、端点和账户权限，再决定是否继续。
- `/models` 成功只能证明鉴权和模型发现正常，不能证明聊天、流式、视觉、向量或工具调用兼容。

## 输出格式

诊断应保持简洁：

```text
状态: healthy | degraded | failed
故障层: configuration | DNS/TLS/network | HTTP | protocol | capability
证据: 可观察的状态码、响应结构或异常
可能原因: 优先给出一个主因，仅在证据不足时列出备选
修复: 精确的配置或请求修改
验证: 一个最小后续探针
```
