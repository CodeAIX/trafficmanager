import React, { useMemo, useState } from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import {
  Alert,
  App as AntApp,
  Button,
  Card,
  ConfigProvider,
  Descriptions,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
  theme,
} from 'antd'
import zhCN from 'antd/locale/zh_CN'
import './styles.css'

const queryClient = new QueryClient()
let csrf = ''

async function api(path: string, options: RequestInit = {}) {
  const headers = new Headers(options.headers)
  if (options.body) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD'].includes(options.method || 'GET') && csrf) headers.set('X-CSRF-Token', csrf)
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail?.message || body.detail || `请求失败（HTTP ${response.status}）`)
  }
  return response.json()
}

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error)

const modeLabels: Record<string, string> = {
  MANAGED: '托管',
  OBSERVE: '观察',
  IGNORE: '忽略',
}

const statusLabels: Record<string, string> = {
  ONLINE: '在线',
  OFFLINE: '离线',
  SUCCESS: '成功',
  FAILED: '失败',
  PARTIAL: '部分成功',
  PENDING: '等待中',
  RUNNING: '执行中',
  MISSED: '已错过',
  OBSERVE: '观察',
  MANAGED: '托管',
  IGNORE: '忽略',
}

function Auth({ children }: { children: React.ReactNode }) {
  const { message } = AntApp.useApp()
  const status = useQuery({ queryKey: ['auth'], queryFn: () => api('/auth/status') })
  if (status.isLoading) return <div className="center">正在加载…</div>
  if (status.data?.authenticated) {
    csrf = status.data.csrfToken
    return children
  }
  const setup = !status.data?.initialized
  return (
    <div className="auth">
      <Card title={setup ? '创建管理员账户' : '管理员登录'}>
        <Typography.Paragraph type="secondary">TrafficManager 流量管理平台</Typography.Paragraph>
        <Form
          layout="vertical"
          onFinish={async values => {
            try {
              const result = await api(setup ? '/auth/setup' : '/auth/login', {
                method: 'POST',
                body: JSON.stringify(values),
              })
              csrf = result.csrfToken
              await status.refetch()
            } catch (error) {
              message.error(errorText(error))
            }
          }}
        >
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input autoFocus autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 10, message: '密码至少需要 10 个字符' }]}>
            <Input.Password autoComplete={setup ? 'new-password' : 'current-password'} />
          </Form.Item>
          <Button block type="primary" htmlType="submit">{setup ? '创建管理员' : '登录'}</Button>
        </Form>
      </Card>
    </div>
  )
}

const fmtBytes = (n?: number | null) => n == null ? '—' : `${(n / 1073741824).toFixed(n > 10737418240 ? 0 : 1)} GiB`
const fmtQuota = (n?: number | null) => n == null || n === 0 ? '不限' : fmtBytes(n)

const statusTag = (value: string) => (
  <Tag color={value === 'ONLINE' || value === 'SUCCESS' ? 'green' : value === 'OBSERVE' || value === 'PARTIAL' ? 'gold' : value === 'MANAGED' || value === 'RUNNING' ? 'blue' : 'red'}>
    {statusLabels[value] || value}
  </Tag>
)

function Dashboard() {
  const { data = {} } = useQuery({ queryKey: ['dashboard'], queryFn: () => api('/api/dashboard'), refetchInterval: 15000 })
  const cards = [
    ['节点总数', data.nodes],
    ['在线节点', data.online_nodes],
    ['客户端总数', data.clients],
    ['托管客户端', data.managed_clients],
    ['策略冲突', data.policy_conflicts],
    ['接近配额', data.near_quota_clients],
    ['失败任务', data.failed_jobs],
    ['总流量', fmtBytes(data.total_traffic)],
  ]
  return <><Typography.Title level={2}>仪表盘</Typography.Title><div className="grid">{cards.map(([title, value]) => <Card key={title as string}><Statistic title={title} value={value ?? 0} /></Card>)}</div></>
}

