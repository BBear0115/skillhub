export function buildInitializeRequest(id = 1) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'initialize',
    params: {},
  }
}

export function buildToolsCallRequest(name: string, args: Record<string, unknown>, id = 2) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'tools/call',
    params: {
      name,
      arguments: args,
    },
  }
}

export function buildCurlExample(apiBaseUrl: string, endpoint: string) {
  return `curl -X POST ${apiBaseUrl}${endpoint}
-H "Authorization: Bearer YOUR_TOKEN"
-H "Content-Type: application/json"
-d '${JSON.stringify(buildInitializeRequest())}'`
}

export function parseToolArguments(input: string) {
  if (!input.trim()) {
    return {}
  }

  const parsed = JSON.parse(input)
  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('工具参数必须是 JSON 对象')
  }

  return parsed as Record<string, unknown>
}
