
import { Dispatch, FormEvent, SetStateAction, useEffect, useMemo, useState } from 'react'
import {
  buildCurlExample,
  buildInitializeRequest,
  buildResourcesListRequest,
  buildToolsCallRequest,
  buildToolsListRequest,
  parseToolArguments,
} from './lib/mcpClean'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? `${window.location.protocol}//${window.location.hostname}:8000`

type AuthMode = 'login' | 'register'
type User = { id: number; account: string }
type Workspace = { id: number; name: string; type: 'personal' | 'team'; owner_id: number; team_id: number | null }
type Team = { id: number; name: string; owner_id: number; membership_role: 'admin' | 'member' | null; has_pending_request: boolean }
type TeamMember = {
  user_id: number
  account: string
  role: 'admin' | 'member'
  skill_preferences: Record<string, boolean>
  skill_preferences_configured: boolean
}
type JoinRequest = { id: number; team_id: number; team_name: string; user_id: number; account: string; status: string }
type ToolDefinition = { id: number; name: string; description: string | null; input_schema: Record<string, unknown> }
type Skill = {
  id: number
  workspace_id: number
  name: string
  description: string | null
  visibility: string
  enabled: boolean
  handler_config: Record<string, unknown>
  tools: ToolDefinition[]
  mcp_endpoint: string
}
type WorkspaceKey = { id: number; workspace_id: number; workspace_name: string; created_at: string; token?: string | null }
type SkillAvailability = { id: number; name: string; description: string | null; enabled: boolean; tool_count: number }
type SelectableSkill = { id: number; name: string; description: string | null; tool_count: number }
type MpcTool = { name: string; description: string; inputSchema: Record<string, unknown> }

function formatErrorDetail(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map(formatErrorDetail).join('; ')
  if (detail && typeof detail === 'object') {
    const record = detail as Record<string, unknown>
    if (typeof record.detail !== 'undefined') return formatErrorDetail(record.detail)
    if (typeof record.msg === 'string') return record.msg
    if (typeof record.message === 'string') return record.message
    return JSON.stringify(record)
  }
  return '请求失败'
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
      // ignore non-json error body
    }
    throw new Error(detail)
  }

  if (response.status === 204) return null as T
  return response.json() as Promise<T>
}

function normalizeToolToken(value: string) {
  const normalized = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return normalized || 'tool'
}

function workspaceToolAlias(skillId: number, toolName: string) {
  return `skill_${skillId}_${normalizeToolToken(toolName)}`
}