function Nodes() {
  const { message } = AntApp.useApp()
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['nodes'], queryFn: () => api('/api/nodes') })
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const add = useMutation({
    mutationFn: (values: any) => api('/api/nodes', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: () => {
      message.success('节点已添加，首次同步已经开始')
      setOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
    onError: error => message.error(errorText(error)),
  })
  return <>
    <Space className="heading">
      <Typography.Title level={2}>节点</Typography.Title>
      <Button type="primary" onClick={() => setOpen(true)}>添加节点</Button>
    </Space>
    <Table rowKey="id" dataSource={data} columns={[
      { title: '状态', dataIndex: 'status', render: statusTag },
      { title: '名称', dataIndex: 'name' },
      { title: '备注', dataIndex: 'remark' },
      { title: '基础地址', dataIndex: 'base_url' },
      { title: 'API 模式', dataIndex: 'api_mode' },
      { title: '入站数', dataIndex: 'inbounds' },
      { title: '客户端数', dataIndex: 'clients' },
      { title: 'TLS', dataIndex: 'tls_verify', render: value => value ? <Tag color="green">已验证</Tag> : <Tag color="red">已跳过</Tag> },
      { title: '操作', render: (_, row: any) => <Button onClick={async () => { try { await api(`/api/nodes/${row.id}/sync`, { method: 'POST' }); message.success('同步完成'); qc.invalidateQueries() } catch (error) { message.error(errorText(error)) } }}>同步</Button> },
    ]} />
    <Modal title="添加 3x-ui 节点" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} confirmLoading={add.isPending} okText="添加" cancelText="取消">
      <Alert type="info" showIcon message="保存前会先测试节点。令牌将加密保存，之后不会在界面中显示。" />
      <Form form={form} layout="vertical" onFinish={values => add.mutate({ ...values, tags: [], tls_verify: values.tls_verify ?? true })}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入节点名称' }]}><Input /></Form.Item>
        <Form.Item name="remark" label="备注"><Input /></Form.Item>
        <Form.Item name="base_url" label="基础地址" rules={[{ required: true, message: '请输入基础地址' }]}><Input placeholder="https://host:2053/webbase" /></Form.Item>
        <Form.Item name="token" label="API 令牌" rules={[{ required: true, message: '请输入 API 令牌' }]}><Input.Password /></Form.Item>
        <Form.Item name="tls_verify" label="验证 TLS 证书" valuePropName="checked" initialValue={true}><Switch /></Form.Item>
      </Form>
    </Modal>
  </>
}

type ResetPreview = {
  start: boolean
  nodes: number
  inbounds: number
  clients: number
}

