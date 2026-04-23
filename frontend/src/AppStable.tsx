import { FormEvent, useEffect, useMemo, useState } from 'react'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? `${window.location.protocol}//${window.location.hostname}:8000`

type AuthMode = 'login' | 'register'
type ViewMode = 'workspace' | 'admin'
type User = { id: number; account: string; is_super_admin: boolean }
type Workspace = { id: number; name: string; type: 'personal' | 'team' | 'admin'; owner_id: number; team_id: number | null }
type SkillVersionSummary = { id: number; version: string; status: string; deployed: boolean }
type ToolSummary = { id: number; name: string; description: string | null; input_schema: Record<string, unknown> }
type Skill = {
  id: number
  workspace_id: number
  name: string
  description: string | null
  visibility: string
  mcp_endpoint: string
  current_approved_version_id: number | null
  current_approved_version: SkillVersionSummary | null
  deployed_version_id: number | null
  deployed_version: SkillVersionSummary | null
  mcp_ready: boolean
  version_count: number
  exposed_to_workspace: boolean | null
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
  is_current_approved: boolean
  is_current_deployed: boolean
  deployment_path: string | null
  deploy_status: string
  deploy_error: string | null
  runtime_path: string | null
  venv_path: string | null
  dependency_manifest: Record<string, unknown>
  published_mcp_endpoint_url: string | null
  created_at: string
  updated_at: string
}
type ReviewSkillVersion = SkillVersion & {
  workspace_id: number
  workspace_name: string
  skill_name: string
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
type WorkspacePrompt = {
  workspace_id: number
  workspace_name: string
  workspace_mcp_url: string
  prompt_text: string
  available_skills: Array<Record<string, unknown>>
  global_tools: Array<Record<string, unknown>>
}

class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(formatErrorDetail).join('; ')
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.detail !== 'undefined') return formatErrorDetail(record.detail)
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
    let detail = response.statusText
    try {
      const data = await response.json()
      detail = formatErrorDetail(data.detail ?? data)
    } catch {
      // ignore non-JSON errors
    }
    throw new ApiError(response.status, detail)
  }

  if (response.status === 204) return null as T
  return response.json() as Promise<T>
}

function formatDate(value: string | null | undefined) {
  if (!value) return 'None'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

function sortByCreatedAt<T extends { created_at: string }>(items: T[]) {
  return [...items].sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())
}

function buildSkillMcpEndpoint(version: ReviewSkillVersion) {
  return `${API_BASE_URL.replace(/\/$/, '')}/mcp/${version.workspace_id}/${version.skill_id}`
}

async function copyText(text: string) {
  await navigator.clipboard.writeText(text)
}

