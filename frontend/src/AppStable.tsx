import { FormEvent, useEffect, useMemo, useState } from 'react'

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? `${window.location.protocol}//${window.location.hostname}:8000`

type ViewMode = 'mine' | 'market' | 'marketHelp' | 'admin'
type AuthMode = 'login' | 'register'
type User = { id: number; account: string; is_super_admin: boolean }
type Workspace = { id: number; name: string; type: 'personal' | 'admin' | 'team'; owner_id: number; team_id: number | null }
type ToolSummary = { id: number; name: string; description: string | null; input_schema: Record<string, unknown> }
type VersionSummary = { id: number; version: string; status: string; deployed: boolean }
type Skill = {
  id: number
  workspace_id: number
  name: string
  description: string | null
  visibility: string
  mcp_endpoint: string
  current_approved_version_id: number | null
  current_approved_version: VersionSummary | null
  deployed_version_id: number | null
  deployed_version: VersionSummary | null
  mcp_ready: boolean
  prompt_content?: string | null
  prompt_join_logic?: string | null
  agent_prompt?: string | null
  version_count: number
  created_at: string
  updated_at: string
}
type SkillVersion = {
  id: number
  skill_id: number
  version: string
  status: string
  upload_filename: string | null
  uploaded_by_account: string | null
  package_download_url: string
  tools: ToolSummary[]
  deploy_status: string
  deploy_error: string | null
  runtime_path: string | null
  venv_path: string | null
  published_mcp_endpoint_url: string | null
  created_at: string
}
type ReviewVersion = SkillVersion & { workspace_id: number; workspace_name: string; skill_name: string }
type MarketSkill = Skill & {
  uploader_account: string | null
  latest_version: SkillVersion | null
  tools: ToolSummary[]
  prompt_content: string
  prompt_join_logic: string
  agent_prompt: string | null
}
type ReviewWorkbench = {
  review_attempt_id: string
  workbench_path: string
  workbench_package_path: string | null
  workbench_extracted_path: string | null
  deployment_kind: string
  deployment_entrypoint: string | null
  deployment_ready: boolean
  tool_count: number
  manifest_data: Record<string, unknown>
  handler_config: Record<string, unknown>
  deployment_steps: string[]
}

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

function detailText(data: unknown): string {
  if (typeof data === 'string') return data
  if (Array.isArray(data)) return data.map(detailText).join('; ')
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>
    if (record.detail !== undefined) return detailText(record.detail)
    if (typeof record.message === 'string') return record.message
    if (typeof record.msg === 'string') return record.msg
    return JSON.stringify(record)
  }
  return 'Request failed'
}

async function apiFetch<T>(path: string, token: string | null, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = response.statusText
    try {
      const data = await response.json()
      message = detailText(data)
    } catch {
      // keep status text
    }
    throw new ApiError(response.status, message)
  }
  if (response.status === 204) return null as T
  return response.json() as Promise<T>
}

function statusText(skill: Pick<Skill, 'mcp_ready' | 'current_approved_version_id' | 'deployed_version_id'>) {
  if (skill.mcp_ready) return 'MCP 已开放'
  if (skill.deployed_version_id) return '已部署未开放'
  if (skill.current_approved_version_id) return '已审核'
  return '待管理员处理'
}

function versionStatusText(version: Pick<SkillVersion, 'status' | 'deploy_status'>) {
  if (version.status === 'rejected') return '已拒绝'
  if (version.deploy_status === 'failed') return '部署失败'
  if (version.status === 'approved' && version.deploy_status === 'deployed') return 'MCP 已开放'
  if (version.deploy_status === 'deployed') return '已部署'
  return '待处理'
}

function badgeClass(text: string) {
  if (text.includes('开放') || text.includes('已部署')) return 'ok'
  if (text.includes('失败') || text.includes('拒绝')) return 'danger'
  if (text.includes('审核')) return 'warn'
  return 'pending'
}

const MCP_CALL_PROMPT_BLOCK = [
  'Use the endpoint as a concrete MCP server, not as a plain REST endpoint.',
  '1. Send a JSON-RPC initialize request to the MCP endpoint with the Authorization header.',
  '2. Read the Mcp-Session-Id response header and include it on every later MCP request.',
  '3. Call tools/list before selecting a business tool. The response contains the callable tool names and input schemas.',
  '4. Call resources/list and resources/read when resources are present. Use the returned Skill instructions before tools/call.',
  '5. Call tools/call with params.name set to the selected tool and params.arguments set to a JSON object matching that tool schema.',
  '6. For file or audio work, upload exactly one input artifact first, call the business tool with that artifact id, download the output, then delete input and output artifacts.',
].join('\n')

const GLOBAL_TRANSFER_PROMPT_BLOCK = [
  'Global upload, output, and cleanup tools are available on every concrete Skill endpoint:',
  '- global_upload_audio_files: upload exactly one audio file per call. Use {"files":[{"filename":"input.wav","mime_type":"audio/wav","content_base64":"<base64>"}]}.',
  '- global_upload_text_files: upload exactly one text file per call. Use {"files":[{"filename":"input.txt","content_text":"<text>","encoding":"utf-8"}]}.',
  '- global_download_processed_artifacts: fetch metadata and download_url for exactly one artifact. Use {"artifact_ids":["<artifact_id>"],"include_inline_text":true}.',
  '- global_download_processed_artifacts_and_cleanup: return a base64 zip payload and delete those processed artifacts. Use {"artifact_ids":["<artifact_id>"],"cleanup_mode":"hard"}.',
  '- global_delete_uploaded_artifacts: delete exactly one uploaded or processed artifact. Use {"artifact_ids":["<artifact_id>"],"mode":"soft"}.',
  'Streaming rule: upload one file, call one business tool, download the result, delete input/output artifacts, then continue with the next file.',
].join('\n')