function Clients() {
  const { message } = AntApp.useApp()
  const qc = useQueryClient()
  const { data = [], isFetching } = useQuery({ queryKey: ['clients'], queryFn: () => api('/api/clients') })
  const [selected, setSelected] = useState<React.Key[]>([])
  const [preview, setPreview] = useState<ResetPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [executing, setExecuting] = useState(false)
  const selectedClients = useMemo(() => data.filter((client: any) => selected.includes(client.id)), [data, selected])
  const blocked = selectedClients.filter((client: any) => client.managed_mode !== 'MANAGED')

  const openResetPreview = async (start = false) => {
    if (!selected.length) {
      message.warning('请先勾选需要操作的客户端')
      return
    }
    if (blocked.length) {
      message.warning(`只有“托管”模式可以重置流量，请先修改：${blocked.map((client: any) => client.email).join('、')}`)
      return
    }
    setPreviewLoading(true)
    try {
      const result = await api('/api/clients/reset-preview', {
        method: 'POST',
        body: JSON.stringify({ client_ids: selected.map(Number), start_new_cycle: start }),
      })
      setPreview({ start, ...result })
    } catch (error) {
      message.error(`无法生成操作预览：${errorText(error)}`)
    } finally {
      setPreviewLoading(false)
    }
  }

  const executeReset = async () => {
    if (!preview) return
    setExecuting(true)
    try {
      const result = await api('/api/clients/bulk-reset', {
        method: 'POST',
        body: JSON.stringify({ client_ids: selected.map(Number), start_new_cycle: preview.start }),
      })
      message.success(`任务 #${result.job_id} 已创建，可在“任务”页面查看进度`)
      setPreview(null)
      setSelected([])
      await Promise.all([
        qc.invalidateQueries({ queryKey: ['jobs'] }),
        qc.invalidateQueries({ queryKey: ['clients'] }),
      ])
    } catch (error) {
      message.error(`操作失败：${errorText(error)}`)
    } finally {
      setExecuting(false)
    }
  }

  const changeMode = async (client: any, mode: string) => {
    try {
      await api(`/api/clients/${client.id}`, { method: 'PATCH', body: JSON.stringify({ managed_mode: mode }) })
      message.success(`${client.email} 已切换为“${modeLabels[mode]}”模式`)
      await qc.invalidateQueries({ queryKey: ['clients'] })
    } catch (error) {
      message.error(`模式修改失败：${errorText(error)}`)
    }
  }

  return <>
    <div className="heading clients-heading">
      <div>
        <Typography.Title level={2}>客户端</Typography.Title>
        <Typography.Text type="secondary">重置操作仅适用于“托管”模式的客户端</Typography.Text>
      </div>
      <Space wrap>
        <Typography.Text>{selected.length ? `已选择 ${selected.length} 项` : '尚未选择'}</Typography.Text>
        <Button disabled={!selected.length} danger loading={previewLoading} onClick={() => openResetPreview(false)}>重置流量</Button>
        <Button disabled={!selected.length} type="primary" loading={previewLoading} onClick={() => openResetPreview(true)}>开始新周期</Button>
      </Space>
    </div>
    {blocked.length > 0 && <Alert className="selection-alert" type="warning" showIcon message="当前选择中包含非托管客户端" description={`请先将以下客户端的模式改为“托管”：${blocked.map((client: any) => client.email).join('、')}`} />}
    <Table
      rowKey="id"
      loading={isFetching}
      rowSelection={{ selectedRowKeys: selected, onChange: setSelected }}
      dataSource={data}
      columns={[
        { title: '节点', dataIndex: 'node' },
        { title: '邮箱标识', dataIndex: 'email' },
        { title: '入站', dataIndex: 'inbounds', render: value => value.map((item: any) => item.remark || item.remote_id).join('、') },
        { title: '管理模式', dataIndex: 'managed_mode', render: (value, row: any) => <Select value={value} style={{ width: 120 }} options={['MANAGED', 'OBSERVE', 'IGNORE'].map(item => ({ value: item, label: modeLabels[item] }))} onChange={mode => changeMode(row, mode)} /> },
        { title: '已用流量', dataIndex: 'used_bytes', render: fmtBytes },
        { title: '配额', dataIndex: 'quota_bytes', render: fmtQuota },
        { title: '使用率', dataIndex: 'percentage', render: value => value == null ? '—' : `${value}%` },
        { title: '生效策略', dataIndex: 'policy', render: (value, row: any) => row.policy_conflict ? <Tag color="gold">策略冲突</Tag> : value || '无' },
        { title: '警告', render: (_, row: any) => row.native_reset_conflict ? <Tag color="gold">原生重置冲突</Tag> : null },
      ]}
    />
    <Modal
      title={preview?.start ? '确认开始新周期' : '确认重置流量'}
      open={Boolean(preview)}
      okText={preview ? `确认操作 ${preview.clients} 个客户端` : '确认'}
      cancelText="取消"
      okButtonProps={{ danger: true }}
      confirmLoading={executing}
      closable={!executing}
      maskClosable={!executing}
      onCancel={() => !executing && setPreview(null)}
      onOk={executeReset}
    >
      {preview && <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Alert type="warning" showIcon message="此操作无法恢复旧的流量计数，请确认目标无误。" />
        <div className="preview-grid">
          <Statistic title="节点" value={preview.nodes} />
          <Statistic title="入站" value={preview.inbounds} />
          <Statistic title="客户端" value={preview.clients} />
        </div>
        <Typography.Text type="secondary">入站总流量计数不会被修改。</Typography.Text>
      </Space>}
    </Modal>
  </>
}

