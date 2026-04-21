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

export function buildToolsListRequest(id = 2) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'tools/list',
    params: {},
  }
}

export function buildResourcesListRequest(id = 3) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'resources/list',
    params: {},
  }
}

export function buildResourceReadRequest(uri: string, id = 4) {
  return {
    jsonrpc: '2.0',
    id,
    method: 'resources/read',
    params: { uri },
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
    throw new Error('Tool arguments must be a JSON object')
  }

  return parsed as Record<string, unknown>
}