function buildPromptFromSkill(skill: Skill, version?: SkillVersion | null, accessToken?: string | null) {
  const endpoint = version?.published_mcp_endpoint_url || `${API_BASE_URL}${skill.mcp_endpoint}`
  const tools = version?.tools?.map((tool) => tool.name).join(', ') || 'Call tools/list to inspect tools'
  return [
    'You can use the following SkillHub MCP skill.',
    '',
    `Skill: ${skill.name}`,
    `Version: ${version?.version || skill.current_approved_version?.version || '-'}`,
    `MCP endpoint: ${endpoint}`,
    `Business tools: ${tools}`,
    'Global MCP helper tools are available on the same endpoint.',
    '',
    GLOBAL_TRANSFER_PROMPT_BLOCK,
    '',
    'Connection and call logic:',
    MCP_CALL_PROMPT_BLOCK,
    '',
    'Authentication:',
    accessToken ? `Use this exact header when connecting: Authorization: Bearer ${accessToken}` : 'Use Authorization: Bearer <access_token> when connecting.',
    'Do not omit the Authorization header. The MCP endpoint returns 401 Authentication required without it.',
  ].join('\n')
}

function buildPromptFromReview(version: ReviewVersion, accessToken?: string | null) {
  const endpoint = version.published_mcp_endpoint_url || `${API_BASE_URL}/mcp/${version.workspace_id}/${version.skill_id}`
  const tools = version.tools?.map((tool) => tool.name).join(', ') || 'Call tools/list to inspect tools'
  return [
    'You can use the following SkillHub MCP skill.',
    '',
    `Skill: ${version.skill_name}`,
    `Version: ${version.version}`,
    `MCP endpoint: ${endpoint}`,
    `Business tools: ${tools}`,
    'Global MCP helper tools are available on the same endpoint.',
    '',
    GLOBAL_TRANSFER_PROMPT_BLOCK,
    '',
    'Connection and call logic:',
    MCP_CALL_PROMPT_BLOCK,
    '',
    'Authentication:',
    accessToken ? `Use this exact header when connecting: Authorization: Bearer ${accessToken}` : 'Use Authorization: Bearer <access_token> when connecting.',
    'Do not omit the Authorization header. The MCP endpoint returns 401 Authentication required without it.',
  ].join('\n')
}