function Policies() {
  const { message } = AntApp.useApp()
  const qc = useQueryClient()
  const { data = [] } = useQuery({ queryKey: ['policies'], queryFn: () => api('/api/policies') })
  const { data: nodes = [] } = useQuery({ queryKey: ['nodes'], queryFn: () => api('/api/nodes') })
  const [open, setOpen] = useState(false)
  const [assigning, setAssigning] = useState<any | null>(null)
  const [assignedNodeIds, setAssignedNodeIds] = useState<number[]>([])
  const [assigningLoading, setAssigningLoading] = useState(false)
  const [form] = Form.useForm()
  const create = async (values: any) => {
    try {
      const { node_ids = [], quota_gib, ...policyValues } = values
      const policy = await api('/api/policies', { method: 'POST', body: JSON.stringify({ ...policyValues, quota_bytes: quota_gib == null ? null : Number(quota_gib) * 1073741824, local_time: values.local_time || '00:00', enabled: true, reset_enabled: values.reset_enabled ?? true, catchup_enabled: true, catchup_max_hours: 168, missing_day_policy: 'LAST_DAY', reactivate_mode: 'PRESERVE' }) })
      if (node_ids.length) await api(`/api/policies/${policy.id}/node-assignments`, { method: 'PUT', body: JSON.stringify({ node_ids }) })
      message.success('策略已创建')
      setOpen(false)
      form.resetFields()
      qc.invalidateQueries({ queryKey: ['policies'] })
    } catch (error) {
      message.error(errorText(error))
    }
  }
  const saveAssignments = async () => {
    if (!assigning) return
    setAssigningLoading(true)
    try {
      await api(`/api/policies/${assigning.id}/node-assignments`, { method: 'PUT', body: JSON.stringify({ node_ids: assignedNodeIds }) })
      message.success('目标节点已更新。只有“托管”客户端会按计划执行。')
      setAssigning(null)
      await Promise.all([qc.invalidateQueries({ queryKey: ['policies'] }), qc.invalidateQueries({ queryKey: ['clients'] })])
    } catch (error) {
      message.error(`节点分配失败：${errorText(error)}`)
    } finally {
      setAssigningLoading(false)
    }
  }
  const nodeNames = new Map(nodes.map((node: any) => [node.id, node.name]))
  return <>
    <Space className="heading"><Typography.Title level={2}>策略</Typography.Title><Button type="primary" onClick={() => setOpen(true)}>创建策略</Button></Space>
    <Alert className="selection-alert" type="info" showIcon message="定时重置需要同时满足：策略已分配到目标节点，且该节点下的客户端处于“托管”模式。" />
    <Table rowKey="id" dataSource={data} columns={[
      { title: '名称', dataIndex: 'name' },
      { title: '配额', dataIndex: 'quota_bytes', render: fmtQuota },
      { title: '计划', render: (_, row: any) => row.reset_enabled ? `每月 ${row.monthly_day} 日 ${row.local_time}` : '已禁用' },
      { title: '时区', dataIndex: 'timezone' },
      { title: '目标节点', dataIndex: 'node_ids', render: (value: number[]) => value?.length ? value.map(id => nodeNames.get(id) || `节点 #${id}`).join('、') : '未分配' },
      { title: '下次执行', dataIndex: 'next_run_at', render: value => value ? new Date(value).toLocaleString('zh-CN') : '—' },
      { title: '启用', dataIndex: 'enabled', render: value => value ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
      { title: '操作', render: (_, row: any) => <Button onClick={() => { setAssigning(row); setAssignedNodeIds(row.node_ids || []) }}>分配节点</Button> },
    ]} />
    <Modal title="创建策略" open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()} okText="创建" cancelText="取消">
      <Form form={form} layout="vertical" onFinish={create}>
        <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入策略名称' }]}><Input /></Form.Item>
        <Form.Item name="quota_gib" label="配额（GiB，留空表示不限）"><InputNumber min={0} precision={0} style={{ width: '100%' }} /></Form.Item>
        <Form.Item name="reset_enabled" label="每月重置" valuePropName="checked" initialValue={true}><Switch /></Form.Item>
        <Form.Item name="monthly_day" label="日期" initialValue={1}><InputNumber min={1} max={31} /></Form.Item>
        <Form.Item name="local_time" label="本地时间" initialValue="00:00"><Input type="time" /></Form.Item>
        <Form.Item name="timezone" label="IANA 时区" initialValue="Asia/Shanghai"><Select showSearch options={['UTC', 'Asia/Shanghai', 'Asia/Tokyo', 'America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin'].map(value => ({ value }))} /></Form.Item>
        <Form.Item name="node_ids" label="目标节点"><Select mode="multiple" placeholder="选择需要按计划重置的节点" options={nodes.map((node: any) => ({ value: node.id, label: node.name }))} /></Form.Item>
      </Form>
    </Modal>
    <Modal title={assigning ? `为“${assigning.name}”分配节点` : '分配节点'} open={Boolean(assigning)} okText="保存" cancelText="取消" confirmLoading={assigningLoading} onOk={saveAssignments} onCancel={() => setAssigning(null)}>
      <Alert type="warning" showIcon message="策略只会操作目标节点下处于“托管”模式的客户端。观察和忽略模式不会被修改。" />
      <Select mode="multiple" value={assignedNodeIds} onChange={setAssignedNodeIds} style={{ width: '100%', marginTop: 16 }} placeholder="选择目标节点" options={nodes.map((node: any) => ({ value: node.id, label: node.name }))} />
    </Modal>
  </>
}