async function mcpRequest(
  endpoint: string,
  token: string,
  body: Record<string, unknown>,
  sessionId?: string | null,
) {
  const response = await fetch(`${API_BASE_URL}${endpoint}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
      ...(sessionId ? { 'Mcp-Session-Id': sessionId } : {}),
    },
    body: JSON.stringify(body),
  })

  const payload = (await response.json()) as { result?: unknown; error?: { message?: string } }
  if (!response.ok || payload.error) {
    throw new Error(payload.error?.message ?? `MCP request failed: ${response.status}`)
  }
  return { payload, sessionId: response.headers.get('Mcp-Session-Id') }
}

export default function AppStable() {
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('skillhub-token'))
  const [authMode, setAuthMode] = useState<AuthMode>('login')
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [authenticating, setAuthenticating] = useState(false)
  const [user, setUser] = useState<User | null>(null)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [teams, setTeams] = useState<Team[]>([])
  const [discoverTeams, setDiscoverTeams] = useState<Team[]>([])
  const [joinRequests, setJoinRequests] = useState<JoinRequest[]>([])
  const [skills, setSkills] = useState<Skill[]>([])
  const [personalSkills, setPersonalSkills] = useState<Skill[]>([])
  const [workspaceKeys, setWorkspaceKeys] = useState<WorkspaceKey[]>([])
  const [members, setMembers] = useState<TeamMember[]>([])
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<number | null>(null)
  const [teamName, setTeamName] = useState('')
  const [memberAccount, setMemberAccount] = useState('')
  const [memberRole, setMemberRole] = useState<'admin' | 'member'>('member')
  const [uploadName, setUploadName] = useState('')
  const [uploadDescription, setUploadDescription] = useState('')
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [workspaceTools, setWorkspaceTools] = useState<MpcTool[]>([])
  const [toolInputs, setToolInputs] = useState<Record<string, string>>({})
  const [toolOutputs, setToolOutputs] = useState<Record<string, string>>({})
  const [runningToolId, setRunningToolId] = useState<string | null>(null)
  const [availability, setAvailability] = useState<SkillAvailability[]>([])
  const [availabilitySelection, setAvailabilitySelection] = useState<number[]>([])
  const [selectableSkills, setSelectableSkills] = useState<SelectableSkill[]>([])
  const [memberSelection, setMemberSelection] = useState<number[]>([])
  const [status, setStatus] = useState('准备就绪')
  const [error, setError] = useState<string | null>(null)

  const selectedWorkspace = useMemo(() => workspaces.find((item) => item.id === selectedWorkspaceId) ?? null, [selectedWorkspaceId, workspaces])
  const activeTeam = useMemo(() => (selectedWorkspace?.team_id ? teams.find((item) => item.id === selectedWorkspace.team_id) ?? null : null), [selectedWorkspace, teams])
  const currentTeamMembership = useMemo(() => (user ? members.find((item) => item.user_id === user.id) ?? null : null), [members, user])
  const isTeamAdmin = currentTeamMembership?.role === 'admin'
  const canManageCurrentWorkspace = selectedWorkspace?.type !== 'team' || isTeamAdmin
  const currentWorkspaceKey = useMemo(() => (selectedWorkspaceId ? workspaceKeys.find((item) => item.workspace_id === selectedWorkspaceId) ?? null : null), [workspaceKeys, selectedWorkspaceId])
  const workspaceAggregatorEndpoint = useMemo(() => (selectedWorkspaceId ? `/mcp/workspaces/${selectedWorkspaceId}` : null), [selectedWorkspaceId])
  const workspaceToolMap = useMemo(() => new Map(workspaceTools.map((tool) => [tool.name, tool])), [workspaceTools])

  useEffect(() => {
    if (!token) {
      setUser(null)
      setWorkspaces([])
      setTeams([])
      setSkills([])
      setPersonalSkills([])
      setMembers([])
      setJoinRequests([])
      setWorkspaceTools([])
      return
    }
    void loadDashboard(token)
  }, [token])

  useEffect(() => {
    if (!token || !selectedWorkspace) return
    if (selectedWorkspace.type === 'team' && selectedWorkspace.team_id !== null) {
      void loadTeamContext(selectedWorkspace.team_id, selectedWorkspace.id, token)
    } else {
      setMembers([])
      setJoinRequests([])
      setAvailability([])
      setAvailabilitySelection([])
      setSelectableSkills([])
      setMemberSelection([])
    }
  }, [selectedWorkspace, token])

  useEffect(() => {
    if (!token || !selectedWorkspaceId) {
      setWorkspaceTools([])
      return
    }
    void loadWorkspaceAgentCatalog(selectedWorkspaceId, token)
  }, [selectedWorkspaceId, token])

  async function createWorkspaceSession(workspaceId: number, currentToken: string) {
    const initialize = await mcpRequest(
      `/mcp/workspaces/${workspaceId}`,
      currentToken,
      buildInitializeRequest(),
    )
    const sessionId = initialize.sessionId
    if (!sessionId) throw new Error('MCP initialize did not return a session id')
    return sessionId
  }

  async function loadWorkspaceAgentCatalog(workspaceId: number, currentToken: string) {
    try {
      const sessionId = await createWorkspaceSession(workspaceId, currentToken)
      const toolsResponse = await mcpRequest(`/mcp/workspaces/${workspaceId}`, currentToken, buildToolsListRequest(), sessionId)
      const toolsResult = toolsResponse.payload.result as { tools?: MpcTool[] } | undefined
      setWorkspaceTools(toolsResult?.tools ?? [])
    } catch (err) {
      setWorkspaceTools([])
      setError(err instanceof Error ? err.message : '加载 MCP 工具目录失败')
    }
  }

  async function loadDashboard(currentToken: string) {
    try {
      setError(null)
      setStatus('正在同步控制台')
      const [me, workspaceList, teamList, discoverList, keyList] = await Promise.all([
        apiFetch<User>('/users/me', currentToken),
        apiFetch<Workspace[]>('/workspaces', currentToken),
        apiFetch<Team[]>('/teams', currentToken),
        apiFetch<Team[]>('/teams/discover', currentToken),
        apiFetch<WorkspaceKey[]>('/users/me/api-keys', currentToken),
      ])
      setUser(me)
      setWorkspaces(workspaceList)
      setTeams(teamList)
      setDiscoverTeams(discoverList)
      setWorkspaceKeys(keyList)
      const nextWorkspaceId = selectedWorkspaceId && workspaceList.some((item) => item.id === selectedWorkspaceId) ? selectedWorkspaceId : (workspaceList[0]?.id ?? null)
      setSelectedWorkspaceId(nextWorkspaceId)
      const personalWorkspace = workspaceList.find((item) => item.type === 'personal')
      setPersonalSkills(personalWorkspace ? await apiFetch<Skill[]>(`/workspaces/${personalWorkspace.id}/skills`, currentToken) : [])
      setSkills(nextWorkspaceId ? await apiFetch<Skill[]>(`/workspaces/${nextWorkspaceId}/skills`, currentToken) : [])
      if (nextWorkspaceId) {
        await loadWorkspaceAgentCatalog(nextWorkspaceId, currentToken)
      } else {
        setWorkspaceTools([])
      }
      setStatus('控制台已同步')
    } catch (err) {
      localStorage.removeItem('skillhub-token')
      setToken(null)
      setUser(null)
      setError(err instanceof Error ? err.message : '加载控制台失败')
      setStatus('同步失败')
    }
  }

  async function loadTeamContext(teamId: number, workspaceId: number, currentToken: string) {
    const [teamMembers, mySkills, requests, openSkills, availableSkills] = await Promise.all([
      apiFetch<TeamMember[]>(`/teams/${teamId}/members`, currentToken).catch(() => []),
      apiFetch<{ enabled_skill_ids: number[] }>(`/teams/${teamId}/me/skills`, currentToken).catch(() => ({ enabled_skill_ids: [] })),
      apiFetch<JoinRequest[]>(`/teams/${teamId}/join-requests`, currentToken).catch(() => []),
      apiFetch<SkillAvailability[]>(`/workspaces/${workspaceId}/skill-availability`, currentToken).catch(() => []),
      apiFetch<SelectableSkill[]>(`/teams/${teamId}/selectable-skills`, currentToken).catch(() => []),
    ])
    setMembers(teamMembers)
    setJoinRequests(requests)
    setAvailability(openSkills)
    setAvailabilitySelection(openSkills.filter((item) => item.enabled).map((item) => item.id))
    setSelectableSkills(availableSkills)
    setMemberSelection(mySkills.enabled_skill_ids)
  }
  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      setAuthenticating(true)
      setError(null)
      setStatus(authMode === 'login' ? '正在登录' : '正在注册')
      const data = await apiFetch<{ access_token: string }>(`/auth/${authMode}`, null, {
        method: 'POST',
        body: JSON.stringify({ account: account.trim(), password }),
      })
      localStorage.setItem('skillhub-token', data.access_token)
      setToken(data.access_token)
      await loadDashboard(data.access_token)
      setPassword('')
      setStatus(authMode === 'login' ? '登录成功' : '注册成功')
    } catch (err) {
      localStorage.removeItem('skillhub-token')
      setToken(null)
      setUser(null)
      setError(err instanceof Error ? err.message : '认证失败')
      setStatus('认证失败')
    } finally {
      setAuthenticating(false)
    }
  }

  async function handleWorkspaceChange(workspaceId: number) {
    if (!token) return
    try {
      setSelectedWorkspaceId(workspaceId)
      setSkills(await apiFetch<Skill[]>(`/workspaces/${workspaceId}/skills`, token))
      setStatus('空间已切换')
    } catch (err) {
      setError(err instanceof Error ? err.message : '切换空间失败')
    }
  }

  async function handleCreateTeam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !teamName.trim()) return
    try {
      setStatus('正在创建团队')
      await apiFetch<Team>('/teams', token, { method: 'POST', body: JSON.stringify({ name: teamName.trim() }) })
      setTeamName('')
      await loadDashboard(token)
      setStatus('团队已创建')
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建团队失败')
    }
  }

  async function handleRequestJoin(teamId: number) {
    if (!token) return
    try {
      await apiFetch('/teams/join-requests', token, { method: 'POST', body: JSON.stringify({ team_id: teamId }) })
      await loadDashboard(token)
      setStatus('已提交加入申请')
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交申请失败')
    }
  }

  async function handleJoinDecision(teamId: number, requestId: number, approve: boolean) {
    if (!token || !selectedWorkspaceId) return
    try {
      await apiFetch(`/teams/${teamId}/join-requests/${requestId}`, token, { method: 'POST', body: JSON.stringify({ approve }) })
      await loadTeamContext(teamId, selectedWorkspaceId, token)
      await loadDashboard(token)
      setStatus(approve ? '已通过申请' : '已拒绝申请')
    } catch (err) {
      setError(err instanceof Error ? err.message : '处理申请失败')
    }
  }

  async function handleAddMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !selectedWorkspace?.team_id || !memberAccount.trim()) return
    try {
      await apiFetch(`/teams/${selectedWorkspace.team_id}/members`, token, {
        method: 'POST',
        body: JSON.stringify({ account: memberAccount.trim(), role: memberRole }),
      })
      setMemberAccount('')
      await loadTeamContext(selectedWorkspace.team_id, selectedWorkspace.id, token)
      setStatus('成员已加入团队')
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加成员失败')
    }
  }

  async function handleCreateOrRotateKey() {
    if (!token || !selectedWorkspaceId) return
    try {
      const created = await apiFetch<WorkspaceKey>('/users/me/api-keys', token, {
        method: 'POST',
        body: JSON.stringify({ workspace_id: selectedWorkspaceId }),
      })
      setWorkspaceKeys((current) => [created, ...current.filter((item) => item.workspace_id !== selectedWorkspaceId)])
      setStatus('空间 Key 已更新')
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成 Key 失败')
    }
  }

  async function handleDeleteCurrentKey() {
    if (!token || !currentWorkspaceKey) return
    try {
      await apiFetch(`/users/me/api-keys/${currentWorkspaceKey.id}`, token, { method: 'DELETE' })
      setWorkspaceKeys((current) => current.filter((item) => item.id !== currentWorkspaceKey.id))
      setStatus('空间 Key 已删除')
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除 Key 失败')
    }
  }

  async function handleUploadSkill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!token || !selectedWorkspaceId || !uploadFile) return
    try {
      const formData = new FormData()
      formData.append('package', uploadFile)
      if (uploadName.trim()) formData.append('name', uploadName.trim())
      if (uploadDescription.trim()) formData.append('description', uploadDescription.trim())
      await apiFetch<Skill>(`/workspaces/${selectedWorkspaceId}/skills/upload`, token, { method: 'POST', body: formData })
      setUploadFile(null)
      setUploadName('')
      setUploadDescription('')
      await loadDashboard(token)
      setStatus('Skill 上传成功')
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传 Skill 失败')
    }
  }

  async function handleCopySkillToCurrent(skill: Skill) {
    if (!token || !selectedWorkspaceId) return
    try {
      await apiFetch(`/skills/${skill.id}/copy`, token, { method: 'POST', body: JSON.stringify({ target_workspace_id: selectedWorkspaceId }) })
      await loadDashboard(token)
      setStatus('已导入到当前空间')
    } catch (err) {
      setError(err instanceof Error ? err.message : '导入 Skill 失败')
    }
  }

  async function handleSaveAvailability() {
    if (!token || !selectedWorkspaceId) return
    try {
      const updated = await apiFetch<SkillAvailability[]>(`/workspaces/${selectedWorkspaceId}/skill-availability`, token, {
        method: 'PUT',
        body: JSON.stringify({ enabled_skill_ids: availabilitySelection }),
      })
      setAvailability(updated)
      setSkills(await apiFetch<Skill[]>(`/workspaces/${selectedWorkspaceId}/skills`, token))
      if (selectedWorkspace?.team_id) await loadTeamContext(selectedWorkspace.team_id, selectedWorkspaceId, token)
      setStatus('对外开放配置已保存')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存开放配置失败')
    }
  }

  async function handleSaveMySkills() {
    if (!token || !selectedWorkspace?.team_id) return
    try {
      await apiFetch(`/teams/${selectedWorkspace.team_id}/me/skills`, token, { method: 'PUT', body: JSON.stringify({ enabled_skill_ids: memberSelection }) })
      setSkills(await apiFetch<Skill[]>(`/workspaces/${selectedWorkspace.id}/skills`, token))
      setStatus('我的 Skill 选择已保存')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存个人 Skill 失败')
    }
  }

  async function handleDeleteSkill(skillId: number) {
    if (!token) return
    try {
      await apiFetch(`/skills/${skillId}`, token, { method: 'DELETE' })
      await loadDashboard(token)
      setStatus('Skill 已删除')
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除 Skill 失败')
    }
  }

  async function handleRunTool(skill: Skill, tool: ToolDefinition) {
    if (!token || !selectedWorkspaceId) return
    const toolAlias = workspaceToolAlias(skill.id, tool.name)
    try {
      setRunningToolId(toolAlias)
      const args = parseToolArguments(toolInputs[toolAlias] ?? '{}')
      const sessionId = await createWorkspaceSession(selectedWorkspaceId, token)
      const callResponse = await mcpRequest(
        `/mcp/workspaces/${selectedWorkspaceId}`,
        token,
        buildToolsCallRequest(toolAlias, args),
        sessionId,
      )
      setToolOutputs((current) => ({ ...current, [toolAlias]: JSON.stringify(callResponse.payload.result, null, 2) }))
      setStatus(`已通过工作区 MCP 调用 ${toolAlias}`)
    } catch (err) {
      setToolOutputs((current) => ({ ...current, [toolAlias]: err instanceof Error ? err.message : '工具调用失败' }))
    } finally {
      setRunningToolId(null)
    }
  }

  function toggleSelection(selection: number[], setter: Dispatch<SetStateAction<number[]>>, skillId: number) {
    setter(selection.includes(skillId) ? selection.filter((item) => item !== skillId) : [...selection, skillId])
  }

  function handleLogout() {
    localStorage.removeItem('skillhub-token')
    setToken(null)
    setUser(null)
    setPassword('')
    setStatus('已退出登录')
  }
  if (!token || !user) {
    return (
      <main className="shell">
        <section className="hero-panel">
          <div className="hero-copy">
            <span className="eyebrow">SkillHub</span>
            <h1>开源的团队 Skill 管理与 MCP 网关</h1>
            <p>统一管理个人 Skill、团队 Skill 和可被 agent 调用的工具入口。</p>
            <p>支持 ZIP 导入、团队权限控制、成员按需启用 Skill，以及单 Skill 与工作区级 MCP 接口。</p>
            <p><a className="doc-link" href="/guide.html" target="_blank" rel="noreferrer">查看使用文档</a></p>
          </div>
          <form className="auth-card" onSubmit={handleAuthSubmit}>
            <div className="tab-row">
              <button type="button" className={authMode === 'login' ? 'active' : ''} onClick={() => setAuthMode('login')}>登录</button>
              <button type="button" className={authMode === 'register' ? 'active' : ''} onClick={() => setAuthMode('register')}>注册</button>
            </div>
            <label>
              账号
              <input value={account} onChange={(event) => setAccount(event.target.value)} placeholder="输入账号" />
            </label>
            <label>
              密码
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入密码" />
            </label>
            <button className="primary-button" type="submit" disabled={authenticating || !account.trim() || !password}>
              {authenticating ? '处理中...' : authMode === 'login' ? '进入控制台' : '注册并进入'}
            </button>
            <p className="muted">{status}</p>
            {error ? <p className="error-text">{error}</p> : null}
          </form>
        </section>
      </main>
    )
  }

  return (
    <main className="dashboard-shell dashboard-split">
      <aside className="sidebar-card scroll-pane">
        <div>
          <span className="eyebrow">控制台</span>
          <h2>{user.account}</h2>
          <p className="muted"><a className="doc-link" href="/guide.html" target="_blank" rel="noreferrer">打开使用文档</a></p>
          <p className="muted">{status}</p>
          {error ? <p className="error-text">{error}</p> : null}
        </div>

        <section className="stack-form">
          <div><span className="eyebrow">空间</span><h3>我的工作区</h3></div>
          <div className="workspace-list">
            {workspaces.map((workspace) => (
              <button key={workspace.id} className={workspace.id === selectedWorkspaceId ? 'workspace-item active' : 'workspace-item'} onClick={() => void handleWorkspaceChange(workspace.id)}>
                <strong>{workspace.name}</strong>
                <span>{workspace.type === 'team' ? '团队空间' : '个人空间'}</span>
              </button>
            ))}
          </div>
        </section>

        {selectedWorkspace?.type === 'team' && selectedWorkspace.team_id ? (
          <section className="stack-form">
            <div>
              <span className="eyebrow">团队入口</span>
              <h3>{activeTeam?.name ?? selectedWorkspace.name}</h3>
              <p className="muted">我的身份：{currentTeamMembership?.role === 'admin' ? '管理员' : '成员'}</p>
            </div>
            {isTeamAdmin ? (
              <>
                <form className="stack-form" onSubmit={handleAddMember}>
                  <label>邀请成员<input value={memberAccount} onChange={(event) => setMemberAccount(event.target.value)} placeholder="输入账号" /></label>
                  <label>角色<select value={memberRole} onChange={(event) => setMemberRole(event.target.value as 'admin' | 'member')}><option value="member">成员</option><option value="admin">管理员</option></select></label>
                  <button className="secondary-button" type="submit">直接加入</button>
                </form>
                <div className="stack-form">
                  <strong>待审核申请</strong>
                  {joinRequests.map((item) => (
                    <article key={item.id} className="member-card">
                      <strong>{item.account}</strong>
                      <div className="action-row">
                        <button className="secondary-button" onClick={() => void handleJoinDecision(item.team_id, item.id, true)}>通过</button>
                        <button className="ghost-button danger" onClick={() => void handleJoinDecision(item.team_id, item.id, false)}>拒绝</button>
                      </div>
                    </article>
                  ))}
                  {joinRequests.length === 0 ? <p className="muted">当前没有待审核申请。</p> : null}
                </div>
              </>
            ) : null}
            <div className="member-grid">
              {members.map((member) => (
                <article key={member.user_id} className="member-card"><strong>{member.account}</strong><span>{member.role}</span></article>
              ))}
            </div>
          </section>
        ) : null}

        <form className="stack-form" onSubmit={handleCreateTeam}>
          <label>新建团队<input value={teamName} onChange={(event) => setTeamName(event.target.value)} placeholder="输入团队名" /></label>
          <button className="secondary-button" type="submit">创建团队空间</button>
        </form>

        <section className="stack-form">
          <div><span className="eyebrow">加入空间</span><h3>发现团队</h3></div>
          {discoverTeams.map((team) => (
            <article key={team.id} className="member-card">
              <strong>{team.name}</strong>
              <span>{team.has_pending_request ? '审核中' : '可申请加入'}</span>
              <button className="secondary-button" disabled={team.has_pending_request} onClick={() => void handleRequestJoin(team.id)}>{team.has_pending_request ? '已提交申请' : '申请加入'}</button>
            </article>
          ))}
          {discoverTeams.length === 0 ? <p className="muted">当前没有可加入的新团队。</p> : null}
        </section>

        <button className="ghost-button" onClick={handleLogout}>退出登录</button>
      </aside>

      <section className="content-grid scroll-pane">
        <header className="top-banner">
          <div>
            <span className="eyebrow">当前空间</span>
            <h1>{selectedWorkspace?.name ?? '未选择空间'}</h1>
            <p>{selectedWorkspace?.type === 'team' ? '团队空间支持审核加入、管理员开放 Skill、成员独立选择使用 Skill。' : '个人空间用于上传、测试和整理你自己的 Skill。'}</p>
          </div>
          <div className="status-box">
            <span>状态</span>
            <strong>{status}</strong>
            {selectedWorkspace ? <p className="muted">工作区 ID：{selectedWorkspace.id}</p> : null}
          </div>
        </header>
        {selectedWorkspace?.type === 'team' ? (
          <section className="panel panel-wide">
            <div className="section-head">
              <div><span className="eyebrow">空间 Key</span><h3>当前团队空间 Key</h3></div>
              <div className="action-row">
                <button className="secondary-button" onClick={() => void handleCreateOrRotateKey()}>{currentWorkspaceKey ? '轮换 Key' : '生成 Key'}</button>
                {currentWorkspaceKey ? <button className="ghost-button danger" onClick={() => void handleDeleteCurrentKey()}>删除 Key</button> : null}
              </div>
            </div>
            <article className="key-card">
              <div>
                <strong>{selectedWorkspace.name}</strong>
                <p>每个成员在当前团队空间中只有一把长期可用 Key。</p>
                {currentWorkspaceKey ? <p>创建时间：{new Date(currentWorkspaceKey.created_at).toLocaleString()}</p> : <p>尚未生成空间 Key。</p>}
                {currentWorkspaceKey?.token ? <code>{currentWorkspaceKey.token}</code> : null}
              </div>
            </article>
          </section>
        ) : null}

        {selectedWorkspace?.type === 'team' && isTeamAdmin ? (
          <section className="panel panel-wide">
            <div className="section-head"><div><span className="eyebrow">管理员配置</span><h3>对外开放的 Skill</h3></div><button className="secondary-button" onClick={() => void handleSaveAvailability()}>保存开放列表</button></div>
            <div className="member-grid">
              {availability.map((item) => (
                <label key={item.id} className="member-card">
                  <strong>{item.name}</strong>
                  <span>{item.tool_count} 个工具</span>
                  <div className="toggle-row">
                    <input className="toggle-input" type="checkbox" checked={availabilitySelection.includes(item.id)} onChange={() => toggleSelection(availabilitySelection, setAvailabilitySelection, item.id)} />
                    <span className={availabilitySelection.includes(item.id) ? 'toggle-pill active' : 'toggle-pill'}>{availabilitySelection.includes(item.id) ? '已开放' : '未开放'}</span>
                  </div>
                  <small className="muted">{item.description ?? '暂无描述'}</small>
                </label>
              ))}
            </div>
          </section>
        ) : null}

        {selectedWorkspace?.type === 'team' ? (
          <section className="panel panel-wide">
            <div className="section-head"><div><span className="eyebrow">成员配置</span><h3>我自己要使用的 Skill</h3></div><button className="secondary-button" onClick={() => void handleSaveMySkills()}>保存我的选择</button></div>
            <div className="member-grid">
              {selectableSkills.map((item) => (
                <label key={item.id} className="member-card">
                  <strong>{item.name}</strong>
                  <span>{item.tool_count} 个工具</span>
                  <div className="toggle-row">
                    <input className="toggle-input" type="checkbox" checked={memberSelection.includes(item.id)} onChange={() => toggleSelection(memberSelection, setMemberSelection, item.id)} />
                    <span className={memberSelection.includes(item.id) ? 'toggle-pill active' : 'toggle-pill'}>{memberSelection.includes(item.id) ? '正在使用' : '不使用'}</span>
                  </div>
                  <small className="muted">取消后，这个 Skill 不会出现在你的列表和接口中。</small>
                </label>
              ))}
              {selectableSkills.length === 0 ? <p className="muted">当前没有可选择的团队 Skill。</p> : null}
            </div>
          </section>
        ) : null}

        <section className="panel panel-wide">
          <div className="section-head"><div><span className="eyebrow">聚合器</span><h3>Agent 工作区 MCP 入口</h3></div></div>
          <div className="mcp-block">
            <strong>工作区 endpoint</strong>
            <code>{workspaceAggregatorEndpoint ? `${API_BASE_URL}${workspaceAggregatorEndpoint}` : '请先选择工作区'}</code>
            <strong>初始化请求</strong>
            <pre>{JSON.stringify(buildInitializeRequest(), null, 2)}</pre>
            <strong>列出当前可调用工具</strong>
            <pre>{JSON.stringify(buildToolsListRequest(), null, 2)}</pre>
            <strong>列出 Skill 说明资源</strong>
            <pre>{JSON.stringify(buildResourcesListRequest(), null, 2)}</pre>
            <p className="muted">agent 应直接使用工作区聚合器返回的扁平工具名，并通过 resources 读取 SKILL.md 说明。实际脚本执行全部留在后端。</p>
          </div>
        </section>

        <section className="panel panel-wide">
          <div className="section-head"><div><span className="eyebrow">Agent 目录</span><h3>当前工作区暴露给 agent 的 Skill 列表</h3></div></div>
          <div className="card-grid skill-grid">
            {skills.map((skill) => (
              <article key={skill.id} className="card skill-card">
                <div className="skill-header">
                  <div>
                    <h4>{skill.name}</h4>
                    <p>{skill.description ?? '暂无描述'}</p>
                  </div>
                  <span className={`visibility ${skill.visibility}`}>{skill.visibility}</span>
                </div>
                <span className="key-meta">{skill.tools.length} 个工具</span>
              </article>
            ))}
            {skills.length === 0 ? <p className="muted">当前工作区还没有暴露给 agent 的 Skill。</p> : null}
          </div>
        </section>

        {canManageCurrentWorkspace ? (
          <>
            <section className="panel">
              <div className="section-head"><div><span className="eyebrow">上传</span><h3>上传 Skill ZIP</h3></div></div>
              <form className="stack-form" onSubmit={handleUploadSkill}>
                <label>覆盖显示名称<input value={uploadName} onChange={(event) => setUploadName(event.target.value)} placeholder="留空则使用 ZIP 内的名称" /></label>
                <label>描述<textarea value={uploadDescription} onChange={(event) => setUploadDescription(event.target.value)} rows={3} placeholder="可选说明" /></label>
                <label>ZIP 文件<input type="file" accept=".zip" onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} /></label>
                <p className="muted">{uploadFile ? `已选择：${uploadFile.name}` : '尚未选择文件'}</p>
                <button className="primary-button" type="submit">上传并注册</button>
              </form>
            </section>
          </>
        ) : (
          <section className="panel">
            <div className="section-head"><div><span className="eyebrow">Skill 管理</span><h3>当前空间由管理员维护</h3></div></div>
            <p className="muted">团队成员可以发现、选择并调用 Skill，但不能修改团队空间中的 Skill。</p>
          </section>
        )}

        {selectedWorkspace?.type === 'team' && canManageCurrentWorkspace ? (
          <section className="panel">
            <div className="section-head"><div><span className="eyebrow">导入</span><h3>从个人空间导入 Skill</h3></div></div>
            <div className="key-list">
              {personalSkills.map((skill) => (
                <article key={skill.id} className="key-card">
                  <div><strong>{skill.name}</strong><p>{skill.description ?? '暂无描述'}</p></div>
                  <button className="secondary-button" onClick={() => void handleCopySkillToCurrent(skill)}>导入到当前团队空间</button>
                </article>
              ))}
              {personalSkills.length === 0 ? <p className="muted">个人空间里还没有可导入的 Skill。</p> : null}
            </div>
          </section>
        ) : null}

        <section className="panel panel-wide">
          <div className="section-head"><div><span className="eyebrow">可见 Skill</span><h3>当前对你可见的 Skill</h3></div></div>
          <div className="skill-grid">
            {skills.map((skill) => (
              <article key={skill.id} className="skill-card">
                <div className="skill-header"><div><h4>{skill.name}</h4><p>{skill.description ?? '暂无描述'}</p></div></div>
                <div className="tool-tags">{skill.tools.map((tool) => <span key={tool.id}>{tool.name}</span>)}</div>
                <div className="mcp-block">
                  <strong>Agent 使用入口</strong>
                  <code>{workspaceAggregatorEndpoint ? `${API_BASE_URL}${workspaceAggregatorEndpoint}` : '请先选择工作区'}</code>
                  <pre>{workspaceAggregatorEndpoint ? buildCurlExample(API_BASE_URL, workspaceAggregatorEndpoint) : '请先选择工作区'}</pre>
                  <p className="muted">该 Skill 的工具通过工作区聚合器暴露给 agent。后端负责脚本执行与结果返回。</p>
                </div>
                <details className="tool-collapse">
                  <summary>展开通过工作区 MCP 的测试与调用</summary>
                  <div className="tool-collapse-body">
                    {skill.tools.map((tool) => (
                      (() => {
                        const alias = workspaceToolAlias(skill.id, tool.name)
                        const exposedTool = workspaceToolMap.get(alias)
                        return (
                          <div key={tool.id} className="tool-tester">
                            <div className="tool-tester-head"><strong>测试 {alias}</strong><span>{tool.description ?? '暂无描述'}</span></div>
                            <p className="muted">agent 工具名：{alias}</p>
                            <textarea rows={4} value={toolInputs[alias] ?? '{}'} onChange={(event) => setToolInputs((current) => ({ ...current, [alias]: event.target.value }))} placeholder='{"text":"hello"}' />
                            <button className="secondary-button" onClick={() => void handleRunTool(skill, tool)} disabled={runningToolId === alias || !exposedTool}>
                              {runningToolId === alias ? '调用中...' : exposedTool ? '通过工作区 MCP 调用' : '当前对 agent 不可见'}
                            </button>
                            <pre>{toolOutputs[alias] ?? '调用结果会显示在这里。'}</pre>
                          </div>
                        )
                      })()
                    ))}
                  </div>
                </details>
                {canManageCurrentWorkspace ? <button className="ghost-button danger" onClick={() => void handleDeleteSkill(skill.id)}>删除 Skill</button> : null}
              </article>
            ))}
            {skills.length === 0 ? <p className="muted">当前没有对你开放的 Skill。</p> : null}
          </div>
        </section>
      </section>
    </main>
  )
}