export default function AppStable() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('skillhub-token'))
  const [authMode, setAuthMode] = useState<AuthMode>('login')
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [user, setUser] = useState<User | null>(null)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('workspace')
  const [skills, setSkills] = useState<Skill[]>([])
  const [versionMap, setVersionMap] = useState<Record<number, SkillVersion[]>>({})
  const [reviewVersions, setReviewVersions] = useState<ReviewSkillVersion[]>([])
  const [reviewWorkbenches, setReviewWorkbenches] = useState<Record<number, ReviewWorkbench>>({})
  const [workspacePrompt, setWorkspacePrompt] = useState<WorkspacePrompt | null>(null)
  const [uploadName, setUploadName] = useState('')
  const [uploadVersion, setUploadVersion] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadVisibility, setUploadVisibility] = useState<'private' | 'public'>('private')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [busyDownloadVersionId, setBusyDownloadVersionId] = useState<number | null>(null)
  const [busyPrompt, setBusyPrompt] = useState(false)
  const [status, setStatus] = useState('Ready')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const adminWorkspace = useMemo(() => workspaces.find((item) => item.type === 'admin') ?? null, [workspaces])
  const userWorkspaces = useMemo(() => workspaces.filter((item) => item.type !== 'admin'), [workspaces])
  const selectedWorkspace = useMemo(
    () => userWorkspaces.find((item) => item.id === selectedWorkspaceId) ?? null,
    [selectedWorkspaceId, userWorkspaces],
  )

  useEffect(() => {
    if (!token) {
      localStorage.removeItem('skillhub-token')
      setUser(null)
      setWorkspaces([])
      setSelectedWorkspaceId(null)
      setSkills([])
      setVersionMap({})
      setReviewVersions([])
      setReviewWorkbenches({})
      return
    }
    void loadDashboard(token)
  }, [token])

  useEffect(() => {
    if (!token || !selectedWorkspaceId || viewMode !== 'workspace') return
    void loadWorkspaceContext(selectedWorkspaceId, token)
  }, [token, selectedWorkspaceId, viewMode])

  useEffect(() => {
    if (!token || !user?.is_super_admin) return
    void loadReviewVersions(token)
  }, [token, user?.is_super_admin])

  async function loadDashboard(currentToken: string) {
    try {
      setError(null)
      setLoading(true)
      const [me, workspaceList] = await Promise.all([
        apiFetch<User>('/users/me', currentToken),
        apiFetch<Workspace[]>('/workspaces', currentToken),
      ])
      const selectable = workspaceList.filter((item) => item.type !== 'admin')
      setUser(me)
      setWorkspaces(workspaceList)
      setSelectedWorkspaceId((current) => (current && selectable.some((item) => item.id === current) ? current : selectable[0]?.id ?? null))
      setViewMode(me.is_super_admin ? 'admin' : 'workspace')
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

  async function loadWorkspaceContext(workspaceId: number, currentToken: string) {
    try {
      setError(null)
      const workspaceSkills = await apiFetch<Skill[]>(`/workspaces/${workspaceId}/skills`, currentToken)
      const versions = await Promise.all(
        workspaceSkills.map(async (skill) => [skill.id, sortByCreatedAt(await apiFetch<SkillVersion[]>(`/skills/${skill.id}/versions`, currentToken))] as const),
      )
      const nextMap: Record<number, SkillVersion[]> = {}
      for (const [skillId, items] of versions) nextMap[skillId] = items
      setSkills(workspaceSkills)
      setVersionMap(nextMap)
      setWorkspacePrompt(null)
      setStatus(`Workspace synced: ${workspaceId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load workspace')
    }
  }

  async function loadReviewVersions(currentToken: string) {
    try {
      const items = await apiFetch<ReviewSkillVersion[]>('/review/skill-versions', currentToken)
      setReviewVersions(sortByCreatedAt(items))
    } catch (err) {
      setReviewVersions([])
      if (user?.is_super_admin) setError(err instanceof Error ? err.message : 'Failed to load review queue')
    }
  }

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      setError(null)
      setLoading(true)
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

  async function handleUploadSkill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !selectedWorkspaceId || !uploadFile) return
    try {
      setError(null)
      const manualVersion = uploadVersion.trim() || window.prompt('Version is required when the package has no manifest version')?.trim() || ''
      if (!manualVersion) {
        setError('Version is required')
        return
      }
      const formData = new FormData()
      formData.append('package', uploadFile)
      if (uploadName.trim()) formData.append('name', uploadName.trim())
      formData.append('version', manualVersion)
      if (uploadDescription.trim()) formData.append('description', uploadDescription.trim())
      formData.append('visibility', uploadVisibility)
      await apiFetch<Skill>(`/workspaces/${selectedWorkspaceId}/skills/upload`, token, { method: 'POST', body: formData })
      setUploadFile(null)
      setUploadName('')
      setUploadVersion('')
      setUploadDescription('')
      await loadWorkspaceContext(selectedWorkspaceId, token)
      if (user?.is_super_admin) await loadReviewVersions(token)
      setStatus('Skill uploaded')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed')
    }
  }

  async function handleCopyPrompt() {
    if (!token || !selectedWorkspaceId) return
    try {
      setBusyPrompt(true)
      setError(null)
      const prompt = await apiFetch<WorkspacePrompt>(`/workspaces/${selectedWorkspaceId}/agent-prompt`, token)
      setWorkspacePrompt(prompt)
      await copyText(prompt.prompt_text)
      setStatus('Skill MCP prompt copied')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to copy prompt')
    } finally {
      setBusyPrompt(false)
    }
  }

  async function handleStartReview(versionId: number) {
    if (!token) return
    try {
      setError(null)
      const workbench = await apiFetch<ReviewWorkbench>(`/skill-versions/${versionId}/start-review`, token, { method: 'POST' })
      setReviewWorkbenches((current) => ({ ...current, [versionId]: workbench }))
      setStatus(`Review workbench prepared: ${versionId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start review')
    }
  }

  async function handleReviewAction(versionId: number, action: 'approve' | 'reject') {
    if (!token) return
    try {
      setError(null)
      await apiFetch(`/skill-versions/${versionId}/${action}`, token, { method: 'POST' })
      if (selectedWorkspaceId && viewMode === 'workspace') await loadWorkspaceContext(selectedWorkspaceId, token)
      await loadReviewVersions(token)
      setStatus(action === 'approve' ? `Version approved: ${versionId}` : `Version rejected: ${versionId}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : `${action} failed`)
    }
  }

  async function handleDeployVersion(version: ReviewSkillVersion) {
    if (!token) return
    try {
      setError(null)
      const workbench = reviewWorkbenches[version.id]
      await apiFetch(`/skill-versions/${version.id}/deploy`, token, {
        method: 'POST',
        body: JSON.stringify({ review_attempt_id: workbench?.review_attempt_id }),
      })
      if (selectedWorkspaceId && viewMode === 'workspace') await loadWorkspaceContext(selectedWorkspaceId, token)
      await loadReviewVersions(token)
      setStatus(`Version deployed: ${version.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Deploy failed')
      await loadReviewVersions(token)
    }
  }

  async function handleDownloadVersion(version: SkillVersion, skillName: string) {
    if (!token) return
    try {
      setBusyDownloadVersionId(version.id)
      const response = await fetch(`${API_BASE_URL}${version.package_download_url}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) throw new Error(`Download failed: ${response.status}`)
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = version.upload_filename ?? `${skillName}-${version.version}.zip`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
      setStatus(`Downloaded ${skillName} ${version.version}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setBusyDownloadVersionId(null)
    }
  }

  function handleLogout() {
    localStorage.removeItem('skillhub-token')
    setToken(null)
    setUser(null)
  }

  if (!token || !user) {
    return (
      <main className="shell">
        <section className="hero-panel">
          <div className="hero-copy">
            <span className="eyebrow">SkillHub</span>
            <h1>Skill 管理与 MCP 网关</h1>
            <p>上传 Skill，交由超管在隔离运行空间部署，再通过单 Skill MCP 暴露给团队使用。</p>
          </div>
          <form className="auth-card" onSubmit={handleAuthSubmit}>
            <div className="tab-row">
              <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>登录</button>
              <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>注册</button>
            </div>
            <label>
              账号
              <input value={account} onChange={(event) => setAccount(event.target.value)} placeholder="account" />
            </label>
            <label>
              密码
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="password" />
            </label>
            <button className="primary-button" type="submit" disabled={loading || !account.trim() || !password}>
              {loading ? '处理中...' : authMode === 'login' ? '登录' : '注册'}
            </button>
            {error ? <p className="error-text">{error}</p> : null}
          </form>
        </section>
      </main>
    )
  }

  return (
    <main className="dashboard-shell dashboard-split">
      <aside className="sidebar-card scroll-pane">
        <div className="stack-form">
          <span className="eyebrow">User</span>
          <h2>{user.account}</h2>
          <div className="role-strip">
            {user.is_super_admin ? <span className="pill super">超管</span> : null}
            {selectedWorkspace ? <span className="pill neutral">{selectedWorkspace.type === 'team' ? '团队空间' : '个人空间'}</span> : null}
          </div>
          <p className="muted">{status}</p>
          {error ? <p className="error-text">{error}</p> : null}
        </div>

        {user.is_super_admin ? (
          <section className="stack-form">
            <button className={viewMode === 'admin' ? 'workspace-item active' : 'workspace-item'} type="button" onClick={() => setViewMode('admin')}>
              <strong>超管工作台</strong>
              <span>{adminWorkspace ? `workspace ${adminWorkspace.id}` : 'server deployment space'}</span>
            </button>
          </section>
        ) : null}

        <section className="stack-form">
          <div>
            <span className="eyebrow">Workspaces</span>
            <h3>业务空间</h3>
          </div>
          <div className="workspace-list">
            {userWorkspaces.map((workspace) => (
              <button
                key={workspace.id}
                className={viewMode === 'workspace' && workspace.id === selectedWorkspaceId ? 'workspace-item active' : 'workspace-item'}
                onClick={() => {
                  setSelectedWorkspaceId(workspace.id)
                  setViewMode('workspace')
                }}
                type="button"
              >
                <strong>{workspace.name}</strong>
                <span>{workspace.type === 'team' ? '团队' : '个人'}</span>
              </button>
            ))}
          </div>
        </section>

        <button className="ghost-button" onClick={handleLogout} type="button">退出登录</button>
      </aside>

      {viewMode === 'admin' ? (
        <section className="content-grid scroll-pane">
          <header className="top-banner">
            <div>
              <span className="eyebrow">Admin Workbench</span>
              <h1>超管部署工作台</h1>
              <p>审核候选版本，部署到服务器隔离运行空间，并只发布单 Skill MCP。</p>
            </div>
            <div className="status-box">
              <span>运行空间</span>
              <strong>{adminWorkspace?.name ?? '未初始化'}</strong>
              {adminWorkspace ? <p className="muted">workspace ID: {adminWorkspace.id}</p> : null}
            </div>
          </header>

          <section className="panel panel-wide">
            <div className="section-head">
              <div>
                <span className="eyebrow">Review Queue</span>
                <h3>候选版本</h3>
              </div>
              <button className="secondary-button" type="button" onClick={() => token && loadReviewVersions(token)}>刷新</button>
            </div>
            <div className="version-list">
              {reviewVersions.map((version) => {
                const workbench = reviewWorkbenches[version.id]
                return (
                  <article key={version.id} className="version-row">
                    <div className="version-main">
                      <div className="badge-row">
                        <strong>{version.skill_name} {version.version}</strong>
                        <span className={`pill ${version.status}`}>{version.status}</span>
                        <span className={`pill ${version.deploy_status === 'deployed' ? 'approved' : version.deploy_status === 'failed' ? 'rejected' : 'neutral'}`}>
                          {version.deploy_status}
                        </span>
                      </div>
                      <p className="muted">{version.workspace_name}</p>
                      <p className="muted">单 Skill MCP: {version.published_mcp_endpoint_url ?? buildSkillMcpEndpoint(version)}</p>
                      {version.runtime_path ? <p className="muted">runtime: {version.runtime_path}</p> : null}
                      {version.venv_path ? <p className="muted">venv: {version.venv_path}</p> : null}
                      {version.deploy_error ? <p className="error-text">{version.deploy_error}</p> : null}
                      {workbench ? (
                        <div className="review-workbench">
                          <strong>workbench</strong>
                          <code>{workbench.workbench_path}</code>
                          <p className="muted">{workbench.deployment_kind} | tools: {workbench.tool_count}</p>
                        </div>
                      ) : null}
                    </div>
                    <div className="action-row">
                      <button className="secondary-button" type="button" onClick={() => void handleDownloadVersion(version, version.skill_name)} disabled={busyDownloadVersionId === version.id}>
                        {busyDownloadVersionId === version.id ? '下载中...' : '下载 ZIP'}
                      </button>
                      <button className="secondary-button" type="button" onClick={() => void handleStartReview(version.id)}>准备工作台</button>
                      <button className="secondary-button" type="button" onClick={() => void handleDeployVersion(version)}>部署到服务器</button>
                      <button className="secondary-button" type="button" onClick={() => void handleReviewAction(version.id, 'approve')}>批准</button>
                      <button className="ghost-button danger" type="button" onClick={() => void handleReviewAction(version.id, 'reject')}>拒绝</button>
                    </div>
                  </article>
                )
              })}
              {reviewVersions.length === 0 ? <p className="muted">当前没有候选版本。</p> : null}
            </div>
          </section>
        </section>
      ) : (
        <section className="content-grid scroll-pane">
          <header className="top-banner">
            <div>
              <span className="eyebrow">Workspace</span>
              <h1>{selectedWorkspace?.name ?? '请选择业务空间'}</h1>
              <p>业务空间只管理上传和可见 Skill；部署由超管工作台完成。</p>
            </div>
            <div className="status-box">
              <span>MCP 暴露</span>
              <strong>单 Skill MCP</strong>
              {selectedWorkspace ? <p className="muted">workspace ID: {selectedWorkspace.id}</p> : null}
            </div>
          </header>

          <section className="panel panel-wide">
            <div className="section-head">
              <div>
                <span className="eyebrow">Upload</span>
                <h3>上传 Skill ZIP</h3>
              </div>
            </div>
            <form className="stack-form" onSubmit={handleUploadSkill}>
              <label>名称<input value={uploadName} onChange={(event) => setUploadName(event.target.value)} placeholder="可选，覆盖包内名称" /></label>
              <label>版本<input value={uploadVersion} onChange={(event) => setUploadVersion(event.target.value)} placeholder="包内没有 version 时必填" /></label>
              <label>描述<textarea value={uploadDescription} onChange={(event) => setUploadDescription(event.target.value)} rows={3} /></label>
              <label>
                可见性
                <select value={uploadVisibility} onChange={(event) => setUploadVisibility(event.target.value as 'private' | 'public')}>
                  <option value="private">私有</option>
                  <option value="public">公开</option>
                </select>
              </label>
              <label>ZIP 包<input type="file" accept=".zip" onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} /></label>
              <button className="primary-button" type="submit" disabled={!selectedWorkspaceId || !uploadFile}>上传</button>
            </form>
          </section>

          <section className="panel panel-wide">
            <div className="section-head">
              <div>
                <span className="eyebrow">Agent Prompt</span>
                <h3>单 Skill MCP 提示词</h3>
              </div>
              <button className="secondary-button" onClick={() => void handleCopyPrompt()} type="button" disabled={!selectedWorkspaceId || busyPrompt}>
                {busyPrompt ? '复制中...' : '复制给 agent'}
              </button>
            </div>
            <div className="mcp-block">
              {workspacePrompt ? <pre>{workspacePrompt.prompt_text}</pre> : <p className="muted">提示词只包含已可用 Skill 的独立 MCP 地址。</p>}
            </div>
          </section>

          <section className="panel panel-wide">
            <div className="section-head">
              <div>
                <span className="eyebrow">Skills</span>
                <h3>Skill 列表</h3>
              </div>
            </div>
            <div className="skill-grid">
              {skills.map((skill) => (
                <article key={skill.id} className="skill-card">
                  <div className="skill-header">
                    <div>
                      <h4>{skill.name}</h4>
                      <p>{skill.description ?? '暂无描述'}</p>
                    </div>
                    <div className="badge-row">
                      <span className="visibility">{skill.visibility}</span>
                      <span className={`pill ${skill.mcp_ready ? 'approved' : 'uploaded'}`}>{skill.mcp_ready ? 'MCP 可用' : '未上线'}</span>
                    </div>
                  </div>
                  {skill.mcp_ready ? <code>{`${API_BASE_URL}${skill.mcp_endpoint}`}</code> : null}
                  <div className="metric-grid">
                    <article className="metric-card"><strong>批准版本</strong><p>{skill.current_approved_version?.version ?? '无'}</p></article>
                    <article className="metric-card"><strong>部署版本</strong><p>{skill.deployed_version?.version ?? '无'}</p></article>
                    <article className="metric-card"><strong>版本数</strong><p>{skill.version_count}</p></article>
                  </div>
                  <div className="version-list">
                    {(versionMap[skill.id] ?? []).map((version) => (
                      <article key={version.id} className="version-row">
                        <div className="version-main">
                          <div className="badge-row">
                            <strong>{version.version}</strong>
                            <span className={`pill ${version.status}`}>{version.status}</span>
                            <span className={`pill ${version.deploy_status === 'deployed' ? 'approved' : version.deploy_status === 'failed' ? 'rejected' : 'neutral'}`}>{version.deploy_status}</span>
                          </div>
                          <p className="muted">上传者: {version.uploaded_by_account ?? '未知'} | {formatDate(version.created_at)}</p>
                          <div className="tool-chip-list">{version.tools.map((tool) => <span key={tool.id}>{tool.name}</span>)}</div>
                        </div>
                        <div className="action-row">
                          <button className="secondary-button" type="button" onClick={() => void handleDownloadVersion(version, skill.name)} disabled={busyDownloadVersionId === version.id}>
                            {busyDownloadVersionId === version.id ? '下载中...' : '下载 ZIP'}
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                </article>
              ))}
              {skills.length === 0 ? <p className="muted">当前业务空间还没有 Skill。</p> : null}
            </div>
          </section>
        </section>
      )}
    </main>
  )
}