function Jobs() {
  const { message } = AntApp.useApp()
  const { data = [] } = useQuery({ queryKey: ['jobs'], queryFn: () => api('/api/jobs'), refetchInterval: 5000 })
  const [detail, setDetail] = useState<any | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const showDetail = async (jobId: number) => {
    setDetailLoading(true)
    try {
      setDetail(await api(`/api/jobs/${jobId}`))
    } catch (error) {
      message.error(`任务详情加载失败：${errorText(error)}`)
    } finally {
      setDetailLoading(false)
    }
  }
  return <><Typography.Title level={2}>任务</Typography.Title><Table rowKey="id" dataSource={data} columns={[
    { title: 'ID', dataIndex: 'id' },
    { title: '类型', dataIndex: 'type', render: value => ({ RESET_TRAFFIC: '重置流量', MONTHLY_CYCLE: '开始新周期' }[value as string] || value) },
    { title: '来源', dataIndex: 'source', render: value => ({ MANUAL: '手动', SCHEDULED: '计划任务', RETRY: '重试' }[value as string] || value) },
    { title: '状态', dataIndex: 'status', render: statusTag },
    { title: '目标数', dataIndex: 'total_targets' },
    { title: '成功', dataIndex: 'success_count' },
    { title: '失败', dataIndex: 'failure_count' },
    { title: '创建时间', dataIndex: 'created_at', render: value => new Date(value).toLocaleString('zh-CN') },
    { title: '摘要', render: (_, row: any) => `${row.success_count} 成功，${row.failure_count} 失败` },
    { title: '操作', render: (_, row: any) => <Button loading={detailLoading} onClick={() => showDetail(row.id)}>详情</Button> },
  ]} />
    <Modal title={detail ? `任务 #${detail.id} 详情` : '任务详情'} open={Boolean(detail)} width={960} footer={null} onCancel={() => setDetail(null)}>
      {detail && <>
        <Descriptions bordered size="small" column={2} items={[
          { key: 'type', label: '类型', children: ({ RESET_TRAFFIC: '重置流量', MONTHLY_CYCLE: '开始新周期' }[detail.type as string] || detail.type) },
          { key: 'status', label: '状态', children: statusTag(detail.status) },
          { key: 'created', label: '创建时间', children: new Date(detail.created_at).toLocaleString('zh-CN') },
          { key: 'finished', label: '完成时间', children: detail.finished_at ? new Date(detail.finished_at).toLocaleString('zh-CN') : '—' },
        ]} />
        <Table className="job-items" rowKey="id" pagination={false} dataSource={detail.items || []} columns={[
          { title: '客户端 ID', dataIndex: 'client_id' },
          { title: '节点 ID', dataIndex: 'node_id' },
          { title: '状态', dataIndex: 'status', render: statusTag },
          { title: '尝试次数', dataIndex: 'attempt_count' },
          { title: '错误详情', dataIndex: 'error', render: value => value || '—' },
        ]} />
      </>}
    </Modal>
  </>
}