export default function AppStable() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('skillhub-token'))
  const [authMode, setAuthMode] = useState<AuthMode>('login')
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [user, setUser] = useState<User | null>(null)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [view, setView] = useState<ViewMode>('mine')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [mine, setMine] = useState<Skill[]>([])
  const [versions, setVersions] = useState<Record<number, SkillVersion[]>>({})
  const [market, setMarket] = useState<MarketSkill[]>([])
  const [review, setReview] = useState<ReviewVersion[]>([])
  const [selected, setSelected] = useState<Skill | MarketSkill | ReviewVersion | null>(null)
  const [selectedKind, setSelectedKind] = useState<'skill' | 'market' | 'review' | null>(null)
  const [marketSearch, setMarketSearch] = useState('')
  const [marketReadyOnly, setMarketReadyOnly] = useState(false)
  const [myFilter, setMyFilter] = useState<'all' | 'owned' | 'market'>('all')
  const [uploadOpen, setUploadOpen] = useState(false)
  const [promptContent, setPromptContent] = useState('')
  const [promptLogic, setPromptLogic] = useState('')
  const [reviewWorkbench, setReviewWorkbench] = useState<ReviewWorkbench | null>(null)
  const [pendingAction, setPendingAction] = useState<string | null>(null)
  const [status, setStatus] = useState('Ready')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const personalWorkspace = useMemo(() => workspaces.find((item) => item.type === 'personal') ?? null, [workspaces])

  useEffect(() => {
    if (!token) {
      localStorage.removeItem('skillhub-token')
      setUser(null)
      setWorkspaces([])
      setMine([])
      setMarket([])
      setReview([])
      return
    }
    void loadDashboard(token)
  }, [token])

  useEffect(() => {
    if (!token || !personalWorkspace) return
    void loadMySkills(token, personalWorkspace.id)
  }, [token, personalWorkspace?.id])

  useEffect(() => {
    if (!token) return
    if (view === 'market') void loadMarket(token)
    if (view === 'admin' && user?.is_super_admin) void loadReview(token)
  }, [view, token, user?.is_super_admin])

  async function loadDashboard(currentToken: string) {
    try {
      setLoading(true)
      setError(null)
      const [me, workspaceList] = await Promise.all([
        apiFetch<User>('/users/me', currentToken),
        apiFetch<Workspace[]>('/workspaces', currentToken),
      ])
      setUser(me)
      setWorkspaces(workspaceList)
      setView(me.is_super_admin ? 'admin' : 'mine')
      await loadMarket(currentToken)
      if (me.is_super_admin) await loadReview(currentToken)
      setStatus('Console synced')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load console')
      if (err instanceof ApiError && err.status === 401) {
        localStorage.removeItem('skillhub-token')
        setToken(null)
      }
    } finally {
      setLoading(false)
    }
  }

  async function loadMySkills(currentToken: string, workspaceId: number) {
    const items = await apiFetch<Skill[]>(`/workspaces/${workspaceId}/skills`, currentToken)
    const pairs = await Promise.all(
      items.map(async (skill) => [skill.id, await apiFetch<SkillVersion[]>(`/skills/${skill.id}/versions`, currentToken)] as const),
    )
    setMine(items)
    setVersions(Object.fromEntries(pairs))
  }

  async function loadMarket(currentToken: string) {
    setMarket(await apiFetch<MarketSkill[]>('/market/skills', currentToken))
  }

  async function loadReview(currentToken: string) {
    setReview(await apiFetch<ReviewVersion[]>('/review/skill-versions', currentToken))
  }

  async function runAction(key: string, fallbackMessage: string, action: () => Promise<void>) {
    try {
      setPendingAction(key)
      setError(null)
      await action()
    } catch (err) {
      setError(err instanceof Error ? err.message : fallbackMessage)
    } finally {
      setPendingAction(null)
    }
  }

  async function handleAuth(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      setLoading(true)
      setError(null)
      const data = await apiFetch<{ access_token: string }>(`/auth/${authMode}`, null, {
        method: 'POST',
        body: JSON.stringify({ account: account.trim(), password }),
      })
      localStorage.setItem('skillhub-token', data.access_token)
      setToken(data.access_token)
      setPassword('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Authentication failed')
    } finally {
      setLoading(false)
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !personalWorkspace) return
    const form = new FormData(event.currentTarget)
    const file = form.get('package')
    if (!(file instanceof File) || !file.name) {
      setError('请上传 ZIP 包')
      return
    }
    await runAction('upload', 'Skill upload failed', async () => {
      const version = String(form.get('version') || '').trim()
      if (!version) form.delete('version')
      form.set('visibility', form.get('public') === 'on' ? 'public' : 'private')
      form.delete('public')
      await apiFetch<Skill>(`/workspaces/${personalWorkspace.id}/skills/upload`, token, { method: 'POST', body: form })
      setUploadOpen(false)
      await loadMySkills(token, personalWorkspace.id)
      await loadMarket(token)
      if (user?.is_super_admin) await loadReview(token)
      setStatus('Skill uploaded')
    })
  }

  async function handleAdminAction(version: ReviewVersion, action: 'start-review' | 'deploy' | 'approve' | 'reject') {
    if (!token) return
    const path = action === 'start-review' ? `/skill-versions/${version.id}/start-review` : `/skill-versions/${version.id}/${action}`
    await runAction(`${action}-${version.id}`, `${action} failed`, async () => {
      const result = await apiFetch<ReviewWorkbench | SkillVersion>(path, token, { method: 'POST', body: action === 'deploy' ? JSON.stringify({}) : undefined })
      if (action === 'start-review') setReviewWorkbench(result as ReviewWorkbench)
      await loadReview(token)
      await loadMarket(token)
      if (personalWorkspace) await loadMySkills(token, personalWorkspace.id)
      const statusByAction: Record<typeof action, string> = {
        'start-review': 'Review workbench prepared',
        deploy: 'Skill deployed; click Open if it is not approved yet',
        approve: 'MCP opened',
        reject: 'Version rejected',
      }
      setStatus(statusByAction[action])
    })
  }

  async function savePrompt(skillId: number) {
    if (!token) return
    await runAction(`prompt-${skillId}`, 'Prompt config save failed', async () => {
      await apiFetch(`/skills/${skillId}/prompt-config`, token, {
        method: 'PUT',
        body: JSON.stringify({ prompt_content: promptContent, prompt_join_logic: promptLogic }),
      })
      await loadMarket(token)
      if (personalWorkspace) await loadMySkills(token, personalWorkspace.id)
      if (user?.is_super_admin) await loadReview(token)
      setStatus('Prompt config saved')
    })
  }

  async function downloadVersion(version: SkillVersion) {
    if (!token) return
    await runAction(`download-${version.id}`, 'Download failed', async () => {
      const response = await fetch(`${API_BASE_URL}${version.package_download_url}`, { headers: { Authorization: `Bearer ${token}` } })
      if (!response.ok) throw new Error(`Download failed: ${response.status}`)
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = version.upload_filename || `skill-${version.skill_id}-${version.version}.zip`
      document.body.appendChild(link)
      link.click()
      link.remove()
      URL.revokeObjectURL(url)
      setStatus('Package download started')
    })
  }

  async function deleteSkillFromMarket(skill: MarketSkill) {
    if (!token) return
    const confirmed = window.confirm(`确认从 Skill Market 删除「${skill.name}」？删除后版本、工具、部署文件会一并移除，无法在界面恢复。`)
    if (!confirmed) return
    try {
      setError(null)
      await apiFetch(`/market/skills/${skill.id}`, token, { method: 'DELETE' })
      setSelected(null)
      await loadMarket(token)
      if (personalWorkspace) await loadMySkills(token, personalWorkspace.id)
      if (user?.is_super_admin) await loadReview(token)
      setStatus('Market skill deleted')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Market skill delete failed')
    }
  }

  async function copyPrompt(text: string | null | undefined) {
    if (!text) return
    try {
      let copied = false
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(text)
          copied = true
        } catch {
          copied = false
        }
      }
      if (!copied) {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.setAttribute('readonly', 'true')
        textarea.style.position = 'fixed'
        textarea.style.left = '-9999px'
        textarea.style.top = '0'
        document.body.appendChild(textarea)
        textarea.focus()
        textarea.select()
        copied = document.execCommand('copy')
        textarea.remove()
        if (!copied) throw new Error('Clipboard copy was blocked')
      }
      setStatus('Agent prompt copied')
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Clipboard copy failed')
    }
  }

  function logout() {
    localStorage.removeItem('skillhub-token')
    setToken(null)
  }

  function selectMarket(skill: MarketSkill) {
    setSelected(skill)
    setSelectedKind('market')
    setReviewWorkbench(null)
    setPromptContent(skill.prompt_content || '')
    setPromptLogic(skill.prompt_join_logic || '')
  }

  async function selectReview(version: ReviewVersion) {
    setSelected(version)
    setSelectedKind('review')
    setReviewWorkbench(null)
    if (!token) return
    try {
      const skill = await apiFetch<Skill>(`/skills/${version.skill_id}`, token)
      setPromptContent(skill.prompt_content || '')
      setPromptLogic(skill.prompt_join_logic || '')
    } catch (err) {
      setPromptContent('')
      setPromptLogic('')
      setError(err instanceof Error ? err.message : 'Failed to load prompt config')
    }
  }

  async function syncMarketSkill(skill: MarketSkill) {
    if (!token || !personalWorkspace || !skill.latest_version) return
    await runAction(`sync-${skill.id}`, 'Market skill sync failed', async () => {
      await apiFetch<Skill>(`/skill-versions/${skill.latest_version!.id}/sync-to-workspace`, token, {
        method: 'POST',
        body: JSON.stringify({ target_workspace_id: personalWorkspace.id, visibility: 'private' }),
      })
      await loadMySkills(token, personalWorkspace.id)
      await loadMarket(token)
      setStatus('Skill added to your workspace')
    })
  }

  const marketRows = market
    .filter((skill) => !marketReadyOnly || skill.mcp_ready)
    .filter((skill) => `${skill.name} ${skill.description || ''} ${skill.uploader_account || ''}`.toLowerCase().includes(marketSearch.toLowerCase()))

  const myRows = [
    ...mine.map((skill) => ({
      kind: market.some((item) => item.name === skill.name && item.id !== skill.id) ? 'market' as const : 'owned' as const,
      skill,
    })),
  ].filter((item) => myFilter === 'all' || item.kind === myFilter)

  function promptForMySkill(_kind: 'owned' | 'market', skill: Skill | MarketSkill) {
    if (skill.agent_prompt) return skill.agent_prompt
    const runtimeMarketSkill = market.find((item) => item.mcp_ready && item.name === skill.name && item.agent_prompt)
    return runtimeMarketSkill?.agent_prompt || null
  }

  if (!token || !user) {
    return (
      <main className="auth-shell">
        <form className="auth-card" onSubmit={handleAuth}>
          <div>
            <strong>SkillHub</strong>
            <p>登录后进入简化工作台</p>
          </div>
          <div className="segmented">
            <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>登录</button>
            <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>注册</button>
          </div>
          <input value={account} onChange={(event) => setAccount(event.target.value)} placeholder="账号" />
          <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="密码" />
          <button className="primary-button" disabled={loading || !account.trim() || !password}>{loading ? '处理中' : authMode === 'login' ? '登录' : '注册'}</button>
          {error ? <p className="error-text">{error}</p> : null}
        </form>
      </main>
    )
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><strong>SkillHub</strong><span>工作台</span></div>
        <span className="status-line">{status}</span>
        <span>{user.account} · {user.is_super_admin ? '超管' : '用户'}</span>
      </header>
      <div className={`body-layout ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
        <aside className={`side-rail ${sidebarCollapsed ? 'collapsed' : ''}`}>
          <button className="collapse-button" onClick={() => setSidebarCollapsed(!sidebarCollapsed)}>{sidebarCollapsed ? '>' : '<'}</button>
          <div className="side-brand"><strong>工作区</strong><span>Skill 操作入口</span></div>
          <nav className="side-nav">
            <button className={`side-nav-item ${view === 'mine' ? 'active' : ''}`} onClick={() => setView('mine')}><span className="side-icon">M</span><span className="side-copy"><strong>我的 Skill</strong><small>上传和使用</small></span></button>
            <button className={`side-nav-item ${view === 'market' ? 'active' : ''}`} onClick={() => setView('market')}><span className="side-icon">S</span><span className="side-copy"><strong>Skill Market</strong><small>公开 Skill</small></span></button>
            <button className={`side-nav-item sub-nav-item ${view === 'marketHelp' ? 'active' : ''}`} onClick={() => setView('marketHelp')}><span className="side-icon">?</span><span className="side-copy"><strong>使用说明</strong><small>SkillHub 全流程</small></span></button>
            {user.is_super_admin ? <button className={`side-nav-item ${view === 'admin' ? 'active' : ''}`} onClick={() => setView('admin')}><span className="side-icon">D</span><span className="side-copy"><strong>部署工作台</strong><small>审核和开放 MCP</small></span></button> : null}
          </nav>
          <button className="danger-button logout-button" onClick={logout}>退出登录</button>
        </aside>

        <section className="workspace">
          {view === 'mine' ? (
            <section className="main-panel">
              <div className="panel-header">
                <div className="panel-title"><h1>我的 Skill</h1><p>个人上传与市场引用都在这里。</p></div>
                <div className="toolbar">
                  <div className="segmented">
                    {(['all', 'owned', 'market'] as const).map((key) => <button key={key} className={myFilter === key ? 'active' : ''} onClick={() => setMyFilter(key)}>{key === 'all' ? '全部' : key === 'owned' ? '我上传' : '来自市场'}</button>)}
                  </div>
                  <button className="primary-button" onClick={() => setUploadOpen(true)}>上传 Skill</button>
                </div>
              </div>
              <div className="content-scroll">
                <table className="table"><thead><tr><th>Skill</th><th>来源</th><th>版本</th><th>可见性</th><th>状态</th><th>操作</th></tr></thead><tbody>
                  {myRows.map(({ kind, skill }) => <tr key={`${kind}-${skill.id}`}><td className="name-cell"><strong>{skill.name}</strong><span>{skill.description || '-'}</span></td><td>{kind === 'owned' ? '我上传' : '来自市场'}</td><td>{skill.current_approved_version?.version || skill.deployed_version?.version || '-'}</td><td><span className={`badge ${skill.visibility === 'public' ? 'ok' : 'neutral'}`}>{skill.visibility === 'public' ? '公开' : '私有'}</span></td><td><span className={`badge ${badgeClass(statusText(skill))}`}>{statusText(skill)}</span></td><td className="actions"><button onClick={() => (setSelected(skill), setSelectedKind('skill'), setReviewWorkbench(null))}>详情</button><button disabled={!promptForMySkill(kind, skill)} onClick={() => copyPrompt(promptForMySkill(kind, skill))}>复制 Prompt</button></td></tr>)}
                </tbody></table>
              </div>
            </section>
          ) : null}

          {view === 'market' ? (
            <section className="main-panel">
              <div className="panel-header"><div className="panel-title"><h1>Skill Market</h1><p>浏览所有公开 Skill。</p></div><div className="filter-row"><input value={marketSearch} onChange={(event) => setMarketSearch(event.target.value)} placeholder="搜索 Skill、描述或上传人" /><label><input type="checkbox" checked={marketReadyOnly} onChange={(event) => setMarketReadyOnly(event.target.checked)} /> 只看 MCP 可用</label></div></div>
              <div className="content-scroll market-grid">{marketRows.map((skill) => {
                const added = mine.some((owned) => owned.id === skill.id || owned.name === skill.name)
                const syncing = pendingAction === `sync-${skill.id}`
                const visibleTools = skill.tools.slice(0, 2)
                return <article className="market-card" key={skill.id}>
                  <div className="market-card-body">
                  <div className="market-card-head">
                    <div className="skill-mark">{skill.name.slice(0, 2).toUpperCase()}</div>
                    <div className="market-card-title">
                      <div className="meta-row"><span>{skill.uploader_account || 'unknown'}</span><span className={`badge ${badgeClass(statusText(skill))}`}>{statusText(skill)}</span></div>
                      <h3>{skill.name}</h3>
                    </div>
                  </div>
                  <p className="market-card-desc">{skill.description || skill.prompt_content || '暂无说明，点击查看详情获取使用方式。'}</p>
                  <div className="market-stats">
                    <span><strong>v{skill.latest_version?.version || '-'}</strong><small>版本</small></span>
                    <span><strong>{skill.tools.length}</strong><small>工具</small></span>
                    <span><strong>{skill.agent_prompt ? '已配置' : '未配置'}</strong><small>Prompt</small></span>
                  </div>
                  <div className="tool-strip">
                    {visibleTools.length ? visibleTools.map((tool) => <span key={tool.id}>{tool.name}</span>) : <span>暂无工具</span>}
                    {skill.tools.length > visibleTools.length ? <span>+{skill.tools.length - visibleTools.length}</span> : null}
                  </div>
                  </div>
                  <div className="market-card-foot">
                    <button className="primary-button" onClick={() => selectMarket(skill)}>查看</button>
                    <button disabled={added || syncing || !skill.latest_version} onClick={() => syncMarketSkill(skill)}>{syncing ? '添加中' : added ? '已添加' : '添加'}</button>
                    <button disabled={!skill.agent_prompt} onClick={() => copyPrompt(skill.agent_prompt)}>Prompt</button>
                    {user.is_super_admin ? <button className="danger-button" onClick={() => deleteSkillFromMarket(skill)}>删除</button> : null}
                  </div>
                </article>
              })}</div>
            </section>
          ) : null}

          {view === 'marketHelp' ? (
            <section className="main-panel">
              <div className="panel-header"><div className="panel-title"><h1>SkillHub 使用说明</h1><p>完整产品文档与操作流程。</p></div></div>
              <div className="content-scroll">
                <article className="product-doc">
                  <section className="doc-section doc-intro">
                    <h2>产品概览</h2>
                    <p>SkillHub 是一个用于上传、审核、部署、公开和调用 Skill 的工作台。用户可以管理自己上传的 Skill，也可以从 Skill Market 引用公开 Skill；超管负责审核版本、部署运行环境并开放 MCP。</p>
                    <p>一个 Skill 从上传到可用通常会经过上传版本、超管审核、部署运行时、开放 MCP、用户复制 Prompt 并调用的完整流程。</p>
                  </section>

                  <section className="doc-section">
                    <h2>角色与权限</h2>
                    <table className="doc-table"><thead><tr><th>角色</th><th>可执行操作</th><th>说明</th></tr></thead><tbody>
                      <tr><td>普通用户</td><td>注册登录、上传 Skill、查看我的 Skill、浏览市场、添加市场 Skill、复制 Prompt</td><td>用于个人 Skill 管理和已开放 Skill 的调用。</td></tr>
                      <tr><td>上传者</td><td>提交 ZIP 包、填写版本号和描述、选择是否公开到 Skill Market</td><td>上传后需要等待审核和部署；公开 Skill 只有开放 MCP 后才可直接调用。</td></tr>
                      {user.is_super_admin ? <tr><td>超管</td><td>查看部署工作台、审核版本、部署运行时、开放 MCP、拒绝版本、删除市场 Skill</td><td>负责控制平台中 Skill 的质量、可用性和公开范围。</td></tr> : null}
                    </tbody></table>
                  </section>

                  <section className="doc-section">
                    <h2>普通用户使用流程</h2>
                    <ol className="doc-steps">
                      <li><strong>登录系统</strong><span>使用账号密码登录。首次使用可以注册账号，登录后进入 SkillHub 工作台。</span></li>
                      <li><strong>上传 Skill</strong><span>在“我的 Skill”点击“上传 Skill”，填写名称、描述并上传 ZIP 包；版本号留空时使用包内 manifest 版本，需要公开时勾选“公开到 Skill Market”。</span></li>
                      <li><strong>查看状态</strong><span>在“我的 Skill”查看版本、可见性和状态。待管理员处理表示还未完成审核、部署或开放 MCP。</span></li>
                      <li><strong>引用市场 Skill</strong><span>进入 Skill Market 搜索公开 Skill，点击“添加”后会出现在“我的 Skill”的“来自市场”分类。</span></li>
                      <li><strong>复制 Prompt</strong><span>Skill 完成 MCP 开放后，点击“复制 Prompt”或详情页中的 Prompt 操作，把连接说明交给 Agent 使用。</span></li>
                    </ol>
                  </section>

                  <section className="doc-section">
                    <h2>主要页面说明</h2>
                    <table className="doc-table"><thead><tr><th>页面</th><th>用途</th></tr></thead><tbody>
                      <tr><td>我的 Skill</td><td>管理个人上传的 Skill 和从市场同步到个人工作区的 Skill；支持查看详情和复制 Prompt。</td></tr>
                      <tr><td>Skill Market</td><td>浏览所有公开 Skill；支持按名称、描述、上传人搜索，并可筛选 MCP 可用的 Skill。</td></tr>
                      <tr><td>使用说明</td><td>查看 SkillHub 的完整操作流程、字段含义、MCP 调用方式和常见问题。</td></tr>
                      {user.is_super_admin ? <tr><td>部署工作台</td><td>超管审核上传版本、部署运行时、开放 MCP 或拒绝版本。</td></tr> : null}
                    </tbody></table>
                  </section>

                  <section className="doc-section">
                    <h2>状态与字段说明</h2>
                    <table className="doc-table"><thead><tr><th>字段</th><th>含义</th></tr></thead><tbody>
                      <tr><td>版本</td><td>Skill 上传或公开的版本号。版本号用于区分不同提交。</td></tr>
                      <tr><td>可见性</td><td>私有表示仅个人工作区可见；公开表示可进入 Skill Market，但仍需要审核和部署后才可完整使用。</td></tr>
                      <tr><td>待管理员处理</td><td>表示 Skill 尚未完成审核、部署或 MCP 开放流程。</td></tr>
                      <tr><td>已审核</td><td>表示版本已经被超管批准，但不一定已经部署。</td></tr>
                      <tr><td>已部署未开放</td><td>表示运行环境已经部署，但还未对用户开放 MCP。</td></tr>
                      <tr><td>MCP 已开放</td><td>表示 Skill 可通过具体 MCP Endpoint 调用，Prompt 通常也可复制。</td></tr>
                      <tr><td>Prompt</td><td>Agent 使用说明，包含 Skill 名称、版本、MCP Endpoint、工具列表、认证头和调用逻辑。</td></tr>
                    </tbody></table>
                  </section>

                  <section className="doc-section">
                    <h2>Prompt 与 MCP 调用</h2>
                    <p>复制 Prompt 后，Agent 会获得 Skill 名称、版本、MCP Endpoint、工具列表、认证头和调用逻辑。调用 MCP 时必须携带当前登录用户的 Authorization Bearer Token。</p>
                    <pre className="doc-code">Authorization: Bearer &lt;access_token&gt;</pre>
                    <p>对于需要文件上传、结果下载或清理的 Skill，详情页和 Prompt 会说明相关全局工具的使用方式。业务处理应按单个输入逐步执行，避免一次性批量提交过多文件。</p>
                  </section>

                  {user.is_super_admin ? (
                    <section className="doc-section">
                      <h2>超管审核与部署流程</h2>
                      <ol className="doc-steps">
                        <li><strong>查看待处理版本</strong><span>进入“部署工作台”，查看上传人、版本、部署状态和当前处理状态。</span></li>
                        <li><strong>查看详情</strong><span>检查包信息、工具列表、依赖和 Prompt 配置，确认 Skill 是否满足开放要求。</span></li>
                        <li><strong>部署运行时</strong><span>完成部署后，系统会生成可调用的 MCP Endpoint。</span></li>
                        <li><strong>开放 MCP</strong><span>部署成功后点击“开放”，普通用户即可复制 Prompt 并通过 MCP 调用。</span></li>
                        <li><strong>拒绝或下架</strong><span>不符合要求的版本可以拒绝；不再公开的市场 Skill 可以从 Skill Market 删除。</span></li>
                      </ol>
                    </section>
                  ) : null}

                  <section className="doc-section">
                    <h2>常见问题</h2>
                    <dl className="doc-faq">
                      <dt>为什么 Prompt 按钮不可用？</dt>
                      <dd>通常是因为 Skill 尚未部署并开放 MCP，或后端未返回可用的 Agent Prompt。</dd>
                      <dt>上传后为什么还是待处理？</dt>
                      <dd>上传只代表版本已提交，仍需要超管审核、部署并开放 MCP。</dd>
                      <dt>添加市场 Skill 后在哪里查看？</dt>
                      <dd>回到“我的 Skill”，切换到“来自市场”筛选项即可查看已添加的市场 Skill。</dd>
                      <dt>为什么搜索不到某个公开 Skill？</dt>
                      <dd>确认该 Skill 是否已公开、是否完成上架流程，以及搜索关键词是否匹配名称、描述或上传人。</dd>
                      <dt>私有 Skill 会进入 Skill Market 吗？</dt>
                      <dd>不会。只有上传时选择公开，且后续完成审核和开放流程的 Skill 才适合在市场中使用。</dd>
                    </dl>
                  </section>
                </article>
              </div>
            </section>
          ) : null}

          {view === 'admin' && user.is_super_admin ? (
            <section className="main-panel">
              <div className="panel-header"><div className="panel-title"><h1>部署工作台</h1><p>审核、部署并开放 MCP。</p></div><button onClick={() => token && loadReview(token)}>刷新</button></div>
              <div className="content-scroll review-grid">{review.map((version) => {
                const isRejected = version.status === 'rejected'
                const isDeploying = version.deploy_status === 'deploying'
                const isDeployed = version.deploy_status === 'deployed'
                const isOpen = version.status === 'approved' && isDeployed
                return <article className="review-card" key={version.id}>
                  <div className="review-card-main">
                    <div className="review-card-title"><strong>{version.skill_name}</strong><span>{version.workspace_name}</span></div>
                    <div className="review-meta"><span>上传人</span><strong>{version.uploaded_by_account || '-'}</strong></div>
                    <div className="review-meta"><span>版本</span><strong>{version.version}</strong></div>
                    <div className="review-meta"><span>部署</span><strong>{version.deploy_status}</strong></div>
                    <span className={`badge ${badgeClass(versionStatusText(version))}`}>{versionStatusText(version)}</span>
                  </div>
                  <div className="review-actions">
                    <button onClick={() => void selectReview(version)}>查看</button>
                    <button disabled={pendingAction === `start-review-${version.id}`} onClick={() => handleAdminAction(version, 'start-review')}>{pendingAction === `start-review-${version.id}` ? '准备中' : '准备审核'}</button>
                    <button disabled={Boolean(pendingAction) || isRejected || isDeploying || isDeployed} onClick={() => handleAdminAction(version, 'deploy')}>{pendingAction === `deploy-${version.id}` || isDeploying ? '部署中' : isDeployed ? '已部署' : '部署'}</button>
                    <button disabled={Boolean(pendingAction) || isRejected || !isDeployed || isOpen} onClick={() => handleAdminAction(version, 'approve')}>{pendingAction === `approve-${version.id}` ? '开放中' : isOpen ? '已开放' : '开放'}</button>
                    <button className="danger-button" disabled={Boolean(pendingAction) || isOpen} onClick={() => handleAdminAction(version, 'reject')}>{pendingAction === `reject-${version.id}` ? '拒绝中' : '拒绝'}</button>
                  </div>
                </article>
              })}</div>
            </section>
          ) : null}
        </section>
      </div>

      {uploadOpen ? <div className="modal-backdrop"><form className="modal-card" onSubmit={handleUpload}><div className="modal-header"><h2>上传 Skill</h2><button type="button" onClick={() => setUploadOpen(false)}>关闭</button></div><div className="form-grid"><label>Skill 名称<input name="name" /></label><label>版本号（可选）<input name="version" placeholder="留空则使用包内版本" /></label><label>描述<textarea name="description" /></label><label>ZIP 包<input name="package" type="file" accept=".zip" /></label><label className="checkbox-row"><input name="public" type="checkbox" /> 公开到 Skill Market</label></div><div className="modal-actions"><button type="button" onClick={() => setUploadOpen(false)}>取消</button><button className="primary-button" disabled={pendingAction === 'upload'}>{pendingAction === 'upload' ? '上传中' : '提交上传'}</button></div></form></div> : null}

      {selected ? <DetailModal selected={selected} kind={selectedKind} versions={versions[(selected as Skill).id] || []} accessToken={token} isAdmin={user.is_super_admin && view === 'admin'} canDeleteMarket={user.is_super_admin && selectedKind === 'market'} promptContent={promptContent} promptLogic={promptLogic} reviewWorkbench={reviewWorkbench} pendingAction={pendingAction} setPromptContent={setPromptContent} setPromptLogic={setPromptLogic} onClose={() => setSelected(null)} onCopy={copyPrompt} onDownload={downloadVersion} onSavePrompt={savePrompt} onDeleteMarket={deleteSkillFromMarket} /> : null}
    </main>
  )
}

function DetailModal(props: {
  selected: Skill | MarketSkill | ReviewVersion
  kind: 'skill' | 'market' | 'review' | null
  versions: SkillVersion[]
  accessToken: string | null
  isAdmin: boolean
  canDeleteMarket: boolean
  promptContent: string
  promptLogic: string
  reviewWorkbench: ReviewWorkbench | null
  pendingAction: string | null
  setPromptContent: (value: string) => void
  setPromptLogic: (value: string) => void
  onClose: () => void
  onCopy: (text: string | null | undefined) => void
  onDownload: (version: SkillVersion) => Promise<void>
  onSavePrompt: (skillId: number) => Promise<void>
  onDeleteMarket: (skill: MarketSkill) => Promise<void>
}) {
  const item = props.selected
  const marketItem = props.kind === 'market' ? (item as MarketSkill) : null
  const reviewItem = props.kind === 'review' ? (item as ReviewVersion) : null
  const name = reviewItem?.skill_name || (item as Skill).name
  const latest = marketItem?.latest_version || reviewItem || props.versions[0]
  const prompt = reviewItem
    ? (latest?.published_mcp_endpoint_url ? buildPromptFromReview(reviewItem, props.accessToken) : null)
    : marketItem?.agent_prompt || (item as Skill).agent_prompt || (latest?.published_mcp_endpoint_url ? buildPromptFromSkill(item as Skill, latest, props.accessToken) : null)
  const tools = marketItem?.tools || latest?.tools || []
  const skillId = reviewItem?.skill_id || (item as Skill).id
  return <div className="modal-backdrop detail-backdrop"><aside className="detail-modal-card"><div className="panel-header"><div className="panel-title"><h2>{name}</h2><p>{marketItem?.uploader_account || latest?.uploaded_by_account || '-'}</p></div><button onClick={props.onClose}>关闭</button></div><div className="detail-body">
    <section className="detail-section"><h3>状态</h3><div className="kv"><span>版本</span><span>{latest?.version || '-'}</span></div><div className="kv"><span>部署</span><span>{latest?.deploy_status || '-'}</span></div><div className="kv"><span>ZIP</span>{latest ? <button disabled={props.pendingAction === `download-${latest.id}`} onClick={() => void props.onDownload(latest)}>{props.pendingAction === `download-${latest.id}` ? '下载中' : '下载 ZIP'}</button> : <span>-</span>}</div></section>
    <section className="detail-section"><h3>Agent Prompt</h3>{prompt ? <><code className="code-block prompt-block">{prompt}</code><button className="primary-button" onClick={() => props.onCopy(prompt)}>复制 Agent Prompt</button></> : <p>管理员尚未开放 MCP。</p>}</section>
    <section className="detail-section"><h3>使用说明</h3><p>{marketItem?.prompt_content || (reviewItem ? props.promptContent : (item as Skill).description) || '-'}</p></section>
    <section className="detail-section"><h3>工具</h3><ul>{tools.map((tool) => <li key={tool.id}>{tool.name}</li>)}</ul></section>
    {reviewItem && props.reviewWorkbench ? <section className="detail-section"><h3>审核材料</h3><div className="kv"><span>Workbench</span><span>{props.reviewWorkbench.workbench_path}</span></div><div className="kv"><span>包目录</span><span>{props.reviewWorkbench.workbench_extracted_path || '-'}</span></div><div className="kv"><span>类型</span><span>{props.reviewWorkbench.deployment_kind}</span></div><div className="kv"><span>工具数</span><span>{props.reviewWorkbench.tool_count}</span></div><ul>{props.reviewWorkbench.deployment_steps.map((step) => <li key={step}>{step}</li>)}</ul></section> : null}
    {props.canDeleteMarket && marketItem ? <section className="detail-section"><h3>危险操作</h3><button className="danger-button" onClick={() => props.onDeleteMarket(marketItem)}>从 Skill Market 删除</button></section> : null}
    {props.isAdmin ? <section className="detail-section"><h3>超管提示词配置</h3><label className="prompt-editor-label">提示词内容<textarea value={props.promptContent} onChange={(event) => props.setPromptContent(event.target.value)} /></label><label className="prompt-editor-label">拼接逻辑<textarea value={props.promptLogic} onChange={(event) => props.setPromptLogic(event.target.value)} /></label><button className="primary-button" disabled={props.pendingAction === `prompt-${skillId}`} onClick={() => void props.onSavePrompt(skillId)}>{props.pendingAction === `prompt-${skillId}` ? '保存中' : '保存提示词配置'}</button></section> : null}
  </div></aside></div>
}