function Audit() {
  const { data = [] } = useQuery({ queryKey: ['audit'], queryFn: () => api('/api/audit') })
  return <><Typography.Title level={2}>审计日志</Typography.Title><Table rowKey="id" dataSource={data} columns={[
    { title: '时间', dataIndex: 'timestamp', render: value => new Date(value).toLocaleString('zh-CN') },
    { title: '操作者', dataIndex: 'actor' },
    { title: '来源', dataIndex: 'source' },
    { title: '操作', dataIndex: 'action' },
    { title: '目标', dataIndex: 'target' },
    { title: '结果', dataIndex: 'result', render: statusTag },
  ]} /></>
}

function Settings() {
  const { data = {} } = useQuery({ queryKey: ['settings'], queryFn: () => api('/api/settings') })
  return <><Typography.Title level={2}>设置</Typography.Title><Card><Typography.Title level={4}>常规与任务</Typography.Title><p>同步间隔：{data.sync_interval_minutes} 分钟</p><p>界面时区：{data.default_ui_timezone}</p><p>并发数：全局 {data.global_concurrency} / 每节点 {data.per_node_concurrency}</p><p>网络重试：{data.network_retries} 次；验证重试：{data.verify_retries} 次</p><Button href="/api/settings/backup">下载数据库备份</Button></Card></>
}

const entries = [['/', '仪表盘'], ['/nodes', '节点'], ['/clients', '客户端'], ['/policies', '策略'], ['/jobs', '任务'], ['/audit', '审计日志'], ['/settings', '设置']]

function Shell({ dark, setDark }: { dark: boolean, setDark: (value: boolean) => void }) {
  return <Layout className="shell">
    <Layout.Sider breakpoint="lg" collapsedWidth="0">
      <div className="brand">TrafficManager<br /><small>3x-ui 流量管理</small></div>
      <Menu theme="dark" mode="inline" items={entries.map(([key, label]) => ({ key, label: <NavLink to={key}>{label}</NavLink> }))} />
    </Layout.Sider>
    <Layout>
      <Layout.Header className="top"><Typography.Text>集中式流量策略与重置管理</Typography.Text><Switch checkedChildren="深色" unCheckedChildren="浅色" checked={dark} onChange={setDark} /></Layout.Header>
      <Layout.Content className="content"><Routes><Route path="/" element={<Dashboard />} /><Route path="/nodes" element={<Nodes />} /><Route path="/clients" element={<Clients />} /><Route path="/policies" element={<Policies />} /><Route path="/jobs" element={<Jobs />} /><Route path="/audit" element={<Audit />} /><Route path="/settings" element={<Settings />} /></Routes></Layout.Content>
    </Layout>
  </Layout>
}

function Root() {
  const [dark, setDark] = useState(false)
  return <ConfigProvider locale={zhCN} theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}><AntApp><QueryClientProvider client={queryClient}><BrowserRouter><Auth><Shell dark={dark} setDark={setDark} /></Auth></BrowserRouter></QueryClientProvider></AntApp></ConfigProvider>
}

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><Root /></React.StrictMode>)
