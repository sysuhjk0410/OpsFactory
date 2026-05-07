/* ═════════════════════════════════════════════
   Ops Factory Dashboard — Main SPA Logic
   ═════════════════════════════════════════════ */

// ── State ──
const state = {
    currentView: 'overview',
    refreshTimer: null,
    refreshInterval: 0,
    sseConnections: {},
    rcaRunId: null,
    daemonLogSSE: null,
    detectionSSE: null,
    podChart: null,
    runtime: {
        offlineMode: false,
        observabilityBackend: 'native',
        offlineProblemId: '',
        offlineDataType: '',
    },
    cloudops: {
        selectedCaseRef: '',
        cases: [],
        summary: null,
    },
    modelProvider: {
        provider: 'local',
        model: 'Qwen/Qwen3-0.6B',
        baseUrl: 'http://127.0.0.1:8000/v1',
        userApi: false,
    },
};

// ── Init ──
document.addEventListener('DOMContentLoaded', async () => {
    initNavigation();
    initOfflineProblemSwitcher();
    initRefresh();
    await healthCheck();
    loadOverview();
    setInterval(healthCheck, 30000);
    applySimpleNavigation();
});

// ─────────────────────────────────────────
// Navigation
// ─────────────────────────────────────────

function initNavigation() {
    document.querySelectorAll('.nav-item').forEach(item => {
        item.addEventListener('click', () => {
            const view = item.dataset.view;
            switchView(view);
        });
    });

    document.getElementById('toggle-sidebar').addEventListener('click', () => {
        document.getElementById('sidebar').classList.toggle('collapsed');
    });
}

function applySimpleNavigation() {
    const visibleViews = new Set(['overview', 'datasource', 'consult', 'rca', 'guard', 'collection', 'chat', 'evolution']);
    document.querySelectorAll('.nav-item').forEach(item => {
        item.style.display = visibleViews.has(item.dataset.view) ? '' : 'none';
    });
}

function initOfflineProblemSwitcher() {
    const select = document.getElementById('offline-problem-select');
    if (!select) return;
    select.addEventListener('change', switchOfflineProblem);
}

function switchView(viewId) {
    // Update nav
    document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
    document.querySelector(`.nav-item[data-view="${viewId}"]`)?.classList.add('active');

    // Update content
    document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    document.getElementById(`view-${viewId}`)?.classList.add('active');

    // Update title
    document.getElementById('view-title').textContent = getViewTitle(viewId);

    state.currentView = viewId;
    refreshCurrentView();
}

function getViewTitle(viewId) {
    const titles = isOfflineMode() ? {
        overview: '运维流程', metrics: '离线指标',
        logs: '离线日志', alerts: '告警中心', multiagent: '多智能体诊断', hermes: 'Hermes RCA', rca: '根因分析',
        traces: '离线链路', daemon: '守护进程',
        alidata: 'AliData (阿里云)', events: '事件追踪', chat: '模型交互', consult: '运维问诊台', guard: '持续守护', datasource: '数据平台', collection: '故障数据收集', evolution: 'RCA 成效看板'
    } : {
        overview: '运维流程', metrics: '指标监控', logs: '日志查询',
        alerts: '告警中心', multiagent: '多智能体诊断', hermes: 'Hermes RCA', rca: '根因分析', traces: '链路追踪',
        daemon: '守护进程', events: '事件追踪', chat: '模型交互', consult: '运维问诊台', guard: '持续守护', alidata: 'AliData (阿里云)', datasource: '数据平台', collection: '故障数据收集', evolution: 'RCA 成效看板'
    };
    return titles[viewId] || viewId;
}

function refreshCurrentView() {
    const loaders = {
        overview: loadOverview,
        metrics: loadMetrics,
        logs: loadLogsView,
        alerts: () => { loadAlertList(); loadDetectionConfig(); },
        rca: loadRcaHubView,
        traces: loadTracesView,
        daemon: loadDaemonStatus,
        alidata: loadAliDataView,
        datasource: loadDatasourceView,
        collection: loadFaultCollectionView,
        multiagent: loadMultiagentView,
        hermes: loadHermesRcaView,
        consult: loadOpsConsultView,
        guard: loadContinuousGuardView,
        events: () => { Promise.all([loadNamespaces('event-ns'), loadEvents()]); },
        evolution: loadEvolutionView,
        chat: initChatView,
    };
    (loaders[state.currentView] || (() => {}))();
}

// ── Auto-Refresh ──

function initRefresh() {
    const refreshSelect = document.getElementById('refresh-interval');
    state.refreshInterval = parseInt(refreshSelect?.value || '0', 10);

    refreshSelect.addEventListener('change', (e) => {
        state.refreshInterval = parseInt(e.target.value, 10);
        clearInterval(state.refreshTimer);
        state.refreshTimer = null;
        if (state.refreshInterval > 0) {
            state.refreshTimer = setInterval(refreshCurrentView, state.refreshInterval * 1000);
        }
    });

    if (state.refreshInterval > 0) {
        state.refreshTimer = setInterval(refreshCurrentView, state.refreshInterval * 1000);
    }
}

// ─────────────────────────────────────────
// API Helpers
// ─────────────────────────────────────────

async function api(path, options = {}) {
    try {
        const res = await fetch(path, options);
        const text = await res.text();
        let payload = null;
        try { payload = text ? JSON.parse(text) : {}; } catch { payload = { detail: text }; }
        if (!res.ok) {
            const message = payload?.detail || payload?.error || `HTTP ${res.status}`;
            console.error(`API error [${path}]:`, message);
            return { error: message, status: res.status };
        }
        return payload;
    } catch (e) {
        console.error(`API error [${path}]:`, e);
        return { error: e.message || String(e) };
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTime(ts) {
    if (!ts) return '-';
    try {
        return new Date(ts).toLocaleString('zh-CN');
    } catch { return ts; }
}

function badgeClass(phase) {
    const map = { Running: 'success', Succeeded: 'success', Pending: 'warning', Failed: 'danger', Unknown: 'gray' };
    return map[phase] || 'gray';
}

function isOfflineMode() {
    return !!state.runtime.offlineMode;
}

function normalizeOfflineProblemId(value) {
    const raw = String(value || '').trim();
    return raw.startsWith('problem_') ? raw.slice('problem_'.length) : raw;
}

function currentOfflineDataType() {
    const val = state.runtime.offlineDataType || 'failure';
    return val === 'auto' ? 'failure' : val;
}

function currentOfflineLabel() {
    if (!isOfflineMode()) return '';
    const pid = state.runtime.offlineProblemId || '?';
    return `problem_${pid}/${currentOfflineDataType()}`;
}

async function syncOfflineProblemSwitcher(forceReload = false) {
    const wrapper = document.getElementById('offline-problem-switcher');
    const select = document.getElementById('offline-problem-select');
    if (!wrapper || !select) return;

    if (!isOfflineMode()) {
        wrapper.style.display = 'none';
        select.dataset.loaded = '';
        select.innerHTML = '<option value="">离线模式未开启</option>';
        return;
    }

    wrapper.style.display = 'inline-flex';

    if (forceReload || select.dataset.loaded !== 'true') {
        await loadOfflineProblemOptions();
        return;
    }

    if (state.runtime.offlineProblemId) {
        select.value = state.runtime.offlineProblemId;
    }
}

async function loadOfflineProblemOptions() {
    const select = document.getElementById('offline-problem-select');
    if (!select) return;

    const previousValue = state.runtime.offlineProblemId || select.value || '';
    const data = await api('/api/offline/problems');
    const problems = data?.problems || [];

    if (!problems.length) {
        const currentValue = normalizeOfflineProblemId(data?.current_problem_id || previousValue);
        const label = currentValue ? `problem_${currentValue}` : '无可用数据';
        select.innerHTML = `<option value="${currentValue}">${label}</option>`;
        select.value = currentValue;
        select.disabled = !currentValue;
        select.dataset.loaded = 'true';
        select.title = data?.error || '未发现可用的离线 problem 数据集';
        return;
    }

    select.innerHTML = problems.map(problem => {
        const flags = [
            problem.has_failure ? 'F' : '',
            problem.has_baseline ? 'B' : '',
        ].filter(Boolean).join('/');
        const suffix = flags ? ` (${flags})` : '';
        return `<option value="${problem.problem_id}">${problem.label}${suffix}</option>`;
    }).join('');

    const nextValue = normalizeOfflineProblemId(data?.current_problem_id || previousValue || problems[0]?.problem_id);
    select.value = nextValue;
    select.disabled = false;
    select.dataset.loaded = 'true';
    select.title = data?.error || '切换当前离线 problem 数据集';
}

async function switchOfflineProblem(event) {
    const select = event?.target || document.getElementById('offline-problem-select');
    if (!select) return;

    const nextProblemId = normalizeOfflineProblemId(select.value);
    const currentProblemId = normalizeOfflineProblemId(state.runtime.offlineProblemId);
    if (!nextProblemId || nextProblemId === currentProblemId) {
        select.value = currentProblemId;
        return;
    }

    const previousValue = currentProblemId;
    select.disabled = true;

    try {
        const res = await fetch('/api/offline/problem', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ offline_problem_id: nextProblemId }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
            throw new Error(payload?.detail || `HTTP ${res.status}`);
        }

        state.runtime.offlineProblemId = normalizeOfflineProblemId(payload.offline_problem_id || nextProblemId);
        state.runtime.offlineDataType = payload.offline_data_type || state.runtime.offlineDataType;
        await syncOfflineProblemSwitcher(true);
        await healthCheck();
        refreshCurrentView();
    } catch (error) {
        console.error('Failed to switch offline dataset:', error);
        select.value = previousValue;
        window.alert(`切换离线数据失败：${error.message}`);
    } finally {
        select.disabled = false;
    }
}

function _safeNumber(val, fallback = 0) {
    const num = Number(val);
    return Number.isFinite(num) ? num : fallback;
}

function _truncate(text, maxLen = 14) {
    const str = String(text || '');
    return str.length > maxLen ? str.substring(0, maxLen - 2) + '..' : str;
}

function currentMetricValue(metrics, keys) {
    for (const key of keys) {
        const value = metrics?.[key]?.current;
        if (value != null) return _safeNumber(value);
    }
    return 0;
}

const OFFLINE_POD_KPI_PRIORITY = [
    'pod_cpu_usage_rate',
    'pod_cpu_usage_rate_vs_request',
    'pod_cpu_usage_rate_vs_limit',
    'pod_memory_usage_bytes',
    'pod_memory_working_set_bytes',
    'pod_memory_usage_vs_request',
    'pod_memory_usage_vs_limit',
];

function orderOfflinePodKpis(kpis) {
    const pending = new Set(kpis);
    const ordered = [];

    OFFLINE_POD_KPI_PRIORITY.forEach(kpi => {
        if (pending.has(kpi)) {
            ordered.push(kpi);
            pending.delete(kpi);
        }
    });

    return ordered.concat([...pending].sort());
}

function setPodTableHeadings(headings) {
    const thead = document.querySelector('#pod-table thead');
    if (!thead) return;
    thead.innerHTML = `<tr>${headings.map(heading => `<th>${escapeHtml(heading)}</th>`).join('')}</tr>`;
}

function setPodTableLayoutMode(offlineSummary) {
    const card = document.getElementById('pod-summary-card');
    const table = document.getElementById('pod-table');
    const title = document.getElementById('metrics-title-6');
    if (!card || !table || !title) return;

    card.classList.toggle('offline-pod-summary-card', offlineSummary);
    table.classList.toggle('pod-summary-table', offlineSummary);
    title.classList.toggle('offline-summary-title', offlineSummary);
}

function offlineKpiDisplayUnit(metricName) {
    if (metricName.includes('memory') && metricName.includes('bytes')) return 'MB';
    if (metricName.includes('latency')) return 'ms';
    if (metricName.includes('cpu') || metricName.endsWith('_vs_limit') || metricName.endsWith('_vs_request')) return '%';
    return '';
}

function formatOfflineKpiHeading(metricName) {
    const unit = offlineKpiDisplayUnit(metricName);
    return unit ? `${metricName} (${unit})` : metricName;
}

function formatOfflineKpiValue(metricName, value) {
    if (value == null || !Number.isFinite(value)) return '-';

    if (metricName.includes('memory') && metricName.includes('bytes')) {
        return (value / (1024 * 1024)).toFixed(1);
    }
    if (metricName.includes('latency')) {
        return (value * 1000).toFixed(1);
    }
    if (metricName.includes('cpu') || metricName.endsWith('_vs_limit') || metricName.endsWith('_vs_request')) {
        return value.toFixed(1);
    }
    return value.toFixed(2);
}

function collectOfflinePodKpis(pods) {
    const kpis = new Set();
    (pods || []).forEach(pod => {
        Object.keys(pod.metrics || {}).forEach(metricName => kpis.add(metricName));
    });
    return orderOfflinePodKpis(kpis);
}

function renderOfflinePodSummaryTable(pods) {
    const tbody = document.querySelector('#pod-table tbody');
    if (!tbody) return;

    const kpis = collectOfflinePodKpis(pods);
    setPodTableHeadings(['Pod', '服务', ...kpis.map(formatOfflineKpiHeading)]);

    if (!pods.length) {
        tbody.innerHTML = `<tr><td colspan="${Math.max(2 + kpis.length, 2)}" class="text-muted" style="text-align:center">暂无离线 Pod 数据</td></tr>`;
        return;
    }

    const sortValue = pod => {
        if (Number.isFinite(pod.cpu) && pod.cpu > 0) return pod.cpu;
        if (Number.isFinite(pod.memRatio) && pod.memRatio > 0) return pod.memRatio;
        for (const kpi of kpis) {
            const value = pod.metrics?.[kpi];
            if (Number.isFinite(value)) return value;
        }
        return 0;
    };

    tbody.innerHTML = [...pods].sort((a, b) => sortValue(b) - sortValue(a)).map(pod => `
        <tr>
            <td><span class="pod-summary-sticky-label" title="${escapeHtml(pod.pod)}">${escapeHtml(pod.pod)}</span></td>
            <td><span class="pod-summary-sticky-label" title="${escapeHtml(pod.service)}">${escapeHtml(pod.service)}</span></td>
            ${kpis.map(metricName => {
                const rawValue = pod.metrics?.[metricName];
                const formatted = formatOfflineKpiValue(metricName, rawValue);
                return `<td class="metric-cell">${formatted === '-' ? '<span class="text-muted">-</span>' : escapeHtml(formatted)}</td>`;
            }).join('')}
        </tr>
    `).join('');
}

function getOfflineK8sPods(k8s) {
    const pods = [];
    for (const [service, podMap] of Object.entries(k8s || {})) {
        for (const [pod, metrics] of Object.entries(podMap || {})) {
            const metricSnapshot = {};
            for (const [metricName, metricSeries] of Object.entries(metrics || {})) {
                if (metricName === 'entity_id' || typeof metricSeries !== 'object' || metricSeries == null) {
                    continue;
                }
                if (metricSeries.current != null) {
                    metricSnapshot[metricName] = _safeNumber(metricSeries.current, 0);
                }
            }

            pods.push({
                service,
                pod,
                cpu: currentMetricValue(metrics, ['pod_cpu_usage_rate', 'pod_cpu_usage_rate_vs_limit', 'pod_cpu_usage_rate_vs_request']),
                memMB: currentMetricValue(metrics, ['pod_memory_working_set_bytes', 'pod_memory_usage_bytes']) / (1024 * 1024),
                memRatio: currentMetricValue(metrics, ['pod_memory_usage_vs_limit', 'pod_memory_usage_vs_request']),
                metrics: metricSnapshot,
            });
        }
    }
    return pods;
}

function getOfflineApmServices(apm) {
    return Object.entries(apm || {}).map(([service, metrics]) => ({
        service,
        requestCount: currentMetricValue(metrics, ['request_count']),
        errorCount: currentMetricValue(metrics, ['error_count']),
        latencyMs: currentMetricValue(metrics, ['avg_request_latency_seconds']) * 1000,
    }));
}

function buildPseudoResults(items, valueKey, metricBuilder) {
    return {
        results: (items || []).map(item => ({
            metric: metricBuilder(item),
            value: [Date.now() / 1000, String(_safeNumber(item[valueKey]))],
        })),
    };
}

function renderSimpleBarChart(canvasId, items, labelKey, valueKey, color = '#38bdf8') {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const entries = (items || []).slice(0, 6);
    if (!entries.length) {
        ctx.fillStyle = '#5b5f73';
        ctx.font = '13px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('暂无数据', canvas.width / 2, canvas.height / 2);
        return;
    }

    const barW = Math.min(42, (canvas.width - 40) / entries.length - 8);
    const maxH = canvas.height - 50;
    const maxVal = Math.max(...entries.map(item => _safeNumber(item[valueKey])), 1);

    entries.forEach((item, i) => {
        const value = _safeNumber(item[valueKey]);
        const x = 20 + i * (barW + 8);
        const h = (value / maxVal) * maxH;
        const y = canvas.height - 30 - h;

        ctx.fillStyle = color;
        ctx.fillRect(x, y, barW, h);

        ctx.fillStyle = '#8b8fa3';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(_truncate(item[labelKey], 10), x + barW / 2, canvas.height - 15);
        ctx.fillText(value.toFixed(0), x + barW / 2, y - 5);
    });
}

// ─────────────────────────────────────────
// Overview
// ─────────────────────────────────────────

async function loadOverview() {
    if (isOfflineMode()) {
        return loadOfflineOverview();
    }

    document.getElementById('overview-stat-label-1').textContent = '节点数';
    document.getElementById('overview-stat-label-2').textContent = 'Pod总数';
    document.getElementById('overview-stat-label-3').textContent = '命名空间';
    document.getElementById('overview-stat-label-4').textContent = '健康状态';

    const data = await api('/api/cluster/overview');

    if (data) {
        const phases = data.pod_phases || {};
        const unhealthyPods = (phases.Failed || 0) + (phases.Pending || 0) + (phases.Unknown || 0);
        document.getElementById('stat-nodes').textContent = data.nodes || 0;
        document.getElementById('stat-pods').textContent = data.pods_total || 0;
        document.getElementById('stat-ns').textContent = data.namespaces || 0;
        document.getElementById('stat-restarts').textContent = unhealthyPods > 0 ? `关注 ${unhealthyPods}` : '正常';
    }
}

async function loadOfflineOverview() {
    const [metricsData, logData, alertsData] = await Promise.all([
        api('/api/alidata/metrics'),
        api('/api/alidata/logs?time_range=1h&size=20'),
        api('/api/alerts/list'),
    ]);

    const k8s = metricsData?.k8s_metrics || {};
    const apm = metricsData?.apm_metrics || {};
    const pods = getOfflineK8sPods(k8s);
    const services = getOfflineApmServices(apm).sort((a, b) => b.requestCount - a.requestCount);
    const alerts = alertsData?.alerts || [];

    document.getElementById('overview-stat-label-1').textContent = '服务数';
    document.getElementById('overview-stat-label-2').textContent = 'Pod数';
    document.getElementById('overview-stat-label-3').textContent = '数据集';
    document.getElementById('overview-stat-label-4').textContent = '日志条数';

    document.getElementById('stat-nodes').textContent = services.length;
    document.getElementById('stat-pods').textContent = pods.length;
    document.getElementById('stat-ns').textContent = currentOfflineLabel();
    document.getElementById('stat-restarts').textContent = logData?.total_hits || logData?.returned || 0;
}

function renderPodChart(phases) {
    const canvas = document.getElementById('pod-chart');
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const colors = { Running: '#22c55e', Succeeded: '#38bdf8', Pending: '#f59e0b', Failed: '#ef4444', Unknown: '#5b5f73' };
    const entries = Object.entries(phases);
    const total = entries.reduce((s, [, v]) => s + v, 0);

    // Simple bar chart
    const barW = Math.min(60, (canvas.width - 40) / entries.length - 10);
    const maxH = canvas.height - 50;
    const maxVal = Math.max(...entries.map(([, v]) => v), 1);

    entries.forEach(([phase, count], i) => {
        const x = 20 + i * (barW + 10);
        const h = (count / maxVal) * maxH;
        const y = canvas.height - 30 - h;

        ctx.fillStyle = colors[phase] || '#5b5f73';
        ctx.fillRect(x, y, barW, h);

        ctx.fillStyle = '#8b8fa3';
        ctx.font = '10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(phase, x + barW / 2, canvas.height - 15);
        ctx.fillText(count, x + barW / 2, y - 5);
    });
}

// ─────────────────────────────────────────
// Metrics (Enhanced with Prometheus)
// ─────────────────────────────────────────

async function loadMetrics() {
    applyMetricsModeUI();
    if (isOfflineMode()) {
        return loadOfflineMetrics();
    }

    const ns = document.getElementById('metrics-ns')?.value || '';

    // Load namespace options (non-blocking)
    loadNamespaces('metrics-ns');

    // Fetch Prometheus metrics + pods in parallel
    const [metrics, podData] = await Promise.all([
        api(`/api/prometheus/metrics_summary?namespace=${ns}`),
        api(`/api/cluster/pods?namespace=${ns}`),
    ]);

    // Render node metrics
    if (metrics) {
        renderMetricBars('metrics-node-cpu', metrics.node_cpu, '%');
        renderMetricBars('metrics-node-memory', metrics.node_memory, '%');
        renderMetricBars('metrics-node-disk', metrics.node_disk, '%');
        renderContainerTop('metrics-cpu-top', metrics.container_cpu_top, '%');
        renderContainerTop('metrics-mem-top', metrics.container_mem_top, 'MB');
    }

    // Render pod table
    if (podData?.pods) {
        const tbody = document.querySelector('#pod-table tbody');
        tbody.innerHTML = podData.pods.map(p => `
            <tr>
                <td>${escapeHtml(p.name)}</td>
                <td>${escapeHtml(p.namespace)}</td>
                <td><span class="badge badge-${badgeClass(p.phase)}">${p.phase}</span></td>
                <td>${p.ready}</td>
                <td class="${p.restarts > 5 ? 'text-danger' : ''}">${p.restarts}</td>
                <td>${escapeHtml(p.node || '')}</td>
            </tr>
        `).join('');
    }
}

function applyMetricsModeUI() {
    const offline = isOfflineMode();
    document.getElementById('metrics-filter-row').style.display = offline ? 'none' : 'flex';
    document.getElementById('promql-card').style.display = offline ? 'none' : 'block';
    setPodTableLayoutMode(offline);

    document.getElementById('metrics-title-1').textContent = offline ? 'Pod CPU 使用率 Top10 (%)' : '节点 CPU 使用率 (%)';
    document.getElementById('metrics-title-2').textContent = offline ? 'Pod 内存使用 Top10 (MB)' : '节点内存使用率 (%)';
    document.getElementById('metrics-title-3').textContent = offline ? '服务请求量 Top10' : '节点磁盘使用率 (%)';
    document.getElementById('metrics-title-4').textContent = offline ? 'Pod CPU Top10 (%)' : '容器 CPU Top10 (cores %)';
    document.getElementById('metrics-title-5').textContent = offline ? '服务延迟 Top10 (ms)' : '容器内存 Top10 (MB)';
    document.getElementById('metrics-title-6').textContent = offline ? `离线 Pod 摘要 (${currentOfflineLabel()})` : 'Pod 状态统计';

    if (offline) {
        setPodTableHeadings(['Pod', '服务', 'KPI 加载中']);
    } else {
        setPodTableHeadings(['名称', '命名空间', '状态', 'Ready', '重启', '节点']);
    }
}

async function loadOfflineMetrics() {
    const data = await api('/api/alidata/metrics');
    const k8s = data?.k8s_metrics || {};
    const apm = data?.apm_metrics || {};
    const pods = getOfflineK8sPods(k8s);
    const services = getOfflineApmServices(apm);

    const topCpuPods = [...pods].sort((a, b) => b.cpu - a.cpu).slice(0, 10);
    const topMemPods = [...pods].sort((a, b) => b.memMB - a.memMB).slice(0, 10);
    const topReqSvcs = [...services].sort((a, b) => b.requestCount - a.requestCount).slice(0, 10);
    const topLatencySvcs = [...services].sort((a, b) => b.latencyMs - a.latencyMs).slice(0, 10);

    renderMetricBars(
        'metrics-node-cpu',
        buildPseudoResults(topCpuPods, 'cpu', item => ({ pod: item.pod, service: item.service })),
        '%'
    );
    renderMetricBars(
        'metrics-node-memory',
        buildPseudoResults(topMemPods, 'memMB', item => ({ pod: item.pod, service: item.service })),
        'MB'
    );
    renderMetricBars(
        'metrics-node-disk',
        buildPseudoResults(topReqSvcs, 'requestCount', item => ({ service: item.service })),
        'req'
    );

    renderContainerTop(
        'metrics-cpu-top',
        buildPseudoResults(topCpuPods, 'cpu', item => ({ pod: item.pod, namespace: item.service })),
        '%'
    );
    renderContainerTop(
        'metrics-mem-top',
        buildPseudoResults(topLatencySvcs, 'latencyMs', item => ({ service: item.service, namespace: currentOfflineLabel() })),
        'ms'
    );

    renderOfflinePodSummaryTable(pods);
}

function renderMetricBars(containerId, data, unit) {
    const el = document.getElementById(containerId);
    if (!el) return;

    const results = data?.results || [];
    if (!results.length) {
        el.innerHTML = '<p class="text-muted" style="padding:8px">暂无数据</p>';
        return;
    }

    const values = results.map(r => parseFloat(r.value?.[1] || 0));
    const maxVal = Math.max(...values, 1);

    el.innerHTML = results.map(r => {
        const label = r.metric?.label || r.metric?.pod || r.metric?.service || r.metric?.instance || r.metric?.node || Object.values(r.metric || {})[0] || 'unknown';
        const shortLabel = label.replace(/:.*$/, '');
        const val = parseFloat(r.value?.[1] || 0).toFixed(1);
        const pct = unit === '%' ? Math.min(parseFloat(val), 100) : (parseFloat(val) / maxVal * 100);
        const color = pct > 90 ? 'var(--danger)' : pct > 75 ? 'var(--warning)' : 'var(--accent)';
        return `
            <div style="margin-bottom:6px">
                <div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:2px">
                    <span>${escapeHtml(shortLabel)}</span>
                    <span style="font-weight:600">${val}${unit}</span>
                </div>
                <div style="background:var(--bg-secondary,#1e1e2e);border-radius:4px;height:16px;overflow:hidden">
                    <div style="width:${pct}%;height:100%;background:${color};border-radius:4px;transition:width 0.3s"></div>
                </div>
            </div>
        `;
    }).join('');
}

function renderContainerTop(tableId, data, unit) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;

    const results = data?.results || [];
    if (!results.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center">暂无数据</td></tr>';
        return;
    }

    tbody.innerHTML = results.map(r => {
        const pod = r.metric?.pod || r.metric?.service || 'unknown';
        const ns = r.metric?.namespace || '-';
        const val = parseFloat(r.value?.[1] || 0).toFixed(2);
        return `<tr><td>${escapeHtml(pod)}</td><td>${escapeHtml(ns)}</td><td>${val} ${unit}</td></tr>`;
    }).join('');
}

async function runPromQL() {
    const query = document.getElementById('promql-input')?.value?.trim();
    const queryType = document.getElementById('promql-type')?.value || 'instant';
    const resultEl = document.getElementById('promql-result');

    if (!query) {
        resultEl.textContent = '请输入 PromQL 查询';
        return;
    }

    resultEl.textContent = '查询中...';
    const data = await api(`/api/prometheus/query?query=${encodeURIComponent(query)}&query_type=${queryType}`);

    if (!data || data.error) {
        resultEl.textContent = `错误: ${data?.error || '请求失败'}`;
        return;
    }

    resultEl.textContent = JSON.stringify(data.results || data, null, 2);
}

// ─────────────────────────────────────────
// Logs
// ─────────────────────────────────────────

async function loadNamespaces(selectId) {
    if (isOfflineMode()) return;
    const data = await api('/api/cluster/namespaces');
    if (!data?.namespaces) return;

    const sel = document.getElementById(selectId);
    const current = sel.value;
    sel.innerHTML = '<option value="">选择命名空间</option>' +
        data.namespaces.map(ns => `<option value="${ns}" ${ns === current ? 'selected' : ''}>${ns}</option>`).join('');
}

async function loadPodsByNs() {
    if (isOfflineMode()) return;
    const ns = document.getElementById('log-ns').value;
    if (!ns) return;

    const data = await api(`/api/cluster/pods?namespace=${ns}`);
    if (!data?.pods) return;

    const sel = document.getElementById('log-pod');
    sel.innerHTML = '<option value="">选择Pod</option>' +
        data.pods.map(p => `<option value="${p.name}">${p.name} (${p.phase})</option>`).join('');
}

async function loadLogsView() {
    applyLogsModeUI();
    if (isOfflineMode()) {
        return fetchLogs();
    }
    return loadNamespaces('log-ns');
}

function applyLogsModeUI() {
    const offline = isOfflineMode();
    document.getElementById('log-ns').style.display = offline ? 'none' : '';
    document.getElementById('log-pod').style.display = offline ? 'none' : '';
    document.getElementById('offline-log-query').style.display = offline ? '' : 'none';
    document.getElementById('offline-log-level').style.display = offline ? '' : 'none';
    document.getElementById('offline-log-timerange').style.display = offline ? '' : 'none';

    const viewer = document.getElementById('log-content');
    if (offline && !viewer.textContent.trim()) {
        viewer.textContent = '输入关键词后点击查询，或直接查看当前离线数据日志...';
    }
}

async function fetchLogs() {
    if (isOfflineMode()) {
        const query = document.getElementById('offline-log-query').value.trim();
        const level = document.getElementById('offline-log-level').value || '';
        const timeRange = document.getElementById('offline-log-timerange').value || '1h';
        const lines = document.getElementById('log-lines').value || 200;
        const viewer = document.getElementById('log-content');
        viewer.textContent = '加载中...';

        let url = `/api/alidata/logs?time_range=${encodeURIComponent(timeRange)}&size=${encodeURIComponent(lines)}`;
        if (query) url += `&query=${encodeURIComponent(query)}`;
        if (level) url += `&level=${encodeURIComponent(level)}`;

        const data = await api(url);
        if (!data || data.error) {
            viewer.textContent = `错误: ${data?.error || '请求失败'}`;
            return;
        }

        const entries = data.entries || [];
        if (!entries.length) {
            viewer.textContent = '无日志内容';
            return;
        }

        viewer.textContent = entries.map(e => {
            const ts = e.timestamp ? formatTime(typeof e.timestamp === 'number' && e.timestamp < 2e10 ? e.timestamp * 1000 : e.timestamp) : '-';
            const levelTag = (e.level || 'info').toUpperCase();
            const source = e.service || e.pod || currentOfflineLabel();
            return `[${ts}] [${levelTag}] [${source}] ${e.message || ''}`;
        }).join('\n');
        return;
    }

    const ns = document.getElementById('log-ns').value;
    const pod = document.getElementById('log-pod').value;
    const lines = document.getElementById('log-lines').value || 200;

    if (!ns || !pod) { alert('请选择命名空间和Pod'); return; }

    const viewer = document.getElementById('log-content');
    viewer.textContent = '加载中...';
    const data = await api(`/api/logs/${ns}/${pod}?lines=${lines}`);
    viewer.textContent = data?.logs || '无日志内容';
}

// ─────────────────────────────────────────
// Alerts
// ─────────────────────────────────────────

let _alertData = [];  // cached alert data
let _filteredAlerts = [];

const SOURCE_CONFIG = {
    k8s_event:   { label: 'K8s事件',    icon: '📅', cls: 'source-k8s' },
    prometheus:  { label: 'Prometheus', icon: '📊', cls: 'source-prom' },
    pod_health:  { label: 'Pod健康',    icon: '🫛', cls: 'source-pod' },
    node_health: { label: '节点健康',   icon: '🖥️', cls: 'source-node' },
    metric_anomaly: { label: '指标异常', icon: '📈', cls: 'source-metric' },
};

async function loadAlertList() {
    const data = await api('/api/alerts/list');
    if (!data) return;
    _alertData = data.alerts || [];
    if (isOfflineMode()) {
        updateSourceFilterButtons({
            prometheus: false,
            k8s_event: false,
            pod_health: false,
            node_health: false,
            metric_anomaly: true,
        });
    }
    renderAlertSourceStats(_alertData);
    filterAlerts();  // apply current filters
}

function renderAlertSourceStats(alerts) {
    const stats = {};
    alerts.forEach(a => { stats[a.source] = (stats[a.source] || 0) + 1; });

    const container = document.getElementById('alert-source-stats');
    const sourceInfo = {
        k8s_event:   { label: 'K8s事件',    color: 'var(--info)' },
        prometheus:  { label: 'Prometheus', color: 'var(--accent)' },
        pod_health:  { label: 'Pod健康',    color: 'var(--danger)' },
        node_health: { label: '节点健康',   color: 'var(--warning)' },
        metric_anomaly: { label: '指标异常', color: 'var(--success)' },
    };

    container.innerHTML = Object.entries(stats).map(([src, count]) => {
        const info = sourceInfo[src] || { label: src, color: 'var(--text-muted)' };
        return `<div class="stat-card" style="border-left:3px solid ${info.color}">
            <div class="stat-label">${info.label}</div>
            <div class="stat-value">${count}</div>
        </div>`;
    }).join('') + `<div class="stat-card accent">
        <div class="stat-label">总告警</div>
        <div class="stat-value">${alerts.length}</div>
    </div>`;
}

function filterAlerts(source) {
    // Update source button highlight
    if (source) {
        document.querySelectorAll('.alert-source-btn').forEach(b => b.classList.remove('active'));
        document.querySelector(`.alert-source-btn[data-source="${source}"]`)?.classList.add('active');
    }

    const activeSource = document.querySelector('.alert-source-btn.active')?.dataset.source || 'all';
    const severityFilter = document.getElementById('alert-severity-filter').value;

    let filtered = _alertData;
    if (activeSource !== 'all') {
        filtered = filtered.filter(a => a.source === activeSource);
    }
    if (severityFilter !== 'all') {
        filtered = filtered.filter(a => a.severity === severityFilter);
    }

    renderAlertTable(filtered);
}

function renderAlertTable(alerts) {
    _filteredAlerts = alerts;
    const tbody = document.getElementById('alert-table-body');
    const empty = document.getElementById('alert-empty');

    if (!alerts.length) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    tbody.innerHTML = alerts.map((a, i) => {
        const src = SOURCE_CONFIG[a.source] || { label: a.source, icon: '❓', cls: '' };
        const sevClass = a.severity === 'critical' ? 'danger' : a.severity === 'warning' ? 'warning' : 'info';
        return `<tr>
            <td><span class="source-badge ${src.cls}">${src.icon} ${src.label}</span></td>
            <td><span class="badge badge-${sevClass}">${a.severity}</span></td>
            <td title="${escapeHtml(a.description || '')}">${escapeHtml(a.title || (a.description || '').substring(0, 80) || '')}</td>
            <td>${escapeHtml(a.service || '')}</td>
            <td>${escapeHtml(a.namespace || '')}</td>
            <td>${formatTime(a.timestamp ? a.timestamp * 1000 : null)}</td>
            <td><button class="btn btn-sm btn-primary" onclick="startRCAFromAlert(${i})">🔍 分析</button></td>
        </tr>`;
    }).join('');
}

async function runAlertScan() {
    const container = document.getElementById('alert-scan-result');
    container.innerHTML = '<div class="loading">扫描中</div>';

    const data = await api('/api/alerts/scan');
    if (!data) { container.innerHTML = '<p class="text-danger">扫描失败</p>'; return; }

    let html = `<div style="margin-bottom:12px">
        <span class="badge badge-info">总告警: ${data.total_alerts || 0}</span>
        <span class="badge badge-success">分组数: ${data.compressed_groups || data.num_groups || 0}</span>
        <span class="badge badge-warning">压缩率: ${((data.compression_ratio || 0) * 100).toFixed(0)}%</span>
    </div>`;

    (data.groups || []).forEach((g, gi) => {
        const severity = (g.severity || '').toLowerCase();
        // Find raw alerts belonging to this group by matching indices
        const rawAlerts = data.raw_alerts || [];
        const groupAlerts = (g.alert_indices || []).map(i => rawAlerts[i]).filter(Boolean);

        html += `
            <div class="alert-group ${severity === 'critical' ? 'critical' : ''}">
                <div class="group-title">${escapeHtml(g.group_label || g.representative || g.common_pattern || '告警组 ' + (gi+1))}</div>
                <div class="group-meta">${g.alert_count || 0} 条告警 · ${escapeHtml(g.severity || 'unknown')}</div>
                ${g.root_cause || g.root_cause_recommendation ? `<div class="group-rca">💡 ${escapeHtml(g.root_cause || g.root_cause_recommendation)}</div>` : ''}
                ${groupAlerts.length ? `<details style="margin-top:8px;font-size:12px">
                    <summary style="cursor:pointer;color:var(--text-muted)">查看组内告警详情</summary>
                    <div style="margin-top:6px">
                    ${groupAlerts.map(a => {
                        const src = SOURCE_CONFIG[a.source] || { label: a.source, icon: '❓', cls: '' };
                        return `<div class="signal-item"><span><span class="source-badge ${src.cls}">${src.icon} ${src.label}</span> ${escapeHtml(a.name || '')} — ${escapeHtml((a.message || '').substring(0, 120))}</span></div>`;
                    }).join('')}
                    </div>
                </details>` : ''}
            </div>
        `;
    });

    container.innerHTML = html;
}

function toggleDetectionSSE() {
    if (state.detectionSSE) {
        state.detectionSSE.close();
        state.detectionSSE = null;
        return;
    }

    const feed = document.getElementById('detection-feed');
    state.detectionSSE = new EventSource('/api/detection/stream');
    state.detectionSSE.onmessage = (e) => {
        try {
            const signal = JSON.parse(e.data);
            const item = document.createElement('div');
            item.className = 'signal-item';
            item.innerHTML = `
                <span><span class="badge badge-${signal.severity === 'critical' ? 'danger' : 'warning'}">${signal.severity || 'info'}</span> ${escapeHtml(signal.description || signal.msg || JSON.stringify(signal).substring(0, 100))}</span>
                <span class="text-muted">${formatTime(signal.timestamp)}</span>
            `;
            feed.prepend(item);
        } catch {}
    };
}

async function clearSignals() {
    await api('/api/detection/signals', { method: 'DELETE' });
    document.getElementById('detection-feed').innerHTML = '';
}

// ─────────────────────────────────────────
// Detection Config Management
// ─────────────────────────────────────────

let _detectionConfig = null;

async function loadDetectionConfig() {
    const data = await api('/api/detection/config');
    if (!data) return;
    _detectionConfig = data;
    renderSourceToggles(data);
    renderCategoryToggles(data);
    renderServiceTags('business-services-tags', data.business_services || [], 'business_services');
    renderServiceTags('db-services-tags', data.db_services || [], 'db_services');
    renderMetricChecksTable(data.metric_checks || []);
    renderCriticalReasons('critical-event-reasons', data.critical_event_reasons || []);
    renderCriticalReasons('critical-pod-reasons', data.critical_pod_reasons || []);
    updateSourceFilterButtons(data.sources_enabled || {});
    // Populate global algorithm params
    const lbEl = document.getElementById('cfg-lookback-m');
    const ztEl = document.getElementById('cfg-z-threshold');
    const esEl = document.getElementById('cfg-ewma-span');
    if (lbEl) lbEl.value = data.default_lookback_m || 30;
    if (ztEl) ztEl.value = data.default_z_threshold || 3.0;
    if (esEl) esEl.value = data.default_ewma_span || 10;
}

function renderSourceToggles(config) {
    const container = document.getElementById('source-toggles');
    const sources = config.sources_enabled || {};
    const labels = {
        prometheus: 'Prometheus',
        k8s_event: 'K8s事件',
        pod_health: 'Pod健康',
        node_health: '节点健康',
        metric_anomaly: '指标异常',
    };

    container.innerHTML = Object.entries(sources).map(([key, enabled]) => `
        <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:13px;cursor:pointer">
            <input type="checkbox" data-source="${escapeHtml(key)}" ${enabled ? 'checked' : ''}
                   onchange="toggleSource('${escapeHtml(key)}', this.checked)">
            ${labels[key] || key}
        </label>
    `).join('');
}

function toggleSource(key, enabled) {
    if (_detectionConfig && _detectionConfig.sources_enabled) {
        _detectionConfig.sources_enabled[key] = enabled;
    }
}

function renderCategoryToggles(config) {
    const container = document.getElementById('category-toggles');
    if (!container) return;
    const cats = config.categories_enabled || {};
    const labels = {
        infrastructure: '基础设施 (Infrastructure)',
        application: '应用 (Application)',
        business: '业务工作负载 (Business)',
        database: '数据库 (Database)',
        k8s_workload: 'K8s工作负载健康',
    };

    container.innerHTML = Object.entries(cats).map(([key, enabled]) => `
        <label style="display:inline-flex;align-items:center;gap:4px;margin-right:12px;font-size:13px;cursor:pointer">
            <input type="checkbox" data-category="${escapeHtml(key)}" ${enabled ? 'checked' : ''}
                   onchange="toggleCategory('${escapeHtml(key)}', this.checked)">
            ${labels[key] || key}
        </label>
    `).join('');
}

function toggleCategory(key, enabled) {
    if (_detectionConfig && _detectionConfig.categories_enabled) {
        _detectionConfig.categories_enabled[key] = enabled;
    }
}

function renderServiceTags(containerId, services, configKey) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = services.map((s, i) => `
        <span class="badge badge-info" style="margin:2px;font-size:12px">
            ${escapeHtml(s)}
            <span style="cursor:pointer;margin-left:4px" onclick="removeServiceTag('${containerId}', '${configKey}', ${i})">&times;</span>
        </span>
    `).join('') + `
        <button class="btn btn-sm" onclick="addServiceTag('${containerId}', '${configKey}')" style="font-size:11px">+ 添加</button>
    `;
}

function addServiceTag(containerId, configKey) {
    const name = prompt('输入服务名称:');
    if (!name) return;
    if (_detectionConfig) {
        _detectionConfig[configKey] = _detectionConfig[configKey] || [];
        _detectionConfig[configKey].push(name.trim());
        renderServiceTags(containerId, _detectionConfig[configKey], configKey);
    }
}

function removeServiceTag(containerId, configKey, index) {
    if (_detectionConfig && _detectionConfig[configKey]) {
        _detectionConfig[configKey].splice(index, 1);
        renderServiceTags(containerId, _detectionConfig[configKey], configKey);
    }
}

function renderMetricChecksTable(checks) {
    const tbody = document.getElementById('metric-checks-body');
    if (!checks.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-muted" style="text-align:center">暂无指标配置</td></tr>';
        return;
    }

    const allMethods = ['threshold', 'zscore', 'ewma', 'spectral_residual', 'pearson_onset', 'rate_change'];
    const defaultMethods = (_detectionConfig && _detectionConfig.default_detect_methods) || ['threshold', 'zscore'];

    tbody.innerHTML = checks.map((c, i) => {
        const methods = c.detect_methods || defaultMethods;
        const methodCheckboxes = allMethods.map(m => {
            const checked = methods.includes(m) ? 'checked' : '';
            return `<label style="display:inline-flex;align-items:center;gap:2px;font-size:11px;white-space:nowrap">
                <input type="checkbox" class="mc-method" data-idx="${i}" data-method="${m}" ${checked}> ${m}
            </label>`;
        }).join(' ');

        return `<tr>
            <td><input class="input-sm mc-name" value="${escapeHtml(c.name || '')}" style="width:120px" data-idx="${i}"></td>
            <td><select class="select-sm mc-level" data-idx="${i}">
                <option value="node" ${c.level === 'node' ? 'selected' : ''}>node</option>
                <option value="container" ${c.level === 'container' ? 'selected' : ''}>container</option>
            </select></td>
            <td><input type="number" class="input-sm mc-warn" value="${c.warn}" style="width:60px" data-idx="${i}"></td>
            <td><input type="number" class="input-sm mc-crit" value="${c.crit}" style="width:60px" data-idx="${i}"></td>
            <td>${escapeHtml(c.unit || '%')}</td>
            <td style="max-width:280px">${methodCheckboxes}</td>
            <td>
                <button class="btn btn-sm" onclick="editMetricCheck(${i})" title="编辑PromQL">✏️</button>
                <button class="btn btn-sm btn-danger" onclick="removeMetricCheck(${i})">🗑️</button>
            </td>
        </tr>`;
    }).join('');
}

function renderCriticalReasons(containerId, reasons) {
    const container = document.getElementById(containerId);
    container.innerHTML = reasons.map((r, i) => `
        <span class="badge badge-danger" style="margin:2px;font-size:12px">
            ${escapeHtml(r)}
            <span style="cursor:pointer;margin-left:4px" onclick="removeCriticalReason('${containerId}', ${i})">&times;</span>
        </span>
    `).join('') + `
        <button class="btn btn-sm" onclick="addCriticalReason('${containerId}')" style="font-size:11px">+ 添加</button>
    `;
}

function addCriticalReason(containerId) {
    const reason = prompt('输入原因名称:');
    if (!reason) return;
    const key = containerId === 'critical-event-reasons' ? 'critical_event_reasons' : 'critical_pod_reasons';
    if (_detectionConfig) {
        _detectionConfig[key] = _detectionConfig[key] || [];
        _detectionConfig[key].push(reason.trim());
        renderCriticalReasons(containerId, _detectionConfig[key]);
    }
}

function removeCriticalReason(containerId, index) {
    const key = containerId === 'critical-event-reasons' ? 'critical_event_reasons' : 'critical_pod_reasons';
    if (_detectionConfig && _detectionConfig[key]) {
        _detectionConfig[key].splice(index, 1);
        renderCriticalReasons(containerId, _detectionConfig[key]);
    }
}

function addMetricCheck() {
    const defaultMethods = (_detectionConfig && _detectionConfig.default_detect_methods) || ['threshold', 'zscore'];
    const newCheck = {
        name: 'new_metric',
        query: '',
        unit: '%',
        label_key: 'instance',
        ns_key: '',
        level: 'node',
        warn: 85,
        crit: 95,
        detect_methods: [...defaultMethods],
    };
    if (_detectionConfig) {
        _detectionConfig.metric_checks = _detectionConfig.metric_checks || [];
        _detectionConfig.metric_checks.push(newCheck);
        renderMetricChecksTable(_detectionConfig.metric_checks);
        // Auto-open edit for the new metric
        editMetricCheck(_detectionConfig.metric_checks.length - 1);
    }
}

function editMetricCheck(index) {
    if (!_detectionConfig || !_detectionConfig.metric_checks) return;
    const check = _detectionConfig.metric_checks[index];
    if (!check) return;

    const query = prompt('PromQL 查询表达式:', check.query || '');
    if (query === null) return;
    check.query = query;

    const labelKey = prompt('标签键 (label_key):', check.label_key || 'instance');
    if (labelKey !== null) check.label_key = labelKey;

    const nsKey = prompt('命名空间键 (ns_key, 可留空):', check.ns_key || '');
    if (nsKey !== null) check.ns_key = nsKey;

    renderMetricChecksTable(_detectionConfig.metric_checks);
}

function removeMetricCheck(index) {
    if (!_detectionConfig || !_detectionConfig.metric_checks) return;
    _detectionConfig.metric_checks.splice(index, 1);
    renderMetricChecksTable(_detectionConfig.metric_checks);
}

function _collectMetricChecksFromUI() {
    if (!_detectionConfig || !_detectionConfig.metric_checks) return;
    const checks = _detectionConfig.metric_checks;
    document.querySelectorAll('.mc-name').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (checks[idx]) checks[idx].name = el.value;
    });
    document.querySelectorAll('.mc-level').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (checks[idx]) checks[idx].level = el.value;
    });
    document.querySelectorAll('.mc-warn').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (checks[idx]) checks[idx].warn = parseFloat(el.value) || 0;
    });
    document.querySelectorAll('.mc-crit').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (checks[idx]) checks[idx].crit = parseFloat(el.value) || 0;
    });
    // Collect detect_methods per metric
    const methodsByIdx = {};
    document.querySelectorAll('.mc-method').forEach(el => {
        const idx = parseInt(el.dataset.idx);
        if (!methodsByIdx[idx]) methodsByIdx[idx] = [];
        if (el.checked) methodsByIdx[idx].push(el.dataset.method);
    });
    for (const [idx, methods] of Object.entries(methodsByIdx)) {
        const i = parseInt(idx);
        if (checks[i]) checks[i].detect_methods = methods;
    }
}

async function saveDetectionConfig() {
    if (!_detectionConfig) return;
    _collectMetricChecksFromUI();

    // Collect global algorithm params from UI
    const lbEl = document.getElementById('cfg-lookback-m');
    const ztEl = document.getElementById('cfg-z-threshold');
    const esEl = document.getElementById('cfg-ewma-span');
    const lookbackM = lbEl ? parseInt(lbEl.value) || 30 : 30;
    const zThreshold = ztEl ? parseFloat(ztEl.value) || 3.0 : 3.0;
    const ewmaSpan = esEl ? parseInt(esEl.value) || 10 : 10;

    const payload = {
        sources_enabled: _detectionConfig.sources_enabled,
        metric_checks: _detectionConfig.metric_checks,
        critical_event_reasons: _detectionConfig.critical_event_reasons,
        critical_pod_reasons: _detectionConfig.critical_pod_reasons,
        default_lookback_m: lookbackM,
        default_z_threshold: zThreshold,
        default_ewma_span: ewmaSpan,
        categories_enabled: _detectionConfig.categories_enabled || {},
        business_services: _detectionConfig.business_services || [],
        db_services: _detectionConfig.db_services || [],
    };

    const statusEl = document.getElementById('detection-save-status');
    statusEl.textContent = '保存中...';

    const result = await api('/api/detection/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });

    if (result && result.status === 'ok') {
        statusEl.textContent = '✅ 已保存';
        // Update source filter buttons
        updateSourceFilterButtons(_detectionConfig.sources_enabled);
        // Reload alert list to reflect changes
        loadAlertList();
    } else {
        statusEl.textContent = '❌ 保存失败';
    }
    setTimeout(() => { statusEl.textContent = ''; }, 3000);
}

function updateSourceFilterButtons(sourcesEnabled) {
    const filterContainer = document.getElementById('alert-source-filters');
    if (!filterContainer) return;
    // Show/hide source filter buttons based on enabled state
    filterContainer.querySelectorAll('.alert-source-btn[data-source]').forEach(btn => {
        const src = btn.dataset.source;
        if (src === 'all') return;
        btn.style.display = (sourcesEnabled[src] !== false) ? '' : 'none';
    });
}

function startRCAFromAlert(index) {
    const a = _filteredAlerts[index];
    if (!a) return;
    const query = `[${(a.severity || 'warning').toUpperCase()}] ${a.title || ''} — ${a.description || ''} (service=${a.service || ''}, namespace=${a.namespace || ''})`;
    switchView('rca');
    document.getElementById('rca-query').value = query;
    document.getElementById('rca-ns').value = a.namespace || '';
    startRCA();
}

// ─────────────────────────────────────────
// RCA
// ─────────────────────────────────────────

async function startRCA() {
    const query = document.getElementById('rca-query').value.trim();
    const ns = document.getElementById('rca-ns').value.trim();
    if (!query) { alert('请描述故障现象'); return; }

    const data = await api('/api/rca/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, namespace: ns }),
    });

    if (!data?.run_id) { alert('启动失败'); return; }

    state.rcaRunId = data.run_id;

    // Show progress card, hide result card
    const progress = document.getElementById('rca-progress');
    progress.style.display = 'block';
    const resultCard = document.getElementById('rca-result-card');
    resultCard.style.display = 'none';

    // Reset UI elements
    const logEl = document.getElementById('rca-log');
    logEl.textContent = '';
    document.getElementById('rca-phases').innerHTML = '';
    document.getElementById('rca-hyp-list').innerHTML = '';
    document.getElementById('rca-hypotheses').style.display = 'none';
    document.getElementById('rca-evidence-grid').innerHTML = '';
    document.getElementById('rca-evidence').style.display = 'none';
    document.getElementById('rca-iteration').style.display = 'none';
    document.getElementById('rca-result-content').innerHTML = '';

    // SSE stream
    const sse = new EventSource(`/api/rca/${data.run_id}/stream`);
    sse.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'log') {
                logEl.textContent += msg.msg + '\n';
                logEl.scrollTop = logEl.scrollHeight;
            } else if (msg.type === 'event') {
                handleRCAEvent(msg.data);
            } else if (msg.type === 'done') {
                sse.close();
                renderRCAFinalResult(msg.result);
                loadRCAHistory();
            }
        } catch {}
    };
    sse.onerror = () => { sse.close(); };
}

// ── RCA Event Dispatcher ──

function handleRCAEvent(evt) {
    switch (evt.event) {
        case 'phase_start':
            updatePhase(evt.phase, evt.name, 'active');
            break;
        case 'phase_complete':
            updatePhase(evt.phase, evt.name, 'done');
            break;
        case 'hypotheses':
            renderHypotheses(evt.items);
            break;
        case 'evidence':
            addEvidenceCard(evt.agent, evt.summary, evt.success);
            break;
        case 'iteration':
            updateIteration(evt.current, evt.total);
            break;
        case 'result':
            // Handled by 'done' SSE message
            break;
        case 'judge':
            renderJudge(evt.data);
            break;
        case 'remediation':
            renderRemediation(evt.data);
            break;
        case 'remediation_executed':
            renderRemediationResult(evt.data);
            break;
    }
}

// ── Phase Progress ──

const PHASE_NAMES = {
    0: '告警压缩',
    1: '上下文检索',
    2: '假设生成',
    3: '证据调查',
    4: '交叉关联',
    5: '图分析',
    6: '报告生成',
    7: '质量评估',
    8: '自动学习',
    9: '自愈修复',
};

function updatePhase(num, name, status) {
    const container = document.getElementById('rca-phases');
    let badge = document.getElementById(`rca-phase-${num}`);
    if (!badge) {
        badge = document.createElement('span');
        badge.id = `rca-phase-${num}`;
        badge.className = 'phase-badge';
        badge.textContent = PHASE_NAMES[num] || name;
        container.appendChild(badge);
    }
    badge.className = `phase-badge ${status}`;
}

// ── Iteration Indicator ──

function updateIteration(current, total) {
    const el = document.getElementById('rca-iteration');
    el.style.display = 'block';
    el.innerHTML = `<span class="iteration-badge">迭代 ${current} / ${total}</span>`;
}

// ── Hypothesis Rendering ──

function renderHypotheses(items) {
    const wrapper = document.getElementById('rca-hypotheses');
    wrapper.style.display = 'block';
    const list = document.getElementById('rca-hyp-list');

    list.innerHTML = items.map((h, i) => {
        const pct = Math.round(h.confidence * 100);
        return `<div class="hyp-item">
            <span class="hyp-rank">#${i + 1}</span>
            <div class="hyp-bar-wrap"><div class="hyp-bar" style="width:${pct}%"></div></div>
            <span class="hyp-conf">${pct}%</span>
            <span class="hyp-desc" title="${escapeHtml(h.description)}">${escapeHtml(h.description)}</span>
        </div>`;
    }).join('');
}

// ── Evidence Cards ──

function addEvidenceCard(agent, summary, success) {
    const wrapper = document.getElementById('rca-evidence');
    wrapper.style.display = 'block';
    const grid = document.getElementById('rca-evidence-grid');

    const agentLabels = {
        metric_agent: '📈 Metric Agent',
        log_agent: '📋 Log Agent',
        trace_agent: '🔗 Trace Agent',
        event_agent: '📅 Event Agent',
    };

    const card = document.createElement('div');
    card.className = `evidence-card ${success ? 'success' : 'error'}`;
    card.innerHTML = `
        <div class="ev-agent">${success ? '✅' : '⚠️'} ${agentLabels[agent] || agent}</div>
        <div class="ev-summary">${escapeHtml(summary || (success ? '分析完成' : '分析失败'))}</div>
    `;
    grid.appendChild(card);
}

// ── Judge Rendering ──

function renderJudge(data) {
    if (!data) return;
    const resultContent = document.getElementById('rca-result-content');
    const level = (data.judge_level || '').toLowerCase();
    const cls = level === 'gold' ? 'gold' : level === 'silver' ? 'silver' : 'bronze';
    const label = level === 'gold' ? '🥇 Gold' : level === 'silver' ? '🥈 Silver' : '🥉 Bronze';

    // Append judge info (will appear after result renders)
    const judgeEl = document.createElement('div');
    judgeEl.id = 'rca-judge-info';
    judgeEl.style.marginTop = '12px';
    judgeEl.innerHTML = `
        <span class="judge-badge ${cls}">${label} — 评分 ${(data.combined_score || data.score || 0).toFixed(3)}</span>
        ${data.needs_review ? '<span class="badge badge-warning" style="margin-left:8px">需要人工复核</span>' : ''}
    `;
    // Store for later insertion after result renders
    state._pendingJudge = judgeEl;
}

// ── Final Result Rendering ──

function renderRCAFinalResult(result) {
    const card = document.getElementById('rca-result-card');
    const content = document.getElementById('rca-result-content');
    card.style.display = 'block';

    if (!result) {
        content.innerHTML = '<p class="text-danger">未获取到结果</p>';
        return;
    }

    const status = result.status || 'unknown';
    // Unwrap nested: PipelineResult.result → rca_engine.result → LLM final_result
    const inner = result.result || result;
    const rca = (inner.result && typeof inner.result === 'object' && !Array.isArray(inner.result))
        ? inner.result : inner;
    const rootCause = rca.root_cause || rca.error || 'N/A';
    const conf = rca.confidence || 0;
    const confPct = Math.round(conf * 100);
    const confClass = conf >= 0.7 ? 'high' : conf >= 0.4 ? 'medium' : 'low';

    let html = `<div class="rca-result-structured">`;

    // Status header
    html += `<div class="result-banner ${status === 'completed' ? 'success' : 'failed'}">
        <h4>${status === 'completed' ? '✅ 根因分析完成' : '❌ 分析失败'}</h4>
    </div>`;

    // Root cause
    html += `<div class="rca-root-cause">${escapeHtml(rootCause)}</div>`;

    // Confidence bar
    html += `<div class="rca-conf-row">
        <span style="font-size:12px;color:var(--text-muted)">置信度</span>
        <div class="rca-conf-bar"><div class="rca-conf-fill ${confClass}" style="width:${confPct}%"></div></div>
        <span class="rca-conf-label">${confPct}%</span>
    </div>`;

    // Meta grid
    html += `<div class="rca-meta-grid">`;
    if (rca.fault_type) {
        html += `<div class="rca-meta-item"><div class="meta-label">故障类型</div><div class="meta-value">${escapeHtml(rca.fault_type)}</div></div>`;
    }
    if (rca.affected_services?.length) {
        html += `<div class="rca-meta-item"><div class="meta-label">受影响服务</div><div class="meta-value">${rca.affected_services.map(s => escapeHtml(s)).join(', ')}</div></div>`;
    }
    if (rca.evidence_summary) {
        const es = rca.evidence_summary;
        for (const [key, val] of Object.entries(es)) {
            if (val) {
                html += `<div class="rca-meta-item"><div class="meta-label">${escapeHtml(key)}</div><div class="meta-value">${escapeHtml(String(val).substring(0, 200))}</div></div>`;
            }
        }
    }
    html += `</div>`;

    // Timeline
    if (rca.timeline?.length) {
        html += `<div><h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">事件时间线</h4><div class="rca-timeline">`;
        rca.timeline.forEach(t => {
            html += `<div class="rca-timeline-item">
                <div class="tl-time">${escapeHtml(t.time || '')}</div>
                <div class="tl-event">${escapeHtml(t.event || '')}</div>
            </div>`;
        });
        html += `</div></div>`;
    }

    // Remediation
    if (rca.remediation_suggestion) {
        html += `<div class="rca-remediation">💡 <strong>修复建议：</strong>${escapeHtml(rca.remediation_suggestion)}</div>`;
    }
    if (rca.prevention) {
        html += `<div class="rca-remediation" style="margin-top:8px">🛡️ <strong>预防措施：</strong>${escapeHtml(rca.prevention)}</div>`;
    }

    html += `</div>`;
    content.innerHTML = html;

    // Append judge info if available
    if (state._pendingJudge) {
        content.appendChild(state._pendingJudge);
        state._pendingJudge = null;
    }

    // Append pending remediation if available
    if (state._pendingRemediation) {
        content.appendChild(state._pendingRemediation);
        state._pendingRemediation = null;
    }
}

// ── Remediation Rendering ──

function renderRemediation(data) {
    if (!data) return;

    const status = data.status || 'unknown';
    const el = document.createElement('div');
    el.id = 'rca-remediation-section';
    el.className = 'rca-remediation-section';

    if (status === 'pending_approval') {
        const plan = data.plan || {};
        const actions = plan.actions || [];

        let actionsHtml = actions.map((a, i) => {
            const risk = (a.risk_level || 'low').toLowerCase();
            const riskClass = risk === 'high' ? 'danger' : risk === 'medium' ? 'warning' : 'success';
            return `<div class="rem-action-item">
                <div class="rem-action-header">
                    <span class="rem-action-num">${i + 1}</span>
                    <span class="badge badge-${riskClass}">${a.risk_level || 'low'}</span>
                    <span class="rem-action-desc">${escapeHtml(a.description || '')}</span>
                </div>
                <div class="rem-action-cmd"><code>${escapeHtml(a.command || '')}</code></div>
                ${a.rollback_command ? `<div class="rem-action-rollback">↩️ ${escapeHtml(a.rollback_command)}</div>` : ''}
            </div>`;
        }).join('');

        el.innerHTML = `
            <h4>🛠️ 自愈修复方案</h4>
            <div class="rem-status-badge pending">等待审批</div>
            ${plan.estimated_recovery_time ? `<div class="rem-meta">预计恢复时间: ${escapeHtml(plan.estimated_recovery_time)}</div>` : ''}
            <div class="rem-actions-list">${actionsHtml}</div>
            <div class="rem-buttons">
                <button class="btn btn-primary" onclick="approveRemediation()">✅ 批准执行</button>
                <button class="btn btn-secondary" onclick="dismissRemediation()">❌ 拒绝</button>
            </div>
        `;
    } else if (status === 'disabled') {
        el.innerHTML = `<div class="rem-status-badge disabled">自愈已禁用</div>`;
    } else if (status === 'skipped') {
        el.innerHTML = `<div class="rem-status-badge skipped">置信度不足，跳过自愈</div>`;
    } else if (status === 'executed') {
        renderRemediationResult(data);
        return;
    }

    // Store for appending to result card
    state._pendingRemediation = el;

    // Also try to append immediately if result card is visible
    const content = document.getElementById('rca-result-content');
    if (content && content.innerHTML) {
        content.appendChild(el);
        state._pendingRemediation = null;
    }
}

function renderRemediationResult(data) {
    const section = document.getElementById('rca-remediation-section');
    const target = section || document.getElementById('rca-result-content');
    if (!target) return;

    const actions = data.actions || [];
    const verification = data.verification || {};

    let html = `<div class="rca-remediation-section">
        <h4>🛠️ 自愈执行结果</h4>
        <div class="rem-status-badge executed">已执行</div>`;

    actions.forEach((a, i) => {
        const ok = a.status === 'executed';
        html += `<div class="rem-result-item ${ok ? 'success' : 'failed'}">
            <span>${ok ? '✅' : '❌'} ${escapeHtml(a.description || `Action ${i+1}`)}</span>
            <span class="rem-result-detail">${escapeHtml((a.result || '').substring(0, 100))}</span>
        </div>`;
    });

    if (data.rollback_available) {
        html += `<div class="rem-buttons">
            <button class="btn btn-warning" onclick="rollbackRemediation()">↩️ 回滚</button>
        </div>`;
    }

    html += `</div>`;

    if (section) {
        section.outerHTML = html;
    } else {
        target.insertAdjacentHTML('beforeend', html);
    }
}

async function approveRemediation() {
    if (!state.rcaRunId) return;
    const btn = event.target;
    btn.disabled = true;
    btn.textContent = '⏳ 执行中...';

    try {
        const result = await api(`/api/rca/${state.rcaRunId}/remediation/approve`, {
            method: 'POST',
        });
        if (result) {
            renderRemediationResult(result);
        }
    } catch (e) {
        alert('执行失败: ' + e.message);
    }
    btn.disabled = false;
}

async function rollbackRemediation() {
    if (!state.rcaRunId) return;
    if (!confirm('确认回滚所有修复操作？')) return;

    try {
        const result = await api(`/api/rca/${state.rcaRunId}/remediation/rollback`, {
            method: 'POST',
        });
        if (result) {
            alert(`回滚完成: ${(result.actions || []).length} 个操作已撤销`);
        }
    } catch (e) {
        alert('回滚失败: ' + e.message);
    }
}

function dismissRemediation() {
    const section = document.getElementById('rca-remediation-section');
    if (section) section.remove();
}

async function loadRCAHistory() {
    const data = await api('/api/rca/history');
    if (!data?.runs) return;

    const container = document.getElementById('rca-history');
    if (!container) return;
    if (data.runs.length === 0) {
        container.innerHTML = '<p class="text-muted">暂无历史记录</p>';
        return;
    }

    container.innerHTML = data.runs.map(r => `
        <div class="signal-item">
            <span>
                <span class="badge badge-${r.status === 'completed' ? 'success' : r.status === 'running' ? 'warning' : 'danger'}">${r.status}</span>
                ${escapeHtml(r.query?.substring(0, 80) || '')}
            </span>
            <span class="text-muted">${formatTime(r.started_at ? r.started_at * 1000 : null)}</span>
        </div>
    `).join('');
}

// ─────────────────────────────────────────
// Daemon
// ─────────────────────────────────────────

async function loadDaemonStatus() {
    const data = await api('/api/daemon/status');
    if (!data) return;

    document.getElementById('daemon-status').innerHTML =
        data.running ? '<span class="text-success">运行中</span>' : '<span class="text-danger">已停止</span>';
    document.getElementById('daemon-uptime').textContent =
        data.uptime_s ? `${Math.floor(data.uptime_s / 60)}m ${Math.floor(data.uptime_s % 60)}s` : '-';
    document.getElementById('daemon-cycles').textContent = data.cycles ?? '-';
    document.getElementById('daemon-pipelines').textContent = data.active_pipelines ?? '-';
}

async function startDaemon() {
    await api('/api/daemon/start', { method: 'POST' });
    loadDaemonStatus();

    // Start log SSE
    if (state.daemonLogSSE) state.daemonLogSSE.close();
    const logEl = document.getElementById('daemon-log');
    logEl.textContent = '';

    state.daemonLogSSE = new EventSource('/api/daemon/logs/stream');
    state.daemonLogSSE.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'log') {
                logEl.textContent += msg.msg + '\n';
                logEl.scrollTop = logEl.scrollHeight;
            } else if (msg.type === 'status') {
                document.getElementById('daemon-cycles').textContent = msg.data?.cycles ?? '-';
                document.getElementById('daemon-pipelines').textContent = msg.data?.active_pipelines ?? '-';
            }
        } catch {}
    };
}

async function stopDaemon() {
    await api('/api/daemon/stop', { method: 'POST' });
    if (state.daemonLogSSE) { state.daemonLogSSE.close(); state.daemonLogSSE = null; }
    setTimeout(loadDaemonStatus, 1000);
}

// ─────────────────────────────────────────
// Traces (Jaeger)
// ─────────────────────────────────────────

async function loadTracesView() {
    applyTracesModeUI();
    if (isOfflineMode()) {
        const data = await api('/api/alidata/services');
        const sel = document.getElementById('trace-service');
        if (data?.services?.length) {
            const current = sel.value;
            sel.innerHTML = '<option value="">选择服务</option>' +
                data.services.filter(s => s).sort().map(s =>
                    `<option value="${s}" ${s === current ? 'selected' : ''}>${s}</option>`
                ).join('');
        } else {
            sel.innerHTML = `<option value="">离线服务不可用</option>`;
        }
        return;
    }

    // Load Jaeger services for the dropdown
    const data = await api('/api/jaeger/services');
    const sel = document.getElementById('trace-service');
    if (data?.services?.length) {
        const current = sel.value;
        sel.innerHTML = '<option value="">选择服务</option>' +
            data.services.filter(s => s).sort().map(s =>
                `<option value="${s}" ${s === current ? 'selected' : ''}>${s}</option>`
            ).join('');
    } else if (data?.error) {
        sel.innerHTML = `<option value="">Jaeger 连接失败</option>`;
        document.getElementById('trace-empty').style.display = 'block';
        document.getElementById('trace-empty').textContent = `Jaeger 连接失败: ${data.error}`;
    }
}

async function loadTraceOperations() {
    if (isOfflineMode()) {
        const sel = document.getElementById('trace-operation');
        sel.innerHTML = '<option value="">离线模式不区分操作</option>';
        return;
    }

    const service = document.getElementById('trace-service')?.value;
    const sel = document.getElementById('trace-operation');
    sel.innerHTML = '<option value="">所有操作</option>';
    if (!service) return;

    const data = await api(`/api/jaeger/operations?service=${encodeURIComponent(service)}`);
    if (data?.operations?.length) {
        sel.innerHTML += data.operations.map(op =>
            `<option value="${op}">${op}</option>`
        ).join('');
    }
}

function applyTracesModeUI() {
    const offline = isOfflineMode();
    const opSel = document.getElementById('trace-operation');
    opSel.disabled = offline;
}

async function searchTraces() {
    const service = document.getElementById('trace-service')?.value;
    if (!service) {
        alert('请先选择服务');
        return;
    }

    const operation = document.getElementById('trace-operation')?.value || '';
    const minDuration = document.getElementById('trace-min-duration')?.value || '';
    const maxDuration = document.getElementById('trace-max-duration')?.value || '';
    const lookback = document.getElementById('trace-lookback')?.value || '1h';
    const limit = document.getElementById('trace-limit')?.value || 20;

    let url = isOfflineMode()
        ? `/api/alidata/traces?service=${encodeURIComponent(service)}&lookback=${lookback}&limit=${limit}`
        : `/api/jaeger/traces?service=${encodeURIComponent(service)}&lookback=${lookback}&limit=${limit}`;
    if (operation) url += `&operation=${encodeURIComponent(operation)}`;
    if (minDuration) url += `&min_duration=${encodeURIComponent(minDuration)}`;
    if (maxDuration) url += `&max_duration=${encodeURIComponent(maxDuration)}`;

    const data = await api(url);
    renderTraceTable(data);
}

function renderTraceTable(data) {
    const tbody = document.getElementById('trace-table-body');
    const emptyEl = document.getElementById('trace-empty');
    const countEl = document.getElementById('trace-count');

    if (!data?.traces?.length) {
        tbody.innerHTML = '';
        emptyEl.style.display = 'block';
        emptyEl.textContent = data?.error ? `错误: ${data.error}` : '未找到 Trace';
        countEl.textContent = '';
        return;
    }

    emptyEl.style.display = 'none';
    countEl.textContent = `共 ${data.traces.length} 条`;

    if (isOfflineMode()) {
        tbody.innerHTML = data.traces.map(t => {
            const durationMs = (t.total_duration_us / 1000).toFixed(1);
            const shortId = t.traceID?.substring(0, 16) || '';
            const mainOp = (t.operations || [])[0] || '-';
            const services = (t.services || []).slice(0, 3).join(', ');
            const moreServices = t.services?.length > 3 ? ` +${t.services.length - 3}` : '';
            const errorRate = t.error_rate != null ? `${(t.error_rate * 100).toFixed(1)}%` : '-';
            return `
                <tr>
                    <td><code style="font-size:11px">${escapeHtml(shortId)}</code></td>
                    <td>${escapeHtml((t.services || [])[0] || '-')}</td>
                    <td>${escapeHtml(mainOp)}</td>
                    <td>${t.span_count}</td>
                    <td style="font-size:11px">${escapeHtml(services)}${moreServices}</td>
                    <td>${durationMs} ms</td>
                    <td style="font-size:11px">${errorRate}</td>
                    <td><button class="btn btn-sm" onclick="viewTraceDetail('${t.traceID}')">详情</button></td>
                </tr>
            `;
        }).join('');
        return;
    }

    tbody.innerHTML = data.traces.map(t => {
        const durationMs = (t.total_duration_us / 1000).toFixed(1);
        const startTime = t.start_time ? new Date(t.start_time / 1000).toLocaleString('zh-CN') : '-';
        const shortId = t.traceID?.substring(0, 16) || '';
        const services = (t.services || []).slice(0, 3).join(', ');
        const moreServices = t.services?.length > 3 ? ` +${t.services.length - 3}` : '';
        return `
            <tr>
                <td><code style="font-size:11px">${escapeHtml(shortId)}</code></td>
                <td>${escapeHtml(t.root_service || '-')}</td>
                <td>${escapeHtml(t.root_operation || '-')}</td>
                <td>${t.span_count}</td>
                <td style="font-size:11px">${escapeHtml(services)}${moreServices}</td>
                <td>${durationMs} ms</td>
                <td style="font-size:11px">${startTime}</td>
                <td><button class="btn btn-sm" onclick="viewTraceDetail('${t.traceID}')">详情</button></td>
            </tr>
        `;
    }).join('');
}

async function lookupTraceById() {
    const traceId = document.getElementById('trace-id-input')?.value?.trim();
    if (!traceId) {
        alert('请输入 Trace ID');
        return;
    }
    await viewTraceDetail(traceId);
}

async function viewTraceDetail(traceId) {
    if (isOfflineMode()) {
        return viewOfflineTraceDetail(traceId);
    }

    const card = document.getElementById('trace-detail-card');
    const content = document.getElementById('trace-detail-content');
    card.style.display = 'block';
    content.innerHTML = '<p class="text-muted">加载中...</p>';

    const data = await api(`/api/jaeger/trace/${traceId}`);
    if (!data || data.error) {
        content.innerHTML = `<p class="text-danger">加载失败: ${data?.error || '未知错误'}</p>`;
        return;
    }

    // Render trace timeline
    const spans = data.spans || [];
    if (!spans.length) {
        content.innerHTML = '<p class="text-muted">无 Span 数据</p>';
        return;
    }

    // Find time range
    const minStart = Math.min(...spans.map(s => s.startTime || Infinity));
    const maxEnd = Math.max(...spans.map(s => (s.startTime || 0) + (s.duration_us || 0)));
    const totalRange = maxEnd - minStart || 1;

    content.innerHTML = `
        <div style="margin-bottom:12px">
            <strong>Trace ID:</strong> <code>${escapeHtml(traceId)}</code>
            &nbsp; <strong>Span数:</strong> ${spans.length}
            &nbsp; <strong>总耗时:</strong> ${((maxEnd - minStart) / 1000).toFixed(1)} ms
        </div>
        <div class="trace-timeline">
            ${spans.map(s => {
                const left = ((s.startTime - minStart) / totalRange * 100).toFixed(2);
                const width = Math.max((s.duration_us / totalRange * 100), 0.5).toFixed(2);
                const dMs = (s.duration_us / 1000).toFixed(1);
                const hasError = s.tags?.['error'] === true || s.tags?.['otel.status_code'] === 'ERROR';
                const barColor = hasError ? 'var(--danger)' : 'var(--accent)';
                return `
                    <div class="trace-span-row" style="display:flex;align-items:center;gap:8px;margin-bottom:2px;font-size:11px">
                        <span style="min-width:120px;text-align:right;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                              title="${escapeHtml(s.serviceName)}">${escapeHtml(s.serviceName)}</span>
                        <span style="min-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                              title="${escapeHtml(s.operationName)}">${escapeHtml(s.operationName)}</span>
                        <div style="flex:1;position:relative;height:14px;background:var(--bg-secondary,#1e1e2e);border-radius:3px">
                            <div style="position:absolute;left:${left}%;width:${width}%;height:100%;background:${barColor};border-radius:3px;min-width:2px"
                                 title="${dMs} ms"></div>
                        </div>
                        <span style="min-width:60px;text-align:right">${dMs} ms</span>
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

async function viewOfflineTraceDetail(traceId) {
    const card = document.getElementById('trace-detail-card');
    const content = document.getElementById('trace-detail-content');
    card.style.display = 'block';
    content.innerHTML = '<p class="text-muted">加载中...</p>';

    const data = await api(`/api/alidata/trace/${traceId}`);
    if (!data || data.error) {
        content.innerHTML = `<p class="text-danger">加载失败: ${data?.error || '未知错误'}</p>`;
        return;
    }

    const traces = data.traces || [];
    if (!traces.length) {
        content.innerHTML = '<p class="text-muted">无 Trace 数据</p>';
        return;
    }

    const trace = traces[0];
    const services = trace.services || [];
    const operations = trace.operations || [];
    const endpoints = trace.endpoints || [];
    const errorSpans = trace.error_spans || [];
    const statusDist = trace.http_status_distribution || {};
    const durationMs = (trace.total_duration_us / 1000).toFixed(1);
    const errorRate = trace.error_rate != null ? `${(trace.error_rate * 100).toFixed(1)}%` : '-';

    let html = `
        <div style="margin-bottom:12px">
            <strong>Trace ID:</strong> <code>${escapeHtml(traceId)}</code>
            &nbsp; <strong>Span数:</strong> ${trace.span_count}
            &nbsp; <strong>总耗时:</strong> ${durationMs} ms
            &nbsp; <strong>错误率:</strong> <span class="${trace.error_rate > 0 ? 'text-danger' : ''}">${errorRate}</span>
        </div>`;

    html += `<div style="margin-bottom:8px">
        <strong>涉及服务:</strong> ${services.map(s => `<span class="badge badge-info" style="margin:2px">${escapeHtml(s)}</span>`).join(' ')}
    </div>`;

    if (operations.length) {
        html += `<div style="margin-bottom:8px">
            <strong>操作:</strong> ${operations.slice(0, 10).map(o => `<span class="badge badge-gray" style="margin:2px">${escapeHtml(o)}</span>`).join(' ')}
        </div>`;
    }

    if (Object.keys(statusDist).length) {
        html += `<div style="margin-bottom:8px">
            <strong>HTTP状态分布:</strong> ${Object.entries(statusDist).map(([code, cnt]) => {
                const cls = code.startsWith('2') ? 'success' : code.startsWith('4') ? 'warning' : code.startsWith('5') ? 'danger' : 'info';
                return `<span class="badge badge-${cls}" style="margin:2px">${code}: ${cnt}</span>`;
            }).join(' ')}
        </div>`;
    }

    if (errorSpans.length) {
        html += `<div style="margin-top:12px">
            <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">错误 Spans</h4>
            <table class="data-table">
                <thead><tr><th>服务</th><th>操作</th><th>状态码</th><th>耗时</th><th>URL</th></tr></thead>
                <tbody>${errorSpans.map(es => `
                    <tr>
                        <td>${escapeHtml(es.service || '')}</td>
                        <td>${escapeHtml(es.operation || '')}</td>
                        <td><span class="badge badge-danger">${es.status_code}</span></td>
                        <td>${es.duration_ms} ms</td>
                        <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                            title="${escapeHtml(es.url || '')}">${escapeHtml(es.url || '-')}</td>
                    </tr>
                `).join('')}</tbody>
            </table>
        </div>`;
    }

    if (endpoints.length) {
        html += `<details style="margin-top:12px;font-size:12px">
            <summary style="cursor:pointer;color:var(--text-muted)">查看请求端点 (${endpoints.length})</summary>
            <div style="margin-top:6px">
                ${endpoints.map(ep => `<div class="signal-item" style="font-size:11px">${escapeHtml(ep)}</div>`).join('')}
            </div>
        </details>`;
    }

    content.innerHTML = html;
}

// ─────────────────────────────────────────
// AliData (Alibaba Cloud Logs & Traces & Metrics)
// ─────────────────────────────────────────

let _alidataRefreshTimer = null;
const _alidataCharts = {};  // canvasId -> Chart instance

const CHART_COLORS = [
    '#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#38bdf8',
    '#a855f7', '#ec4899', '#14b8a6', '#f97316', '#8b5cf6',
    '#06b6d4', '#84cc16', '#e11d48', '#0ea5e9', '#d946ef',
];

async function loadAliDataView() {
    const [statusData, servicesData] = await Promise.all([
        api('/api/alidata/status'),
        api('/api/alidata/services'),
    ]);

    if (statusData) {
        document.getElementById('alidata-conn-status').innerHTML = statusData.connected
            ? '<span class="text-success">已连接</span>' : '<span class="text-danger">未连接</span>';
        document.getElementById('alidata-log-status').innerHTML = statusData.log_ok
            ? '<span class="text-success">正常</span>' : '<span class="text-danger">异常</span>';
        document.getElementById('alidata-trace-status').innerHTML = statusData.trace_ok
            ? '<span class="text-success">正常</span>' : '<span class="text-danger">异常</span>';
    }

    if (servicesData?.services?.length) {
        const services = servicesData.services.filter(s => s).sort();
        const traceSel = document.getElementById('alidata-trace-service');
        const traceCur = traceSel.value;
        traceSel.innerHTML = '<option value="">选择服务</option>' +
            services.map(s => `<option value="${s}" ${s === traceCur ? 'selected' : ''}>${s}</option>`).join('');
        const metricSel = document.getElementById('alidata-metric-service');
        if (metricSel) {
            const metricCur = metricSel.value;
            metricSel.innerHTML = '<option value="">所有服务</option>' +
                services.map(s => `<option value="${s}" ${s === metricCur ? 'selected' : ''}>${s}</option>`).join('');
        }
    }

    loadAliDataMetrics();
    setAliDataAutoRefresh();
}

function setAliDataAutoRefresh() {
    if (_alidataRefreshTimer) { clearInterval(_alidataRefreshTimer); _alidataRefreshTimer = null; }
    const interval = parseInt(document.getElementById('alidata-refresh-interval')?.value || '0');
    if (interval > 0) {
        _alidataRefreshTimer = setInterval(() => {
            if (state.currentView === 'alidata') loadAliDataMetrics();
        }, interval * 1000);
    }
}

async function loadAliDataMetrics() {
    const emptyEl = document.getElementById('alidata-metrics-empty');
    if (emptyEl) emptyEl.textContent = '加载中...';

    const data = await api('/api/alidata/metrics');
    if (!data || data.error) {
        if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = `错误: ${data?.error || '请求失败'}`; }
        return;
    }

    const filterSvc = document.getElementById('alidata-metric-service')?.value || '';
    const k8s = data.k8s_metrics || {};
    const apm = data.apm_metrics || {};

    // ── K8s CPU & Memory Charts ──
    const cpuDatasets = [];
    const memDatasets = [];
    let colorIdx = 0;

    for (const [svc, pods] of Object.entries(k8s)) {
        if (filterSvc && svc !== filterSvc) continue;
        for (const [pod, metrics] of Object.entries(pods)) {
            const cpuData = metrics['pod_cpu_usage_rate'];
            const memData = metrics['pod_memory_working_set_bytes'] || metrics['pod_memory_usage_bytes'];
            const shortPod = pod.length > 25 ? pod.substring(0, 23) + '..' : pod;
            const color = CHART_COLORS[colorIdx % CHART_COLORS.length];

            if (cpuData?.values?.length) {
                cpuDatasets.push({
                    label: shortPod,
                    data: cpuData.values.map(v => ({ x: v[0] * 1000, y: parseFloat(v[1]) })),
                    borderColor: color, backgroundColor: color + '20',
                    borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false,
                });
            }
            if (memData?.values?.length) {
                memDatasets.push({
                    label: shortPod,
                    data: memData.values.map(v => ({ x: v[0] * 1000, y: parseFloat(v[1]) / (1024 * 1024) })),
                    borderColor: color, backgroundColor: color + '20',
                    borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false,
                });
            }
            colorIdx++;
        }
    }

    renderChart('chart-k8s-cpu', 'Pod CPU 使用率 (%)', cpuDatasets, '%');
    renderChart('chart-k8s-mem', 'Pod 内存使用 (MB)', memDatasets, 'MB');

    // ── APM Charts ──
    const reqDatasets = [];
    const latDatasets = [];
    colorIdx = 0;

    for (const [svc, metrics] of Object.entries(apm)) {
        if (filterSvc && svc !== filterSvc) continue;
        const reqData = metrics['request_count'];
        const latData = metrics['avg_request_latency_seconds'];
        const color = CHART_COLORS[colorIdx % CHART_COLORS.length];

        if (reqData?.values?.length) {
            reqDatasets.push({
                label: svc,
                data: reqData.values.map(v => ({ x: v[0] * 1000, y: parseFloat(v[1]) })),
                borderColor: color, backgroundColor: color + '30',
                borderWidth: 2, pointRadius: 1, tension: 0.3, fill: true,
            });
        }
        if (latData?.values?.length) {
            latDatasets.push({
                label: svc,
                data: latData.values.map(v => ({ x: v[0] * 1000, y: parseFloat(v[1]) * 1000 })),
                borderColor: color, backgroundColor: color + '20',
                borderWidth: 2, pointRadius: 1, tension: 0.3, fill: false,
            });
        }
        colorIdx++;
    }

    renderChart('chart-apm-requests', '服务请求量 (req/30s)', reqDatasets, '');
    renderChart('chart-apm-latency', '平均延迟 (ms)', latDatasets, 'ms');

    // ── APM Summary Table ──
    const apmBody = document.getElementById('alidata-apm-body');
    const apmEntries = Object.entries(apm).filter(([svc]) => !filterSvc || svc === filterSvc);
    if (apmEntries.length) {
        apmBody.innerHTML = apmEntries.sort((a, b) =>
            (b[1]?.request_count?.current || 0) - (a[1]?.request_count?.current || 0)
        ).map(([svc, metrics]) => {
            const reqCount = metrics.request_count?.current || 0;
            const latency = metrics.avg_request_latency_seconds?.current || 0;
            const latencyMs = (latency * 1000).toFixed(1);
            const latencyClass = latency > 1 ? 'text-danger' : latency > 0.5 ? 'text-warning' : '';
            return `<tr>
                <td><strong>${escapeHtml(svc)}</strong></td>
                <td>${reqCount.toFixed(0)}</td>
                <td class="${latencyClass}">${latency > 0 ? latency.toFixed(4) + ' (' + latencyMs + 'ms)' : '-'}</td>
            </tr>`;
        }).join('');
        if (emptyEl) emptyEl.style.display = 'none';
    } else if (cpuDatasets.length || memDatasets.length) {
        apmBody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center">暂无 APM 数据</td></tr>';
        if (emptyEl) emptyEl.style.display = 'none';
    } else {
        if (emptyEl) { emptyEl.style.display = 'block'; emptyEl.textContent = '暂无指标数据'; }
    }
}

function renderChart(canvasId, title, datasets, unit) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    if (_alidataCharts[canvasId]) { _alidataCharts[canvasId].destroy(); delete _alidataCharts[canvasId]; }
    if (!datasets.length) {
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#5b5f73'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('暂无数据', canvas.width / 2, canvas.height / 2);
        return;
    }
    _alidataCharts[canvasId] = new Chart(canvas, {
        type: 'line', data: { datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: { duration: 300 },
            interaction: { mode: 'index', intersect: false },
            plugins: {
                title: { display: true, text: title, color: '#8b8fa3', font: { size: 13, weight: '600' } },
                legend: { display: datasets.length <= 8, position: 'bottom',
                    labels: { color: '#8b8fa3', font: { size: 10 }, boxWidth: 12, padding: 8 } },
                tooltip: { backgroundColor: '#1e2130', titleColor: '#e4e6ef', bodyColor: '#8b8fa3',
                    borderColor: '#2a2d3e', borderWidth: 1,
                    callbacks: {
                        title: function(ctx) { return ctx[0] ? new Date(ctx[0].parsed.x).toLocaleTimeString('zh-CN') : ''; },
                        label: function(ctx) { return ctx.dataset.label + ': ' + ctx.parsed.y.toFixed(2) + (unit ? ' ' + unit : ''); }
                    }
                },
            },
            scales: {
                x: { type: 'linear',
                    ticks: { color: '#5b5f73', font: { size: 10 },
                        callback: function(val) { return new Date(val).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }); },
                        maxTicksLimit: 8 },
                    grid: { color: '#2a2d3e' } },
                y: { ticks: { color: '#5b5f73', font: { size: 10 },
                        callback: function(val) { return val.toFixed(1) + (unit ? ' ' + unit : ''); } },
                    grid: { color: '#2a2d3e' }, beginAtZero: true },
            },
        },
    });
}

async function loadAliDataLogs() {
    const query = document.getElementById('alidata-log-query')?.value?.trim() || '';
    const level = document.getElementById('alidata-log-level')?.value || '';
    const timeRange = document.getElementById('alidata-log-timerange')?.value || '1h';
    const ns = document.getElementById('alidata-log-ns')?.value?.trim() || '';
    const size = document.getElementById('alidata-log-size')?.value || 200;

    const tbody = document.getElementById('alidata-log-body');
    const emptyEl = document.getElementById('alidata-log-empty');
    const statsEl = document.getElementById('alidata-log-stats');

    tbody.innerHTML = '';
    emptyEl.style.display = 'block';
    emptyEl.textContent = '加载中...';

    let url = `/api/alidata/logs?time_range=${timeRange}&size=${size}`;
    if (query) url += `&query=${encodeURIComponent(query)}`;
    if (level) url += `&level=${encodeURIComponent(level)}`;
    if (ns) url += `&namespace=${encodeURIComponent(ns)}`;

    const data = await api(url);

    if (!data || data.error) {
        emptyEl.textContent = `错误: ${data?.error || '请求失败'}`;
        statsEl.innerHTML = '';
        return;
    }

    const entries = data.entries || [];
    if (!entries.length) {
        emptyEl.textContent = '未找到日志';
        statsEl.innerHTML = `<span class="badge badge-gray">共 0 条</span>`;
        return;
    }

    emptyEl.style.display = 'none';

    // Stats
    const levelCounts = {};
    entries.forEach(e => { levelCounts[e.level] = (levelCounts[e.level] || 0) + 1; });
    statsEl.innerHTML = `<span class="badge badge-info">共 ${entries.length} 条</span> ` +
        Object.entries(levelCounts).map(([lv, cnt]) => {
            const cls = lv === 'error' ? 'danger' : lv === 'warn' ? 'warning' : 'info';
            return `<span class="badge badge-${cls}">${lv}: ${cnt}</span>`;
        }).join(' ');

    // Render table
    tbody.innerHTML = entries.map(e => {
        const lvCls = e.level === 'error' ? 'danger' : e.level === 'warn' ? 'warning' : 'info';
        const ts = e.timestamp ? formatTime(
            typeof e.timestamp === 'number' && e.timestamp < 2e10
                ? e.timestamp * 1000 : e.timestamp
        ) : '-';
        return `<tr>
            <td style="white-space:nowrap;font-size:11px">${ts}</td>
            <td><span class="badge badge-${lvCls}">${e.level}</span></td>
            <td>${escapeHtml(e.service || '-')}</td>
            <td style="font-size:11px">${escapeHtml(e.pod || '-')}</td>
            <td style="font-size:11px;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                title="${escapeHtml(e.message || '')}">${escapeHtml(e.message || '')}</td>
        </tr>`;
    }).join('');
}

// ─────────────────────────────────────────
// Cloud-OpsBench Workbench
// ─────────────────────────────────────────

async function loadCloudOpsWorkbench() {
    const [summaryData, casesData] = await Promise.all([
        api('/api/cloudopsbench/summary'),
        loadCloudOpsCases(false),
    ]);

    if (!summaryData) return;

    state.cloudops.summary = summaryData;
    state.cloudops.selectedCaseRef = summaryData.selected_case_ref || state.cloudops.selectedCaseRef || '';

    document.getElementById('cloudops-stat-systems').textContent = summaryData.system_count ?? '-';
    document.getElementById('cloudops-stat-categories').textContent = summaryData.fault_category_count ?? '-';
    document.getElementById('cloudops-stat-cases').textContent = summaryData.case_count ?? '-';
    document.getElementById('cloudops-stat-mode').textContent = summaryData.injection_mode || '-';
    document.getElementById('cloudops-injection-message').textContent = summaryData.injection_message || '未获取到平台信息';

    hydrateCloudOpsFilters(summaryData);

    const selectedRef = state.cloudops.selectedCaseRef || summaryData.selected_case_ref || casesData?.cases?.[0]?.ref;
    if (selectedRef) {
        await loadCloudOpsCaseDetail(selectedRef);
    }
}

function hydrateCloudOpsFilters(summaryData) {
    const systemSelect = document.getElementById('cloudops-system');
    const categorySelect = document.getElementById('cloudops-category');
    if (!systemSelect || !categorySelect) return;

    const currentSystem = systemSelect.value;
    const currentCategory = categorySelect.value;

    const systems = summaryData?.systems || [];
    const categories = summaryData?.fault_categories || [];

    systemSelect.innerHTML = '<option value="">全部系统</option>' +
        systems.map(item => `<option value="${escapeHtml(item)}" ${item === currentSystem ? 'selected' : ''}>${escapeHtml(item)}</option>`).join('');
    categorySelect.innerHTML = '<option value="">全部故障类别</option>' +
        categories.map(item => `<option value="${escapeHtml(item)}" ${item === currentCategory ? 'selected' : ''}>${escapeHtml(item)}</option>`).join('');
}

async function loadCloudOpsCases(autoSelect = true) {
    const system = document.getElementById('cloudops-system')?.value || '';
    const category = document.getElementById('cloudops-category')?.value || '';
    const search = document.getElementById('cloudops-search')?.value?.trim() || '';

    let url = '/api/cloudopsbench/cases';
    const params = new URLSearchParams();
    if (system) params.set('system', system);
    if (category) params.set('fault_category', category);
    if (search) params.set('search', search);
    if ([...params.keys()].length) url += `?${params.toString()}`;

    const data = await api(url);
    const cases = data?.cases || [];
    state.cloudops.cases = cases;
    document.getElementById('cloudops-case-count-badge').textContent = String(cases.length);

    const tbody = document.querySelector('#cloudops-case-table tbody');
    if (!tbody) return data;

    if (!cases.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-muted" style="text-align:center">未找到案例</td></tr>';
        return data;
    }

    tbody.innerHTML = cases.map(item => `
        <tr class="${item.ref === state.cloudops.selectedCaseRef ? 'cloudops-case-active' : ''}">
            <td>
                <button class="cloudops-case-button" onclick="loadCloudOpsCaseDetail('${escapeHtml(item.ref)}')">
                    <strong>${escapeHtml(item.ref)}</strong>
                    <span class="text-muted">${escapeHtml(item.namespace || '-')}</span>
                </button>
            </td>
            <td>${escapeHtml(item.query || '-')}</td>
            <td>${escapeHtml(item.root_cause || item.fault_object || '-')}</td>
        </tr>
    `).join('');

    if (autoSelect && !state.cloudops.selectedCaseRef && cases[0]?.ref) {
        await loadCloudOpsCaseDetail(cases[0].ref);
    }

    return data;
}

async function loadCloudOpsCaseDetail(caseRef) {
    if (!caseRef) return;
    const detail = await api(`/api/cloudopsbench/case/${encodeURIComponent(caseRef)}`);
    if (!detail) return;

    state.cloudops.selectedCaseRef = caseRef;
    document.getElementById('cloudops-selected-case').textContent = caseRef;
    highlightCloudOpsSelectedCase();

    const result = detail.result || {};
    const replaySteps = (detail.replay_steps || []).map(step => `
        <li><strong>${escapeHtml(step.title)}:</strong> ${escapeHtml(step.description)}</li>
    `).join('');

    const services = (detail.service_inventory || []).slice(0, 18).map(item => `<span class="badge badge-gray">${escapeHtml(item)}</span>`).join(' ');
    const metrics = (detail.metric_inventory || []).slice(0, 18).map(item => `<span class="badge badge-info">${escapeHtml(item)}</span>`).join(' ');
    const trajectories = (detail.golden_trajectories || []).map(item => escapeHtml(item.name)).join(', ') || '无';

    document.getElementById('cloudops-case-detail').innerHTML = `
        <div class="cloudops-detail-grid">
            <div><span class="detail-label">系统</span><div>${escapeHtml(detail.system || '-')}</div></div>
            <div><span class="detail-label">类别</span><div>${escapeHtml(detail.fault_category || '-')}</div></div>
            <div><span class="detail-label">命名空间</span><div>${escapeHtml(detail.namespace || '-')}</div></div>
            <div><span class="detail-label">模式</span><div>${escapeHtml(detail.platform?.mode || '-')}</div></div>
        </div>
        <p><strong>症状：</strong>${escapeHtml(detail.query || '-')}</p>
        <p><strong>Ground Truth：</strong>${escapeHtml(result.root_cause || '-')} (${escapeHtml(result.fault_taxonomy || '-')}, ${escapeHtml(result.fault_object || '-')})</p>
        <p class="text-muted">${escapeHtml(detail.platform?.continuous_injection_message || '')}</p>
        <div class="cloudops-action-row">
            <button class="btn btn-sm btn-primary" onclick="startCloudOpsRCA()">送入 Ops Factory RCA</button>
            <button class="btn btn-sm" onclick="runCloudOpsOpsAug()">运行 OpsAug</button>
            <button class="btn btn-sm" onclick="runCloudOpsPromCopilot()">生成 PromQL</button>
        </div>
        <div class="cloudops-section">
            <h4>数据可用性</h4>
            <div class="cloudops-badges">
                ${Object.entries(detail.data_availability || {}).map(([key, val]) =>
                    `<span class="badge ${val ? 'badge-success' : 'badge-gray'}">${escapeHtml(key)}: ${val ? 'yes' : 'no'}</span>`
                ).join(' ')}
            </div>
        </div>
        <div class="cloudops-section">
            <h4>故障回放步骤</h4>
            <ol class="cloudops-steps">${replaySteps || '<li>暂无步骤</li>'}</ol>
        </div>
        <div class="cloudops-section">
            <h4>服务依赖</h4>
            <div class="cloudops-badges">${services || '<span class="text-muted">无</span>'}</div>
        </div>
        <div class="cloudops-section">
            <h4>指标列</h4>
            <div class="cloudops-badges">${metrics || '<span class="text-muted">无</span>'}</div>
        </div>
        <div class="cloudops-section">
            <h4>Golden Trajectory</h4>
            <div>${trajectories}</div>
        </div>
    `;
}

function highlightCloudOpsSelectedCase() {
    document.querySelectorAll('#cloudops-case-table tbody tr').forEach(row => row.classList.remove('cloudops-case-active'));
    document.querySelectorAll('#cloudops-case-table .cloudops-case-button').forEach(button => {
        const label = button.querySelector('strong')?.textContent || '';
        if (label === state.cloudops.selectedCaseRef) {
            button.closest('tr')?.classList.add('cloudops-case-active');
        }
    });
}

async function runCloudOpsOpsAug() {
    const caseRef = state.cloudops.selectedCaseRef;
    if (!caseRef) {
        window.alert('请先选择一个案例');
        return;
    }
    const output = document.getElementById('cloudops-opsaug-output');
    output.textContent = '运行中...';
    const data = await api(`/api/cloudopsbench/case/${encodeURIComponent(caseRef)}/opsaug`);
    output.textContent = JSON.stringify(data || { error: '请求失败' }, null, 2);
}

async function runCloudOpsPromCopilot() {
    const caseRef = state.cloudops.selectedCaseRef;
    const question = document.getElementById('cloudops-prom-question')?.value?.trim() || '';
    if (!caseRef) {
        window.alert('请先选择一个案例');
        return;
    }
    if (!question) {
        window.alert('请输入 PromQL 问题');
        return;
    }

    const output = document.getElementById('cloudops-prom-output');
    output.textContent = '生成中...';
    const data = await api(`/api/cloudopsbench/case/${encodeURIComponent(caseRef)}/promcopilot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
    });
    output.textContent = JSON.stringify(data || { error: '请求失败' }, null, 2);
}

async function startCloudOpsRCA() {
    const caseRef = state.cloudops.selectedCaseRef;
    if (!caseRef) {
        window.alert('请先选择一个案例');
        return;
    }
    const payload = await api(`/api/cloudopsbench/case/${encodeURIComponent(caseRef)}/rca_payload`, {
        method: 'POST',
    });
    if (!payload) {
        window.alert('无法生成 RCA 上下文');
        return;
    }

    switchView('rca');
    document.getElementById('rca-query').value = payload.query || '';
    document.getElementById('rca-ns').value = payload.namespace || '';

    const data = await api('/api/rca/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            query: payload.query || '',
            namespace: payload.namespace || '',
            context: payload.context || '',
            case_ref: caseRef,
        }),
    });

    if (!data?.run_id) {
        window.alert('启动 RCA 失败');
        return;
    }

    state.rcaRunId = data.run_id;
    const progress = document.getElementById('rca-progress');
    progress.style.display = 'block';
    document.getElementById('rca-result-card').style.display = 'none';
    document.getElementById('rca-log').textContent = '';
    document.getElementById('rca-phases').innerHTML = '';
    document.getElementById('rca-hyp-list').innerHTML = '';
    document.getElementById('rca-hypotheses').style.display = 'none';
    document.getElementById('rca-evidence-grid').innerHTML = '';
    document.getElementById('rca-evidence').style.display = 'none';
    document.getElementById('rca-iteration').style.display = 'none';
    document.getElementById('rca-result-content').innerHTML = '';

    const logEl = document.getElementById('rca-log');
    const sse = new EventSource(`/api/rca/${data.run_id}/stream`);
    sse.onmessage = (e) => {
        try {
            const msg = JSON.parse(e.data);
            if (msg.type === 'log') {
                logEl.textContent += msg.msg + '\n';
                logEl.scrollTop = logEl.scrollHeight;
            } else if (msg.type === 'event') {
                handleRCAEvent(msg.data);
            } else if (msg.type === 'done') {
                sse.close();
                renderRCAFinalResult(msg.result);
                loadRCAHistory();
            }
        } catch {}
    };
    sse.onerror = () => { sse.close(); };
}

async function searchAliDataTraces() {
    const service = document.getElementById('alidata-trace-service')?.value;
    if (!service) { alert('请先选择服务'); return; }

    const lookback = document.getElementById('alidata-trace-lookback')?.value || '1h';
    const limit = document.getElementById('alidata-trace-limit')?.value || 20;

    const tbody = document.getElementById('alidata-trace-body');
    const emptyEl = document.getElementById('alidata-trace-empty');
    tbody.innerHTML = '';
    emptyEl.style.display = 'block';
    emptyEl.textContent = '搜索中...';

    const data = await api(`/api/alidata/traces?service=${encodeURIComponent(service)}&lookback=${lookback}&limit=${limit}`);

    if (!data?.traces?.length) {
        emptyEl.textContent = data?.error ? `错误: ${data.error}` : '未找到 Trace';
        return;
    }

    emptyEl.style.display = 'none';

    tbody.innerHTML = data.traces.map(t => {
        const durationMs = (t.total_duration_us / 1000).toFixed(1);
        const shortId = t.traceID?.substring(0, 16) || '';
        const services = (t.services || []).slice(0, 3).join(', ');
        const moreServices = t.services?.length > 3 ? ` +${t.services.length - 3}` : '';
        const errorRate = t.error_rate != null ? `${(t.error_rate * 100).toFixed(1)}%` : '-';
        const errorCls = t.error_rate > 0 ? 'text-danger' : '';
        return `<tr>
            <td><code style="font-size:11px">${escapeHtml(shortId)}</code></td>
            <td>${t.span_count}</td>
            <td style="font-size:11px">${escapeHtml(services)}${moreServices}</td>
            <td>${durationMs} ms</td>
            <td class="${errorCls}">${errorRate}</td>
            <td><button class="btn btn-sm" onclick="viewAliDataTraceDetail('${t.traceID}')">详情</button></td>
        </tr>`;
    }).join('');
}

async function lookupAliDataTraceById() {
    const traceId = document.getElementById('alidata-trace-id-input')?.value?.trim();
    if (!traceId) { alert('请输入 Trace ID'); return; }
    await viewAliDataTraceDetail(traceId);
}

async function viewAliDataTraceDetail(traceId) {
    const card = document.getElementById('alidata-trace-detail-card');
    const content = document.getElementById('alidata-trace-detail-content');
    card.style.display = 'block';
    content.innerHTML = '<p class="text-muted">加载中...</p>';

    const data = await api(`/api/alidata/trace/${traceId}`);

    if (!data || data.error) {
        content.innerHTML = `<p class="text-danger">加载失败: ${data?.error || '未知错误'}</p>`;
        return;
    }

    const traces = data.traces || [];
    if (!traces.length) {
        content.innerHTML = '<p class="text-muted">无 Trace 数据</p>';
        return;
    }

    const trace = traces[0];
    const services = trace.services || [];
    const operations = trace.operations || [];
    const endpoints = trace.endpoints || [];
    const errorSpans = trace.error_spans || [];
    const statusDist = trace.http_status_distribution || {};
    const durationMs = (trace.total_duration_us / 1000).toFixed(1);
    const errorRate = trace.error_rate != null ? `${(trace.error_rate * 100).toFixed(1)}%` : '-';

    let html = `
        <div style="margin-bottom:12px">
            <strong>Trace ID:</strong> <code>${escapeHtml(traceId)}</code>
            &nbsp; <strong>Span数:</strong> ${trace.span_count}
            &nbsp; <strong>总耗时:</strong> ${durationMs} ms
            &nbsp; <strong>错误率:</strong> <span class="${trace.error_rate > 0 ? 'text-danger' : ''}">${errorRate}</span>
        </div>`;

    // Services
    html += `<div style="margin-bottom:8px">
        <strong>涉及服务:</strong> ${services.map(s => `<span class="badge badge-info" style="margin:2px">${escapeHtml(s)}</span>`).join(' ')}
    </div>`;

    // Operations
    if (operations.length) {
        html += `<div style="margin-bottom:8px">
            <strong>操作:</strong> ${operations.slice(0, 10).map(o => `<span class="badge badge-gray" style="margin:2px">${escapeHtml(o)}</span>`).join(' ')}
        </div>`;
    }

    // HTTP Status Distribution
    if (Object.keys(statusDist).length) {
        html += `<div style="margin-bottom:8px">
            <strong>HTTP状态分布:</strong> ${Object.entries(statusDist).map(([code, cnt]) => {
                const cls = code.startsWith('2') ? 'success' : code.startsWith('4') ? 'warning' : code.startsWith('5') ? 'danger' : 'info';
                return `<span class="badge badge-${cls}" style="margin:2px">${code}: ${cnt}</span>`;
            }).join(' ')}
        </div>`;
    }

    // Error Spans
    if (errorSpans.length) {
        html += `<div style="margin-top:12px">
            <h4 style="font-size:13px;color:var(--text-secondary);margin-bottom:8px">错误 Spans</h4>
            <table class="data-table">
                <thead><tr><th>服务</th><th>操作</th><th>状态码</th><th>耗时</th><th>URL</th></tr></thead>
                <tbody>${errorSpans.map(es => `
                    <tr>
                        <td>${escapeHtml(es.service || '')}</td>
                        <td>${escapeHtml(es.operation || '')}</td>
                        <td><span class="badge badge-danger">${es.status_code}</span></td>
                        <td>${es.duration_ms} ms</td>
                        <td style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
                            title="${escapeHtml(es.url || '')}">${escapeHtml(es.url || '-')}</td>
                    </tr>
                `).join('')}</tbody>
            </table>
        </div>`;
    }

    // Endpoints
    if (endpoints.length) {
        html += `<details style="margin-top:12px;font-size:12px">
            <summary style="cursor:pointer;color:var(--text-muted)">查看请求端点 (${endpoints.length})</summary>
            <div style="margin-top:6px">
                ${endpoints.map(ep => `<div class="signal-item" style="font-size:11px">${escapeHtml(ep)}</div>`).join('')}
            </div>
        </details>`;
    }

    content.innerHTML = html;
}

// ─────────────────────────────────────────
// Events
// ─────────────────────────────────────────

async function loadEvents() {
    if (isOfflineMode()) {
        const tbody = document.querySelector('#event-table tbody');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="6" class="text-muted" style="text-align:center">离线模式下无事件数据</td></tr>';
        }
        return;
    }

    const ns = document.getElementById('event-ns')?.value || '';
    const data = await api(`/api/cluster/events?namespace=${ns}&limit=100`);
    if (!data?.events) return;

    const tbody = document.querySelector('#event-table tbody');
    tbody.innerHTML = data.events.map(e => `
        <tr>
            <td><span class="badge badge-${e.type === 'Warning' ? 'warning' : 'info'}">${e.type}</span></td>
            <td>${escapeHtml(e.reason)}</td>
            <td>${escapeHtml(e.object)}</td>
            <td>${escapeHtml(e.message?.substring(0, 120) || '')}</td>
            <td>${e.count}</td>
            <td>${formatTime(e.last_seen)}</td>
        </tr>
    `).join('');
}

// ─────────────────────────────────────────
// Health Check
// ─────────────────────────────────────────

async function healthCheck() {
    const data = await api('/api/health');
    const prevOffline = state.runtime.offlineMode;
    const prevProblemId = state.runtime.offlineProblemId;
    const prevDataType = state.runtime.offlineDataType;
    const dot = document.querySelector('#health-dot .dot');
    const text = document.querySelector('#health-dot span:last-child');
    const badge = document.getElementById('cluster-badge');
    const llmWarning = document.getElementById('llm-warning');
    const eventsNav = document.querySelector('.nav-item[data-view="events"]');

    if (data) {
        state.runtime.offlineMode = !!data.offline_mode;
        state.runtime.observabilityBackend = data.observability_backend || 'native';
        state.runtime.offlineProblemId = data.offline_problem_id || '';
        state.runtime.offlineDataType = data.offline_data_type || '';
    }

    await syncOfflineProblemSwitcher(prevOffline !== state.runtime.offlineMode);

    if (data?.status === 'ok') {
        dot.className = 'dot dot-green';
        text.textContent = isOfflineMode() ? '离线模式' : '系统正常';
        badge.className = 'badge badge-success';
        badge.textContent = isOfflineMode() ? `离线: ${currentOfflineLabel()}` : '已连接';
        document.getElementById('view-title').textContent = getViewTitle(state.currentView);
        if (eventsNav) eventsNav.style.display = '';
        if (isOfflineMode() && state.currentView === 'events') {
            // Allow events view in offline mode but show offline message
        }

        // Model source notice
        if (llmWarning) {
            const title = document.getElementById('model-source-title');
            const desc = document.getElementById('model-source-desc');
            const provider = data.llm_provider || 'local';
            if (title) title.textContent = provider === 'local' ? '本地 Qwen-0.6B' : '用户自带 API';
            if (desc) desc.textContent = provider === 'local'
                ? '系统默认使用本地模型；需要更大模型时可主动填写你自己的 API。'
                : `当前使用 ${provider}，API Key 由用户自行提供，Ops Factory 不内置 API。`;
            llmWarning.style.display = provider === 'local' ? 'none' : 'flex';
        }
    } else {
        dot.className = 'dot dot-red';
        text.textContent = '连接异常';
        badge.className = 'badge badge-danger';
        badge.textContent = '连接异常';
    }

    if (
        prevOffline !== state.runtime.offlineMode
        || prevProblemId !== state.runtime.offlineProblemId
        || prevDataType !== state.runtime.offlineDataType
    ) {
        refreshCurrentView();
    }
}

// ─────────────────────────────────────────
// Model Chat and Provider Selection
// ─────────────────────────────────────────

const chatState = {
    sessionId: 'default',
    isStreaming: false,
    messages: [],
};

async function initChatView() {
    await loadModelInfo();
    await loadChatHistory();
    await loadChatSessions();

    // Setup input listeners
    const input = document.getElementById('chat-input');
    if (input) {
        input.addEventListener('input', () => {
            autoResizeTextarea(input);
            updateCharCount();
        });
    }
}

async function loadModelInfo() {
    const statusEl = document.getElementById('chat-model-status');
    if (!statusEl) return;

    try {
        const res = await fetch('/api/model/info');
        const data = await res.json();
        state.modelProvider = {
            provider: data.provider || 'local',
            model: data.model || 'Qwen/Qwen3-0.6B',
            baseUrl: data.base_url || 'http://127.0.0.1:8000/v1',
            userApi: !!data.user_api,
        };
        syncProviderModalFromInfo(data);

        if (data && data.provider === 'local' && data.reachable) {
            statusEl.innerHTML = `
                <span class="text-success">已连接</span> |
                默认本地模型: <strong>${escapeHtml(data.model || 'Qwen/Qwen3-0.6B')}</strong> |
                端点: <code>${escapeHtml(data.base_url || 'http://127.0.0.1:8000/v1')}</code>
            `;
        } else if (data && data.provider === 'local') {
            statusEl.innerHTML = `
                <span class="text-danger">本地模型未启动</span><br>
                <code>${escapeHtml(data.base_url)}</code>
                <button class="btn btn-sm btn-primary" style="margin-top:10px" onclick="startLocalModelServer()">启动本地 Qwen</button>
                <div class="text-muted" style="margin-top:6px;font-size:12px">${escapeHtml(data.health?.error || '')}</div>
            `;
        } else if (data && data.configured) {
            const healthNote = data.health?.ok
                ? '<span class="text-success">配置已启用</span>'
                : '<span class="text-muted">配置已启用，调用时验证可用性</span>';
            statusEl.innerHTML = `
                ${healthNote} |
                来源: <strong>${escapeHtml(data.provider_label || '用户自带 API')}</strong><br>
                模型: <strong>${escapeHtml(data.model || '-')}</strong> |
                端点: <code>${escapeHtml(data.base_url || '-')}</code><br>
                <small>Ops Factory 不提供内置外部 API；当前 Key 来自用户输入，且只保存在当前服务进程内。</small>
            `;
        } else {
            statusEl.innerHTML = `
                <span class="text-danger">用户自带 API 配置不完整</span> — 可恢复本地 Qwen-0.6B 默认模型
            `;
        }
    } catch (e) {
        statusEl.innerHTML = `
            <span class="text-danger">连接失败</span> — ${e.message}
        `;
    }
}

async function startLocalModelServer() {
    const statusEl = document.getElementById('chat-model-status');
    if (statusEl) statusEl.innerHTML = '<span class="text-muted">正在启动本地 Qwen 模型服务，首次加载可能需要几十秒...</span>';
    const data = await api('/api/model/start_local', { method: 'POST' });
    if (statusEl) {
        statusEl.innerHTML = `${escapeHtml(data?.message || '启动请求已发送')}<br><small>${escapeHtml(data?.health?.error || data?.health?.url || '')}</small>`;
    }
    await loadModelInfo();
}

function openModelProviderModal() {
    const modal = document.getElementById('model-provider-modal');
    if (!modal) return;
    modal.style.display = 'grid';
    loadModelInfo();
}

function closeModelProviderModal() {
    const modal = document.getElementById('model-provider-modal');
    if (modal) modal.style.display = 'none';
}

function getSelectedProvider() {
    return document.querySelector('input[name="model-provider"]:checked')?.value || 'local';
}

function toggleProviderFields() {
    const provider = getSelectedProvider();
    const fields = document.getElementById('provider-api-fields');
    const note = document.getElementById('provider-local-note');
    if (fields) fields.style.display = provider === 'local' ? 'none' : 'grid';
    if (note) note.style.display = provider === 'local' ? 'block' : 'none';
    if (provider === 'anthropic') {
        const base = document.getElementById('provider-base-url');
        if (base && !base.value.trim()) base.value = 'https://api.anthropic.com/v1';
    }
}

function syncProviderModalFromInfo(data) {
    if (!data) return;
    const provider = data.provider || 'local';
    const radio = document.querySelector(`input[name="model-provider"][value="${provider}"]`);
    if (radio) radio.checked = true;
    const base = document.getElementById('provider-base-url');
    const model = document.getElementById('provider-model');
    const key = document.getElementById('provider-api-key');
    if (base && provider !== 'local') base.value = data.base_url || '';
    if (model && provider !== 'local') model.value = data.model || '';
    if (key) key.value = '';
    toggleProviderFields();
}

async function saveModelProvider() {
    const provider = getSelectedProvider();
    const status = document.getElementById('model-provider-status');
    if (status) status.textContent = '正在应用模型来源...';
    if (provider === 'local') {
        await resetModelProviderToLocal();
        return;
    }
    const payload = { provider };
    payload.base_url = document.getElementById('provider-base-url')?.value?.trim() || '';
    payload.model = document.getElementById('provider-model')?.value?.trim() || '';
    payload.api_key = document.getElementById('provider-api-key')?.value || '';
    const data = await api('/api/model/provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (data?.error) {
        if (status) status.textContent = data.error;
        return;
    }
    if (status) status.textContent = data?.message || '已应用。';
    await loadModelInfo();
    await healthCheck();
}

async function resetModelProviderToLocal() {
    const status = document.getElementById('model-provider-status');
    if (status) status.textContent = '正在恢复本地 Qwen-0.6B...';
    const data = await api('/api/model/provider/local', { method: 'POST' });
    if (data?.error) {
        if (status) status.textContent = data.error;
        return;
    }
    if (status) status.textContent = data?.message || '已恢复本地默认。';
    await loadModelInfo();
    await healthCheck();
}

async function loadChatHistory() {
    const data = await api(`/api/model/chat/history/${chatState.sessionId}`);
    if (!data?.messages) return;

    chatState.messages = data.messages;
    renderChatMessages();
}

function renderChatMessages() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    if (chatState.messages.length === 0) {
        container.innerHTML = `
            <div class="chat-welcome pro">
                <div class="welcome-kicker">Agent Console</div>
                <h3>从 RCA 结果进入可解释复盘</h3>
                <p>你可以让模型解释工具链、质疑根因候选、总结失败样本，或把自进化记录转换成下一轮 Agent 策略。</p>
                <div class="quick-actions">
                    <button class="quick-action-btn" onclick="sendQuickMessage('请解释上一轮 RCA 的工具调用链：每个工具做了什么，输出了什么，为什么影响最终候选。')">工具链解释</button>
                    <button class="quick-action-btn" onclick="sendQuickMessage('请检查一次 RCA 结果是否可能把受害服务误判为根因服务。')">根因质检</button>
                    <button class="quick-action-btn" onclick="sendQuickMessage('请根据历史失败案例，为基础模型生成一版更好的 RCA 提示词。')">优化提示词</button>
                    <button class="quick-action-btn" onclick="sendQuickMessage('请设计下一轮故障注入和验证计划，用于提升 Agent 能力。')">迭代计划</button>
                </div>
            </div>
        `;
        return;
    }

    container.innerHTML = chatState.messages.map(msg => {
        if (msg.role === 'user') {
            return `<div class="chat-message user">
                <div class="message-content">${escapeHtml(msg.content)}</div>
            </div>`;
        } else {
            return `<div class="chat-message assistant">
                <div class="message-avatar">🤖</div>
                <div class="message-content">${formatMarkdown(msg.content)}</div>
            </div>`;
        }
    }).join('');

    container.scrollTop = container.scrollHeight;
}

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const message = input?.value?.trim();
    if (!message || chatState.isStreaming) return;

    input.value = '';
    autoResizeTextarea(input);
    updateCharCount();

    chatState.messages.push({ role: 'user', content: message });
    renderChatMessages();

    const container = document.getElementById('chat-messages');
    const loadingEl = document.createElement('div');
    loadingEl.className = 'chat-message assistant loading';
    loadingEl.id = 'chat-loading';
    loadingEl.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
            <small class="text-muted" id="chat-loading-note">快速检查模型连接...</small>
            <span class="typing-indicator">
                <span></span><span></span><span></span>
            </span>
        </div>
    `;
    container.appendChild(loadingEl);
    container.scrollTop = container.scrollHeight;

    chatState.isStreaming = true;
    updateSendButton(true);

    let slowNoticeTimer = null;
    try {
        slowNoticeTimer = window.setTimeout(() => {
            const note = document.getElementById('chat-loading-note');
            if (note) note.textContent = '模型仍在生成中，本次不会自动截断回答；你可以继续等待。';
        }, 12000);
        const response = await fetch('/api/model/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: chatState.sessionId,
                stream: false,
            }),
        });
        window.clearTimeout(slowNoticeTimer);
        slowNoticeTimer = null;

        const data = await response.json();

        if (data?.response) {
            chatState.messages.push({ role: 'assistant', content: data.response });
        } else if (data?.detail) {
            chatState.messages.push({ role: 'assistant', content: `模型服务不可用：${data.detail}\n\n如果使用本地模型，请点击“启动本地 Qwen”；如果使用你自己的 API，请在“模型来源”里检查 Base URL、模型名和 Key。` });
        }
    } catch (e) {
        chatState.messages.push({ role: 'assistant', content: `请求失败: ${e.message}` });
    } finally {
        if (slowNoticeTimer) window.clearTimeout(slowNoticeTimer);
        const loading = document.getElementById('chat-loading');
        if (loading) loading.remove();

        chatState.isStreaming = false;
        updateSendButton(false);
        renderChatMessages();
    }
}

function sendQuickMessage(message) {
    const input = document.getElementById('chat-input');
    if (input) {
        input.value = message;
        sendChatMessage();
    }
}

function handleChatKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
    }
}

function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 150) + 'px';
}

function updateCharCount() {
    const input = document.getElementById('chat-input');
    const countEl = document.getElementById('chat-char-count');
    if (input && countEl) {
        const len = input.value.length;
        countEl.textContent = `${len} / 2000`;
        countEl.style.color = len > 1800 ? 'var(--danger)' : 'var(--text-muted)';
    }
}

function updateSendButton(loading) {
    const btn = document.getElementById('chat-send-btn');
    if (btn) {
        btn.disabled = loading;
        btn.innerHTML = loading
            ? '<span class="loading-spinner"></span>'
            : '<span class="send-icon">Send</span>';
    }
}

async function clearChatHistory() {
    if (!confirm('确定要清空当前对话记录吗？')) return;

    await api(`/api/model/chat/history/${chatState.sessionId}`, { method: 'DELETE' });
    chatState.messages = [];
    renderChatMessages();
}

function exportChatHistory() {
    if (chatState.messages.length === 0) {
        alert('暂无对话记录可导出');
        return;
    }

    const content = chatState.messages.map(m =>
        `【${m.role === 'user' ? '用户' : '助手'}】\n${m.content}`
    ).join('\n\n---\n\n');

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `chat-export-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
}

async function loadChatSessions() {
    const data = await api('/api/model/chat/sessions');
    const container = document.getElementById('chat-sessions');
    if (!container || !data?.sessions?.length) {
        container.innerHTML = '<p class="text-muted">暂无历史对话</p>';
        return;
    }

    container.innerHTML = data.sessions.map(s => `
        <div class="chat-session-item ${s.session_id === chatState.sessionId ? 'active' : ''}"
             onclick="switchChatSession('${s.session_id}')">
            <span class="session-id">${s.session_id}</span>
            <span class="session-meta">${s.message_count} 条消息</span>
        </div>
    `).join('');
}

function switchChatSession(sessionId) {
    chatState.sessionId = sessionId;
    loadChatHistory();
    loadChatSessions();
}

function formatMarkdown(text) {
    if (!text) return '';
    let html = escapeHtml(text);

    // Code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code class="language-$1">$2</code></pre>');

    // Inline code
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

    // Italic
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

    // Line breaks
    html = html.replace(/\n/g, '<br>');

    return html;
}

// ═══════════════════════════════════════════

// ═══════════════════════════════════════════
// Data Platform View (view-datasource)
// ═══════════════════════════════════════════

let _dsType = 'static';
let _dsCaseId = null;
let _dsSourceId = null;
let _dsCaseName = '';
let _dsLoadedOnce = false;
let _dsEvidence = null;
let _dsTopologyScene = null;
let _dsToolPlan = null;
let _dsFaultType = '';
let _dsFaultTarget = '';
let _dsCanRestore = false;

function _localDateTimeValue(offsetMinutes = 1) {
    const d = new Date(Date.now() + offsetMinutes * 60000);
    d.setSeconds(0, 0);
    const tz = d.getTimezoneOffset() * 60000;
    return new Date(d.getTime() - tz).toISOString().slice(0, 16);
}

function loadDatasourceView() {
    var radio = document.querySelector(`input[name="ds-type"][value="${_dsType}"]`);
    if (radio) radio.checked = true;
    document.getElementById('ds-static-section').style.display = _dsType === 'static' ? 'block' : 'none';
    document.getElementById('ds-dynamic-section').style.display = _dsType === 'dynamic' ? 'block' : 'none';
    const customSection = document.getElementById('ds-custom-section');
    if (customSection) customSection.style.display = _dsType === 'custom' ? 'block' : 'none';
    wfSetStep(_dsCaseId ? (_dsType === 'dynamic' ? 3 : 2) : 1);
    // Check if Cloud-OpsBench is available
    dsLoadStaticCases().then(function() {
        // If no cases, auto-switch to dynamic
        var tbody = document.getElementById('ds-static-case-body');
        if (!_dsLoadedOnce && tbody && tbody.textContent.indexOf('不可用') >= 0) {
            // Static not available - auto select dynamic
            var dynRadio = document.querySelector('input[name="ds-type"][value="dynamic"]');
            if (dynRadio) { dynRadio.checked = true; dsSwitch('dynamic'); }
        }
        _dsLoadedOnce = true;
    });
}

function dsSwitch(type) {
    _dsType = type;
    _dsCaseId = null;
    _dsSourceId = null;
    _dsCaseName = '';
    _dsFaultType = '';
    _dsFaultTarget = '';
    _dsCanRestore = false;
    document.getElementById('ds-static-section').style.display = type === 'static' ? 'block' : 'none';
    document.getElementById('ds-dynamic-section').style.display = type === 'dynamic' ? 'block' : 'none';
    const customSection = document.getElementById('ds-custom-section');
    if (customSection) customSection.style.display = type === 'custom' ? 'block' : 'none';
    document.getElementById('ds-static-confirm').style.display = 'none';
    document.getElementById('ds-dyn-confirm').style.display = 'none';
    const customConfirm = document.getElementById('ds-custom-confirm');
    if (customConfirm) customConfirm.style.display = 'none';
    document.getElementById('ds-topology-card').style.display = 'none';
    const toolPlanCard = document.getElementById('ds-tool-plan-card');
    if (toolPlanCard) toolPlanCard.style.display = 'none';
    _dsToolPlan = null;
    const evidenceCard = document.getElementById('ds-evidence-card');
    if (evidenceCard) evidenceCard.style.display = 'none';
    // Update workflow bar: step 1 complete, step 2 active
    wfSetStep(2);
    if (type === 'static') dsLoadStaticCases();
    if (type === 'dynamic') dsResetDynamic();
    if (type === 'custom') dsFillCustomExample(false);
}

function wfSetStep(step) {
    for (let i = 1; i <= 4; i++) {
        const el = document.getElementById('wf-step-' + ['source','inject','analyze','result'][i-1]);
        if (!el) continue;
        el.classList.remove('active', 'completed');
        if (i < step) el.classList.add('completed');
        if (i === step) el.classList.add('active');
    }
    // Update separators
    document.querySelectorAll('.workflow-bar-separator').forEach((sep, idx) => {
        sep.classList.toggle('completed', idx + 1 < step);
    });
}

async function dsLoadStaticCases() {
    const tbody = document.getElementById('ds-static-case-body');
    const search = document.getElementById('ds-static-search')?.value?.trim() || '';
    const data = await api('/api/cloudopsbench/cases?limit=100' + (search ? '&search=' + encodeURIComponent(search) : ''));
    if (!data || data.error) {
        tbody.innerHTML = `<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--danger);">Cloud-OpsBench 不可用<br><small>${escapeHtml(data?.error || '请检查数据目录和依赖环境')}</small></td></tr>`;
        return;
    }
    const cases = data?.cases || [];
    tbody.innerHTML = cases.length === 0 
        ? '<tr><td colspan="3" style="text-align:center;padding:20px;color:var(--text-muted);">暂无案例，请使用「动态数据」模式</td></tr>'
        : cases.map(c => 
        `<tr class="${c.ref === _dsCaseId ? 'cloudops-case-active' : ''}" data-ds-case-ref="${escapeHtml(c.ref)}" data-ds-case-name="${escapeHtml(c.query || c.ref)}">
            <td><button class="cloudops-case-button" type="button"><strong>${escapeHtml(c.ref)}</strong></button></td>
            <td>${escapeHtml((c.query || '-').substring(0, 60))}</td>
            <td><button class="btn btn-sm" type="button">选择</button></td>
        </tr>`
    ).join('') || '<tr><td colspan="3" class="text-muted">无案例</td></tr>';

    tbody.querySelectorAll('tr[data-ds-case-ref]').forEach(row => {
        row.addEventListener('click', () => {
            dsSelectStatic(row.dataset.dsCaseRef || '', row.dataset.dsCaseName || '');
        });
    });
}

async function dsSelectStatic(ref, name) {
    if (!ref) return;
    _dsType = 'static';
    _dsCaseId = ref;
    _dsCaseName = name || ref;
    _dsSourceId = 'cloud-opsbench';
    _dsFaultType = '';
    _dsFaultTarget = '';
    _dsCanRestore = false;
    document.getElementById('ds-static-selected-name').textContent = _dsCaseName;
    document.getElementById('ds-static-confirm').style.display = 'block';
    const confirmDesc = document.getElementById('ds-static-case-desc');
    if (confirmDesc) {
        confirmDesc.textContent = '正在读取 Cloud-OpsBench 快照，生成系统传播可视化、原始证据面板和多 Agent 工具预案...';
    }
    document.querySelectorAll('#ds-static-case-body tr').forEach(row => {
        row.classList.toggle('cloudops-case-active', row.dataset.dsCaseRef === ref);
    });
    const topologyCard = document.getElementById('ds-topology-card');
    const toolPlanCard = document.getElementById('ds-tool-plan-card');
    const evidenceCard = document.getElementById('ds-evidence-card');
    if (topologyCard) topologyCard.style.display = 'none';
    if (toolPlanCard) toolPlanCard.style.display = 'none';
    if (evidenceCard) evidenceCard.style.display = 'none';
    _dsToolPlan = null;
    wfSetStep(2);
    const detail = await api(`/api/cloudopsbench/case/${encodeURIComponent(ref)}`);
    if (detail && !detail.error) {
        _dsCaseName = detail.query || name || ref;
        document.getElementById('ds-static-selected-name').textContent = _dsCaseName;
        const result = detail.result || {};
        const summary = [
            `系统 ${detail.system || '-'}`,
            `类别 ${detail.fault_category || '-'}`,
            `根因标注 ${result.root_cause || result.fault_object || '-'}`,
            `模式 ${detail.platform?.mode || 'snapshot_replay'}`,
        ].join(' · ');
        if (confirmDesc) {
            confirmDesc.textContent = summary + '。下方已生成可视化和工具预案，可进入根因分析。';
        }
    } else if (confirmDesc) {
        confirmDesc.textContent = '案例已选中，但详情读取失败：' + (detail?.error || '未知错误');
    }
    await dsLoadTopology(_dsSourceId, _dsCaseId);
}

function dsResetDynamic() {
    _dsFaultType = '';
    _dsFaultTarget = '';
    _dsCanRestore = false;
    document.getElementById('ds-dyn-platform').value = '';
    document.getElementById('ds-dyn-fault').innerHTML = '<option value="">故障类型</option>';
    document.getElementById('ds-dyn-target').innerHTML = '<option value="">目标服务</option>';
    document.getElementById('ds-dyn-result').style.display = 'none';
    document.getElementById('ds-dyn-confirm').style.display = 'none';
    document.getElementById('ds-topology-card').style.display = 'none';
    const injectBtn = document.getElementById('ds-inject-button');
    if (injectBtn) {
        injectBtn.disabled = false;
        injectBtn.textContent = '💥 注入真实故障';
        injectBtn.title = '';
    }
    const toolPlanCard = document.getElementById('ds-tool-plan-card');
    if (toolPlanCard) toolPlanCard.style.display = 'none';
    _dsToolPlan = null;
    const evidenceCard = document.getElementById('ds-evidence-card');
    if (evidenceCard) evidenceCard.style.display = 'none';
    document.getElementById('ds-dyn-status').textContent = '';
    const clusterDiag = document.getElementById('ds-cluster-diagnostic');
    if (clusterDiag) clusterDiag.style.display = 'none';
    const startInput = document.getElementById('ds-dyn-start-time');
    if (startInput && !startInput.value) startInput.value = _localDateTimeValue(1);
    const durationInput = document.getElementById('ds-dyn-duration');
    const windowInput = document.getElementById('ds-dyn-window');
    const intervalInput = document.getElementById('ds-dyn-interval');
    if (durationInput) durationInput.value = durationInput.value || '180';
    if (windowInput) windowInput.value = windowInput.value || '300';
    if (intervalInput) intervalInput.value = intervalInput.value || '15';
}

async function dsLoadDynInfo() {
    const sourceId = document.getElementById('ds-dyn-platform')?.value;
    if (!sourceId) return;
    const statusEl = document.getElementById('ds-dyn-status');
    statusEl.textContent = '加载中...';
    const info = await api('/api/datasources/' + sourceId + '/info');
    if (!info || info.error) { statusEl.textContent = '不可用: ' + (info?.error || '未知错误'); return; }
    const injectBtn = document.getElementById('ds-inject-button');
    const health = info.health || {};
    const healthy = health.status === 'healthy';
    const healthLabel = healthy ? '可真实注入' : '等待集群配置';
    statusEl.textContent = `${info.injection_capability?.note || '真实注入'} 当前状态: ${healthLabel} · ${health.message || health.detail || '-'}`;
    if (injectBtn) {
        injectBtn.disabled = !healthy;
        injectBtn.title = healthy ? '执行真实 Kubernetes 故障注入' : (health.detail || health.message || '目标集群未就绪，真实故障注入不可用');
        injectBtn.textContent = healthy ? '💥 注入真实故障' : (health.health_state === 'kubectl_missing' ? '先配置 kubectl' : '集群待配置');
    }
    renderDatasourceClusterDiagnostic(info);
    
    const faults = info.faults || [];
    const fsel = document.getElementById('ds-dyn-fault');
    fsel.innerHTML = '<option value="">故障类型</option>' +
        faults.map(f => `<option value="${f.fault_type || f.case_id}">${escapeHtml(f.case_name || f.description || '')}</option>`).join('');
    
    const tsel = document.getElementById('ds-dyn-target');
    tsel.innerHTML = '<option value="">目标服务</option>';
    if (faults.length) {
        try {
            const cd = await api('/api/datasources/' + sourceId + '/case/' + faults[0].case_id);
            (cd?.service_graph?.services || []).forEach(s => {
                tsel.innerHTML += `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`;
            });
        } catch(e) {
            console.warn('target load failed', e);
        }
    }
}

function renderDatasourceClusterDiagnostic(info) {
    const el = document.getElementById('ds-cluster-diagnostic');
    if (!el) return;
    const health = info?.health || {};
    if (!health || health.status === 'healthy') {
        el.style.display = health.status === 'healthy' ? 'block' : 'none';
        if (health.status === 'healthy') {
            el.className = 'cluster-diagnostic ok';
            el.innerHTML = `
                <div class="cluster-diagnostic-title">真实注入运行时已就绪</div>
                <div class="cluster-diagnostic-grid">
                    <div><span>Context</span><strong>${escapeHtml(health.current_context || '-')}</strong></div>
                    <div><span>Namespace</span><strong>${escapeHtml(health.namespace || '-')}</strong></div>
                    <div><span>kubectl</span><strong>${escapeHtml(health.kubectl_path || '-')}</strong></div>
                </div>
            `;
        }
        return;
    }
    el.style.display = 'block';
    el.className = 'cluster-diagnostic warn';
    const actions = health.action_items || [];
    const envKeys = health.namespace_env_keys || [];
    el.innerHTML = `
        <div class="cluster-diagnostic-title">为什么三个平台都显示不可用？</div>
        <p>${escapeHtml(health.message || '真实注入运行时尚未接入 Kubernetes。')}</p>
        <div class="cluster-diagnostic-grid">
            <div><span>共同原因</span><strong>${escapeHtml(health.health_state || health.status || '-')}</strong></div>
            <div><span>kubectl</span><strong>${escapeHtml(health.kubectl_path || '未找到')}</strong></div>
            <div><span>KUBECONFIG</span><strong>${escapeHtml(health.kubeconfig || '-')}</strong></div>
            <div><span>目标命名空间</span><strong>${escapeHtml(health.namespace || '-')}</strong></div>
        </div>
        ${health.detail ? `<pre>${escapeHtml(health.detail)}</pre>` : ''}
        <div class="cluster-action-list">
            ${actions.map(item => `<span>${escapeHtml(item)}</span>`).join('')}
            ${envKeys.length ? `<span>命名空间可用环境变量: ${escapeHtml(envKeys.join(' / '))}</span>` : ''}
        </div>
        <button class="btn btn-sm" onclick="dsLoadDynInfo()">重新检测</button>
    `;
}

async function dsInjectFault() {
    const sourceId = document.getElementById('ds-dyn-platform').value;
    const faultType = document.getElementById('ds-dyn-fault').value;
    const target = document.getElementById('ds-dyn-target').value;
    const scheduledAt = document.getElementById('ds-dyn-start-time')?.value || '';
    const durationSeconds = parseInt(document.getElementById('ds-dyn-duration')?.value || '180', 10);
    const observationWindowSeconds = parseInt(document.getElementById('ds-dyn-window')?.value || '300', 10);
    const collectionIntervalSeconds = parseInt(document.getElementById('ds-dyn-interval')?.value || '15', 10);
    const injectionMode = document.getElementById('ds-dyn-mode')?.value || 'live_kubernetes_required';
    const resultEl = document.getElementById('ds-dyn-result');
    if (!sourceId || !faultType || !target) {
        resultEl.style.display = 'block';
        resultEl.innerHTML = '请完整选择';
        resultEl.className = 'fault-injection-result error';
        return;
    }
    resultEl.style.display = 'block';
    resultEl.innerHTML = '注入中...';
    resultEl.className = 'fault-injection-result';
    
    const data = await api('/api/datasources/' + sourceId + '/inject', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            fault_type: faultType,
            target: target,
            kwargs: {
                scheduled_at: scheduledAt,
                duration_seconds: durationSeconds,
                observation_window_seconds: observationWindowSeconds,
                collection_interval_seconds: collectionIntervalSeconds,
                injection_mode: injectionMode,
            },
        }),
    });
    if (!data || data.error || data.status === 'error') {
        const errText = data?.message || data?.error || '未知';
        const restoreHint = /0 副本|没有恢复|未恢复|no Ready Pod|没有 Ready Pod/i.test(errText)
            ? '检测到目标服务可能仍处于上一次真实故障注入后的未恢复状态；请先恢复该服务或按错误里的 kubectl scale 命令恢复，再重新注入。'
            : '系统不会生成仿真故障 case；请先连通 kubectl/目标集群后重试。';
        resultEl.innerHTML = '真实注入失败: ' + escapeHtml(errText) + '<br><small>' + escapeHtml(restoreHint) + '</small>';
        resultEl.className = 'fault-injection-result error';
        return;
    }
    _dsCaseId = data.case_id;
    _dsSourceId = sourceId;
    _dsCaseName = faultType + ' → ' + target;
    _dsFaultType = faultType;
    _dsFaultTarget = target;
    _dsCanRestore = true;
    
    const inj = data.fault_injection || {};
    resultEl.innerHTML = `
        <div><strong>注入请求已创建</strong> Case: ${escapeHtml(data.case_id)}</div>
        <div class="fault-injection-meta">
            <span>时间: ${escapeHtml(inj.scheduled_at || scheduledAt || '-')}</span>
            <span>持续: ${escapeHtml(String(inj.duration_seconds || durationSeconds))}s</span>
            <span>观测: ${escapeHtml(String(inj.observation_window_seconds || observationWindowSeconds))}s</span>
            <span>模式: ${escapeHtml(inj.execution_mode || data.status || '-')}</span>
        </div>
        <small>${escapeHtml(inj.honesty_note || data.message || '')}</small>
    `;
    resultEl.className = 'fault-injection-result success';
    wfSetStep(2); // Step 2 complete
    
    // Load topology
    await dsLoadTopology(sourceId, data.case_id);
    
    // Show confirm
    document.getElementById('ds-dyn-case-name').textContent = _dsCaseName;
    document.getElementById('ds-dyn-case-desc').textContent =
        '平台: ' + sourceId + ' | Case: ' + data.case_id + ' | 时间窗: ' +
        (inj.duration_seconds || durationSeconds) + 's / ' + (inj.observation_window_seconds || observationWindowSeconds) + 's';
    document.getElementById('ds-dyn-confirm').style.display = 'block';
    document.getElementById('ds-dyn-status').textContent = '已就绪，可继续注入新故障或进入 RCA';
}

function dsFillCustomExample(force = true) {
    const box = document.getElementById('ds-custom-json');
    if (!box) return;
    if (!force && box.value.trim()) return;
    const root = document.getElementById('ds-custom-root')?.value || 'payment';
    const example = {
        case_name: 'Internal payment timeout fault',
        severity: 'critical',
        root_cause_ground_truth: `${root} is the root cause.`,
        metrics: {
            series_summary: [
                { column: `${root}-error_rate`, service: root, mean: 0.42, std: 0.13, min: 0.01, max: 0.94, range: 0.93 },
                { column: `${root}-latency_p99`, service: root, mean: 2.8, std: 0.7, min: 0.12, max: 7.4, range: 7.28 },
                { column: 'front-end-latency_p99', service: 'front-end', mean: 1.9, std: 0.4, min: 0.08, max: 5.1, range: 5.02 },
            ],
        },
        logs: {
            entries: [
                { timestamp: new Date().toISOString(), service: root, level: 'ERROR', message: 'payment provider timeout after 3000ms' },
                { timestamp: new Date().toISOString(), service: 'front-end', level: 'WARN', message: 'checkout request failed: downstream payment timeout' },
            ],
        },
        traces: {
            spans: [
                { trace_id: 'internal-t-1', span_id: 's1', service: 'front-end', operation: 'POST /checkout', duration_ms: 3850 },
                { trace_id: 'internal-t-1', span_id: 's2', parent_span_id: 's1', service: root, operation: 'charge', duration_ms: 3600 },
            ],
        },
        alerts: {
            alerts: [
                { name: 'CheckoutErrorRateHigh', severity: 'critical', service: 'front-end', message: 'checkout error rate > 35%' },
                { name: 'PaymentLatencyHigh', severity: 'critical', service: root, message: 'p99 latency > 3s' },
            ],
        },
        service_graph: {
            services: ['front-end', 'orders', root, 'shipping', 'mysql'],
            edges: [
                { source: 'front-end', target: 'orders', call_type: 'http' },
                { source: 'orders', target: root, call_type: 'http' },
                { source: 'orders', target: 'shipping', call_type: 'http' },
                { source: root, target: 'mysql', call_type: 'tcp' },
            ],
        },
        enterprise_metadata: {
            origin_system: 'internal-platform',
            note: 'Replace this JSON with your real logs/traces/metrics payload.',
        },
    };
    box.value = JSON.stringify(example, null, 2);
}

function dsFillOtelExample(force = true) {
    const box = document.getElementById('ds-custom-json');
    if (!box) return;
    if (!force && box.value.trim()) return;
    const root = document.getElementById('ds-custom-root')?.value || 'payment';
    const nowNs = String(Date.now()) + '000000';
    const traceId = '4fd0b1a54d6f4b9ab77f1f18e5c2aa31';
    const example = {
        case_id: 'otel-checkout-timeout',
        case_name: 'OTEL checkout timeout from collector',
        severity: 'critical',
        root_cause_ground_truth: `${root} is the root cause.`,
        otel: {
            traces: {
                resourceSpans: [
                    {
                        resource: { attributes: [{ key: 'service.name', value: { stringValue: 'front-end' } }] },
                        scopeSpans: [{
                            scope: { name: 'checkout-api' },
                            spans: [
                                {
                                    traceId,
                                    spanId: '1111111111111111',
                                    name: 'POST /checkout',
                                    kind: 'SPAN_KIND_SERVER',
                                    startTimeUnixNano: nowNs,
                                    endTimeUnixNano: String(Date.now() + 3850) + '000000',
                                    attributes: [
                                        { key: 'http.route', value: { stringValue: '/checkout' } },
                                        { key: 'http.status_code', value: { intValue: '500' } },
                                    ],
                                    status: { code: 'STATUS_CODE_ERROR', message: 'downstream payment timeout' },
                                },
                            ],
                        }],
                    },
                    {
                        resource: { attributes: [{ key: 'service.name', value: { stringValue: root } }] },
                        scopeSpans: [{
                            scope: { name: 'payment-client' },
                            spans: [
                                {
                                    traceId,
                                    spanId: '2222222222222222',
                                    parentSpanId: '1111111111111111',
                                    name: 'POST /charge',
                                    kind: 'SPAN_KIND_CLIENT',
                                    startTimeUnixNano: nowNs,
                                    endTimeUnixNano: String(Date.now() + 3600) + '000000',
                                    attributes: [
                                        { key: 'rpc.system', value: { stringValue: 'http' } },
                                        { key: 'peer.service', value: { stringValue: 'payment-provider' } },
                                    ],
                                    status: { code: 'STATUS_CODE_ERROR', message: 'timeout after 3000ms' },
                                },
                            ],
                        }],
                    },
                ],
            },
            metrics: {
                resourceMetrics: [{
                    resource: { attributes: [{ key: 'service.name', value: { stringValue: root } }] },
                    scopeMetrics: [{
                        scope: { name: 'otel-metrics' },
                        metrics: [
                            {
                                name: 'http.server.request.duration.p99',
                                unit: 'ms',
                                gauge: {
                                    dataPoints: [
                                        { timeUnixNano: nowNs, asDouble: 3800, attributes: [{ key: 'http.route', value: { stringValue: '/charge' } }] },
                                        { timeUnixNano: String(Date.now() + 15000) + '000000', asDouble: 5200, attributes: [{ key: 'http.route', value: { stringValue: '/charge' } }] },
                                    ],
                                },
                            },
                            {
                                name: 'http.server.error_rate',
                                unit: '1',
                                gauge: {
                                    dataPoints: [
                                        { timeUnixNano: nowNs, asDouble: 0.38 },
                                        { timeUnixNano: String(Date.now() + 15000) + '000000', asDouble: 0.47 },
                                    ],
                                },
                            },
                        ],
                    }],
                }],
            },
            logs: {
                resourceLogs: [{
                    resource: { attributes: [{ key: 'service.name', value: { stringValue: root } }] },
                    scopeLogs: [{
                        scope: { name: 'application-log' },
                        logRecords: [
                            {
                                timeUnixNano: nowNs,
                                severityText: 'ERROR',
                                body: { stringValue: 'payment provider timeout after 3000ms' },
                                traceId,
                                spanId: '2222222222222222',
                                attributes: [
                                    { key: 'error.type', value: { stringValue: 'TimeoutError' } },
                                    { key: 'k8s.namespace.name', value: { stringValue: 'production' } },
                                ],
                            },
                        ],
                    }],
                }],
            },
        },
        enterprise_metadata: {
            origin_system: 'opentelemetry-collector',
            collector_pipeline: 'otlp/http json',
        },
    };
    box.value = JSON.stringify(example, null, 2);
}

async function dsLoadCustomSchema() {
    const schemaEl = document.getElementById('ds-custom-schema');
    const data = await api('/api/datasources/custom/schema');
    schemaEl.style.display = 'block';
    schemaEl.textContent = JSON.stringify(data, null, 2);
}

async function dsLoadOtelSchema() {
    const schemaEl = document.getElementById('ds-custom-schema');
    const data = await api('/api/datasources/custom/otel/schema');
    schemaEl.style.display = 'block';
    schemaEl.textContent = JSON.stringify(data, null, 2);
}

async function dsRegisterCustomCase() {
    const statusEl = document.getElementById('ds-custom-status');
    const box = document.getElementById('ds-custom-json');
    const caseId = document.getElementById('ds-custom-case-id')?.value?.trim();
    const root = document.getElementById('ds-custom-root')?.value?.trim();
    statusEl.textContent = '注册中...';
    let payload = {};
    try {
        payload = JSON.parse(box.value || '{}');
    } catch (e) {
        statusEl.textContent = 'JSON 格式错误: ' + e.message;
        return;
    }
    if (caseId) payload.case_id = caseId;
    if (root && !payload.root_cause_ground_truth) payload.root_cause_ground_truth = `${root} is the root cause.`;
    const data = await api('/api/datasources/custom/register_case', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!data || data.error) {
        statusEl.textContent = '注册失败: ' + (data?.error || '未知错误');
        return;
    }
    _dsType = 'custom';
    _dsCaseId = data.case_id;
    _dsSourceId = 'custom-enterprise';
    _dsCaseName = payload.case_name || data.case_id;
    _dsFaultType = '';
    _dsFaultTarget = '';
    _dsCanRestore = false;
    statusEl.textContent = '已注册，可进入 RCA';
    document.getElementById('ds-custom-case-name').textContent = _dsCaseName;
    document.getElementById('ds-custom-case-desc').textContent = `Source: custom-enterprise | Case: ${data.case_id}`;
    document.getElementById('ds-custom-confirm').style.display = 'block';
    await dsLoadTopology(_dsSourceId, _dsCaseId);
}

async function dsRegisterOtelCase() {
    const statusEl = document.getElementById('ds-custom-status');
    const box = document.getElementById('ds-custom-json');
    const caseId = document.getElementById('ds-custom-case-id')?.value?.trim();
    const root = document.getElementById('ds-custom-root')?.value?.trim();
    statusEl.textContent = '注册 OTEL 中...';
    let payload = {};
    try {
        payload = JSON.parse(box.value || '{}');
    } catch (e) {
        statusEl.textContent = 'JSON 格式错误: ' + e.message;
        return;
    }
    if (caseId) payload.case_id = caseId;
    if (root && !payload.root_cause_ground_truth) payload.root_cause_ground_truth = `${root} is the root cause.`;
    const data = await api('/api/datasources/custom/otel/register_case', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!data || data.error) {
        statusEl.textContent = 'OTEL 注册失败: ' + (data?.error || '未知错误');
        return;
    }
    _dsType = 'custom';
    _dsCaseId = data.case_id;
    _dsSourceId = 'custom-enterprise';
    _dsCaseName = payload.case_name || data.case_id;
    _dsFaultType = 'otel_observability_case';
    _dsFaultTarget = root || '';
    _dsCanRestore = false;
    const stats = data.otel_stats || {};
    statusEl.textContent = `OTEL 已注册：${stats.span_count || 0} spans / ${stats.metric_point_count || 0} metric points / ${stats.log_count || 0} logs`;
    document.getElementById('ds-custom-case-name').textContent = _dsCaseName;
    document.getElementById('ds-custom-case-desc').textContent =
        `Source: custom-enterprise | Case: ${data.case_id} | OTEL: ${stats.service_count || 0} services`;
    document.getElementById('ds-custom-confirm').style.display = 'block';
    await dsLoadTopology(_dsSourceId, _dsCaseId);
}

async function dsLoadTopology(sourceId, caseId) {
    try {
        const topologyCard = document.getElementById('ds-topology-card');
        const topologyCanvas = document.getElementById('ds-topology-svg');
        const topologyLegend = document.getElementById('ds-topology-legend');
        if (topologyCard) topologyCard.style.display = 'block';
        if (topologyCanvas) topologyCanvas.innerHTML = '<div class="tool-plan-loading">正在生成 3D 系统拓扑与故障传播视图...</div>';
        if (topologyLegend) topologyLegend.innerHTML = '<span class="text-muted">读取服务、依赖边和根因标注中...</span>';
        const detail = await api('/api/datasources/' + sourceId + '/case/' + encodeURIComponent(caseId) + '/topology');
        if (!detail || detail.error) throw new Error(detail?.error || 'topology unavailable');
        const services = detail?.services || [];
        const edges = detail?.edges || [];
        const rootSvc = detail?.root_service || '';
        const affected = new Set(detail?.affected_services || []);
        
        renderTopology3D(detail, services, edges, rootSvc, affected);
        document.getElementById('ds-topology-card').style.display = 'block';
        try {
            await dsLoadToolPlan(sourceId, caseId);
        } catch (planError) {
            console.warn('Tool plan load failed:', planError);
            const content = document.getElementById('ds-tool-plan-content');
            const card = document.getElementById('ds-tool-plan-card');
            if (card && content) {
                card.style.display = 'block';
                content.innerHTML = `<div class="rca-candidate-item danger">工具预案生成失败：${escapeHtml(planError.message || String(planError))}</div>`;
            }
        }
        await dsLoadEvidence(sourceId, caseId);
    } catch(e) {
        console.log('Topology load failed:', e);
        const topologyCard = document.getElementById('ds-topology-card');
        const topologyCanvas = document.getElementById('ds-topology-svg');
        const topologyLegend = document.getElementById('ds-topology-legend');
        if (topologyCard) topologyCard.style.display = 'block';
        if (topologyCanvas) {
            topologyCanvas.innerHTML = `<div class="collection-errors">可视化加载失败：${escapeHtml(e.message || String(e))}</div>`;
        }
        if (topologyLegend) topologyLegend.innerHTML = '<span class="text-muted">请确认数据源案例路径有效。</span>';
    }
}

async function dsLoadToolPlan(sourceId, caseId) {
    const card = document.getElementById('ds-tool-plan-card');
    const content = document.getElementById('ds-tool-plan-content');
    if (!card || !content) return;
    card.style.display = 'block';
    content.innerHTML = '<div class="tool-plan-loading">多 Agent 正在读取故障数据、记忆和工具收益，规划本轮工具调用...</div>';
    _dsToolPlan = null;
    const data = await api('/api/multiagent/tool-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_id: sourceId, case_id: caseId, run_tools: null }),
    });
    if (!data || data.error) {
        content.innerHTML = `<div class="rca-candidate-item danger">工具预案生成失败：${escapeHtml(data?.error || '未知错误')}</div>`;
        return;
    }
    _dsToolPlan = data;
    const ordered = data.ordered_plan || [];
    const catalog = data.available_tool_catalog || [
        ...ordered.map(item => ({ ...item, selected: true, kind: 'built_in', executable: true })),
        ...(data.skipped_tools || []).map(item => ({ ...item, selected: false, kind: 'built_in', executable: true })),
    ];
    const budget = data.context_contract?.budget || {};
    content.innerHTML = `
        <div class="tool-plan-menu-shell">
            <div class="tool-plan-menu-head">
                <div>
                    <strong>工具池 ${data.available_tool_count ?? catalog.length} 个</strong>
                    <span>${ordered.length} 个本轮调用 · ${(data.enterprise_tool_count || 0)} 个企业工具 · ${escapeHtml(data.framework || 'vendored_langchain_aiops_rca')}</span>
                </div>
                <button class="tool-plan-add-btn" type="button" onclick="dsToggleEnterpriseToolForm()" title="接入企业内部已有工具">+</button>
            </div>
            <div id="ds-enterprise-tool-form" class="enterprise-tool-form" style="display:none;">
                <div class="enterprise-tool-fields">
                    <input id="ds-enterprise-tool-name" class="input-sm" placeholder="工具名称，例如 Internal Trace Summarizer">
                    <input id="ds-enterprise-tool-endpoint" class="input-sm" placeholder="Endpoint / 调用标识">
                    <input id="ds-enterprise-tool-modalities" class="input-sm" placeholder="输入模态：logs,traces,metrics">
                </div>
                <textarea id="ds-enterprise-tool-desc" class="custom-json-input small" placeholder="工具说明、触发条件、输出约定"></textarea>
                <div class="filter-row" style="margin-top:10px;">
                    <button class="btn btn-sm btn-primary" onclick="dsRegisterEnterpriseTool()">接入并重新评估</button>
                    <button class="btn btn-sm" onclick="dsToggleEnterpriseToolForm(false)">取消</button>
                    <span id="ds-enterprise-tool-status" class="text-muted"></span>
                </div>
            </div>
            <div class="tool-plan-menu">
                ${catalog.map((item, idx) => {
                    const order = ordered.findIndex(o => o.tool === item.tool) + 1;
                    return `
                    <div class="tool-plan-menu-item ${item.selected ? 'selected' : 'muted'} ${item.kind === 'enterprise' ? 'enterprise' : ''}">
                        <div class="tool-plan-menu-index">${item.selected ? order || idx + 1 : ''}</div>
                        <div class="tool-plan-main">
                            <div class="tool-plan-title">
                                <strong>${escapeHtml(item.tool || '-')}</strong>
                                <span>${item.kind === 'enterprise' ? 'Enterprise' : 'Built-in'} · reward ${Number(item.learned_reward || 0).toFixed(2)}</span>
                            </div>
                            <div><b>${item.selected ? '选择原因' : '未选择原因'}：</b>${escapeHtml(item.reason || '由工具路由 Agent 根据当前数据重新评估。')}</div>
                            <div><b>预期效果：</b>${escapeHtml(item.expected_effect || '')}</div>
                        </div>
                    </div>
                `}).join('')}
            </div>
            <div class="tool-plan-agent-decision">
                <div>
                    <strong>${escapeHtml(data.planner || 'langchain_multiagent_tool_decision_agent')}</strong>
                    <span>raw=${escapeHtml(budget.raw_evidence || '-')} · tool=${escapeHtml(budget.tool_result || '-')} · memory=${escapeHtml(budget.memory || '-')}</span>
                </div>
                <div class="tool-plan-selected-flow">
                    ${ordered.map(item => `
                        <div class="tool-plan-flow-chip">
                            <span>${item.order}</span>
                            <strong>${escapeHtml(item.tool || '-')}</strong>
                        </div>
                    `).join('') || '<span class="text-muted">本轮未选择外部工具，将直接进入模型/规则推理。</span>'}
                </div>
            </div>
        </div>
    `;
}

function dsToggleEnterpriseToolForm(force) {
    const form = document.getElementById('ds-enterprise-tool-form');
    if (!form) return;
    const nextVisible = typeof force === 'boolean' ? force : form.style.display === 'none';
    form.style.display = nextVisible ? 'block' : 'none';
}

async function dsRegisterEnterpriseTool() {
    const statusEl = document.getElementById('ds-enterprise-tool-status');
    const name = document.getElementById('ds-enterprise-tool-name')?.value?.trim();
    const endpoint = document.getElementById('ds-enterprise-tool-endpoint')?.value?.trim();
    const modalities = document.getElementById('ds-enterprise-tool-modalities')?.value?.split(',').map(x => x.trim()).filter(Boolean) || [];
    const desc = document.getElementById('ds-enterprise-tool-desc')?.value?.trim();
    if (!name) {
        if (statusEl) statusEl.textContent = '请填写工具名称';
        return;
    }
    if (statusEl) statusEl.textContent = '接入中...';
    const data = await api('/api/multiagent/tools/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name,
            endpoint,
            description: desc,
            input_modalities: modalities.length ? modalities : ['logs', 'traces', 'metrics'],
            output_contract: 'enterprise evidence summary + top signals + artifact diff',
            trigger_condition: '由多 Agent 根据当前数据模态、历史收益和人工确认触发',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '接入失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = '已接入，正在重新评估...';
    if (_dsSourceId && _dsCaseId) await dsLoadToolPlan(_dsSourceId, _dsCaseId);
}

function dsToggleEnterpriseRcaFlow(force) {
    const form = document.getElementById('ds-enterprise-rca-flow');
    if (!form) return;
    const nextVisible = typeof force === 'boolean' ? force : form.style.display === 'none';
    form.style.display = nextVisible ? 'block' : 'none';
}

async function dsRegisterEnterpriseRcaFlow() {
    const statusEl = document.getElementById('ds-enterprise-flow-status');
    const name = document.getElementById('ds-enterprise-flow-name')?.value?.trim();
    const endpoint = document.getElementById('ds-enterprise-flow-endpoint')?.value?.trim();
    const algorithmType = document.getElementById('ds-enterprise-flow-type')?.value?.trim();
    const desc = document.getElementById('ds-enterprise-flow-desc')?.value?.trim();
    if (!name) {
        if (statusEl) statusEl.textContent = '请填写 RCA 算法/流程名称';
        return;
    }
    if (statusEl) statusEl.textContent = '接入中...';
    const data = await api('/api/enterprise-rca/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name,
            endpoint,
            algorithm_type: algorithmType || 'enterprise_rca_flow',
            description: desc,
            input_modalities: ['logs', 'traces', 'metrics', 'topology'],
            trigger_condition: '故障注入后由工具路由智能体结合数据模态、历史收益和人工确认重新评估',
            output_contract: 'Top-K RCA candidates + evidence summary + confidence + remediation notes',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '接入失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = '已接入，正在刷新工具预案...';
    if (_dsSourceId && _dsCaseId) await dsLoadToolPlan(_dsSourceId, _dsCaseId);
}

function renderTopologySVG(services, edges, rootSvc, affectedInput = null) {
    const container = document.getElementById('ds-topology-svg');
    if (!services.length) { container.innerHTML = '<p class="text-muted" style="padding:16px;">无拓扑数据</p>'; return; }
    
    const W = container.clientWidth || 600;
    const H = 400;
    const cx = W / 2, cy = H / 2, R = Math.min(W, H) * 0.35;
    
    // Position nodes in a circle
    const n = services.length;
    const positions = {};
    services.forEach((svc, i) => {
        const angle = (2 * Math.PI * i) / n - Math.PI / 2;
        positions[svc] = { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
    });
    
    // Determine affected services (those connected to root cause)
    const affected = affectedInput || new Set();
    if (rootSvc) {
        edges.forEach(e => {
            if (e.source === rootSvc) affected.add(e.target);
            if (e.target === rootSvc) affected.add(e.source);
        });
    }
    
    // Build SVG
    let svg = `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="服务拓扑图">`;
    
    // Edges
    edges.forEach(e => {
        const s = positions[e.source], t = positions[e.target];
        if (s && t) {
            const isAffected = (e.source === rootSvc || e.target === rootSvc);
            const color = isAffected ? '#f97316' : '#d1d5db';
            const width = isAffected ? 2 : 1;
            svg += `<line x1="${s.x}" y1="${s.y}" x2="${t.x}" y2="${t.y}" stroke="${color}" stroke-width="${width}" opacity="0.6"/>`;
        }
    });
    
    // Nodes
    Object.entries(positions).forEach(([svc, pos]) => {
        let color = '#22c55e', size = 8, stroke = '#16a34a';
        if (svc === rootSvc) { color = '#ef4444'; size = 12; stroke = '#dc2626'; }
        else if (affected.has(svc)) { color = '#f97316'; size = 10; stroke = '#ea580c'; }
        svg += `<circle cx="${pos.x}" cy="${pos.y}" r="${size}" fill="${color}" stroke="${stroke}" stroke-width="2"/>`;
        svg += `<text x="${pos.x}" y="${pos.y - size - 4}" text-anchor="middle" font-size="10" fill="#374151" font-weight="${svc === rootSvc ? 'bold' : 'normal'}">${svc}</text>`;
    });
    
    svg += '</svg>';
    container.innerHTML = svg;
    
    // Legend
    document.getElementById('ds-topology-legend').innerHTML = 
        `<strong>根因:</strong> ${rootSvc || '未知'}<br>` +
        `<strong>受影响:</strong> ${[...affected].join(', ') || '无'}<br>` +
        `<strong>服务总数:</strong> ${n}<br>` +
        `<strong>依赖边:</strong> ${edges.length}`;
}

function renderNativeSystem3D(payload, services, edges, rootSvc, affectedInput = null) {
    const container = document.getElementById('ds-topology-svg');
    if (_dsTopologyScene?.raf) cancelAnimationFrame(_dsTopologyScene.raf);
    container.innerHTML = '';

    const canvas = document.createElement('canvas');
    canvas.className = 'system-3d-canvas';
    const hint = document.createElement('div');
    hint.className = 'topology-drag-hint';
    hint.textContent = '拖拽旋转 · 滚轮缩放 · 动态粒子表示故障传播';
    container.appendChild(canvas);
    container.appendChild(hint);
    const ctx = canvas.getContext('2d');

    const overview = payload.system_overview || {};
    const dimensions = (overview.dimensions || payload.system_layers || []).length
        ? (overview.dimensions || payload.system_layers)
        : [
            { id: 'fault', label: 'Fault Injection Point', health: 0.55, pressure: 45 },
            { id: 'service', label: 'Service Mesh / Calls', health: 0.45, pressure: 55 },
            { id: 'runtime', label: 'Runtime / Kubernetes', health: 0.62, pressure: 38 },
            { id: 'data', label: 'Data / Message Plane', health: 0.72, pressure: 28 },
            { id: 'experience', label: 'User / Business Impact', health: 0.5, pressure: 50 },
        ];
    const frames = payload.propagation_frames || [];
    const affected = affectedInput || new Set(payload.affected_services || []);
    const state3d = {
        rotX: -0.42,
        rotY: 0.58,
        zoom: 1,
        dragging: false,
        lastX: 0,
        lastY: 0,
        tick: 0,
        width: 0,
        height: 0,
    };

    function resize() {
        const dpr = Math.min(window.devicePixelRatio || 1, 2);
        const w = container.clientWidth || 760;
        const h = 540;
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
        canvas.style.width = w + 'px';
        canvas.style.height = h + 'px';
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        state3d.width = w;
        state3d.height = h;
    }

    function rotate(point) {
        let { x, y, z } = point;
        const cy = Math.cos(state3d.rotY), sy = Math.sin(state3d.rotY);
        const cx = Math.cos(state3d.rotX), sx = Math.sin(state3d.rotX);
        const x1 = x * cy - z * sy;
        const z1 = x * sy + z * cy;
        const y1 = y * cx - z1 * sx;
        const z2 = y * sx + z1 * cx;
        return { x: x1, y: y1, z: z2 };
    }

    function project(point) {
        const p = rotate(point);
        const perspective = 520;
        const scale = (perspective / (perspective + p.z)) * state3d.zoom;
        return {
            x: state3d.width / 2 + p.x * scale,
            y: state3d.height / 2 + p.y * scale,
            z: p.z,
            scale,
        };
    }

    function layerColor(health, alpha = 1) {
        if (health < 0.35) return `rgba(220,38,38,${alpha})`;
        if (health < 0.68) return `rgba(249,115,22,${alpha})`;
        return `rgba(15,118,110,${alpha})`;
    }

    function buildTopologyLayout() {
        const serviceList = [...new Set((services || []).filter(Boolean))];
        if (rootSvc && !serviceList.includes(rootSvc)) serviceList.unshift(rootSvc);
        const root = rootSvc && serviceList.includes(rootSvc) ? rootSvc : serviceList[0];
        const downstream = {};
        const upstream = {};
        serviceList.forEach(svc => { downstream[svc] = []; upstream[svc] = []; });
        edges.forEach(edge => {
            if (!edge.source || !edge.target) return;
            if (!downstream[edge.source]) downstream[edge.source] = [];
            if (!upstream[edge.target]) upstream[edge.target] = [];
            downstream[edge.source].push(edge.target);
            upstream[edge.target].push(edge.source);
        });

        const level = {};
        if (root) level[root] = 0;
        function walk(start, graph, step) {
            const queue = [start];
            const seen = new Set([start]);
            while (queue.length) {
                const cur = queue.shift();
                (graph[cur] || []).forEach(next => {
                    if (!serviceList.includes(next) || seen.has(next)) return;
                    seen.add(next);
                    const nextLevel = (level[cur] || 0) + step;
                    if (level[next] == null || Math.abs(nextLevel) < Math.abs(level[next])) {
                        level[next] = nextLevel;
                    }
                    queue.push(next);
                });
            }
        }
        if (root) {
            walk(root, downstream, 1);
            walk(root, upstream, -1);
        }
        serviceList.forEach((svc, idx) => {
            if (level[svc] == null) level[svc] = (idx % 5) - 2;
        });

        const buckets = {};
        serviceList.forEach(svc => {
            const key = String(Math.max(-3, Math.min(4, level[svc])));
            if (!buckets[key]) buckets[key] = [];
            buckets[key].push(svc);
        });
        const positions = {};
        const sortedLevels = Object.keys(buckets).map(Number).sort((a, b) => a - b);
        sortedLevels.forEach(lvl => {
            const bucket = buckets[String(lvl)];
            bucket.forEach((svc, idx) => {
                const n = Math.max(bucket.length, 1);
                const offset = idx - (n - 1) / 2;
                const ring = Math.max(1, Math.ceil(n / 4));
                positions[svc] = {
                    x: lvl * 74,
                    y: -8 + Math.sin(idx * 1.31 + lvl) * 18,
                    z: offset * Math.min(42, Math.max(24, 170 / Math.max(n, 1))) + Math.cos(idx * 0.9) * 10 * ring,
                    level: lvl,
                };
            });
        });
        if (root && positions[root]) {
            positions[root] = { ...positions[root], x: 0, y: -12, z: 0, level: 0 };
        }
        return { positions, root };
    }

    const { positions: servicePositions, root: layoutRoot } = buildTopologyLayout();
    let serviceLabelBoxes = [];

    function drawRing3D(radiusX, radiusZ, y, color, label) {
        const points = [];
        for (let i = 0; i <= 96; i++) {
            const a = (Math.PI * 2 * i) / 96;
            points.push(project({ x: Math.cos(a) * radiusX, y, z: Math.sin(a) * radiusZ }));
        }
        ctx.beginPath();
        points.forEach((p, idx) => {
            if (idx === 0) ctx.moveTo(p.x, p.y);
            else ctx.lineTo(p.x, p.y);
        });
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.1;
        ctx.stroke();
        const lp = project({ x: -radiusX - 8, y, z: -radiusZ * 0.28 });
        drawLabel(label, lp.x, lp.y, 'rgba(226,232,240,0.78)', 'right', 150);
    }

    function drawLabel(text, x, y, color = '#e5eefb', align = 'center', maxWidth = 190, fontSize = 12) {
        ctx.save();
        ctx.font = `700 ${fontSize}px Inter, system-ui, sans-serif`;
        ctx.textAlign = align;
        ctx.textBaseline = 'middle';
        ctx.lineWidth = 4;
        ctx.strokeStyle = 'rgba(7,17,31,0.84)';
        ctx.strokeText(text, x, y, maxWidth - 10);
        ctx.fillStyle = color;
        ctx.fillText(text, x, y, maxWidth - 10);
        ctx.restore();
    }

    function labelBox(text, x, y, align = 'center', maxWidth = 150, fontSize = 11) {
        ctx.save();
        ctx.font = `700 ${fontSize}px Inter, system-ui, sans-serif`;
        const width = Math.min(maxWidth, Math.max(42, ctx.measureText(text).width + 16));
        ctx.restore();
        const left = align === 'right' ? x - width : align === 'left' ? x : x - width / 2;
        return { x: left, y: y - fontSize * 0.7 - 5, w: width, h: fontSize + 10 };
    }

    function boxesOverlap(a, b, pad = 3) {
        return !(a.x + a.w + pad < b.x || b.x + b.w + pad < a.x || a.y + a.h + pad < b.y || b.y + b.h + pad < a.y);
    }

    function boxPenalty(box) {
        let penalty = 0;
        for (const other of serviceLabelBoxes) {
            if (!boxesOverlap(box, other, 2)) continue;
            const ix = Math.max(0, Math.min(box.x + box.w, other.x + other.w) - Math.max(box.x, other.x));
            const iy = Math.max(0, Math.min(box.y + box.h, other.y + other.h) - Math.max(box.y, other.y));
            penalty += ix * iy + 120;
        }
        if (box.x < 8) penalty += (8 - box.x) * 16;
        if (box.y < 8) penalty += (8 - box.y) * 16;
        if (box.x + box.w > state3d.width - 8) penalty += (box.x + box.w - state3d.width + 8) * 16;
        if (box.y + box.h > state3d.height - 8) penalty += (box.y + box.h - state3d.height + 8) * 16;
        return penalty;
    }

    function clampLabelCandidate(candidate, text, maxWidth, fontSize) {
        let box = labelBox(text, candidate.x, candidate.y, candidate.align, maxWidth, fontSize);
        let x = candidate.x;
        let y = candidate.y;
        if (box.x < 8) x += 8 - box.x;
        if (box.x + box.w > state3d.width - 8) x -= box.x + box.w - state3d.width + 8;
        if (box.y < 8) y += 8 - box.y;
        if (box.y + box.h > state3d.height - 8) y -= box.y + box.h - state3d.height + 8;
        box = labelBox(text, x, y, candidate.align, maxWidth, fontSize);
        return { ...candidate, x, y, box };
    }

    function drawServiceLabel(svc, p, pos, radius, color, fontSize, maxWidth) {
        const text = String(svc);
        const base = Math.max(14, radius + 12);
        const wobble = ((Math.abs(pos.level || 0) + text.length) % 3) * 4;
        const candidates = [
            { x: p.x, y: p.y - base - wobble, align: 'center' },
            { x: p.x, y: p.y + base + wobble, align: 'center' },
            { x: p.x + base + 10, y: p.y - 2, align: 'left' },
            { x: p.x - base - 10, y: p.y - 2, align: 'right' },
            { x: p.x + base + 12, y: p.y - base * 0.8, align: 'left' },
            { x: p.x - base - 12, y: p.y - base * 0.8, align: 'right' },
            { x: p.x + base + 12, y: p.y + base * 0.8, align: 'left' },
            { x: p.x - base - 12, y: p.y + base * 0.8, align: 'right' },
        ].map(c => clampLabelCandidate(c, text, maxWidth, fontSize));

        let chosen = candidates.find(c => boxPenalty(c.box) === 0);
        if (!chosen) {
            chosen = candidates
                .map(c => ({ ...c, penalty: boxPenalty(c.box) }))
                .sort((a, b) => a.penalty - b.penalty)[0];
            let attempts = 0;
            while (attempts < 8 && serviceLabelBoxes.some(b => boxesOverlap(chosen.box, b, 2))) {
                const dir = attempts % 2 === 0 ? 1 : -1;
                const shift = Math.ceil((attempts + 1) / 2) * (fontSize + 9) * dir;
                chosen = clampLabelCandidate({ ...chosen, y: chosen.y + shift }, text, maxWidth, fontSize);
                attempts += 1;
            }
        }

        if (Math.abs(chosen.x - p.x) > 8 || Math.abs(chosen.y - p.y) > base + 6) {
            ctx.beginPath();
            ctx.moveTo(p.x, p.y);
            ctx.lineTo(chosen.x, chosen.y);
            ctx.strokeStyle = 'rgba(148,163,184,0.26)';
            ctx.lineWidth = 0.8;
            ctx.stroke();
        }
        drawLabel(text, chosen.x, chosen.y, color, chosen.align, maxWidth, fontSize);
        serviceLabelBoxes.push(chosen.box);
    }

    function drawServiceNode(item) {
        const { svc, pos, p } = item;
        const isRoot = svc === layoutRoot;
        const isAffected = affected.has(svc);
        const radius = Math.max(2.8, (isRoot ? 6.8 : isAffected ? 5.2 : 3.7) * p.scale);
        const core = isRoot ? '#ef4444' : isAffected ? '#f59e0b' : '#38bdf8';
        const rim = isRoot ? '#fecaca' : isAffected ? '#fed7aa' : '#bae6fd';
        const glow = ctx.createRadialGradient(p.x, p.y, 1, p.x, p.y, radius * (isRoot ? 5.2 : 3.8));
        glow.addColorStop(0, isRoot ? 'rgba(239,68,68,0.28)' : isAffected ? 'rgba(245,158,11,0.2)' : 'rgba(56,189,248,0.12)');
        glow.addColorStop(1, 'rgba(15,23,42,0)');
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius * (isRoot ? 5.2 : 3.8), 0, Math.PI * 2);
        ctx.fillStyle = glow;
        ctx.fill();

        const grad = ctx.createRadialGradient(p.x - radius * 0.35, p.y - radius * 0.45, 1, p.x, p.y, radius);
        grad.addColorStop(0, '#f8fafc');
        grad.addColorStop(0.25, core);
        grad.addColorStop(1, '#0f172a');
        ctx.beginPath();
        ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
        ctx.strokeStyle = rim;
        ctx.lineWidth = isRoot ? 1.5 : 0.9;
        ctx.stroke();

        const podCount = isRoot ? 4 : isAffected ? 3 : 2;
        for (let i = 0; i < podCount; i++) {
            const a = state3d.tick * 1.8 + i * (Math.PI * 2 / podCount);
            const sat = project({
                x: pos.x + Math.cos(a) * (10 + i * 1.5),
                y: pos.y + Math.sin(a * 0.7) * 4,
                z: pos.z + Math.sin(a) * (10 + i),
            });
            ctx.beginPath();
            ctx.arc(sat.x, sat.y, Math.max(1.1, 1.8 * sat.scale), 0, Math.PI * 2);
            ctx.fillStyle = isRoot ? 'rgba(252,165,165,0.9)' : 'rgba(186,230,253,0.72)';
            ctx.fill();
        }

        const labelSize = services.length > 36 ? 8.5 : services.length > 24 ? 9.5 : 10.5;
        const labelColor = isRoot ? '#fecaca' : isAffected ? '#fed7aa' : '#dbeafe';
        const labelWidth = services.length > 36 ? 112 : services.length > 24 ? 132 : 160;
        drawServiceLabel(svc, p, pos, radius, labelColor, labelSize, labelWidth);
    }

    function drawEdge(edge, hot) {
        const s = servicePositions[edge.source], t = servicePositions[edge.target];
        if (!s || !t) return;
        const a = project(s), b = project(t);
        const mid = project({
            x: (s.x + t.x) / 2,
            y: Math.min(s.y, t.y) - (hot ? 28 : 16),
            z: (s.z + t.z) / 2,
        });
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(mid.x, mid.y, b.x, b.y);
        ctx.strokeStyle = hot ? 'rgba(248,113,113,0.72)' : 'rgba(148,163,184,0.28)';
        ctx.lineWidth = hot ? 2 : 1;
        ctx.stroke();

        if (!hot) return;
        for (let i = 0; i < 3; i++) {
            const tt = (state3d.tick * 0.62 + i * 0.33) % 1;
            const x = (1 - tt) * (1 - tt) * a.x + 2 * (1 - tt) * tt * mid.x + tt * tt * b.x;
            const y = (1 - tt) * (1 - tt) * a.y + 2 * (1 - tt) * tt * mid.y + tt * tt * b.y;
            ctx.beginPath();
            ctx.arc(x, y, 2.2, 0, Math.PI * 2);
            ctx.fillStyle = i % 2 ? 'rgba(251,146,60,0.95)' : 'rgba(239,68,68,0.95)';
            ctx.fill();
        }
    }

    function isHotEdge(edge) {
        if (!edge.source || !edge.target) return false;
        return edge.source === layoutRoot || edge.target === layoutRoot || affected.has(edge.source) || affected.has(edge.target);
    }

    function currentPropagationFrame() {
        if (!frames.length) return null;
        return frames[Math.floor(state3d.tick * 1.8) % frames.length];
    }

    function drawImpactLink(from, to, color, phase = 0) {
        const a = project(from);
        const b = project(to);
        const mid = project({
            x: (from.x + to.x) / 2,
            y: Math.min(from.y, to.y) - 54,
            z: (from.z + to.z) / 2,
        });
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.quadraticCurveTo(mid.x, mid.y, b.x, b.y);
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.setLineDash([5, 7]);
        ctx.lineDashOffset = -state3d.tick * 40 - phase;
        ctx.stroke();
        ctx.setLineDash([]);
    }

    function drawPropagationField() {
        const rootPos = servicePositions[layoutRoot];
        if (!rootPos) return;
        const frame = currentPropagationFrame();
        const activeServices = new Set(frame?.active_services || []);
        const impactAnchors = [
            { label: 'Business Impact', id: 'experience', point: { x: -218, y: -76, z: -118 }, color: 'rgba(248,113,113,0.45)' },
            { label: 'Runtime Pressure', id: 'runtime', point: { x: 218, y: 28, z: -82 }, color: 'rgba(251,146,60,0.40)' },
            { label: 'Data Plane', id: 'data', point: { x: 184, y: 78, z: 106 }, color: 'rgba(45,212,191,0.34)' },
        ];
        impactAnchors.forEach((anchor, idx) => {
            drawImpactLink(rootPos, anchor.point, anchor.color, idx * 9);
            const p = project(anchor.point);
            const wave = 11 + ((state3d.tick * 38 + idx * 13) % 32);
            ctx.beginPath();
            ctx.arc(p.x, p.y, wave * p.scale, 0, Math.PI * 2);
            ctx.strokeStyle = anchor.color;
            ctx.lineWidth = 1.1;
            ctx.stroke();
            drawLabel(anchor.label, p.x, p.y - 18, 'rgba(226,232,240,0.78)', 'center', 140, 10);
        });

        Object.entries(servicePositions).forEach(([svc, pos], idx) => {
            if (svc === layoutRoot || (!affected.has(svc) && !activeServices.has(svc))) return;
            drawImpactLink(rootPos, pos, activeServices.has(svc) ? 'rgba(248,113,113,0.38)' : 'rgba(251,146,60,0.28)', idx * 3);
        });
    }

    function drawSystemTelemetryPanel() {
        const frame = currentPropagationFrame();
        const compactPanel = state3d.width < 380;
        const w = Math.min(312, Math.max(230, state3d.width - 36));
        const x = Math.max(18, state3d.width - w - 18);
        const y = 52;
        const rowH = compactPanel ? 46 : 43;
        const visibleDims = dimensions.slice(0, compactPanel ? 3 : 5);
        const h = 78 + visibleDims.length * rowH;
        ctx.save();
        ctx.fillStyle = 'rgba(7,17,31,0.96)';
        ctx.strokeStyle = 'rgba(226,232,240,0.42)';
        ctx.shadowColor = 'rgba(0,0,0,0.42)';
        ctx.shadowBlur = 14;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.roundRect(x, y, w, h, 10);
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.stroke();
        ctx.fillStyle = '#f8fafc';
        ctx.font = '800 12px Inter, system-ui, sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText('System Propagation State', x + 14, y + 20);
        ctx.fillStyle = '#cbd5e1';
        ctx.font = '700 10px Inter, system-ui, sans-serif';
        ctx.fillText(_truncate(frame ? `${frame.label || 'propagating'} · health=${frame.system_health ?? '-'}` : 'waiting for propagation evidence', compactPanel ? 30 : 42), x + 14, y + 41);
        visibleDims.forEach((dim, idx) => {
            const yy = y + 66 + idx * rowH;
            const health = Math.max(0, Math.min(1, _safeNumber(dim.health, 0.6)));
            const pressure = Math.max(0, Math.min(100, _safeNumber(dim.pressure, (1 - health) * 100)));
            ctx.fillStyle = '#e5eefb';
            ctx.font = '700 10px Inter, system-ui, sans-serif';
            ctx.textAlign = 'left';
            ctx.fillText(_truncate(dim.label || dim.id, compactPanel ? 24 : 28), x + 14, yy + 2);
            ctx.fillStyle = '#93c5fd';
            ctx.font = '700 9px Inter, system-ui, sans-serif';
            ctx.fillText(`health ${(health * 100).toFixed(0)}% · pressure ${pressure.toFixed(0)}`, x + 14, yy + 17);
            ctx.fillStyle = 'rgba(148,163,184,0.20)';
            ctx.fillRect(x + 14, yy + 27, w - 28, 7);
            ctx.fillStyle = layerColor(health, 0.9);
            ctx.fillRect(x + 14, yy + 27, (w - 28) * health, 7);
            ctx.textAlign = 'left';
        });
        ctx.restore();
    }

    function drawScene() {
        state3d.tick += 0.012;
        ctx.clearRect(0, 0, state3d.width, state3d.height);
        const bg = ctx.createLinearGradient(0, 0, 0, state3d.height);
        bg.addColorStop(0, '#07111f');
        bg.addColorStop(1, '#0f172a');
        ctx.fillStyle = bg;
        ctx.fillRect(0, 0, state3d.width, state3d.height);

        ctx.strokeStyle = 'rgba(148,163,184,0.12)';
        ctx.lineWidth = 1;
        for (let i = -5; i <= 5; i++) {
            const a = project({ x: i * 42, y: 148, z: -190 });
            const b = project({ x: i * 42, y: 148, z: 190 });
            const c = project({ x: -210, y: 148, z: i * 42 });
            const d = project({ x: 210, y: 148, z: i * 42 });
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y); ctx.stroke();
        }

        const serviceHealth = dimensions.find(x => x.id === 'service')?.health ?? 0.7;
        const runtimeHealth = dimensions.find(x => x.id === 'runtime')?.health ?? 0.7;
        const dataHealth = dimensions.find(x => x.id === 'data')?.health ?? 0.7;
        const expHealth = dimensions.find(x => x.id === 'experience')?.health ?? 0.7;
        drawRing3D(236, 150, -76, layerColor(expHealth, 0.22), 'User / Business');
        drawRing3D(205, 130, -28, layerColor(serviceHealth, 0.28), 'Service Mesh');
        drawRing3D(178, 112, 28, layerColor(runtimeHealth, 0.25), 'Runtime');
        drawRing3D(146, 92, 78, layerColor(dataHealth, 0.22), 'Data Plane');

        drawPropagationField();

        edges
            .map(edge => ({ edge, hot: isHotEdge(edge) }))
            .sort((a, b) => Number(a.hot) - Number(b.hot))
            .forEach(item => drawEdge(item.edge, item.hot));

        const nodeItems = Object.entries(servicePositions)
            .map(([svc, pos]) => ({ svc, pos, p: project(pos) }))
            .sort((a, b) => b.p.z - a.p.z);
        serviceLabelBoxes = [];
        nodeItems.forEach(drawServiceNode);

        const root = servicePositions[layoutRoot];
        if (root) {
            const center = project(root);
            const pulse = 14 + (Math.sin(state3d.tick * 5) + 1) * 24;
            ctx.beginPath();
            ctx.arc(center.x, center.y, pulse * center.scale, 0, Math.PI * 2);
            ctx.strokeStyle = 'rgba(239,68,68,0.26)';
            ctx.lineWidth = 1.6;
            ctx.stroke();
        }

        drawSystemTelemetryPanel();

        _dsTopologyScene.raf = requestAnimationFrame(drawScene);
    }

    canvas.addEventListener('pointerdown', (event) => {
        state3d.dragging = true;
        state3d.lastX = event.clientX;
        state3d.lastY = event.clientY;
        canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', (event) => {
        if (!state3d.dragging) return;
        const dx = event.clientX - state3d.lastX;
        const dy = event.clientY - state3d.lastY;
        state3d.lastX = event.clientX;
        state3d.lastY = event.clientY;
        state3d.rotY += dx * 0.008;
        state3d.rotX = Math.max(-1.2, Math.min(0.55, state3d.rotX + dy * 0.006));
    });
    canvas.addEventListener('pointerup', (event) => {
        state3d.dragging = false;
        try { canvas.releasePointerCapture(event.pointerId); } catch (_) {}
    });
    canvas.addEventListener('wheel', (event) => {
        event.preventDefault();
        state3d.zoom = Math.max(0.62, Math.min(1.65, state3d.zoom + (event.deltaY < 0 ? 0.08 : -0.08)));
    }, { passive: false });
    window.addEventListener('resize', resize, { passive: true });

    resize();
    _dsTopologyScene = { raf: null, renderer: 'native-canvas', resize };
    drawScene();

    const signals = overview.signals || {};
    document.getElementById('ds-topology-legend').innerHTML =
        `<strong>注入点:</strong> ${escapeHtml(layoutRoot || rootSvc || '未知')}<br>` +
        `<strong>系统影响分:</strong> ${overview.impact_score ?? '-'}<br>` +
        `<strong>系统层:</strong><br>${dimensions.slice(0, 5).map(d => `${escapeHtml(d.label || d.id)} · health=${Math.round(_safeNumber(d.health, 0) * 100)}% · pressure=${Math.round(_safeNumber(d.pressure, 0))}`).join('<br>')}<br>` +
        `<strong>证据:</strong> log=${signals.log_count || 0}, trace=${signals.trace_count || 0}, metric=${signals.metric_count || 0}, alert=${signals.alert_count || 0}<br>` +
        `<strong>传播帧:</strong><br>${frames.map(f => `${escapeHtml(f.label)} · health=${f.system_health} · active=${escapeHtml((f.active_services || []).join(', ') || '-')}`).join('<br>') || '暂无传播帧'}<br>` +
        `<strong>服务名称清单:</strong><div class="service-name-list">${services.map((svc, idx) => `<span>${idx + 1}. ${escapeHtml(svc)}</span>`).join('')}</div>` +
        `<small>主画面基于服务依赖拓扑，但同时渲染业务影响、运行时压力、数据平面和传播阶段；每个服务节点都会显示名称。</small>`;
}

function renderTopology3D(payload, services, edges, rootSvc, affectedInput = null) {
    renderNativeSystem3D(payload, services, edges, rootSvc, affectedInput);
}

async function dsLoadEvidence(sourceId, caseId) {
    const data = await api('/api/datasources/' + sourceId + '/case/' + encodeURIComponent(caseId) + '/evidence');
    if (!data || data.error) {
        const card = document.getElementById('ds-evidence-card');
        const content = document.getElementById('ds-evidence-content');
        if (card && content) {
            card.style.display = 'block';
            content.textContent = '原始证据加载失败：' + (data?.error || '未知错误');
        }
        return;
    }
    _dsEvidence = data;
    document.getElementById('ds-evidence-card').style.display = 'block';
    dsShowModality('logs');
}

function dsShowModality(modality) {
    document.querySelectorAll('.modality-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.modality === modality));
    const el = document.getElementById('ds-evidence-content');
    const raw = _dsEvidence?.raw || {};
    let data = raw[modality];
    if (modality === 'metrics') data = raw.metrics || {};
    el.textContent = JSON.stringify(data || [], null, 2);
}

function dsPrepareDiagnosisGlobals(type) {
    if (!_dsCaseId) { alert('请先选择数据'); return; }
    const previousCaseKey = window._rcaCaseKey || '';
    const nextCaseKey = `${_dsSourceId || ''}::${_dsCaseId || ''}`;
    wfSetStep(3);
    window._rcaSourceId = _dsSourceId;
    window._rcaCaseId = _dsCaseId;
    window._rcaCaseKey = nextCaseKey;
    window._rcaCaseName = _dsCaseName;
    window._rcaSourceType = type;
    window._rcaToolPlan = _dsToolPlan;
    window._rcaPlannedTools = Array.isArray(_dsToolPlan?.selected_tools) ? _dsToolPlan.selected_tools : null;
    window._rcaFaultType = _dsFaultType;
    window._rcaFaultTarget = _dsFaultTarget;
    window._rcaCanRestore = !!(_dsCanRestore && type === 'dynamic');
    window._faultRestored = false;
    window._faultRestoreResult = null;
    if (previousCaseKey !== nextCaseKey) {
        window._rcaFullResult = null;
        window._rcaBackendPromise = null;
        window._rcaProgressiveRunning = false;
        window._hermesRcaResult = null;
        window._enterpriseSelectedFlowId = '';
        window._enterpriseSelectedFlow = null;
        if (typeof resetHermesStepState === 'function') resetHermesStepState();
    }
    return true;
}

function dsConfirmAndGo(type) {
    if (!dsPrepareDiagnosisGlobals(type)) return;
    window._diagnosisMode = 'rca_hub';
    window._rcaSelectedPath = '';
    switchView('rca');
    setTimeout(() => loadRcaHubView(), 120);
}

function dsConfirmAndOpenHermes(type) {
    if (!dsPrepareDiagnosisGlobals(type)) return;
    window._diagnosisMode = 'hermes';
    window._rcaSelectedPath = 'hermes';
    switchView('rca');
    setTimeout(() => rcaSelectPath('hermes'), 120);
}

// ═══════════════════════════════════════════
// Multi-Agent Diagnosis View (view-multiagent)
// ═══════════════════════════════════════════

async function ensureMultiagentPlan() {
    if (window._rcaToolPlan?.agent_workflow && window._rcaCaseId) return window._rcaToolPlan;
    if (!window._rcaSourceId || !window._rcaCaseId) return null;
    const data = await api('/api/multiagent/tool-plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source_id: window._rcaSourceId,
            case_id: window._rcaCaseId,
            run_tools: Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : null,
        }),
    });
    if (!data || data.error) return data;
    window._rcaToolPlan = data;
    window._rcaPlannedTools = Array.isArray(data.selected_tools) ? data.selected_tools : null;
    _dsToolPlan = data;
    return data;
}

async function loadMultiagentView() {
    const empty = document.getElementById('multiagent-no-data');
    const workbench = document.getElementById('multiagent-workbench');
    if (!empty || !workbench) return;
    if (!window._rcaCaseId) {
        empty.style.display = 'block';
        workbench.style.display = 'none';
        return;
    }
    empty.style.display = 'none';
    workbench.style.display = 'block';
    document.getElementById('multiagent-case-title').textContent = window._rcaCaseName || window._rcaCaseId || '-';
    document.getElementById('multiagent-case-subtitle').textContent = '正在装载 LangChain 多智能体工作流...';
    const plan = await ensureMultiagentPlan();
    if (!plan || plan.error) {
        document.getElementById('multiagent-case-subtitle').textContent = '多智能体预案生成失败: ' + (plan?.error || '未知错误');
        return;
    }
    renderMultiagentView(plan);
}

function _safeList(value) {
    if (!value) return [];
    return Array.isArray(value) ? value.filter(v => v != null && v !== '') : [value];
}

function _compactJson(value, limit = 1600) {
    const text = JSON.stringify(value || {}, null, 2);
    return text.length > limit ? text.slice(0, limit) + '\n...' : text;
}

function renderMultiagentView(plan) {
    const workflow = plan.agent_workflow || {};
    const agents = workflow.agents || [];
    const stages = workflow.stages || [];
    const handoffs = workflow.handoffs || [];
    const decision = workflow.tool_decision || {};
    const promptContext = workflow.prompt_context || {};
    const dataReadiness = workflow.data_readiness || {};
    const selectedTools = plan.selected_tools || decision.selected_tools || [];
    const skippedTools = plan.skipped_tools || [];
    const ordered = plan.ordered_plan || [];
    const caseName = workflow.case_name || plan.case_name || window._rcaCaseName || window._rcaCaseId || '-';

    document.getElementById('multiagent-case-title').textContent = caseName;
    document.getElementById('multiagent-case-subtitle').textContent =
        `${escapeHtml(workflow.process || 'fault_injection_to_multiagent_rca')} · LangChain Multi-Agent · ${escapeHtml(workflow.langchain_repo_path || plan.langchain_repo_path || '')}`;
    document.getElementById('multiagent-framework').textContent = plan.framework || workflow.framework || 'langchain';

    const metrics = [
        { label: '可用工具', value: plan.available_tool_count ?? decision.available_tool_count ?? 0, note: `${plan.enterprise_tool_count || 0} 个企业工具` },
        { label: '本轮选择', value: selectedTools.length, note: selectedTools.join(' / ') || '等待工具决策' },
        { label: 'Prompt 版本', value: 'v' + (promptContext.prompt_version || 1), note: promptContext.active_template?.name || 'langchain_rca_contrastive_json_v1' },
        { label: '记忆命中', value: (promptContext.memory_capsules?.semantic || 0) + (promptContext.memory_capsules?.failures || 0), note: `semantic ${promptContext.memory_capsules?.semantic || 0} / failure ${promptContext.memory_capsules?.failures || 0}` },
        { label: '人工确认门', value: workflow.human_gates?.length || 0, note: '每一步交接前后可确认继续或终止' },
    ];
    document.getElementById('multiagent-metrics').innerHTML = metrics.map(item => `
        <div class="multiagent-metric">
            <span>${escapeHtml(item.label)}</span>
            <strong>${escapeHtml(String(item.value))}</strong>
            <small>${escapeHtml(item.note || '')}</small>
        </div>
    `).join('');

    document.getElementById('multiagent-agent-grid').innerHTML = agents.map(agent => `
        <div class="multiagent-agent-card ${escapeHtml(agent.status || 'planned')}">
            <div class="multiagent-agent-top">
                <strong>${escapeHtml(agent.name || agent.role || agent.id)}</strong>
                <span>${escapeHtml(agent.status || 'planned')}</span>
            </div>
            <div class="multiagent-agent-role">${escapeHtml(agent.role || '')}</div>
            <p>${escapeHtml(agent.objective || agent.goal || '')}</p>
            <div class="agent-chip-row">
                ${_safeList(agent.memory).slice(0, 4).map(x => `<span>${escapeHtml(x)}</span>`).join('')}
                ${_safeList(agent.tools).slice(0, 3).map(x => `<span class="tool">${escapeHtml(x)}</span>`).join('')}
            </div>
        </div>
    `).join('') || '<span class="text-muted">暂无 Agent blueprint</span>';

    document.getElementById('multiagent-stage-rail').innerHTML = stages.map(stage => `
        <div class="multiagent-stage">
            <div class="multiagent-stage-index">${stage.order || '-'}</div>
            <div>
                <div class="multiagent-stage-title">${escapeHtml(stage.title || stage.agent || '-')}</div>
                <div class="multiagent-stage-meta">${escapeHtml(stage.agent || '')} → ${escapeHtml(stage.handoff_to || 'END')} ${stage.human_gate ? '· 需要人工确认' : ''}</div>
                <p>${escapeHtml(stage.action || '')}</p>
                <small>${escapeHtml(stage.input_artifact || '')} → ${escapeHtml(stage.output_artifact || '')}</small>
            </div>
        </div>
    `).join('') || '<span class="text-muted">暂无执行轨道</span>';

    document.getElementById('multiagent-handoffs').innerHTML = handoffs.map(item => `
        <div class="handoff-item">
            <strong>${escapeHtml(item.from || '-')} → ${escapeHtml(item.to || '-')}</strong>
            <span>${escapeHtml(item.contract || '')}</span>
        </div>
    `).join('') || '<span class="text-muted">暂无交接契约</span>';

    const skippedHtml = skippedTools.length ? skippedTools.map(item => `
        <div class="multiagent-skipped-tool">
            <strong>${escapeHtml(item.tool || '-')}</strong>
            <span>${escapeHtml(item.reason || '')}</span>
        </div>
    `).join('') : '<span class="text-muted">暂无跳过工具</span>';
    document.getElementById('multiagent-tool-decision').innerHTML = `
        <div class="multiagent-selected-tools">
            ${ordered.map(item => `
                <div class="tool-plan-flow-chip">
                    <span>${item.order || ''}</span>
                    <strong>${escapeHtml(item.tool || '-')}</strong>
                </div>
            `).join('') || '<span class="text-muted">未选择外部工具</span>'}
        </div>
        <div class="multiagent-plan-reason">${escapeHtml(plan.explanation || '')}</div>
        <h4>未选工具</h4>
        ${skippedHtml}
    `;

    const modality = dataReadiness.modalities || {};
    document.getElementById('multiagent-context-panel').innerHTML = `
        <div class="context-contract-strip">
            <strong>上下文预算</strong>
            <pre>${escapeHtml(_compactJson(promptContext.context_budget || plan.context_contract?.budget || {}, 900))}</pre>
        </div>
        <div class="context-contract-strip">
            <strong>模态摘要</strong>
            <pre>${escapeHtml(_compactJson(modality, 1200))}</pre>
        </div>
        <div class="context-contract-strip">
            <strong>Prompt / 失败反例补丁</strong>
            <pre>${escapeHtml(_compactJson({
                learned_patches: promptContext.learned_patches || [],
                failure_contrast_rules: promptContext.failure_contrast_rules || [],
            }, 1200))}</pre>
        </div>
    `;

    renderMultiagentRuntimeTrace();
    renderMultiagentModelStatus(window._rcaFullResult || null);
}

async function renderMultiagentModelStatus(result = null) {
    const el = document.getElementById('multiagent-model-status');
    if (!el) return;
    const rca = result?.rca_result || {};
    const llmStatus = rca.llm_status || result?.llm_status || {};
    if (result) {
        const used = !!rca.llm_used;
        const fallback = !!rca.fallback_used;
        const model = llmStatus.model || rca.model || result?.llm_status?.model || '-';
        const detail = used
            ? (fallback ? '模型已返回，候选解析不足，最终候选由规则补齐。' : 'Diagnosis Agent 已调用模型并直接生成 Top-K 根因候选。')
            : (llmStatus.error || '本轮没有进入模型调用阶段。');
        el.innerHTML = `
            <div class="multiagent-model-dot ${used ? 'used' : 'miss'}"></div>
            <div>
                <strong>执行结果：${used ? '已使用大模型' : '未使用大模型'} · ${fallback ? '规则补齐候选' : '模型直出候选'}</strong>
                <span>${escapeHtml(detail)}</span>
                <small>模型：${escapeHtml(model)} · Provider：${escapeHtml(llmStatus.provider || result?.llm_status?.provider || 'local')} · Health：${escapeHtml(String((llmStatus.health || result?.llm_status?.health || {}).ok ?? '-'))}</small>
            </div>
        `;
        return;
    }

    el.innerHTML = `
        <div class="multiagent-model-dot waiting"></div>
        <div>
            <strong>模型预检中</strong>
            <span>正在读取当前模型来源；多智能体 RCA 执行时会以 use_llm=true 调用当前模型。</span>
        </div>
    `;
    const info = await api('/api/model/info');
    if (!info || info.error) {
        el.innerHTML = `
            <div class="multiagent-model-dot error"></div>
            <div>
                <strong>模型状态不可读</strong>
                <span>${escapeHtml(info?.error || '无法读取模型来源。')}</span>
                <small>执行 RCA 前建议打开“模型来源”确认本地 Qwen 或用户自带 API 配置。</small>
            </div>
        `;
        return;
    }
    const ready = !!info.reachable;
    el.innerHTML = `
        <div class="multiagent-model-dot ${ready ? 'ready' : 'miss'}"></div>
        <div>
            <strong>当前模型：${escapeHtml(info.provider_label || info.provider || 'local')} · ${ready ? '已就绪' : '未就绪'}</strong>
            <span>多智能体 RCA 的 Diagnosis Agent 会调用该模型；不选择用户 API 时默认走本地 Qwen-0.6B。</span>
            <small>模型：${escapeHtml(info.model || '-')} · Base URL：${escapeHtml(info.base_url || '-')}</small>
        </div>
    `;
}

function renderMultiagentRuntimeTrace() {
    const card = document.getElementById('multiagent-runtime-card');
    const el = document.getElementById('multiagent-runtime-trace');
    if (!card || !el) return;
    const trace = window._rcaFullResult?.agent_execution?.execution_trace
        || window._rcaFullResult?.execution_trace
        || window._rcaFullResult?.rca_agent_task?.execution_trace
        || [];
    if (!trace.length) {
        card.style.display = 'none';
        return;
    }
    card.style.display = 'block';
    el.innerHTML = trace.map((item, idx) => `
        <div class="multiagent-stage">
            <div class="multiagent-stage-index">${idx + 1}</div>
            <div>
                <div class="multiagent-stage-title">${escapeHtml(item.stage || '-')}</div>
                <div class="multiagent-stage-meta">${escapeHtml(item.agent || '')}</div>
                <p>${escapeHtml(item.summary || item.reason || item.change || '')}</p>
            </div>
        </div>
    `).join('');
}

function multiagentConfirmAndRun() {
    if (!window._rcaCaseId) {
        alert('请先选择故障数据');
        return;
    }
    const selected = window._rcaToolPlan?.selected_tools || [];
    window._rcaPlannedTools = Array.isArray(selected) ? selected : window._rcaPlannedTools;
    try { wfSetStep(4); } catch(e) {}
    switchView('rca');
    setTimeout(() => rcaInit(), 160);
}

function updateAgenticModelBadge(data = null) {
    const badge = document.getElementById('agentic-model-badge');
    if (!badge) return;
    badge.classList.remove('used', 'miss');
    if (!data) {
        badge.textContent = '模型: 待执行';
        return;
    }
    const rca = data.rca_result || {};
    const llmStatus = rca.llm_status || data.llm_status || {};
    if (rca.llm_used) {
        badge.textContent = `模型: 已调用 ${llmStatus.model || rca.model || 'LLM'}`;
        badge.classList.add('used');
    } else {
        badge.textContent = `模型: 未调用${llmStatus.error ? ' · ' + llmStatus.error : ''}`;
        badge.classList.add('miss');
    }
}

// ═══════════════════════════════════════════
// Hermes Standalone RCA View
// ═══════════════════════════════════════════

let _hermesVisualTimer = null;
let _hermesVisualIndex = 0;
let _hermesVisualProgress = 0;
let _hermesStepIndex = -1;
let _hermesStepRunning = false;
let _hermesPlaybackTimer = null;

const HERMES_VISUAL_STEPS = [
    { id: 'context', title: '上下文构建', desc: '读取故障窗口、拓扑传播、多模态证据和约束。', code: 'CTX' },
    { id: 'memory', title: '记忆检索', desc: '检索相似案例、失败反例和 Prompt 规则。', code: 'MEM' },
    { id: 'route', title: '工具路由', desc: '根据数据模态和收益选择本轮工具。', code: 'ROUTE' },
    { id: 'tools', title: '工具执行', desc: '生成工具前后数据对比和证据摘要。', code: 'TOOLS' },
    { id: 'reason', title: '根因推理', desc: '综合工具证据、拓扑和记忆生成 Top-K 根因。', code: 'RCA' },
    { id: 'learn', title: '评估学习', desc: '评估命中情况并更新记忆、Prompt 和工具收益。', code: 'LEARN' },
];

function sanitizeHermesText(text) {
    return String(text || '')
        .replace(/SkillClaw\s*/g, 'Hermes ')
        .replace(/技能检索/g, '能力检索')
        .replace(/技能命中/g, '能力命中');
}

function normalizeHermesVisualStages(stages = []) {
    const fromRuntime = (stages || [])
        .filter(stage => stage && (stage.title || stage.id))
        .map((stage, idx) => ({
            id: stage.id || `stage_${idx + 1}`,
            title: sanitizeHermesText(stage.title || stage.id || HERMES_VISUAL_STEPS[idx]?.title),
            desc: sanitizeHermesText(stage.analysis || stage.explanation || HERMES_VISUAL_STEPS[idx]?.desc || ''),
            code: HERMES_VISUAL_STEPS[idx]?.code || String(idx + 1),
        }));
    return fromRuntime.length ? fromRuntime.slice(0, 6) : HERMES_VISUAL_STEPS;
}

function getHermesRuntimeStages(data = {}) {
    const runtimeStages = Array.isArray(data?.stages) ? data.stages.filter(stage => stage && (stage.title || stage.id)) : [];
    if (runtimeStages.length) return runtimeStages;
    return HERMES_VISUAL_STEPS.map((step, idx) => ({
        id: step.id,
        title: step.title,
        analysis: step.desc,
        input_artifact: idx === 0 ? 'fault_case' : HERMES_VISUAL_STEPS[idx - 1].code,
        output_artifact: step.code,
    }));
}

function resetHermesStepState() {
    _hermesStepIndex = -1;
    _hermesStepRunning = false;
    stopHermesVisualRun();
    stopHermesAutoPlayback();
    const resultCard = document.getElementById('hermes-result-card');
    if (resultCard) resultCard.style.display = 'none';
    updateHermesRunButton();
}

function updateHermesRunButton(data = window._hermesRcaResult) {
    const button = document.getElementById('hermes-run-btn');
    if (!button) return;
    if (_hermesStepRunning) {
        button.disabled = true;
        button.textContent = 'Hermes RCA Agent 自动执行中...';
        return;
    }
    button.disabled = false;
    if (!window._rcaCaseId) {
        button.textContent = '执行 Hermes RCA Agent';
        return;
    }
    if (!data) {
        button.textContent = '执行 Hermes RCA Agent';
        return;
    }
    button.textContent = '重新执行 Hermes RCA Agent';
}

function clampHermesProgress(progress) {
    const value = Number(progress);
    if (!Number.isFinite(value)) return 0;
    return Math.max(0, Math.min(100, value));
}

function hermesStageDisplayMs(stage = {}, index = 0) {
    const duration = Number(stage.duration_s || 0);
    const backendMs = Number.isFinite(duration) && duration > 0 ? duration * 1000 : 0;
    const baseline = 1250 + (index % 4) * 160;
    return Math.max(baseline, Math.min(3600, backendMs || baseline));
}

function renderHermesVisual(stages = [], activeIndex = -1, mode = 'idle', progress = 0) {
    const stage = document.getElementById('hermes-agent-stage');
    const stepsEl = document.getElementById('hermes-visual-steps');
    const nameEl = document.getElementById('hermes-visual-step-name');
    const descEl = document.getElementById('hermes-visual-step-desc');
    if (!stage || !stepsEl) return;
    const visualStages = normalizeHermesVisualStages(stages);
    const boundedIndex = activeIndex >= 0 ? Math.min(activeIndex, visualStages.length - 1) : -1;
    const activeProgress = mode === 'done' ? 100 : clampHermesProgress(progress);
    stage.classList.toggle('running', mode === 'running');
    stage.classList.toggle('done', mode === 'done');
    stage.classList.toggle('error', mode === 'error');
    const active = boundedIndex >= 0 ? visualStages[boundedIndex] : null;
    if (nameEl) nameEl.textContent = active ? active.title : '等待诊断';
    if (descEl) descEl.textContent = active ? active.desc : '等待故障案例进入 Hermes 独立诊断流程。';
    const positions = [
        { x: 50, y: 10, z: -80, ry: 0 },
        { x: 78, y: 30, z: 18, ry: -20 },
        { x: 76, y: 66, z: -16, ry: -28 },
        { x: 50, y: 82, z: 46, ry: 0 },
        { x: 22, y: 66, z: -16, ry: 28 },
        { x: 22, y: 30, z: 18, ry: 20 },
    ];
    stepsEl.innerHTML = visualStages.map((item, idx) => {
        const pos = positions[idx % positions.length];
        const state = idx < boundedIndex ? 'completed' : idx === boundedIndex ? 'active' : 'pending';
        const panelProgress = state === 'completed' ? 100 : state === 'active' ? activeProgress : 0;
        return `
            <div class="hermes-step-panel ${state}" style="left:${pos.x}%;top:${pos.y}%;--z:${pos.z}px;--ry:${pos.ry}deg;--progress:${panelProgress}%;">
                <span>${escapeHtml(item.code || String(idx + 1))}</span>
                <strong>${escapeHtml(item.title)}</strong>
                <small>${escapeHtml(item.desc)}</small>
                ${state === 'active' ? `<em>${Math.round(panelProgress)}%</em>` : ''}
            </div>
        `;
    }).join('');
}

function startHermesVisualRun(activeIndex = 0) {
    stopHermesVisualRun();
    _hermesVisualIndex = Math.max(0, Math.min(activeIndex, HERMES_VISUAL_STEPS.length - 1));
    _hermesVisualProgress = 8;
    renderHermesVisual([], _hermesVisualIndex, 'running', _hermesVisualProgress);
    _hermesVisualTimer = window.setInterval(() => {
        _hermesVisualProgress += 8;
        if (_hermesVisualProgress >= 100) {
            _hermesVisualProgress = 10;
            _hermesVisualIndex = Math.min(HERMES_VISUAL_STEPS.length - 1, _hermesVisualIndex + 1);
        }
        renderHermesVisual([], _hermesVisualIndex, 'running', _hermesVisualProgress);
    }, 900);
}

function stopHermesVisualRun() {
    if (_hermesVisualTimer) {
        clearInterval(_hermesVisualTimer);
        _hermesVisualTimer = null;
    }
}

function stopHermesAutoPlayback() {
    if (_hermesPlaybackTimer) {
        clearTimeout(_hermesPlaybackTimer);
        _hermesPlaybackTimer = null;
    }
}

function startHermesAutoPlayback(data) {
    stopHermesAutoPlayback();
    const stages = getHermesRuntimeStages(data);
    const status = document.getElementById('hermes-status');
    _hermesStepIndex = -1;
    _hermesStepRunning = true;
    updateHermesRunButton(data);

    const revealNext = () => {
        _hermesStepIndex += 1;
        const final = _hermesStepIndex >= stages.length - 1;
        if (status) status.textContent = final ? '已完成' : `自动执行 ${_hermesStepIndex + 1}/${stages.length}`;
        renderHermesRca(data, _hermesStepIndex);
        if (final) {
            _hermesStepRunning = false;
            _hermesPlaybackTimer = null;
            updateHermesRunButton(data);
            return;
        }
        _hermesPlaybackTimer = window.setTimeout(revealNext, hermesStageDisplayMs(stages[_hermesStepIndex], _hermesStepIndex));
    };

    revealNext();
}

function renderHermesPlaybackStage(stages, activeIndex, waiting = false, progress = 0) {
    const stream = document.getElementById('hermes-stage-stream');
    const status = document.getElementById('hermes-status');
    const visualStages = normalizeHermesVisualStages(stages);
    const bounded = Math.max(0, Math.min(activeIndex, visualStages.length - 1));
    const activeProgress = waiting ? Math.max(96, clampHermesProgress(progress)) : clampHermesProgress(progress);
    renderHermesVisual(visualStages, bounded, 'running', activeProgress);
    if (status) {
        status.textContent = waiting
            ? '等待真实模型/工具结果返回'
            : `自动执行 ${bounded + 1}/${visualStages.length} · ${Math.round(activeProgress)}%`;
    }
    if (!stream) return;
    stream.innerHTML = visualStages.map((stage, idx) => {
        const state = idx < bounded ? 'done' : idx === bounded ? 'active' : 'pending';
        const desc = idx === bounded && waiting
            ? '流程展示已推进到这里，正在等待后端真实 RCA 结果回填。'
            : stage.desc || stage.analysis || '';
        return `
            <div class="hermes-stage ${state}">
                <div class="hermes-stage-index">${idx + 1}</div>
                <div>
                    <strong>${escapeHtml(sanitizeHermesText(stage.title || stage.id || '-'))}</strong>
                    <p>${escapeHtml(sanitizeHermesText(desc))}</p>
                    <span>${idx < bounded ? '已完成 · 100%' : idx === bounded ? `执行中 · ${Math.round(activeProgress)}%` : '等待'} · ${(hermesStageDisplayMs(stage, idx) / 1000).toFixed(1)}s</span>
                </div>
            </div>
        `;
    }).join('');
}

async function animateHermesPlaybackStage(stages, index) {
    const duration = hermesStageDisplayMs(stages[index], index);
    const start = performance.now();
    let progress = 0;
    while (progress < 100) {
        const elapsed = performance.now() - start;
        progress = Math.min(100, Math.round((elapsed / duration) * 100));
        renderHermesPlaybackStage(stages, index, false, progress);
        if (progress >= 100) break;
        await sleep(120);
    }
    renderHermesPlaybackStage(stages, index, false, 100);
    await sleep(180);
}

function loadHermesRcaView() {
    const empty = document.getElementById('hermes-no-data');
    const workbench = document.getElementById('hermes-workbench');
    if (!empty || !workbench) return;
    if (!window._rcaCaseId) {
        empty.style.display = 'block';
        workbench.style.display = 'none';
        return;
    }
    empty.style.display = 'none';
    workbench.style.display = 'block';
    document.getElementById('hermes-case-title').textContent = window._rcaCaseName || window._rcaCaseId || '-';
    document.getElementById('hermes-case-subtitle').textContent =
        `来源 ${window._rcaSourceId || '-'} · Hermes 独立上下文/记忆 + AIOps 工具路由 + RCA 推理`;
    if (window._hermesRcaResult) {
        const stages = getHermesRuntimeStages(window._hermesRcaResult);
        if (_hermesStepIndex < 0) _hermesStepIndex = 0;
        const final = _hermesStepIndex >= stages.length - 1;
        document.getElementById('hermes-status').textContent = final ? '已完成' : `已完成 ${_hermesStepIndex + 1}/${stages.length}`;
        renderHermesRca(window._hermesRcaResult, _hermesStepIndex);
    } else {
        document.getElementById('hermes-status').textContent = '等待执行';
        renderHermesVisual([], -1, 'idle');
        renderFaultRestorePanel('hermes-restore-panel', 'hermes');
    }
    updateHermesRunButton();
    if (window._autoRunHermes) {
        window._autoRunHermes = false;
        setTimeout(() => runHermesRca(), 180);
    }
}

async function runHermesRca() {
    if (!window._rcaCaseId || !window._rcaSourceId) {
        alert('请先在数据平台选择故障案例');
        return;
    }
    const status = document.getElementById('hermes-status');
    const stream = document.getElementById('hermes-stage-stream');
    const resultCard = document.getElementById('hermes-result-card');
    if (_hermesStepRunning) return;

    if (window._hermesRcaResult) {
        window._hermesRcaResult = null;
        resetHermesStepState();
    }

    _hermesStepRunning = true;
    updateHermesRunButton();
    if (status) status.textContent = '运行中';
    stopHermesVisualRun();
    if (resultCard) resultCard.style.display = 'none';
    renderHermesPlaybackStage(HERMES_VISUAL_STEPS, 0, false, 0);
    let backendDone = false;
    let backendData = null;
    let backendError = null;
    const backendPromise = api('/api/hermes-rca/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source_id: window._rcaSourceId,
            case_id: window._rcaCaseId,
            run_tools: Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : null,
            use_llm: true,
        }),
    }).then(data => {
        backendDone = true;
        backendData = data;
        return data;
    }).catch(err => {
        backendDone = true;
        backendError = err;
        return { error: err.message || String(err) };
    });

    const reasonIndex = Math.max(0, HERMES_VISUAL_STEPS.findIndex(stage => stage.id === 'reason'));
    for (let i = 0; i <= reasonIndex; i++) {
        await animateHermesPlaybackStage(HERMES_VISUAL_STEPS, i);
    }
    if (!backendDone) {
        renderHermesPlaybackStage(HERMES_VISUAL_STEPS, reasonIndex, true, 96);
    }
    const data = backendData || await backendPromise;
    if (!data || data.error) {
        if (status) status.textContent = '失败';
        _hermesStepRunning = false;
        updateHermesRunButton();
        stopHermesVisualRun();
        renderHermesVisual([{ title: '执行失败', analysis: data?.error || backendError?.message || 'Hermes RCA 执行失败', id: 'error' }], 0, 'error');
        if (stream) stream.innerHTML = `<div class="collection-errors">${escapeHtml(data?.error || backendError?.message || 'Hermes RCA 执行失败')}</div>`;
        return;
    }
    for (let i = reasonIndex + 1; i < HERMES_VISUAL_STEPS.length; i++) {
        await animateHermesPlaybackStage(HERMES_VISUAL_STEPS, i);
    }
    window._hermesRcaResult = data;
    if (status) status.textContent = '已完成';
    stopHermesVisualRun();
    _hermesStepRunning = false;
    _hermesStepIndex = getHermesRuntimeStages(data).length - 1;
    renderHermesRca(data, _hermesStepIndex);
    updateHermesRunButton(data);
}

function diagnosticReportButton(runId, label = '下载 PDF 运维诊断文档') {
    if (!runId) return '';
    return `
        <div class="diagnostic-report-actions">
            <a class="btn btn-primary" href="/api/rca/${encodeURIComponent(runId)}/diagnostic-report" target="_blank" rel="noopener">${escapeHtml(label)}</a>
        </div>
    `;
}

function renderHermesRca(data, revealIndex = Infinity) {
    const stages = getHermesRuntimeStages(data);
    const boundedReveal = stages.length
        ? Math.max(0, Math.min(Number.isFinite(revealIndex) ? revealIndex : stages.length - 1, stages.length - 1))
        : -1;
    const visibleStages = boundedReveal >= 0 ? stages.slice(0, boundedReveal + 1) : [];
    const finalStage = stages.length ? boundedReveal >= stages.length - 1 : true;
    const stream = document.getElementById('hermes-stage-stream');
    renderHermesVisual(stages, boundedReveal >= 0 ? boundedReveal : -1, finalStage ? 'done' : 'running', finalStage ? 100 : 100);
    if (stream) {
        stream.innerHTML = visibleStages.map((stage, idx) => `
            <div class="hermes-stage ${idx === visibleStages.length - 1 && !finalStage ? 'active' : 'done'}">
                <div class="hermes-stage-index">${idx + 1}</div>
                <div>
                    <strong>${escapeHtml(sanitizeHermesText(stage.title || stage.id || '-'))}</strong>
                    <p>${escapeHtml(sanitizeHermesText(stage.analysis || ''))}</p>
                    <span>${escapeHtml(stage.input_artifact || '-')} → ${escapeHtml(stage.output_artifact || '-')} · ${(
                        hermesStageDisplayMs(stage, idx) / 1000
                    ).toFixed(1)}s</span>
                </div>
            </div>
        `).join('') + (!finalStage ? `
            <div class="hermes-stage pending">
                <div class="hermes-stage-index">${visibleStages.length + 1}</div>
                <div>
                    <strong>自动进入下一流程</strong>
                    <p>下一步：${escapeHtml(sanitizeHermesText(stages[boundedReveal + 1]?.title || '下一流程'))}</p>
                    <span>Hermes 会自动跑完整条 RCA 流程，并逐段展开证据与结果。</span>
                </div>
            </div>
        ` : '') || '<div class="text-muted">暂无 Hermes 执行轨迹</div>';
    }

    const skillPanel = document.getElementById('hermes-skill-panel');
    const memory = data.hermes_context?.memory_capsules || {};
    const reachedMemory = visibleStages.some(stage => /memory|mem|记忆/i.test(`${stage.id || ''} ${stage.title || ''}`)) || boundedReveal >= 1;
    const reachedLearning = finalStage || visibleStages.some(stage => /learn|eval|学习|评估/i.test(`${stage.id || ''} ${stage.title || ''}`));
    if (skillPanel) {
        skillPanel.innerHTML = `
            <div class="context-contract-strip">
                <strong>上下文策略</strong>
                <pre>${escapeHtml(_compactJson({
                    role: 'Hermes RCA diagnostician',
                    protected_context: ['fault injection window', 'topology propagation', 'tool evidence', 'memory guardrails'],
                    output_contract: 'ranked root cause candidates + evidence + uncertainty',
                }, 1200))}</pre>
            </div>
            <div class="context-contract-strip">
                <strong>Hermes 记忆胶囊</strong>
                <pre>${escapeHtml(reachedMemory ? _compactJson({
                    short_term: memory.short_term,
                    semantic_hits: (memory.semantic || []).length,
                    failure_hits: (memory.failures || []).length,
                    prompt_rules: memory.prompt_rules,
                    fencing: memory.fencing,
                }, 1400) : '等待“记忆检索”流程执行后展示。')}</pre>
            </div>
            <div class="context-contract-strip">
                <strong>学习补丁</strong>
                <pre>${escapeHtml(reachedLearning ? _compactJson(data.learning_update || {}, 1600) : '等待“评估学习”流程执行后生成。')}</pre>
            </div>
        `;
    }

    const toolPanel = document.getElementById('hermes-tool-panel');
    const selected = data.selected_tools || [];
    const toolPlan = data.tool_plan || [];
    const reachedRoute = visibleStages.some(stage => /route|router|工具路由/i.test(`${stage.id || ''} ${stage.title || ''}`)) || boundedReveal >= 2;
    const reachedTools = visibleStages.some(stage => /^tool_|tools|工具执行/i.test(`${stage.id || ''} ${stage.title || ''}`)) || boundedReveal >= 3;
    const toolStages = visibleStages.filter(s => String(s.id || '').startsWith('tool_') || /工具执行|tools/i.test(`${s.id || ''} ${s.title || ''}`));
    if (toolPanel) {
        toolPanel.innerHTML = `
            <div class="multiagent-selected-tools">
                ${reachedRoute ? (selected.map((tool, idx) => `<div class="tool-plan-flow-chip"><span>${idx + 1}</span><strong>${escapeHtml(tool)}</strong></div>`).join('') || '<span class="text-muted">未选择工具</span>') : '<span class="text-muted">等待“工具路由”流程执行后展示选择结果。</span>'}
            </div>
            <div class="context-contract-strip">
                <strong>工具选择/跳过原因</strong>
                <pre>${escapeHtml(reachedRoute ? _compactJson(toolPlan, 1600) : '等待工具路由输出。')}</pre>
            </div>
            <div class="context-contract-strip">
                <strong>工具处理后的数据长什么样</strong>
                <pre>${escapeHtml(reachedTools ? _compactJson(toolStages.map(s => ({
                    tool_stage: s.title,
                    before: s.output?.data_flow?.before_data?.stage,
                    after: s.output?.data_flow?.after_data?.stage,
                    changed_summary: s.output?.data_flow?.changed_summary,
                    output_sample: s.output?.data_flow?.output_sample,
                })), 1800) : '等待“工具执行”流程执行后展示前后数据对比。')}</pre>
            </div>
        `;
    }

    const resultCard = document.getElementById('hermes-result-card');
    const resultPanel = document.getElementById('hermes-result-panel');
    const rca = data.rca_result || {};
    const evalData = data.evaluation || {};
    const candidates = rca.parsed_candidates || rca.candidates || [];
    if (resultCard) resultCard.style.display = finalStage ? 'block' : 'none';
    if (!finalStage) {
        if (resultPanel) {
            resultPanel.innerHTML = `<div class="failure-learning-empty">已完成 ${visibleStages.length}/${stages.length} 个 Hermes 流程。根因候选会在最后一个流程完成后展示。</div>`;
        }
        const restorePanel = document.getElementById('hermes-restore-panel');
        if (restorePanel) {
            restorePanel.style.display = 'none';
            restorePanel.innerHTML = '';
        }
        updateHermesRunButton(data);
        return;
    }
    if (resultPanel) {
        resultPanel.innerHTML = `
            <div class="agent-candidate-strip">
                ${candidates.slice(0, 5).map(c => `
                    <div class="agent-candidate-chip">
                        <span>#${escapeHtml(String(c.rank || '-'))}</span>
                        <strong>${escapeHtml(c.service || '-')}</strong>
                        <small>${escapeHtml(String(c.score || ''))}</small>
                        <p>${escapeHtml(c.reason || '')}</p>
                    </div>
                `).join('') || '<span class="text-muted">暂无候选</span>'}
            </div>
            <div class="agent-score-grid">
                <div><span>LLM 使用</span><strong>${rca.llm_used ? '已调用' : '未调用'}</strong></div>
                <div><span>候选补齐</span><strong>${rca.fallback_used ? '规则补齐' : '模型直出'}</strong></div>
                <div><span>ACC@1</span><strong>${escapeHtml(String(evalData['ACC@1'] ?? '-'))}</strong></div>
                <div><span>MRR</span><strong>${escapeHtml(String(evalData.MRR ?? '-'))}</strong></div>
            </div>
            <div class="context-contract-strip">
                <strong>LLM 状态</strong>
                <pre>${escapeHtml(_compactJson(rca.llm_status || data.llm_status || {}, 1200))}</pre>
            </div>
            ${diagnosticReportButton(data.run_id)}
        `;
    }
    renderFaultRestorePanel('hermes-restore-panel', 'hermes');
    updateHermesRunButton(data);
}

function canRestoreCurrentFault() {
    return !!(
        window._rcaCanRestore
        && window._rcaSourceType === 'dynamic'
        && window._rcaSourceId
        && window._rcaCaseId
        && !window._faultRestored
    );
}

function renderFaultRestorePanel(panelId, origin) {
    const panel = document.getElementById(panelId);
    if (!panel) return;
    if (!window._rcaCanRestore || window._rcaSourceType !== 'dynamic') {
        panel.style.display = 'none';
        panel.innerHTML = '';
        return;
    }
    panel.style.display = 'block';
    if (window._faultRestored) {
        const verification = window._faultRestoreResult?.verification || {};
        const replicas = verification.replicas || {};
        panel.innerHTML = `
            <div class="fault-restore-card restored">
                <div>
                    <strong>故障已恢复并通过校验</strong>
                    <span>${escapeHtml(window._rcaFaultTarget || '-')} 已完成 Kubernetes 恢复；ready=${escapeHtml(String(replicas.ready ?? '-'))}, available=${escapeHtml(String(replicas.available ?? '-'))}, strategy=${escapeHtml(window._faultRestoreResult?.recovery_strategy || '-')}</span>
                </div>
                <span class="fault-restore-status">Verified</span>
            </div>
            <details class="restore-verification-details">
                <summary>查看恢复校验明细</summary>
                <pre>${escapeHtml(_compactJson(window._faultRestoreResult || {}, 1800))}</pre>
            </details>
        `;
        return;
    }
    panel.innerHTML = `
        <div class="fault-restore-card">
            <div>
                <strong>诊断完成后的故障恢复</strong>
                <span>当前真实注入目标：${escapeHtml(window._rcaSourceId || '-')} / ${escapeHtml(window._rcaFaultTarget || '-')} / ${escapeHtml(window._rcaFaultType || '-')}</span>
            </div>
            <button class="btn btn-danger" onclick="restoreCurrentFault('${escapeHtml(origin || 'diagnosis')}')">恢复故障</button>
            <span class="fault-restore-status" data-restore-origin="${escapeHtml(origin || 'diagnosis')}">等待人工确认</span>
        </div>
    `;
}

async function restoreCurrentFault(origin = 'diagnosis') {
    if (!window._rcaSourceId || !window._rcaCaseId) {
        alert('没有可恢复的动态故障 case');
        return;
    }
    document.querySelectorAll('.fault-restore-status').forEach(el => {
        el.textContent = '恢复中...';
        el.classList.remove('error');
    });
    document.querySelectorAll('.fault-restore-card button').forEach(btn => { btn.disabled = true; });
    const data = await api('/api/datasources/' + encodeURIComponent(window._rcaSourceId) + '/case/' + encodeURIComponent(window._rcaCaseId) + '/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            target: window._rcaFaultTarget || '',
            fault_type: window._rcaFaultType || '',
            origin,
        }),
    });
    if (!data || data.error || data.status === 'error') {
        document.querySelectorAll('.fault-restore-status').forEach(el => {
            el.textContent = '恢复失败: ' + (data?.error || data?.message || '未知错误');
            el.classList.add('error');
        });
        document.querySelectorAll('.fault-restore-card button').forEach(btn => { btn.disabled = false; });
        return;
    }
    if (!data.actual_cluster_recovery && !data.restore_verified) {
        document.querySelectorAll('.fault-restore-status').forEach(el => {
            el.textContent = '恢复未通过校验: ' + (data?.verification?.reason || data?.message || '请检查 Deployment 状态');
            el.classList.add('error');
        });
        document.querySelectorAll('.fault-restore-card button').forEach(btn => { btn.disabled = false; });
        window._faultRestoreResult = data;
        return;
    }
    window._faultRestoreResult = data;
    window._faultRestored = true;
    renderFaultRestorePanel('rca-restore-panel', 'multiagent');
    renderFaultRestorePanel('hermes-restore-panel', 'hermes');
    renderFaultRestorePanel('rca-enterprise-restore-panel', 'enterprise');
    document.getElementById('ds-dyn-status') && (document.getElementById('ds-dyn-status').textContent = '故障已恢复，可继续注入新故障');
}

// ═══════════════════════════════════════════
// Ops Consult and Continuous Guard Views
// ═══════════════════════════════════════════

async function loadOpsConsultView() {
    const badge = document.getElementById('consult-context-badge');
    if (badge) {
        badge.textContent = window._rcaCaseId ? `${window._rcaSourceId || '-'} / ${window._rcaCaseId}` : '未绑定 case';
    }
    const examplesEl = document.getElementById('consult-examples');
    if (examplesEl && !examplesEl.dataset.loaded) {
        const data = await api('/api/ops-consult/examples');
        const examples = data?.examples || [];
        examplesEl.innerHTML = examples.map(text => `
            <button class="consult-example" type="button" onclick="setConsultQuestion('${encodeURIComponent(text)}')">${escapeHtml(text)}</button>
        `).join('');
        examplesEl.dataset.loaded = '1';
    }
}

function setConsultQuestion(encoded) {
    const input = document.getElementById('consult-question');
    if (input) input.value = decodeURIComponent(encoded || '');
}

async function opsConsultAsk() {
    const statusEl = document.getElementById('consult-status');
    const answerEl = document.getElementById('consult-answer');
    const question = document.getElementById('consult-question')?.value?.trim();
    if (!question) {
        if (statusEl) statusEl.textContent = '请输入问题';
        return;
    }
    if (statusEl) statusEl.textContent = '正在读取证据上下文...';
    const data = await api('/api/ops-consult/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            question,
            source_id: window._rcaSourceId || _dsSourceId || '',
            case_id: window._rcaCaseId || _dsCaseId || '',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '问诊失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = '已生成';
    const answer = data.answer || {};
    const summary = answer.case_summary || {};
    if (answerEl) {
        answerEl.innerHTML = `
            <div class="consult-answer-head">
                <strong>${escapeHtml(answer.summary || '')}</strong>
                <span>${escapeHtml(data.created_at || '')}</span>
            </div>
            <div class="consult-evidence-grid">
                <div><span>Case</span><strong>${escapeHtml(summary.case || '-')}</strong></div>
                <div><span>Root Hint</span><strong>${escapeHtml(summary.root_hint || '-')}</strong></div>
                <div><span>Log/Trace/Metric</span><strong>${escapeHtml(JSON.stringify(summary.counts || {}))}</strong></div>
                <div><span>Affected</span><strong>${escapeHtml((summary.affected_services || []).join(' / ') || '-')}</strong></div>
            </div>
            <details class="agent-output-details" open>
                <summary>证据摘要</summary>
                <pre>${escapeHtml(_compactJson(summary, 2200))}</pre>
            </details>
            <div class="consult-next-steps">
                ${(answer.recommended_next_steps || []).map(step => `<span>${escapeHtml(step)}</span>`).join('')}
            </div>
            <p class="text-muted">${escapeHtml(answer.tool_hint || '')}</p>
        `;
    }
}

async function loadContinuousGuardView(highlightPlanId = '') {
    const data = await api('/api/continuous-guard/state');
    const list = document.getElementById('guard-plan-list');
    const summary = document.getElementById('guard-summary');
    const endpointEl = document.getElementById('guard-local-sim-endpoint');
    if (endpointEl) endpointEl.textContent = guardLocalSimulationEndpoint();
    renderGuardTargets(data?.targets || []);
    renderGuardSystem(data?.guard_system || null);
    if (summary) {
        const rt = data?.runtime || {};
        summary.textContent = `active ${rt.active_plans || 0} / draft ${rt.draft_plans || 0} / gates ${rt.human_confirm_gates || 0}`;
    }
    if (!list) return;
    const plans = data?.plans || [];
    list.innerHTML = plans.length ? plans.map(plan => `
        <div class="guard-plan-card ${plan.id === highlightPlanId ? 'just-created' : ''}">
            <div class="guard-plan-top">
                <div>
                    <strong>${escapeHtml(plan.name || plan.id)}</strong>
                    <span>${escapeHtml(plan.objective || '')}</span>
                    ${plan.last_action ? `<small>${escapeHtml(plan.last_action)}</small>` : ''}
                </div>
                <button class="btn btn-sm btn-primary" onclick="guardRunPlan('${escapeHtml(plan.id)}')">执行一次</button>
            </div>
            <div class="guard-plan-meta">
                <span>${escapeHtml(plan.status || 'draft')}</span>
                <span>${escapeHtml(plan.scope || '-')}</span>
                <span>${escapeHtml(plan.cadence || '-')}</span>
                <span>${escapeHtml(plan.risk_level || '-')}</span>
                <span>${plan.human_confirm_required ? '需要人工确认' : '只读巡检'}</span>
            </div>
            <div class="guard-step-strip">
                ${(plan.execution_map || []).map(step => `
                    <div class="guard-step">
                        <strong>${escapeHtml(step.step || '-')}</strong>
                        <span>${escapeHtml(step.purpose || '')}</span>
                        ${step.gate ? '<em>人工确认</em>' : ''}
                    </div>
                `).join('')}
            </div>
            ${(plan.reports || []).length ? `<details class="agent-output-details"><summary>最近报告</summary><pre>${escapeHtml(_compactJson(plan.reports[0], 1200))}</pre></details>` : ''}
        </div>
    `).join('') : '暂无守护计划';
}

function guardDefaultObjective() {
    const selected = document.getElementById('guard-target-scenario');
    const scenarioLabel = selected?.selectedOptions?.[0]?.textContent || '内置可观测模拟系统';
    return `持续巡检 ${scenarioLabel.replace(/^内置可观测模拟：/, '')} 的 SLO、风险传播和 RCA 预热信号`;
}

function guardDefaultScope() {
    const mode = guardSelectedTargetMode();
    if (mode === 'simulated') {
        return document.getElementById('guard-target-scenario')?.value || 'checkout_latency';
    }
    return document.getElementById('guard-target-endpoint')?.value || 'all connected platforms';
}

async function guardCreatePlan() {
    const statusEl = document.getElementById('guard-status');
    const objective = document.getElementById('guard-objective')?.value.trim() || guardDefaultObjective();
    const scope = document.getElementById('guard-scope')?.value.trim() || guardDefaultScope();
    if (statusEl) statusEl.textContent = '生成中：正在写入下方计划列表...';
    const data = await api('/api/continuous-guard/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            objective,
            scope,
            cadence: document.getElementById('guard-cadence')?.value || 'manual',
            risk_level: document.getElementById('guard-risk')?.value || 'medium',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '生成失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = `已生成：${data.plan?.name || data.plan?.id || '守护计划'}，可在下方点击执行一次`;
    await loadContinuousGuardView(data.plan?.id || '');
}

function guardSelectedTargetMode() {
    return document.querySelector('input[name="guard-target-mode"]:checked')?.value || 'real';
}

function guardLocalSimulationEndpoint() {
    const scenario = document.getElementById('guard-target-scenario')?.value || 'checkout_latency';
    return `${window.location.origin}/api/guard-sim/health?scenario=${encodeURIComponent(scenario)}`;
}

function guardToggleTargetMode() {
    const mode = guardSelectedTargetMode();
    const real = document.getElementById('guard-real-target-fields');
    const sim = document.getElementById('guard-sim-target-fields');
    if (real) real.style.display = mode === 'real' ? 'grid' : 'none';
    if (sim) sim.style.display = mode === 'simulated' ? 'block' : 'none';
    const endpointEl = document.getElementById('guard-local-sim-endpoint');
    if (endpointEl) endpointEl.textContent = guardLocalSimulationEndpoint();
}

async function guardRefreshSimulationPreview() {
    const endpointEl = document.getElementById('guard-local-sim-endpoint');
    if (endpointEl) endpointEl.textContent = guardLocalSimulationEndpoint();
    const scenario = document.getElementById('guard-target-scenario')?.value || 'checkout_latency';
    const data = await api('/api/guard-sim/system?scenario=' + encodeURIComponent(scenario));
    if (data && !data.error) renderGuardSystem(data);
}

function renderGuardTargets(targets) {
    const list = document.getElementById('guard-target-list');
    const summary = document.getElementById('guard-target-summary');
    if (summary) summary.textContent = targets.length ? `${targets.length} 个对象` : '未接入';
    if (!list) return;
    list.innerHTML = targets.length ? targets.map(target => {
        const probe = target.last_probe || {};
        const ok = probe.reachable ? 'ok' : 'warn';
        return `
            <div class="guard-target-item ${ok}">
                <div>
                    <strong>${escapeHtml(target.name || target.id)}</strong>
                    <span>${escapeHtml(target.mode || '-')} · ${escapeHtml(target.endpoint || target.scenario || '-')}</span>
                    <small>${escapeHtml(probe.summary || '尚未探测')}</small>
                    ${probe.signals ? `<em>${escapeHtml(JSON.stringify(probe.signals))}</em>` : ''}
                </div>
                <button class="btn btn-sm" onclick="guardProbeTarget('${escapeHtml(target.id)}')">重新探测</button>
            </div>
        `;
    }).join('') : '暂无守护对象。可以接入真实系统端口，也可以先使用内置可观测模拟系统验证持续守护流程。';
}

function renderGuardSystem(system) {
    const nameEl = document.getElementById('guard-system-name');
    const symptomEl = document.getElementById('guard-system-symptom');
    const statusEl = document.getElementById('guard-visual-status');
    const kpiEl = document.getElementById('guard-system-kpis');
    if (!system) {
        if (nameEl) nameEl.textContent = '等待守护对象';
        if (symptomEl) symptomEl.textContent = '接入真实系统端口或内置可观测模拟系统后显示系统巡检沙盘。';
        renderGuard3DFallback(null);
        return;
    }
    if (nameEl) nameEl.textContent = system.name || '内置可观测模拟系统';
    if (symptomEl) symptomEl.textContent = `${system.scenario_name || system.scenario || '-'}：${system.symptom || ''}`;
    if (statusEl) statusEl.textContent = `机器人巡检中 · ${system.status || 'unknown'} · root ${system.root || '-'}`;
    if (kpiEl) {
        const critical = (system.services || []).filter(s => s.status === 'critical').length;
        const degraded = (system.services || []).filter(s => s.status === 'degraded').length;
        kpiEl.innerHTML = `
            <div><span>SLO P95</span><strong>${escapeHtml(String(system.slo?.latency_p95_ms ?? '-'))}ms</strong></div>
            <div><span>Error Budget Burn</span><strong>${escapeHtml(String(system.slo?.error_budget_burn ?? '-'))}x</strong></div>
            <div><span>Critical</span><strong>${critical}</strong></div>
            <div><span>Degraded</span><strong>${degraded}</strong></div>
        `;
    }
    renderGuard3DScene(system);
}

let _guard3D = null;

function disposeThreeObject(obj) {
    if (!obj) return;
    obj.traverse?.(child => {
        if (child.geometry) child.geometry.dispose?.();
        if (child.material) {
            const materials = Array.isArray(child.material) ? child.material : [child.material];
            materials.forEach(mat => {
                mat.map?.dispose?.();
                mat.dispose?.();
            });
        }
    });
}

function makeGuardTextSprite(text, color = '#e2e8f0') {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.82)';
    ctx.strokeStyle = 'rgba(125, 211, 252, 0.55)';
    ctx.lineWidth = 3;
    ctx.roundRect?.(16, 24, 480, 70, 16);
    if (ctx.roundRect) {
        ctx.fill();
        ctx.stroke();
    } else {
        ctx.fillRect(16, 24, 480, 70);
    }
    ctx.font = '700 30px system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const label = String(text || '-');
    ctx.fillText(label.length > 24 ? label.slice(0, 22) + '..' : label, 256, 59);
    const texture = new THREE.CanvasTexture(canvas);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }));
    sprite.scale.set(1.9, 0.48, 1);
    return sprite;
}

function makeGuardMapLabel(text, color = '#e2e8f0') {
    const canvas = document.createElement('canvas');
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.font = '800 34px system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.lineWidth = 8;
    ctx.strokeStyle = 'rgba(2, 6, 23, 0.88)';
    ctx.fillStyle = color;
    const label = String(text || '-');
    const safe = label.length > 22 ? label.slice(0, 20) + '..' : label;
    ctx.strokeText(safe, 256, 64);
    ctx.fillText(safe, 256, 64);
    const texture = new THREE.CanvasTexture(canvas);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthWrite: false }));
    sprite.scale.set(1.35, 0.34, 1);
    return sprite;
}

function createGuardPatrolRobot() {
    const robot = new THREE.Group();
    const blue = new THREE.MeshStandardMaterial({ color: 0x38bdf8, metalness: 0.55, roughness: 0.22, emissive: 0x075985, emissiveIntensity: 0.55 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x0f172a, metalness: 0.6, roughness: 0.28 });
    const white = new THREE.MeshStandardMaterial({ color: 0xe2e8f0, metalness: 0.24, roughness: 0.2 });
    const eyeMat = new THREE.MeshBasicMaterial({ color: 0x67e8f9 });
    const bodyGeometry = THREE.CapsuleGeometry
        ? new THREE.CapsuleGeometry(0.16, 0.28, 8, 16)
        : new THREE.CylinderGeometry(0.17, 0.2, 0.42, 18);
    const body = new THREE.Mesh(bodyGeometry, blue);
    body.position.y = 0.33;
    robot.add(body);
    const head = new THREE.Mesh(new THREE.BoxGeometry(0.34, 0.22, 0.28), white);
    head.position.y = 0.66;
    robot.add(head);
    const face = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.08, 0.02), dark);
    face.position.set(0, 0.67, -0.151);
    robot.add(face);
    [-0.07, 0.07].forEach(x => {
        const eye = new THREE.Mesh(new THREE.SphereGeometry(0.025, 12, 8), eyeMat);
        eye.position.set(x, 0.68, -0.168);
        robot.add(eye);
    });
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.28, 0.12, 24), dark);
    base.position.y = 0.08;
    robot.add(base);
    const antenna = new THREE.Mesh(new THREE.CylinderGeometry(0.01, 0.01, 0.18, 8), white);
    antenna.position.y = 0.86;
    robot.add(antenna);
    const beacon = new THREE.Mesh(new THREE.SphereGeometry(0.045, 16, 8), new THREE.MeshBasicMaterial({ color: 0xf59e0b }));
    beacon.position.y = 0.98;
    beacon.userData.pulse = true;
    beacon.userData.baseScale = 1;
    robot.add(beacon);
    const scanner = new THREE.Mesh(
        new THREE.ConeGeometry(0.78, 1.65, 32, 1, true),
        new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.22, depthWrite: false })
    );
    scanner.name = 'scanner';
    scanner.position.set(0, 0.34, -0.66);
    scanner.rotation.x = Math.PI / 2;
    robot.add(scanner);
    const aura = new THREE.Mesh(
        new THREE.TorusGeometry(0.42, 0.025, 10, 52),
        new THREE.MeshBasicMaterial({ color: 0x67e8f9, transparent: true, opacity: 0.72 })
    );
    aura.name = 'aura';
    aura.rotation.x = Math.PI / 2;
    aura.position.y = 0.08;
    robot.add(aura);
    const nameTag = makeGuardTextSprite('巡检机器人', '#fef3c7');
    nameTag.position.y = 1.34;
    nameTag.scale.set(1.18, 0.3, 1);
    robot.add(nameTag);
    robot.scale.setScalar(2.25);
    robot.userData.kind = 'patrol_robot';
    return robot;
}

function guardPatrolBotMarkup() {
    return `
        <div class="guard-patrol-bot">
            <div class="guard-patrol-bot-body">
                <div class="guard-patrol-bot-head"><i></i><i></i></div>
                <div class="guard-patrol-bot-core"></div>
            </div>
            <span>巡检机器人</span>
        </div>
    `;
}

function ensureGuardPatrolOverlay(container) {
    if (!container || container.querySelector('.guard-patrol-bot')) return;
    const wrapper = document.createElement('div');
    wrapper.innerHTML = guardPatrolBotMarkup().trim();
    container.appendChild(wrapper.firstElementChild);
}

function renderGuard3DScene(system) {
    const container = document.getElementById('guard-visual-3d');
    if (!container || !system || !window.THREE) {
        renderGuard3DFallback(system);
        return;
    }
    const width = Math.max(container.clientWidth || 720, 320);
    const height = Math.max(container.clientHeight || 390, 300);
    container.innerHTML = '';
    if (!_guard3D) {
        const scene = new THREE.Scene();
        scene.fog = new THREE.Fog(0x07111f, 9, 24);
        const camera = new THREE.PerspectiveCamera(48, width / height, 0.1, 100);
        camera.position.set(0, 6.2, 12.4);
        camera.lookAt(1.2, 0.1, 0);
        const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(width, height);
        container.appendChild(renderer.domElement);
        const ambient = new THREE.AmbientLight(0xcbd5e1, 0.88);
        const key = new THREE.PointLight(0x38bdf8, 2.3, 28);
        key.position.set(-4, 7, 6);
        const rim = new THREE.PointLight(0xf59e0b, 1.7, 20);
        rim.position.set(6, 4, -5);
        const group = new THREE.Group();
        scene.add(ambient, key, rim, group);
        _guard3D = { scene, camera, renderer, group, rotationY: -0.22, rotationX: 0.02, dragging: false, lastX: 0, lastY: 0, robot: null, route: null, robotStart: Date.now() };
        renderer.domElement.addEventListener('pointerdown', evt => {
            _guard3D.dragging = true;
            _guard3D.lastX = evt.clientX;
            _guard3D.lastY = evt.clientY;
        });
        renderer.domElement.addEventListener('pointermove', evt => {
            if (!_guard3D.dragging) return;
            const dx = evt.clientX - _guard3D.lastX;
            const dy = evt.clientY - _guard3D.lastY;
            _guard3D.rotationY += dx * 0.008;
            _guard3D.rotationX = Math.max(-0.45, Math.min(0.35, _guard3D.rotationX + dy * 0.004));
            _guard3D.lastX = evt.clientX;
            _guard3D.lastY = evt.clientY;
        });
        window.addEventListener('pointerup', () => { if (_guard3D) _guard3D.dragging = false; });
        const animate = () => {
            if (!_guard3D) return;
            _guard3D.group.rotation.y = _guard3D.rotationY;
            _guard3D.group.rotation.x = _guard3D.rotationX;
            _guard3D.group.children.forEach(child => {
                if (child.userData?.pulse) {
                    child.scale.setScalar((child.userData.baseScale || 1) * (1 + Math.sin(Date.now() / 360) * 0.08));
                }
            });
            if (_guard3D.robot && _guard3D.route) {
                const duration = 18000;
                const t = ((Date.now() - _guard3D.robotStart) % duration) / duration;
                const p = _guard3D.route.getPointAt(t);
                const q = _guard3D.route.getPointAt((t + 0.012) % 1);
                _guard3D.robot.position.copy(p);
                const yaw = Math.atan2(q.x - p.x, q.z - p.z);
                _guard3D.robot.rotation.set(0, yaw + Math.PI, 0);
                const scanner = _guard3D.robot.getObjectByName('scanner');
                if (scanner) scanner.rotation.z = Math.sin(Date.now() / 420) * 0.28;
                const aura = _guard3D.robot.getObjectByName('aura');
                if (aura) aura.scale.setScalar(1 + Math.sin(Date.now() / 300) * 0.16);
            }
            _guard3D.renderer.render(_guard3D.scene, _guard3D.camera);
            _guard3D.frame = requestAnimationFrame(animate);
        };
        animate();
    } else {
        _guard3D.renderer.setSize(width, height);
        _guard3D.camera.aspect = width / height;
        _guard3D.camera.updateProjectionMatrix();
        if (_guard3D.renderer.domElement.parentElement !== container) {
            container.appendChild(_guard3D.renderer.domElement);
        }
    }
    ensureGuardPatrolOverlay(container);
    while (_guard3D.group.children.length) {
        const child = _guard3D.group.children.pop();
        disposeThreeObject(child);
    }
    const services = system.services || [];
    const byId = Object.fromEntries(services.map(s => [s.id, s]));
    const floorY = -1.82;
    const map = new THREE.Mesh(
        new THREE.PlaneGeometry(14.6, 8.6, 1, 1),
        new THREE.MeshStandardMaterial({ color: 0x08111f, roughness: 0.72, metalness: 0.18, transparent: true, opacity: 0.96 })
    );
    map.rotation.x = -Math.PI / 2;
    map.position.y = floorY - 0.06;
    _guard3D.group.add(map);
    const grid = new THREE.GridHelper(14.4, 12, 0x164e63, 0x1e293b);
    grid.position.y = floorY - 0.02;
    grid.material.transparent = true;
    grid.material.opacity = 0.62;
    _guard3D.group.add(grid);
    [
        { name: 'Edge Zone', x: -3.2, z: -2.3, w: 4.8, h: 2.6, color: 0x0ea5e9 },
        { name: 'Service Mesh', x: 0.5, z: 1.2, w: 6.0, h: 3.6, color: 0x22c55e },
        { name: 'Data Plane', x: 4.9, z: -1.5, w: 4.2, h: 2.6, color: 0xf59e0b },
    ].forEach(zone => {
        const plane = new THREE.Mesh(
            new THREE.PlaneGeometry(zone.w, zone.h),
            new THREE.MeshBasicMaterial({ color: zone.color, transparent: true, opacity: 0.075, side: THREE.DoubleSide })
        );
        plane.rotation.x = -Math.PI / 2;
        plane.position.set(zone.x, floorY + 0.01, zone.z);
        _guard3D.group.add(plane);
        const zoneLabel = makeGuardMapLabel(zone.name, '#bae6fd');
        zoneLabel.position.set(zone.x, floorY + 0.04, zone.z - zone.h / 2 + 0.24);
        zoneLabel.rotation.x = -Math.PI / 2;
        _guard3D.group.add(zoneLabel);
    });
    const matByStatus = {
        healthy: new THREE.MeshStandardMaterial({ color: 0x22c55e, metalness: 0.15, roughness: 0.32, emissive: 0x052e16, emissiveIntensity: 0.35 }),
        degraded: new THREE.MeshStandardMaterial({ color: 0xf59e0b, metalness: 0.15, roughness: 0.28, emissive: 0x451a03, emissiveIntensity: 0.55 }),
        critical: new THREE.MeshStandardMaterial({ color: 0xef4444, metalness: 0.2, roughness: 0.22, emissive: 0x7f1d1d, emissiveIntensity: 0.7 }),
    };
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x67e8f9, transparent: true, opacity: 0.42 });
    (system.edges || []).forEach(edge => {
        const a = byId[edge.source]?.position;
        const b = byId[edge.target]?.position;
        if (!a || !b) return;
        const geometry = new THREE.BufferGeometry().setFromPoints([
            new THREE.Vector3(a.x, floorY + 0.055, a.z),
            new THREE.Vector3(b.x, floorY + 0.055, b.z),
        ]);
        const line = new THREE.Line(geometry, edgeMat.clone());
        line.material.opacity = edge.risk_flow > 0.7 ? 0.92 : edge.risk_flow > 0.45 ? 0.62 : 0.34;
        line.material.color = new THREE.Color(edge.risk_flow > 0.7 ? 0xef4444 : edge.risk_flow > 0.45 ? 0xf59e0b : 0x67e8f9);
        _guard3D.group.add(line);
    });
    const serviceLabelOffsets3D = {
        gateway: { x: -0.55, y: 0.18, z: -0.42 },
        user: { x: -0.45, y: 0.1, z: -0.58 },
        catalog: { x: -0.4, y: 0.12, z: 0.55 },
        recommendation: { x: -0.64, y: 0.24, z: -0.72 },
        cart: { x: -0.15, y: 0.16, z: 0.08 },
        checkout: { x: 0.44, y: 0.2, z: 0.58 },
        payment: { x: 0.6, y: 0.16, z: -0.46 },
        inventory: { x: 0.6, y: 0.16, z: 0.66 },
        shipping: { x: 0.5, y: 0.18, z: 0.48 },
        'node-a': { x: -0.55, y: 0.12, z: -0.45 },
        'node-b': { x: 0.55, y: 0.12, z: 0.45 },
        database: { x: 0.7, y: 0.18, z: -0.58 },
    };
    services.forEach(svc => {
        const pos = svc.position || { x: 0, y: 0, z: 0 };
        const risk = Number(svc.risk || 0.18);
        const height = svc.layer === 'infra' ? 0.42 : svc.layer === 'data' ? 0.82 : 0.44 + risk * 1.28;
        const size = svc.layer === 'infra' ? 0.55 : svc.layer === 'data' ? 0.62 : 0.46;
        const geometry = svc.layer === 'infra'
            ? new THREE.BoxGeometry(size * 1.35, height, size * 1.35)
            : svc.layer === 'data'
                ? new THREE.CylinderGeometry(size * 0.62, size * 0.72, height, 22)
                : new THREE.BoxGeometry(size, height, size);
        const mesh = new THREE.Mesh(geometry, (matByStatus[svc.status] || matByStatus.healthy).clone());
        mesh.position.set(pos.x, floorY + height / 2, pos.z);
        mesh.userData.pulse = svc.status === 'critical';
        mesh.userData.baseScale = 1;
        _guard3D.group.add(mesh);
        const beacon = new THREE.Mesh(
            new THREE.SphereGeometry(0.11, 18, 10),
            new THREE.MeshBasicMaterial({ color: svc.status === 'critical' ? 0xffb4a2 : svc.status === 'degraded' ? 0xfde68a : 0xbbf7d0 })
        );
        beacon.position.set(pos.x, floorY + height + 0.16, pos.z);
        beacon.userData.pulse = svc.status !== 'healthy';
        beacon.userData.baseScale = 1;
        _guard3D.group.add(beacon);
        const labelOffset = serviceLabelOffsets3D[svc.id] || { x: 0.42, y: 0.16, z: -0.36 };
        const label = makeGuardTextSprite(svc.name || svc.id, svc.status === 'critical' ? '#fecaca' : svc.status === 'degraded' ? '#fde68a' : '#dcfce7');
        label.position.set(pos.x + labelOffset.x, floorY + height + 0.52 + labelOffset.y, pos.z + labelOffset.z);
        label.scale.set(1.04, 0.27, 1);
        _guard3D.group.add(label);
        if (svc.id === system.root) {
            const ring = new THREE.Mesh(
                new THREE.TorusGeometry(size * 1.75, 0.035, 12, 72),
                new THREE.MeshBasicMaterial({ color: 0xf97316, transparent: true, opacity: 0.88 })
            );
            ring.position.set(pos.x, floorY + 0.1, pos.z);
            ring.rotation.x = Math.PI / 2;
            ring.userData.pulse = true;
            ring.userData.baseScale = 1;
            _guard3D.group.add(ring);
        }
    });
    const patrolIds = ['gateway', 'user', 'catalog', 'recommendation', 'cart', 'checkout', 'payment', 'database', 'inventory', 'shipping', 'node-b', 'node-a'];
    const routePoints = patrolIds
        .map(id => byId[id]?.position)
        .filter(Boolean)
        .map(pos => new THREE.Vector3(pos.x, floorY + 0.46, pos.z));
    if (routePoints.length >= 3) {
        const route = new THREE.CatmullRomCurve3(routePoints, true, 'catmullrom', 0.18);
        const road = new THREE.Mesh(
            new THREE.TubeGeometry(route, 180, 0.045, 8, true),
            new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.62 })
        );
        _guard3D.group.add(road);
        const robot = createGuardPatrolRobot();
        _guard3D.robot = robot;
        _guard3D.route = route;
        _guard3D.robotStart = Date.now();
        _guard3D.group.add(robot);
        const patrolLabel = makeGuardMapLabel('SRE Patrol Bot', '#fef3c7');
        patrolLabel.position.set(-5.35, floorY + 0.3, 3.45);
        _guard3D.group.add(patrolLabel);
    }
}

function renderGuard3DFallback(system) {
    const container = document.getElementById('guard-visual-3d');
    if (!container) return;
    if (!system) {
        container.innerHTML = '<div class="guard-visual-empty">接入守护对象后显示系统巡检沙盘。</div>';
        return;
    }
    const services = system.services || [];
    const byId = Object.fromEntries(services.map(svc => [svc.id, svc]));
    const project = (pos = {}) => ({
        x: Math.max(4, Math.min(96, 8 + ((Number(pos.x || 0) + 5.2) / 12.2) * 84)),
        y: Math.max(5, Math.min(95, 12 + ((Number(pos.z || 0) + 2.5) / 5.4) * 72)),
    });
    const roads = (system.edges || []).map((edge, idx) => {
        const src = byId[edge.source]?.position;
        const dst = byId[edge.target]?.position;
        if (!src || !dst) return '';
        const a = project(src);
        const b = project(dst);
        const state = edge.risk_flow > 0.7 ? 'critical' : edge.risk_flow > 0.45 ? 'degraded' : 'healthy';
        return `<line class="guard-map-road ${state}" x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" style="--delay:${idx * 0.22}s"></line>`;
    }).join('');
    const labelOffsets = {
        gateway: { x: -34, y: -30 },
        user: { x: -48, y: -12 },
        catalog: { x: -42, y: 34 },
        recommendation: { x: -58, y: 54 },
        cart: { x: -18, y: -44 },
        checkout: { x: 42, y: -48 },
        payment: { x: 56, y: -22 },
        inventory: { x: 54, y: 36 },
        shipping: { x: 48, y: 62 },
        'node-a': { x: -58, y: 62 },
        'node-b': { x: 56, y: 54 },
        database: { x: 54, y: -52 },
    };
    const buildings = services.map(svc => {
        const p = project(svc.position);
        const risk = Number(svc.risk || 0.18);
        const height = Math.round(22 + risk * 46 + (svc.layer === 'data' ? 8 : 0));
        return `
            <div class="guard-map-building ${escapeHtml(svc.status || 'healthy')} ${svc.id === system.root ? 'root' : ''}" style="--x:${p.x}%;--y:${p.y}%;--h:${height}px;--risk:${risk.toFixed(2)}">
                <i></i>
            </div>
        `;
    }).join('');
    const offsetScale = (container.clientWidth || 720) < 560 ? 0.48 : 1;
    const labels = services.map(svc => {
        const p = project(svc.position);
        const offset = labelOffsets[svc.id] || { x: 36, y: -28 };
        const layerLabel = { edge: '入口', service: '服务', infra: '基础设施', data: '数据' }[svc.layer] || '服务';
        return `
            <div class="guard-map-service-label ${escapeHtml(svc.status || 'healthy')}" style="--x:${p.x}%;--y:${p.y}%;--ox:${Math.round(offset.x * offsetScale)}px;--oy:${Math.round(offset.y * offsetScale)}px;">
                <strong>${escapeHtml(svc.name || svc.id)}</strong>
                <span>${escapeHtml(layerLabel)} · ${escapeHtml(svc.status || '-')}</span>
            </div>
        `;
    }).join('');
    const rootPoint = project(byId[system.root]?.position || services[0]?.position || {});
    container.innerHTML = `
        <div class="guard-map-sandbox" aria-label="系统巡检沙盘">
            <div class="guard-map-board">
                <div class="guard-map-zone zone-entry"><span>入口区</span></div>
                <div class="guard-map-zone zone-service"><span>服务网格</span></div>
                <div class="guard-map-zone zone-data"><span>数据平面</span></div>
                <svg class="guard-map-road-svg" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
                    ${roads}
                </svg>
                ${buildings}
                <div class="guard-map-root-pulse" style="--x:${rootPoint.x}%;--y:${rootPoint.y}%;"></div>
            </div>
            <div class="guard-map-label-layer">${labels}</div>
        </div>
        ${guardPatrolBotMarkup()}
    `;
}

async function guardAttachLocalSimulation() {
    const simRadio = document.querySelector('input[name="guard-target-mode"][value="simulated"]');
    if (simRadio) simRadio.checked = true;
    guardToggleTargetMode();
    const nameInput = document.getElementById('guard-target-name');
    if (nameInput && !nameInput.value) nameInput.value = '内置可观测模拟系统';
    await guardRegisterTarget();
}

async function guardRegisterTarget() {
    const statusEl = document.getElementById('guard-target-status');
    const mode = guardSelectedTargetMode();
    if (statusEl) statusEl.textContent = '接入中...';
    const data = await api('/api/continuous-guard/targets/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            mode,
            name: document.getElementById('guard-target-name')?.value || '',
            endpoint: mode === 'simulated' ? guardLocalSimulationEndpoint() : (document.getElementById('guard-target-endpoint')?.value || ''),
            port: document.getElementById('guard-target-port')?.value || '',
            health_path: mode === 'simulated' ? '' : (document.getElementById('guard-target-health-path')?.value || ''),
            system_path: mode === 'simulated' ? '' : (document.getElementById('guard-target-system-path')?.value || ''),
            token: document.getElementById('guard-target-token')?.value || '',
            scenario: document.getElementById('guard-target-scenario')?.value || 'checkout_latency',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '接入失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = data.probe?.reachable ? '探测成功' : '已接入，探测未通过';
    await loadContinuousGuardView();
}

async function guardProbeTarget(targetId) {
    const statusEl = document.getElementById('guard-target-status');
    if (statusEl) statusEl.textContent = '探测中...';
    const data = await api('/api/continuous-guard/targets/' + encodeURIComponent(targetId) + '/probe', { method: 'POST' });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '探测失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = data.probe?.reachable ? '探测成功' : '探测未通过';
    await loadContinuousGuardView();
}

async function guardRunPlan(planId) {
    const statusEl = document.getElementById('guard-status');
    if (statusEl) statusEl.textContent = '执行中：正在采集快照、归因风险并生成报告...';
    const data = await api('/api/continuous-guard/' + encodeURIComponent(planId) + '/run', { method: 'POST' });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '执行失败: ' + (data?.error || '未知错误');
        alert('执行失败: ' + (data?.error || '未知错误'));
        return;
    }
    if (statusEl) statusEl.textContent = `执行完成：${data.report?.summary || '报告已写入计划'}`;
    await loadContinuousGuardView(planId);
}

// ═══════════════════════════════════════════
// Fault Data Collection View (view-collection)
// ═══════════════════════════════════════════

let _faultCollectionLoaded = false;

async function loadFaultCollectionView() {
    toggleFaultCollectionCustom();
    const policyEl = document.getElementById('fc-policy');
    const historyEl = document.getElementById('fc-history');
    if (!_faultCollectionLoaded && policyEl) policyEl.textContent = '加载 AIOps 故障数据采集策略中...';
    try {
        const cfg = await api('/api/fault-collection/config');
        if (policyEl && cfg && !cfg.error) {
            policyEl.innerHTML = `
                <strong>${escapeHtml(cfg.harness_policy?.collector || 'AIOps dataset harness')}</strong>
                <span>${escapeHtml((cfg.harness_policy?.trajectory_contract || []).join(' → '))}</span>
            `;
        }
    } catch (e) {
        if (policyEl) policyEl.textContent = '采集策略加载失败';
    }
    try {
        const sessions = await api('/api/fault-collection/sessions');
        if (historyEl) {
            const rows = sessions?.sessions || [];
            historyEl.innerHTML = rows.length ? rows.map(item => `
                <div class="collection-history-item" onclick="loadFaultCollectionSession('${escapeHtml(item.session_id || '')}')">
                    <strong>${escapeHtml(item.session_id || '-')}</strong>
                    <span>${escapeHtml(item.format_type || '-')} · ${escapeHtml(String(item.sample_count || 0))} samples · ${escapeHtml(item.created_at || '')}</span>
                </div>
            `).join('') : '暂无采集任务';
        }
    } catch (e) {
        if (historyEl) historyEl.textContent = '任务历史加载失败';
    }
    _faultCollectionLoaded = true;
}

function toggleFaultCollectionCustom() {
    const format = document.getElementById('fc-format')?.value || 'alpaca_sft';
    const custom = document.getElementById('fc-custom-template');
    if (custom) custom.style.display = format === 'custom' ? 'block' : 'none';
}

function _selectedFaultCollectionPlatforms() {
    return Array.from(document.querySelectorAll('#fc-platforms input[type="checkbox"]:checked'))
        .map(el => el.value)
        .filter(Boolean);
}

async function startFaultCollection() {
    const statusEl = document.getElementById('fc-status');
    const resultCard = document.getElementById('fc-result-card');
    const platforms = _selectedFaultCollectionPlatforms();
    if (!platforms.length) {
        if (statusEl) statusEl.textContent = '请至少选择一个平台';
        return;
    }
    if (statusEl) statusEl.textContent = '正在连续注入故障并整理训练样本...';
    if (resultCard) resultCard.style.display = 'none';
    const payload = {
        platforms,
        format_type: document.getElementById('fc-format')?.value || 'alpaca_sft',
        rounds_per_platform: parseInt(document.getElementById('fc-rounds')?.value || '1', 10),
        duration_seconds: parseInt(document.getElementById('fc-duration')?.value || '120', 10),
        observation_window_seconds: parseInt(document.getElementById('fc-window')?.value || '240', 10),
        collection_interval_seconds: parseInt(document.getElementById('fc-interval')?.value || '15', 10),
        custom_template: document.getElementById('fc-custom-template')?.value || '',
    };
    const data = await api('/api/fault-collection/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    if (!data || data.error || data.status === 'error') {
        if (statusEl) statusEl.textContent = '采集失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = '采集完成';
    renderFaultCollectionSession(data);
    await loadFaultCollectionView();
}

async function loadFaultCollectionSession(sessionId) {
    if (!sessionId) return;
    const data = await api('/api/fault-collection/session/' + encodeURIComponent(sessionId));
    if (data && !data.error) renderFaultCollectionSession(data);
}

function renderFaultCollectionSession(data) {
    const card = document.getElementById('fc-result-card');
    const summary = document.getElementById('fc-session-summary');
    const preview = document.getElementById('fc-preview');
    const format = document.getElementById('fc-result-format');
    if (!card || !summary || !preview) return;
    card.style.display = 'block';
    if (format) format.textContent = data.format_type || '-';
    summary.innerHTML = `
        <div class="collection-summary-grid">
            <div><span>Session</span><strong>${escapeHtml(data.session_id || '-')}</strong></div>
            <div><span>样本数</span><strong>${escapeHtml(String(data.sample_count || 0))}</strong></div>
            <div><span>平台</span><strong>${escapeHtml((data.platforms || []).join(' / '))}</strong></div>
            <div><span>输出文件</span><strong>${escapeHtml(data.jsonl_path || '-')}</strong></div>
        </div>
        ${(data.errors || []).length ? `<div class="collection-errors">${escapeHtml(JSON.stringify(data.errors, null, 2))}</div>` : ''}
    `;
    preview.textContent = JSON.stringify({
        harness_policy: data.harness_policy,
        events: data.events,
        preview: data.preview,
    }, null, 2);
}

// ═══════════════════════════════════════════
// RCA Pipeline View (view-rca)
// ═══════════════════════════════════════════

let _rcaStepIndex = 0;
let _rcaToolResults = [];
let _rcaAllDone = false;
let _rcaToolSequence = [];

const RCA_AGENTS = [
    { id: 'sop_agent', code: 'SOP', name: 'SOP 智能体', role: 'Runbook Orchestrator', desc: '把故障注入案例转成 RCA 目标、成功标准、停止条件和人工确认门。' },
    { id: 'context_prompt_agent', code: 'CTX', name: 'Prompt/上下文管理智能体', role: 'Context Manager', desc: '压缩 log / trace / metric / alert / topology，生成 Prompt 包和上下文预算。' },
    { id: 'memory_agent', code: 'MEM', name: '记忆检索智能体', role: 'Memory Retriever', desc: '检索相似成功策略和失败反例，给诊断模型提供反事实约束。' },
    { id: 'tool_decision_agent', code: 'ROUTE', name: '工具调用决策智能体', role: 'Tool Router', desc: '根据数据可用性、历史 reward 和上下文预算选择工具，而不是全量调用。' },
    { id: 'evidence_agent', code: 'EVD', name: '多模态证据分析智能体', role: 'Evidence Analyst', desc: '执行被选工具，输出工具前后数据对比和证据交接件。' },
    { id: 'diagnosis_agent', code: 'RCA', name: '诊断智能体', role: 'LLM RCA Diagnostician', desc: '读取上游 Agent 交接件、拓扑传播和记忆约束，生成 Top-K 根因候选。' },
    { id: 'critic_learning_agent', code: 'LEARN', name: '评估/终身学习智能体', role: 'Learning Critic', desc: '计算 ACC@K/MRR，并把成功/失败结果写回记忆、Prompt 和工具 reward。' },
];

function getRcaAgentSequence() {
    const stages = window._rcaToolPlan?.agent_workflow?.stages || [];
    if (stages.length) {
        return stages
            .filter(stage => stage.agent && stage.agent !== 'enterprise_gateway_agent')
            .map(stage => {
                const meta = RCA_AGENTS.find(agent => agent.id === stage.agent) || {};
                return {
                    ...meta,
                    id: stage.agent,
                    order: stage.order,
                    name: meta.name || stage.title || stage.agent,
                    role: meta.role || stage.agent,
                    desc: stage.action || meta.desc || '',
                    stageTitle: stage.title || meta.name || stage.agent,
                    inputArtifact: stage.input_artifact,
                    outputArtifact: stage.output_artifact,
                    handoffTo: stage.handoff_to,
                    humanGate: !!stage.human_gate,
                };
            });
    }
    return RCA_AGENTS.slice();
}

function getRcaToolSequence() {
    return getRcaAgentSequence();
}

function rcaResultBelongsToCurrentCase(data) {
    if (!data || !window._rcaCaseId) return false;
    return !data.case_id || String(data.case_id) === String(window._rcaCaseId);
}

function rcaBuildMultiagentShell(preserved = false) {
    const chain = document.getElementById('rca-tool-chain');
    if (!chain) return;
    const plannedNames = Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : [];
    chain.innerHTML =
        `<div class="agentic-rca-shell">
            <div class="agentic-rca-head">
                <div>
                    <span class="agentic-kicker">Agent Handoff RCA${preserved ? ' · 已保留' : ''}</span>
                    <h3>${escapeHtml(window._rcaCaseName || window._rcaCaseId)}</h3>
                    <p>来源 ${escapeHtml(window._rcaSourceId || '-')} · 工具候选 ${escapeHtml(plannedNames.join(' / ') || '由工具决策智能体选择')}</p>
                </div>
                <div class="agentic-badge-stack">
                    <div class="agentic-final-badge" id="agentic-final-badge">等待接力</div>
                    <div class="agentic-final-badge" id="agentic-model-badge">模型: 待执行</div>
                </div>
            </div>
            <div id="rca-agent-relay" class="agent-relay"></div>
            <div id="rca-agent-live-log" class="agent-live-log">
                <div class="agent-live-line">${preserved ? '已恢复本 case 的多智能体执行过程；恢复故障前切换路径不会清空。' : '多智能体诊断图已装载，等待执行第一个 Agent。'}</div>
            </div>
            <div id="rca-agent-result-list" class="agent-result-list"></div>
        </div>`;
}

function rcaRenderPreservedMultiagent(data = window._rcaFullResult) {
    if (!rcaResultBelongsToCurrentCase(data)) return false;
    _rcaToolSequence = getRcaAgentSequence();
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaToolSequence();
    document.getElementById('rca-no-data') && (document.getElementById('rca-no-data').style.display = 'none');
    document.getElementById('rca-pipeline') && (document.getElementById('rca-pipeline').style.display = 'block');
    rcaBuildMultiagentShell(true);
    renderRcaAgentRelay(sequence.length - 1, sequence.length - 1);
    const finalRoot = data?.multiagent_diagnosis?.final_root_cause || data?.rca_result?.primary_root_cause || '-';
    const finalBadge = document.getElementById('agentic-final-badge');
    if (finalBadge) finalBadge.textContent = `最终根因: ${finalRoot}`;
    updateAgenticModelBadge(data);
    renderMultiagentModelStatus(data);
    const list = document.getElementById('rca-agent-result-list');
    if (list) list.innerHTML = '';
    sequence.forEach((_, i) => rcaDisplayOneResult(data, i, { suppressLogs: true }));
    document.getElementById('rca-results') && (document.getElementById('rca-results').style.display = 'block');
    rcaDisplayFinalResults(data);
    _rcaStepIndex = sequence.length;
    _rcaAllDone = true;
    window._rcaProgressiveRunning = false;
    document.getElementById('rca-pipeline-status').textContent = '已保留本 case 的多智能体执行过程';
    document.getElementById('rca-step-prompt').innerHTML = '<strong>多智能体 RCA 已完成并保留。</strong><br><small>恢复故障前可以切换查看 Hermes 或企业 RCA 流程，再回来继续查看本过程。</small>';
    document.getElementById('rca-btn-continue').textContent = '查看成效看板';
    document.getElementById('rca-btn-continue').onclick = () => switchView('evolution');
    document.getElementById('rca-step-controls').style.display = 'block';
    return true;
}

function loadRcaHubView() {
    const badge = document.getElementById('rca-hub-case-badge');
    const title = document.getElementById('rca-hub-case-title');
    const subtitle = document.getElementById('rca-hub-case-subtitle');
    if (badge) badge.textContent = window._rcaCaseId ? `${window._rcaSourceId || '-'} / ${window._rcaCaseId}` : '未绑定 case';
    if (title) title.textContent = window._rcaCaseName || window._rcaCaseId || '请先从数据平台选择或注入故障';
    const selected = window._rcaSelectedPath || '';
    if (subtitle) {
        subtitle.textContent = window._rcaCaseId
            ? `来源 ${window._rcaSourceId || '-'} · ${selected ? '当前路径 ' + rcaPathLabel(selected) : '请选择一条诊断路径'}`
            : '多智能体、Hermes RCA Agent 和企业内部 RCA 流程都会在这里作为可选路径运行。';
    }
    rcaUpdatePathCards(selected);
    const hasCase = !!window._rcaCaseId;
    document.getElementById('rca-multiagent-panel') && (document.getElementById('rca-multiagent-panel').style.display = selected === 'multiagent' ? 'block' : 'none');
    document.getElementById('rca-hermes-embed') && (document.getElementById('rca-hermes-embed').style.display = selected === 'hermes' ? 'block' : 'none');
    document.getElementById('rca-enterprise-panel') && (document.getElementById('rca-enterprise-panel').style.display = selected === 'enterprise' ? 'block' : 'none');
    if (selected !== 'multiagent') {
        document.getElementById('rca-results') && (document.getElementById('rca-results').style.display = 'none');
        document.getElementById('rca-report-actions') && (document.getElementById('rca-report-actions').innerHTML = '');
        document.getElementById('rca-restore-panel') && (document.getElementById('rca-restore-panel').style.display = 'none');
    }
    if (selected === 'multiagent' && !hasCase) {
        document.getElementById('rca-no-data') && (document.getElementById('rca-no-data').style.display = 'block');
        document.getElementById('rca-pipeline') && (document.getElementById('rca-pipeline').style.display = 'none');
    }
    if (selected === 'enterprise') {
        document.getElementById('rca-no-data') && (document.getElementById('rca-no-data').style.display = 'none');
        document.getElementById('rca-pipeline') && (document.getElementById('rca-pipeline').style.display = 'none');
        rcaLoadEnterpriseFlows();
    } else if (selected === 'hermes') {
        rcaMoveHermesIntoHub();
        loadHermesRcaView();
    }
}

function rcaPathLabel(path) {
    return {
        multiagent: '多智能体诊断',
        hermes: 'Hermes RCA Agent',
        enterprise: '企业 RCA 流程',
    }[path] || '多智能体诊断';
}

function rcaUpdatePathCards(path) {
    document.querySelectorAll('.rca-path-card').forEach(card => {
        card.classList.toggle('active', card.dataset.rcaPath === path);
    });
}

function rcaMoveHermesIntoHub() {
    const embed = document.getElementById('rca-hermes-embed');
    const noData = document.getElementById('hermes-no-data');
    const workbench = document.getElementById('hermes-workbench');
    if (!embed || !noData || !workbench) return;
    if (noData.parentElement !== embed) embed.appendChild(noData);
    if (workbench.parentElement !== embed) embed.appendChild(workbench);
}

function rcaSelectPath(path) {
    window._rcaSelectedPath = path || 'multiagent';
    rcaUpdatePathCards(window._rcaSelectedPath);
    loadRcaHubView();
    if (!window._rcaCaseId && window._rcaSelectedPath !== 'enterprise') {
        return;
    }
    if (window._rcaSelectedPath === 'multiagent') {
        rcaInit();
    } else if (window._rcaSelectedPath === 'hermes') {
        rcaMoveHermesIntoHub();
        loadHermesRcaView();
    } else if (window._rcaSelectedPath === 'enterprise') {
        rcaLoadEnterpriseFlows();
    }
}

function rcaToggleEnterpriseForm(force) {
    const form = document.getElementById('rca-enterprise-form');
    if (!form) return;
    const nextVisible = typeof force === 'boolean' ? force : form.style.display === 'none';
    form.style.display = nextVisible ? 'block' : 'none';
}

async function rcaLoadEnterpriseFlows() {
    const list = document.getElementById('rca-enterprise-flow-list');
    if (!list) return;
    list.textContent = '正在读取企业 RCA 流程...';
    const data = await api('/api/enterprise-rca/flows');
    if (!data || data.error) {
        list.innerHTML = `<div class="collection-errors">读取失败：${escapeHtml(data?.error || '未知错误')}</div>`;
        return;
    }
    const flows = data.flows || [];
    list.innerHTML = flows.length ? flows.map(flow => `
        <div class="enterprise-flow-card ${window._enterpriseSelectedFlowId === flow.id ? 'active' : ''}">
            <div>
                <strong>${escapeHtml(flow.name || flow.id)}</strong>
                <span>${escapeHtml(flow.algorithm_type || 'enterprise_rca_flow')} · ${escapeHtml(flow.endpoint || 'local-adapter')}</span>
                <p>${escapeHtml(flow.description || flow.trigger_condition || '')}</p>
            </div>
            <button class="btn btn-sm btn-primary" onclick="rcaUseEnterpriseFlow('${escapeHtml(flow.id)}')">选择此流程</button>
        </div>
    `).join('') : `
        <div class="enterprise-flow-empty">
            <strong>尚未接入企业内部 RCA 流程</strong>
            <span>点击右上角 +，接入内部算法、Runbook、图算法或 MCP 工具，系统会把它纳入根因定位候选路径。</span>
        </div>
    `;
    if (window._enterpriseSelectedFlowId) {
        const selectedFlow = flows.find(flow => flow.id === window._enterpriseSelectedFlowId) || window._enterpriseSelectedFlow;
        rcaRenderEnterpriseFlowExecution(selectedFlow);
    } else {
        rcaRenderEnterpriseFlowExecution(null);
    }
}

async function rcaRegisterEnterpriseFlow() {
    const statusEl = document.getElementById('rca-enterprise-flow-status');
    const name = document.getElementById('rca-enterprise-flow-name')?.value?.trim();
    const endpoint = document.getElementById('rca-enterprise-flow-endpoint')?.value?.trim();
    const algorithmType = document.getElementById('rca-enterprise-flow-type')?.value?.trim();
    const desc = document.getElementById('rca-enterprise-flow-desc')?.value?.trim();
    if (!name) {
        if (statusEl) statusEl.textContent = '请填写 RCA 算法/流程名称';
        return;
    }
    if (statusEl) statusEl.textContent = '接入中...';
    const data = await api('/api/enterprise-rca/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            name,
            endpoint,
            algorithm_type: algorithmType || 'enterprise_rca_flow',
            description: desc,
            input_modalities: ['logs', 'traces', 'metrics', 'topology'],
            trigger_condition: '根因分析路径选择后，由工具路由和人工确认共同决定是否执行',
            output_contract: 'Top-K RCA candidates + evidence summary + confidence + remediation notes',
        }),
    });
    if (!data || data.error) {
        if (statusEl) statusEl.textContent = '接入失败: ' + (data?.error || '未知错误');
        return;
    }
    if (statusEl) statusEl.textContent = '已接入，正在刷新流程列表...';
    rcaToggleEnterpriseForm(false);
    await rcaLoadEnterpriseFlows();
    if (_dsSourceId && _dsCaseId) await dsLoadToolPlan(_dsSourceId, _dsCaseId);
}

function rcaUseEnterpriseFlow(flowId) {
    const statusEl = document.getElementById('rca-enterprise-flow-status');
    if (statusEl) statusEl.textContent = '已选择企业流程，后续会由企业适配器返回候选与证据。';
    document.querySelectorAll('.enterprise-flow-card').forEach(card => card.classList.remove('active'));
    const button = Array.from(document.querySelectorAll('.enterprise-flow-card button')).find(btn => btn.getAttribute('onclick')?.includes(flowId));
    const card = button?.closest('.enterprise-flow-card');
    card?.classList.add('active');
    window._enterpriseSelectedFlowId = flowId;
    window._enterpriseSelectedFlow = {
        id: flowId,
        name: card?.querySelector('strong')?.textContent || flowId,
        meta: card?.querySelector('span')?.textContent || '',
        description: card?.querySelector('p')?.textContent || '',
    };
    rcaRenderEnterpriseFlowExecution(window._enterpriseSelectedFlow);
}

function rcaRenderEnterpriseFlowExecution(flow) {
    const panel = document.getElementById('rca-enterprise-panel');
    if (!panel) return;
    let box = document.getElementById('rca-enterprise-execution');
    if (!box) {
        box = document.createElement('div');
        box.id = 'rca-enterprise-execution';
        box.className = 'enterprise-flow-execution';
        panel.appendChild(box);
    }
    if (!flow) {
        box.style.display = 'none';
        box.innerHTML = '';
        return;
    }
    box.style.display = 'block';
    const flowMeta = flow.meta || `${flow.algorithm_type || 'enterprise_rca_flow'} · ${flow.endpoint || 'local-adapter'}`;
    box.innerHTML = `
        <div class="failure-learning-outcome ready">
            <span>企业流程状态</span>
            <strong>${escapeHtml(flow.name || flow.id || '企业 RCA 流程')}</strong>
            <small>${escapeHtml(flowMeta)}</small>
        </div>
        <div class="failure-learning-flow failure-learning-operator">
            <div class="failure-stage done">
                <span>输入接收</span>
                <small>绑定当前故障 case、四类证据和拓扑上下文。</small>
                <em>${escapeHtml(window._rcaSourceId || '-')} / ${escapeHtml(window._rcaCaseId || '-')}</em>
            </div>
            <div class="failure-stage done">
                <span>流程选择</span>
                <small>人工选择企业内部 RCA 算法/Runbook/诊断流水线。</small>
                <em>${escapeHtml(flow.description || flow.trigger_condition || '已进入统一 RCA 路由')}</em>
            </div>
            <div class="failure-stage ready">
                <span>输出契约</span>
                <small>企业算法应返回 Top-K 根因、证据摘要、置信度和处置建议。</small>
                <em>${escapeHtml(flow.output_contract || 'Top-K RCA candidates + evidence summary + confidence')}</em>
            </div>
            <div class="failure-stage waiting">
                <span>结果保留</span>
                <small>恢复故障前，切换多智能体或 Hermes 后再回来，本企业流程选择和过程仍会保留。</small>
                <em>等待企业内部真实算法返回或人工接入 endpoint。</em>
            </div>
        </div>
        <div id="rca-enterprise-restore-panel" class="fault-restore-panel"></div>
    `;
    renderFaultRestorePanel('rca-enterprise-restore-panel', 'enterprise');
}

function rcaInit() {
    window._rcaSelectedPath = 'multiagent';
    rcaUpdatePathCards('multiagent');
    document.getElementById('rca-multiagent-panel') && (document.getElementById('rca-multiagent-panel').style.display = 'block');
    document.getElementById('rca-hermes-embed') && (document.getElementById('rca-hermes-embed').style.display = 'none');
    document.getElementById('rca-enterprise-panel') && (document.getElementById('rca-enterprise-panel').style.display = 'none');
    if (!window._rcaCaseId) {
        document.getElementById('rca-no-data').style.display = 'block';
        document.getElementById('rca-pipeline').style.display = 'none';
        return;
    }
    document.getElementById('rca-no-data').style.display = 'none';
    document.getElementById('rca-pipeline').style.display = 'block';
    if (window._rcaFullResult && rcaResultBelongsToCurrentCase(window._rcaFullResult)) {
        rcaRenderPreservedMultiagent(window._rcaFullResult);
        return;
    }
    if (window._rcaProgressiveRunning && document.getElementById('rca-agent-result-list')) {
        document.getElementById('rca-pipeline-status').textContent = '多智能体仍在执行，过程已保留';
        return;
    }
    document.getElementById('rca-results').style.display = 'none';
    document.getElementById('rca-pipeline-status').textContent = '初始化...';
    _rcaToolSequence = getRcaAgentSequence();
    const plannedNames = Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : [];
    rcaBuildMultiagentShell(false);
    
    _rcaStepIndex = 0;
    _rcaToolResults = [];
    _rcaAllDone = false;
    window._rcaFullResult = null;
    window._rcaBackendPromise = null;
    window._rcaProgressiveRunning = false;
    
    renderRcaAgentRelay(0, -1);
    showRcaStepPrompt(0);
}

function sleep(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
}

function rcaStageDisplayMs(agentStep = {}, index = 0) {
    const duration = Number(agentStep.duration_s || 0);
    const backendMs = Number.isFinite(duration) && duration > 0 ? duration * 1000 : 0;
    const baseline = index === 0 ? 900 : 1300 + (index % 3) * 180;
    return Math.max(baseline, Math.min(3800, backendMs || baseline));
}

function startRcaBackendProgressAnimation(sequence) {
    let active = 0;
    let tick = 0;
    appendRcaAgentLiveLog('后端正在执行完整多智能体图，前端同步播放每个 Agent 的接力状态。');
    renderRcaAgentRelay(0, -1, 'running');
    return window.setInterval(() => {
        tick += 1;
        active = Math.min(sequence.length - 1, Math.floor(tick / 2));
        const completed = Math.max(-1, active - 1);
        const agent = sequence[active] || sequence[0] || {};
        renderRcaAgentRelay(active, completed, 'running');
        document.getElementById('rca-pipeline-status').textContent = `执行中: ${agent.name || 'Agent'} · 后端图运行中...`;
        if (tick % 2 === 0) {
            appendRcaAgentLiveLog(`${agent.name || 'Agent'} 正在处理上下文、工具证据或模型推理。`);
        }
    }, 900);
}

async function holdRcaStageForReadablePlayback(agent, agentStep, index, startedAt) {
    const targetMs = rcaStageDisplayMs(agentStep, index);
    const elapsed = performance.now() - startedAt;
    const remaining = Math.max(0, targetMs - elapsed);
    if (remaining > 120) {
        document.getElementById('rca-pipeline-status').textContent =
            `执行中: ${(agentStep?.name || agent?.name || 'Agent')} · 正在整理交接件...`;
        await sleep(remaining);
    }
}

function rcaStepCardId(index) {
    return `rca-agent-step-card-${index}`;
}

function rcaDisplayRunningStep(agentMeta, index, progress = 0) {
    const chain = document.getElementById('rca-agent-result-list') || document.getElementById('rca-tool-chain');
    if (!chain) return;
    let card = document.getElementById(rcaStepCardId(index));
    if (!card) {
        card = document.createElement('div');
        card.id = rcaStepCardId(index);
        chain.appendChild(card);
    }
    const pct = Math.max(0, Math.min(100, Math.round(progress)));
    const dur = (rcaStageDisplayMs({}, index) / 1000).toFixed(1);
    card.className = 'agent-result-card active';
    card.innerHTML =
        `<div class="agent-result-grid">
            <div class="agent-result-avatar">
                <div class="agent-result-face"><i></i><i></i></div>
                <span>${escapeHtml(agentMeta.code || String(index + 1))}</span>
            </div>
            <div class="agent-result-main">
                <div class="agent-result-title">
                    <div>
                        <strong>${escapeHtml(agentMeta.name || agentMeta.id || 'Agent')}</strong>
                        <small>${escapeHtml(agentMeta.role || '')}</small>
                    </div>
                    <span>running · ${pct}% · ${dur}s</span>
                </div>
                <div class="agent-result-desc">${escapeHtml(agentMeta.desc || '')}</div>
                <div class="agent-output-title">${escapeHtml(agentMeta.outputArtifact || 'Agent output')}</div>
                <div class="agent-output-box">
                    正在读取上一步交接件、压缩证据并生成输出...
                    <div class="agent-step-progress"><span style="width:${pct}%"></span></div>
                </div>
            </div>
        </div>`;
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function rcaPreviewOutput(agentMeta, index) {
    const plan = window._rcaToolPlan || {};
    const workflow = plan.agent_workflow || {};
    const prompt = workflow.prompt_context || {};
    const decision = workflow.tool_decision || {};
    const readiness = workflow.data_readiness || {};
    const selectedTools = plan.selected_tools || decision.selected_tools || window._rcaPlannedTools || [];
    const skippedTools = plan.skipped_tools || [];
    const caseLabel = window._rcaCaseName || window._rcaCaseId || '-';
    const sourceLabel = window._rcaSourceId || '-';
    const outputByAgent = {
        sop_agent: {
            title: 'sop_contract',
            body: {
                case: caseLabel,
                source: sourceLabel,
                success_criteria: ['输出 Top-K 根因候选', '引用工具证据/拓扑证据', '保留人工确认与恢复入口'],
                next: '交接给上下文管理智能体',
            },
        },
        context_prompt_agent: {
            title: 'context_contract + prompt_pack',
            body: {
                context_budget: prompt.context_budget || plan.context_contract?.budget || {},
                modalities: readiness.modalities || {},
                prompt_version: prompt.prompt_version || 1,
                next: '交接给记忆检索智能体',
            },
        },
        memory_agent: {
            title: 'memory_capsules',
            body: {
                memory_capsules: prompt.memory_capsules || {},
                learned_patches: prompt.learned_patches || [],
                failure_contrast_rules: prompt.failure_contrast_rules || [],
                next: '交接给工具调用决策智能体',
            },
        },
        tool_decision_agent: {
            title: 'ordered_tool_plan',
            body: {
                selected_tools: selectedTools,
                skipped_tools: skippedTools.map(item => ({ tool: item.tool, reason: item.reason })),
                planner: plan.planner || workflow.framework || 'multiagent_tool_router',
                next: '交接给多模态证据分析智能体',
            },
        },
        evidence_agent: {
            title: 'tool_evidence_artifacts',
            body: {
                planned_tools: selectedTools,
                artifact_contract: '每个工具输出摘要、证据和 before/after artifact diff',
                available_modalities: readiness.modalities || {},
                next: '交接给诊断智能体',
            },
        },
    };
    return outputByAgent[agentMeta.id] || {
        title: agentMeta.outputArtifact || `agent_output_${index + 1}`,
        body: {
            status: '等待真实后端结果',
            note: '该步骤需要模型/工具真实结果，系统会停在当前智能体等待结果返回。',
        },
    };
}

function rcaDisplayPreviewResult(agentMeta, index) {
    const preview = rcaPreviewOutput(agentMeta, index);
    const chain = document.getElementById('rca-agent-result-list') || document.getElementById('rca-tool-chain');
    if (!chain) return;
    let card = document.getElementById(rcaStepCardId(index));
    if (!card) {
        card = document.createElement('div');
        card.id = rcaStepCardId(index);
        chain.appendChild(card);
    }
    card.className = 'agent-result-card completed';
    card.innerHTML =
        `<div class="agent-result-grid">
            <div class="agent-result-avatar">
                <div class="agent-result-face"><i></i><i></i></div>
                <span>${escapeHtml(agentMeta.code || String(index + 1))}</span>
            </div>
            <div class="agent-result-main">
                <div class="agent-result-title">
                    <div>
                        <strong>${escapeHtml(agentMeta.name || agentMeta.id || 'Agent')}</strong>
                        <small>${escapeHtml(agentMeta.role || '')}</small>
                    </div>
                    <span>completed · 100%</span>
                </div>
                <div class="agent-result-desc">${escapeHtml(agentMeta.desc || '')}</div>
                <div class="agent-output-title">${escapeHtml(preview.title)}</div>
                ${renderAgentOutput(agentMeta.id, preview.body)}
                <div class="agent-handoff-payload">
                    <strong>本 Agent 已完成，进入下一步</strong>
                    <span>${escapeHtml(agentMeta.outputArtifact || preview.title)}</span>
                </div>
            </div>
        </div>`;
    card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function animateRcaAgentStep(agentMeta, index) {
    const duration = rcaStageDisplayMs({}, index);
    const start = performance.now();
    let progress = 0;
    while (progress < 100) {
        const elapsed = performance.now() - start;
        progress = Math.min(100, Math.round((elapsed / duration) * 100));
        rcaDisplayRunningStep(agentMeta, index, progress);
        if (progress >= 100) break;
        await sleep(120);
    }
    rcaDisplayRunningStep(agentMeta, index, 100);
    await sleep(160);
}

async function rcaStartBackendRun() {
    if (window._rcaBackendPromise) return window._rcaBackendPromise;
    window._rcaBackendPromise = api('/api/rca/orchestrated', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            source_id: window._rcaSourceId,
            case_id: window._rcaCaseId,
            run_tools: Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : null,
            use_llm: true,
        }),
    });
    return window._rcaBackendPromise;
}

function rcaFinalizeProgressiveResult(data, sequence) {
    window._rcaFullResult = data;
    updateAgenticModelBadge(data);
    renderMultiagentModelStatus(data);
    _rcaStepIndex = sequence.length;
    _rcaAllDone = true;
    document.getElementById('rca-results').style.display = 'block';
    rcaDisplayFinalResults(data);
    document.getElementById('rca-pipeline-status').textContent = '分析完成，等待确认结果';
    try { wfSetStep(4); } catch(e) {}
    renderRcaAgentRelay(sequence.length - 1, sequence.length - 1);
    const finalRoot = data?.multiagent_diagnosis?.final_root_cause || data?.rca_result?.primary_root_cause || '-';
    const finalBadge = document.getElementById('agentic-final-badge');
    if (finalBadge) finalBadge.textContent = `最终根因: ${finalRoot}`;
    document.getElementById('rca-step-prompt').innerHTML = '<strong>本轮 RCA 已完成。</strong><br><small>请查看根因候选和命中情况；若是动态故障，请先在下方恢复故障，再进入成效看板。</small>';
    document.getElementById('rca-btn-continue').textContent = '查看成效看板';
    document.getElementById('rca-btn-continue').onclick = () => switchView('evolution');
    document.getElementById('rca-step-controls').style.display = 'block';
}

async function rcaRunProgressivePipeline(startIndex = 0) {
    if (window._rcaProgressiveRunning) return;
    window._rcaProgressiveRunning = true;
    document.getElementById('rca-step-controls').style.display = 'none';
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaToolSequence();
    const resultDiv = document.getElementById('rca-agent-result-list') || document.getElementById('rca-tool-chain');
    let backendDone = false;
    let backendData = null;
    let backendError = null;
    const loading = document.createElement('div');
    loading.id = 'rca-loading';
    loading.className = 'agent-loading';
    loading.innerHTML = '<div class="loading-spinner"></div> 后端多智能体图已启动，前端按 SOP → 上下文 → 记忆 → 工具 → 诊断 → 学习逐步播放...';
    resultDiv?.appendChild(loading);
    const backendPromise = rcaStartBackendRun()
        .then(data => { backendDone = true; backendData = data; return data; })
        .catch(err => { backendDone = true; backendError = err; return { error: err.message || String(err) }; });

    appendRcaAgentLiveLog('多智能体后端任务已启动；前端不再把整条链路压在第一步等待。');
    for (let i = startIndex; i < sequence.length; i++) {
        const agent = sequence[i] || RCA_AGENTS[i] || RCA_AGENTS[0];
        renderRcaAgentRelay(i, i - 1, 'running');
        document.getElementById('rca-pipeline-status').textContent = `执行中: ${agent.name} (${i + 1}/${sequence.length})`;
        appendRcaAgentLiveLog(`${agent.name} 开始执行，输出 ${agent.outputArtifact || '下一步交接件'}。`);
        await animateRcaAgentStep(agent, i);
        const needsBackendResult = ['diagnosis_agent', 'critic_learning_agent'].includes(agent.id);
        if (needsBackendResult && !backendDone) {
            rcaDisplayRunningStep(agent, i, 96);
            document.getElementById('rca-pipeline-status').textContent = `等待真实结果: ${agent.name}`;
            appendRcaAgentLiveLog(`${agent.name} 已完成前置流程，正在等待后端模型/工具真实结果。`);
            const data = await backendPromise;
            backendData = data;
            backendDone = true;
        }
        if (backendError || backendData?.error) break;
        if (backendDone && getAgentDiagnosisStep(backendData, agent.id)) {
            rcaDisplayOneResult(backendData, i, { replace: true });
        } else {
            rcaDisplayPreviewResult(agent, i);
        }
        renderRcaAgentRelay(i + 1 < sequence.length ? i + 1 : i, i);
        _rcaStepIndex = i + 1;
    }

    if (!backendDone) {
        const diagnosisIndex = Math.max(0, sequence.findIndex(agent => agent.id === 'diagnosis_agent'));
        const diagnosisAgent = sequence[diagnosisIndex] || sequence[sequence.length - 1] || {};
        renderRcaAgentRelay(diagnosisIndex, diagnosisIndex - 1, 'running');
        rcaDisplayRunningStep(diagnosisAgent, diagnosisIndex, 96);
        document.getElementById('rca-pipeline-status').textContent = '等待真实模型/工具结果返回...';
        appendRcaAgentLiveLog('展示流程已播放完成，正在等待后端真实工具证据和模型 RCA 结果。');
    }

    const data = backendData || await backendPromise;
    document.getElementById('rca-loading')?.remove();
    if (backendError || !data || data.error) {
        window._rcaProgressiveRunning = false;
        document.getElementById('rca-pipeline-status').textContent = '失败';
        resultDiv.innerHTML += `<div class="rca-candidate-item danger">执行失败：${escapeHtml(data?.error || backendError?.message || '未知错误')}</div>`;
        return;
    }
    appendRcaAgentLiveLog('后端多智能体诊断图已返回真实接力轨迹，最终结果已生成。');
    const lastIndex = sequence.length - 1;
    if (getAgentDiagnosisStep(data, sequence[lastIndex]?.id)) {
        rcaDisplayOneResult(data, lastIndex, { replace: true });
    }
    rcaFinalizeProgressiveResult(data, sequence);
    window._rcaProgressiveRunning = false;
}

function showRcaStepPrompt(index) {
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaToolSequence();
    if (index >= sequence.length) {
        rcaAllDone();
        return;
    }
    const agent = sequence[index];
    renderRcaAgentRelay(index, index - 1);
    document.getElementById('rca-step-prompt').innerHTML = 
        `<strong>${escapeHtml(agent.name)}</strong>: ${escapeHtml(agent.desc || '')}<br>
        <small style="color:var(--text-muted);">输入 ${escapeHtml(agent.inputArtifact || '上一步交接件')} → 输出 ${escapeHtml(agent.outputArtifact || '下一步交接件')}</small><br>
        <small style="color:var(--text-muted);">(步骤 ${index+1}/${sequence.length})</small>`;
    document.getElementById('rca-btn-continue').textContent = '执行 ' + agent.name;
    document.getElementById('rca-btn-continue').onclick = () => rcaExecuteStep(index);
    document.getElementById('rca-step-controls').style.display = 'block';
    document.getElementById('rca-pipeline-status').textContent = `等待执行: ${agent.name}`;
}

async function rcaExecuteStep(index) {
    if (!window._rcaFullResult) {
        await rcaRunProgressivePipeline(index);
        return;
    }
    document.getElementById('rca-step-controls').style.display = 'none';
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaToolSequence();
    const activeAgent = sequence[index] || RCA_AGENTS[index] || RCA_AGENTS[0];
    const stageStartedAt = performance.now();
    let backendProgressTimer = null;
    document.getElementById('rca-pipeline-status').textContent = `执行中: ${activeAgent.name}...`;
    renderRcaAgentRelay(index, index - 1, 'running');
    appendRcaAgentLiveLog(`${activeAgent.name} 开始分析，读取 ${activeAgent.inputArtifact || '上一步交接件'}。`);
    
    if (!window._rcaFullResult) {
        const resultDiv = document.getElementById('rca-agent-result-list') || document.getElementById('rca-tool-chain');
        resultDiv.innerHTML += `<div id="rca-loading" class="agent-loading"><div class="loading-spinner"></div> 多智能体图正在运行：SOP → 上下文/Prompt → 记忆 → 工具决策 → 证据 → 诊断 → 学习...</div>`;
        backendProgressTimer = startRcaBackendProgressAnimation(sequence);
        
        const data = await api('/api/rca/orchestrated', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                source_id: window._rcaSourceId,
                case_id: window._rcaCaseId,
                run_tools: Array.isArray(window._rcaPlannedTools) ? window._rcaPlannedTools : null,
                use_llm: true,
            }),
        });
        
        if (backendProgressTimer) {
            clearInterval(backendProgressTimer);
            backendProgressTimer = null;
        }
        document.getElementById('rca-loading')?.remove();
        
        if (!data || data.error) {
            document.getElementById('rca-pipeline-status').textContent = '失败';
            resultDiv.innerHTML += `<div class="rca-candidate-item danger">执行失败：${escapeHtml(data?.error || '未知错误')}</div>`;
            return;
        }
        
        window._rcaFullResult = data;
        updateAgenticModelBadge(data);
        renderMultiagentModelStatus(data);
        appendRcaAgentLiveLog('后端多智能体诊断图已返回完整接力轨迹，开始逐个 Agent 展示交接结果。');
    }

    const agentStep = getAgentDiagnosisStep(window._rcaFullResult, activeAgent.id) || {};
    await holdRcaStageForReadablePlayback(activeAgent, agentStep, index, stageStartedAt);
    rcaDisplayOneResult(window._rcaFullResult, index);
    _rcaStepIndex = index + 1;
    if (_rcaStepIndex >= sequence.length) {
        _rcaAllDone = true;
        document.getElementById('rca-results').style.display = 'block';
        rcaDisplayFinalResults(window._rcaFullResult);
        document.getElementById('rca-pipeline-status').textContent = '分析完成，等待确认结果';
        try { wfSetStep(4); } catch(e) {}
        renderRcaAgentRelay(index, index);
        const finalRoot = window._rcaFullResult?.multiagent_diagnosis?.final_root_cause || window._rcaFullResult?.rca_result?.primary_root_cause || '-';
        const finalBadge = document.getElementById('agentic-final-badge');
        if (finalBadge) finalBadge.textContent = `最终根因: ${finalRoot}`;
        document.getElementById('rca-step-prompt').innerHTML = '<strong>本轮 RCA 已完成。</strong><br><small>请查看根因候选和命中情况；若是动态故障，请先在下方恢复故障，再进入成效看板。</small>';
        document.getElementById('rca-btn-continue').textContent = '查看成效看板';
        document.getElementById('rca-btn-continue').onclick = () => switchView('evolution');
        document.getElementById('rca-step-controls').style.display = 'block';
    } else {
        const next = sequence[_rcaStepIndex];
        renderRcaAgentRelay(_rcaStepIndex, _rcaStepIndex - 1);
        document.getElementById('rca-step-prompt').innerHTML =
            `<strong>上一个 Agent 已完成并交接。</strong><br><small>下一步执行 ${escapeHtml(next.name)}：${escapeHtml(next.desc || '')}</small>`;
        document.getElementById('rca-btn-continue').textContent = '继续交给 ' + next.name;
        document.getElementById('rca-btn-continue').onclick = () => rcaExecuteStep(_rcaStepIndex);
        document.getElementById('rca-step-controls').style.display = 'block';
        document.getElementById('rca-pipeline-status').textContent = `等待确认: ${next.name}`;
    }
}

function appendRcaAgentLiveLog(text) {
    const log = document.getElementById('rca-agent-live-log');
    if (!log) return;
    const line = document.createElement('div');
    line.className = 'agent-live-line';
    line.textContent = `[${new Date().toLocaleTimeString('zh-CN')}] ${text}`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

function renderRcaAgentRelay(activeIndex = 0, completedIndex = -1, mode = 'idle') {
    const relay = document.getElementById('rca-agent-relay');
    if (!relay) return;
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaAgentSequence();
    relay.innerHTML = sequence.map((agent, idx) => {
        const stateCls = idx <= completedIndex ? 'done' : idx === activeIndex ? `active ${mode}` : 'pending';
        const next = idx < sequence.length - 1 ? '<div class="agent-hop-line"><span></span></div>' : '';
        return `
            <div class="agent-bot-node ${stateCls}">
                <div class="agent-bot-card">
                    <div class="agent-bot-avatar">
                        <div class="agent-bot-antenna"></div>
                        <div class="agent-bot-face"><i></i><i></i></div>
                    </div>
                    <div class="agent-bot-code">${escapeHtml(agent.code || String(idx + 1))}</div>
                    <strong>${escapeHtml(agent.name || agent.id)}</strong>
                    <small>${escapeHtml(agent.role || '')}</small>
                </div>
                ${next}
            </div>
        `;
    }).join('');
}

function getAgentDiagnosisStep(data, agentId) {
    const steps = data?.multiagent_diagnosis?.steps || [];
    return steps.find(step => step.agent_id === agentId) || null;
}

function rcaDisplayOneResult(data, i, options = {}) {
    const chain = document.getElementById('rca-agent-result-list') || document.getElementById('rca-tool-chain');
    const sequence = _rcaToolSequence.length ? _rcaToolSequence : getRcaToolSequence();
    const agentMeta = sequence[i] || RCA_AGENTS[i] || RCA_AGENTS[0];
    const agentStep = getAgentDiagnosisStep(data, agentMeta.id) || {};
    const status = agentStep.status || 'completed';
    const dur = agentStep.duration_s != null ? agentStep.duration_s : 0;
    const displayDur = Math.max(Number(dur) || 0, rcaStageDisplayMs(agentStep, i) / 1000);
    const output = agentStep.output || {};
    const logs = agentStep.logs || [];
    if (!options.suppressLogs) {
        logs.forEach(line => appendRcaAgentLiveLog(`${agentStep.name || agentMeta.name}: ${line}`));
    }
    const outputHtml = renderAgentOutput(agentMeta.id, output);
    const subtasksHtml = renderAgentSubtasks(agentStep.subtasks || []);
    const handoffHtml = (agentStep.handoff_payload || []).length ? `
        <div class="agent-handoff-payload">
            <strong>交接给 ${escapeHtml(agentStep.handoff_to || agentMeta.handoffTo || '下一个 Agent')}</strong>
            ${(agentStep.handoff_payload || []).map(item => `<span>${escapeHtml(item)}</span>`).join('')}
        </div>
    ` : '';
    let card = options.replace ? document.getElementById(rcaStepCardId(i)) : null;
    if (!card) {
        card = document.createElement('div');
        card.id = rcaStepCardId(i);
    }
    card.className = 'agent-result-card';
    card.innerHTML =
        `<div class="agent-result-grid">
            <div class="agent-result-avatar">
                <div class="agent-result-face"><i></i><i></i></div>
                <span>${escapeHtml(agentMeta.code || String(i + 1))}</span>
            </div>
            <div class="agent-result-main">
                <div class="agent-result-title">
                    <div>
                        <strong>${escapeHtml(agentStep.name || agentMeta.name)}</strong>
                        <small>${escapeHtml(agentStep.role || agentMeta.role || '')}</small>
                    </div>
                    <span>${escapeHtml(status)} · ${displayDur.toFixed(1)}s</span>
                </div>
                <div class="agent-result-desc">${escapeHtml(agentStep.analysis || agentMeta.desc || '')}</div>
                <div class="agent-output-title">${escapeHtml(agentStep.output_title || agentMeta.outputArtifact || 'Agent output')}</div>
                ${outputHtml}
                ${subtasksHtml}
                ${handoffHtml}
            </div>
        </div>`;
    if (!card.parentElement) chain.appendChild(card);
    renderRcaAgentRelay(i, i);
    document.getElementById('rca-pipeline-status').textContent = `已完成: ${agentStep.name || agentMeta.name} (${i+1}/${sequence.length})`;
}

function renderAgentOutput(agentId, output) {
    if (!output || typeof output !== 'object') {
        return `<div class="agent-output-box">${escapeHtml(String(output || '无输出'))}</div>`;
    }
    if (agentId === 'diagnosis_agent') {
        const cands = output.top_candidates || [];
        return `
            <div class="agent-candidate-strip">
                ${cands.slice(0, 5).map(c => `
                    <div class="agent-candidate-chip">
                        <span>#${escapeHtml(c.rank || '?')}</span>
                        <strong>${escapeHtml(c.service || '-')}</strong>
                        <small>${Number(c.score || 0).toFixed(3)}</small>
                        <em>${escapeHtml(c.reason || '')}</em>
                    </div>
                `).join('') || '<span class="text-muted">诊断智能体未返回候选</span>'}
            </div>
            <div class="agent-output-box">模型调用: ${output.llm_used ? '已使用' : '未使用'} · 候选补齐: ${output.fallback_used ? '规则补齐' : '模型直出'} · 模型: ${escapeHtml(output.model || '-')}</div>
        `;
    }
    if (agentId === 'critic_learning_agent') {
        const ev = output.evaluation || {};
        return `
            <div class="agent-score-grid">
                <div><span>ACC@1</span><strong>${escapeHtml(String(ev['ACC@1'] ?? '-'))}</strong></div>
                <div><span>ACC@3</span><strong>${escapeHtml(String(ev['ACC@3'] ?? '-'))}</strong></div>
                <div><span>MRR</span><strong>${escapeHtml(String(ev['MRR'] ?? '-'))}</strong></div>
                <div><span>Top1</span><strong>${escapeHtml(ev.top_candidate || '-')}</strong></div>
            </div>
        `;
    }
    return `
        <details class="agent-output-details" open>
            <summary>查看 Agent 输出</summary>
            <pre>${escapeHtml(JSON.stringify(output, null, 2).slice(0, 3600))}</pre>
        </details>
    `;
}

function renderAgentSubtasks(subtasks) {
    if (!subtasks.length) return '';
    return `
        <div class="agent-subtask-grid">
            ${subtasks.map(task => `
                <div class="agent-subtask ${escapeHtml(task.status || 'pending')}">
                    <div><strong>${escapeHtml(task.tool || '-')}</strong><span>${escapeHtml(task.status || '-')}</span></div>
                    <p>${escapeHtml(task.reason || '')}</p>
                    <small>${escapeHtml(task.before_stage || 'previous')} → ${escapeHtml(task.after_stage || 'no-change')}</small>
                    ${task.summary ? `<em>${escapeHtml(task.summary)}</em>` : ''}
                </div>
            `).join('')}
        </div>
    `;
}

function rcaDisplayFinalResults(data) {
    const ev = data.evaluation || {};
    const rca = data.rca_result || {};
    const llmEl = document.getElementById('rca-llm-used');
    if (llmEl) {
        const llmStatus = rca.llm_status || data.llm_status || {};
        if (rca.llm_used) {
            const fallbackNote = rca.fallback_used ? '；模型已返回，候选解析不足，已用规则补齐最终候选' : '';
            llmEl.textContent = `已使用 ${llmStatus.model || rca.model || 'LLM'}${fallbackNote}`;
        } else if (llmStatus.attempted || llmStatus.attempted_health_check) {
            llmEl.textContent = `已尝试模型调用，未产出可解析候选：${llmStatus.error || '返回格式不满足 RCA 候选约束'}`;
        } else {
            llmEl.textContent = `未进入模型调用：${llmStatus.error || 'LLM 服务未就绪'}`;
        }
        llmEl.className = rca.llm_used ? 'rca-acc-hit' : 'rca-acc-miss';
    }
    
    const setAcc = (id, val) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = val === 1 ? '命中' : val === 0 ? '未命中' : '-';
        el.className = val ? 'rca-acc-hit' : 'rca-acc-miss';
    };
    setAcc('rca-acc1', ev['ACC@1']);
    setAcc('rca-acc3', ev['ACC@3']);
    setAcc('rca-acc5', ev['ACC@5']);
    setAcc('rca-acc10', ev['ACC@10']);
    const mrrEl = document.getElementById('rca-mrr');
    if (mrrEl) mrrEl.textContent = ev['MRR'] ?? '-';
    
    const cands = rca.parsed_candidates || rca.candidates || [];
    document.getElementById('rca-candidates').innerHTML = cands.slice(0, 5).map(c =>
        `<div class="rca-candidate-item">
            <span class="rca-candidate-rank">#${c.rank || '?'}</span> ${escapeHtml(c.service || '?')}
            <span class="rca-candidate-score">置信分 ${(c.score || 0).toFixed(3)}</span>
            <div class="text-muted" style="font-size:12px;margin-top:4px;">${escapeHtml(c.reason || '')}</div>
        </div>`
    ).join('') || '无候选';
    
    document.getElementById('rca-ground-truth').textContent = 
        `${data.ground_truth || 'N/A'} | Top1 ${ev.hit_at_1 ? '命中' : '未命中'}。该结果是按候选排名评估，不代表系统总是百分百正确。`;
    const llmSummaryEl = document.getElementById('rca-llm-input-summary');
    if (llmSummaryEl) {
        const summary = data.llm_input_summary || rca.llm_input_summary || {};
        llmSummaryEl.textContent = JSON.stringify(summary, null, 2);
    }
    const reportActions = document.getElementById('rca-report-actions');
    if (reportActions) {
        reportActions.innerHTML = diagnosticReportButton(data.run_id);
    }
    
    // Notify evolution view to refresh
    window._lastRcaResult = data;
    renderFaultRestorePanel('rca-restore-panel', 'multiagent');
}

function rcaContinue() { rcaExecuteStep(_rcaStepIndex); }
function rcaStop() {
    _rcaAllDone = true;
    document.getElementById('rca-step-controls').style.display = 'none';
    document.getElementById('rca-pipeline-status').textContent = '⏹ 已终止';
}

function rcaAllDone() {
    document.getElementById('rca-step-controls').style.display = 'none';
}

// ═══════════════════════════════════════════
// Self-Evolution View (view-evolution)
// ═══════════════════════════════════════════

let _evoChart = null;
let _lastFailureLearningReport = null;

async function loadEvolutionView() {
    const insights = await api('/api/evolution/insights');
    const agent = await api('/api/multiagent/state');
    if ((!insights || insights.error) && (!agent || agent.error)) return;

    const metrics = agent?.lifelong_learning?.metrics || {};
    const events = (agent?.learning_events || []).filter(e => e.case_id);
    const successPatterns = agent?.lifelong_learning?.success_patterns || [];
    const negativeTrajectories = agent?.lifelong_learning?.negative_trajectories || [];
    const promptPatches = agent?.prompt_engine?.learned_patches || [];
    const failureRules = agent?.prompt_engine?.failure_contrast_rules || [];
    const toolRewards = agent?.tool_engine?.tool_rewards || {};
    const runtime = agent?.graph_runtime || agent?.crew_runtime || {};

    document.getElementById('evo-total').textContent = metrics.runs ?? insights?.total_runs ?? 0;
    document.getElementById('evo-success-rate').textContent = metrics.top1_success_rate != null ? (metrics.top1_success_rate * 100).toFixed(1) + '%' : (insights?.success_rate != null ? (insights.success_rate * 100).toFixed(1) + '%' : '-');
    document.getElementById('evo-avg-mrr').textContent = metrics.avg_mrr != null ? metrics.avg_mrr.toFixed(3) : (insights?.avg_mrr != null ? insights.avg_mrr.toFixed(3) : '-');
    document.getElementById('evo-avg-dur').textContent = `${((metrics.llm_usage_rate || 0) * 100).toFixed(1)}%`;

    const successCount = events.filter(e => e.hit_at_1).length;
    const failCount = events.length - successCount;
    document.getElementById('evo-by-source').innerHTML = `
        <div style="margin:6px 0; padding:8px; background:var(--bg-hover); border-radius:6px;">
            <strong>${escapeHtml(agent?.agent_name || 'Ops Factory Multi-Agent RCA')}</strong><br>
            运行 ${events.length || metrics.runs || 0} 次 · 成功 ${successCount} 次 · 失败 ${failCount} 次
        </div>
        <div style="margin:6px 0; padding:8px; background:var(--bg-hover); border-radius:6px;">
            <strong>Agent Runtime</strong><br>
            ${escapeHtml(runtime.framework || agent?.langchain_mode || 'vendored_langchain_aiops_rca')} · ${escapeHtml(runtime.process || 'graph_orchestrated')} · memory ${runtime.memory_enabled !== false ? 'on' : 'off'}
        </div>
        <div style="margin:6px 0; padding:8px; background:var(--bg-hover); border-radius:6px;">
            <strong>最近更新</strong><br>
            ${escapeHtml(agent?.agent_state?.last_update_reason || 'initial')}
        </div>`;

    document.getElementById('evo-patterns').innerHTML = successPatterns.slice(-10).reverse().map(p =>
        `<div style="margin:4px 0; padding:6px 8px; background:rgba(34,197,94,0.05); border-radius:4px; border-left:3px solid var(--success);">
            <strong>${escapeHtml(p.case_id || '?')}</strong><br>
            <span>${escapeHtml(p.pattern || '')}</span>
        </div>`
    ).join('') || '<span class="text-muted">多 Agent RCA 命中后会把成功工具链和 Prompt 补丁写入这里</span>';

    const weakTools = Object.entries(toolRewards)
        .filter(([, stat]) => (stat.runs || 0) > 0 && (stat.score || 0) < 0.35)
        .map(([tool, stat]) => `${tool}: reward=${Number(stat.score || 0).toFixed(2)}，下一轮降低上下文优先级`);
    const improvements = [...failureRules.slice(-6), ...weakTools, ...(insights?.improvement_suggestions || []).slice(0, 2)];
    if (_lastFailureLearningReport) {
        renderFailureLearning(_lastFailureLearningReport, improvements);
    } else {
        renderFailureLearningIdle(improvements);
    }

    document.getElementById('evo-failures').innerHTML = negativeTrajectories.slice(-8).reverse().map(f =>
        `<div style="margin:4px 0; padding:6px; background:rgba(239,68,68,0.05); border-radius:4px;">
            <strong>${escapeHtml(f.source || '?')}</strong>/${escapeHtml(f.case_id || '?')}
            | GT: ${escapeHtml(f.ground_truth || '?')}
            | Top: ${escapeHtml(f.wrong_top || '?')}
            <div style="color:var(--text-muted); margin-top:4px;">${escapeHtml(f.lesson || '')}</div>
        </div>`
    ).join('') || '<span class="text-muted">无失败轨迹。出现未命中后会在这里保留反例记忆。</span>';

    if (promptPatches.length && !successPatterns.length) {
        document.getElementById('evo-patterns').innerHTML = promptPatches.slice(-8).map(p =>
            `<div style="margin:4px 0; padding:6px 8px; background:rgba(34,197,94,0.05); border-radius:4px; border-left:3px solid var(--success);">${escapeHtml(p)}</div>`
        ).join('');
    }

    // Chart: multi-agent RCA success trend if available, otherwise historical records.
    const timeline = await api('/api/evolution/timeline?limit=20');
    const records = timeline?.records || [];
    const trendRecords = events.length ? events : records;

    const trendDiv = document.getElementById('evo-trend-chart');
    if (trendDiv && trendDiv.getContext) {
        const ctx = trendDiv.getContext('2d');
        if (_evoChart) _evoChart.destroy();
        const labels = trendRecords.length ? trendRecords.map((_, i) => String(i + 1)) : ['累计'];
        const values = trendRecords.length ? trendRecords.map((_, i) => {
            const head = trendRecords.slice(0, i + 1);
            return head.reduce((sum, r) => sum + (r.hit_at_1 ? 1 : 0), 0) / head.length * 100;
        }) : [((metrics.top1_success_rate ?? insights?.success_rate) || 0) * 100];
        _evoChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [{
                    label: '累计Top1命中率', data: values,
                    borderColor: '#2563eb', backgroundColor: 'rgba(37,99,235,0.12)',
                    fill: true, tension: 0.4,
                }]
            },
            options: { responsive: true, maintainAspectRatio: false,
                scales: { y: { min: 0, max: 100, ticks: { callback: v => v + '%' } } }
            }
        });
    }
}

async function runFailureLearning(publish = false) {
    const panel = document.getElementById('evo-failure-learning');
    const status = document.getElementById('evo-learning-status');
    const buttons = document.querySelectorAll('.failure-learning-actions button');
    buttons.forEach(btn => { btn.disabled = true; });
    if (panel) {
        panel.innerHTML = publish
            ? '<div class="failure-learning-empty">正在执行 vNext 发布：回放守门 → 写入策略 → 刷新下一次 RCA 运行时规则...</div>'
            : '<div class="failure-learning-empty">正在生成改进候选：读取失败 case → 归因 → 生成补丁 → 离线回放...</div>';
    }
    if (status) status.textContent = publish ? '发布中...' : '生成中...';
    try {
        const result = await api('/api/evolution/failure-learning/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ limit: 8, publish }),
        });
        _lastFailureLearningReport = result && !result.error ? result : null;
        renderFailureLearning(result, [], { publishAttempted: publish });
    } finally {
        buttons.forEach(btn => { btn.disabled = false; });
    }
}

function renderFailureLearningIdle(fallbackSuggestions = []) {
    const panel = document.getElementById('evo-failure-learning');
    const status = document.getElementById('evo-learning-status');
    const suggestionBox = document.getElementById('evo-suggestions');
    if (status) status.textContent = '待生成';
    if (panel) {
        panel.innerHTML = `
            <div class="failure-learning-operator">
                <div class="failure-learning-outcome">
                    <span>现在还没有开始分析</span>
                    <strong>点击按钮后才会真正读取失败 case</strong>
                    <small>系统会自动完成：找出诊断失败的案例 → 解释为什么错 → 写出下次怎么避免 → 用历史案例回放验证 → 可通过时发布到下一版策略。</small>
                </div>
                ${['找到失败诊断', '解释失败原因', '生成改进动作', '回放验证安全性', '发布下次生效'].map((label, idx) => `
                    <div class="failure-learning-step waiting">
                        <b>${idx + 1}</b>
                        <strong>${label}</strong>
                        <span>等待生成</span>
                    </div>
                `).join('')}
            </div>
        `;
    }
    if (suggestionBox) {
        suggestionBox.innerHTML = fallbackSuggestions.length
            ? `<strong>可参考的改进线索：</strong>${fallbackSuggestions.slice(0, 6).map(s => `<div>${escapeHtml(s)}</div>`).join('')}`
            : '<span class="text-muted">生成前不会提前展示失败归因或发布信息；点击按钮后才会进入候选态。</span>';
    }
}

function failureStagePlain(item = {}) {
    const map = {
        capture: ['找到失败诊断', '从历史 RCA 中挑出 Top1 没命中的 case。'],
        attribute: ['解释为什么错', '把失败拆成上下文缺失、工具选择、Prompt 偏置、服务别名等原因。'],
        patch: ['生成改进动作', '把失败原因变成下一轮会执行的记忆、Prompt、工具路由或评估规则。'],
        replay: ['回放验证安全性', '用历史成功和失败 case 做离线回放，确认不是越改越差。'],
        release: ['发布下次生效', '通过守门后写入 agent_state，下一次 RCA 自动消费。'],
    };
    const [title, desc] = map[item.key] || [item.label || '-', item.detail || ''];
    return { title, desc };
}

function failureModuleLabel(module) {
    return {
        memory_engine: '失败记忆',
        prompt_engine: '提示词规则',
        tool_router: '工具选择策略',
        context_builder: '上下文构建',
        sop_engine: '诊断检查清单',
        evaluator: '结果重排评估',
    }[module] || module || '改进模块';
}

function failureReleasePlain(report, candidate, replay, patches) {
    const feedback = report.publish_feedback || {};
    if (report.published) {
        return {
            state: 'published',
            title: feedback.title || `已发布到 Harness v${report.published_version}`,
            desc: feedback.detail || `已应用 ${report.applied_patches?.length || patches.length} 条改进，下次 RCA 会自动使用。`,
        };
    }
    if (report.publish_status === 'no_new_patch') {
        return {
            state: 'hold',
            title: feedback.title || '未发布：没有新的补丁需要写入',
            desc: feedback.detail || '这些规则已经存在于当前策略中，系统没有重复写入。',
        };
    }
    if (report.publish_status === 'blocked_by_replay_gate') {
        return {
            state: 'hold',
            title: feedback.title || '发布失败：回放守门未通过',
            desc: feedback.detail || candidate.reason || '候选补丁暂不满足发布条件。',
        };
    }
    if (candidate.can_publish) {
        return {
            state: 'ready',
            title: '已通过回放验证，可以发布',
            desc: `预计 Top1 成功数从 ${replay.current_successes || 0} 提升到 ${replay.projected_successes || 0}，影响 ${replay.improved_failures || 0} 个失败 case。`,
        };
    }
    if (report.status === 'no_failures') {
        return { state: 'waiting', title: '当前没有失败案例', desc: '系统会继续积累 RCA 结果，出现未命中后再生成改进。' };
    }
    return { state: 'hold', title: '暂不建议发布', desc: candidate.reason || '回放守门未通过，先保留候选不写入生产策略。' };
}

function renderFailurePublishNotice(report, publishAttempted = false) {
    if (!publishAttempted && !report?.published && !report?.publish_feedback) return '';
    const feedback = report?.publish_feedback || {};
    const cls = report?.published ? 'success' : (publishAttempted ? 'failed' : 'info');
    const statusText = report?.published ? '发布成功' : (publishAttempted ? '发布未完成' : '候选已生成');
    const changed = feedback.changed_file ? `<small>变更位置：${escapeHtml(feedback.changed_file)}</small>` : '<small>本次没有写入策略文件。</small>';
    const count = feedback.applied_patch_count != null ? `<small>写入补丁：${escapeHtml(String(feedback.applied_patch_count))} 条</small>` : '';
    const release = feedback.release_id ? `<small>Release ID：${escapeHtml(feedback.release_id)}</small>` : '';
    return `
        <div class="failure-publish-notice ${cls}">
            <strong>${escapeHtml(statusText)}：${escapeHtml(feedback.title || report?.publish_status || '-')}</strong>
            <span>${escapeHtml(feedback.detail || '')}</span>
            ${changed}
            ${count}
            ${release}
        </div>
    `;
}

function renderFailureLearning(report, fallbackSuggestions = [], options = {}) {
    const panel = document.getElementById('evo-failure-learning');
    const status = document.getElementById('evo-learning-status');
    const suggestionBox = document.getElementById('evo-suggestions');
    if (!panel) return;

    if (!report || report.error) {
        if (status) status.textContent = '不可用';
        panel.innerHTML = `<div class="failure-learning-empty">失败学习引擎暂不可用：${escapeHtml(report?.error || 'unknown')}</div>`;
        return;
    }

    const replay = report.replay || {};
    const candidate = report.release_candidate || {};
    const patches = report.harness_patches || [];
    const cases = report.cases || [];
    const agentState = report.agent_state || {};
    const pct = value => `${((Number(value || 0)) * 100).toFixed(1)}%`;
    const stageClass = item => item.status === 'done' ? 'done' : item.status === 'ready' ? 'ready' : item.status === 'blocked' ? 'blocked' : 'waiting';
    const releasePlain = failureReleasePlain(report, candidate, replay, patches);

    if (status) {
        status.textContent = report.published ? `已发布 v${report.published_version}` :
            options.publishAttempted ? '发布未完成' :
            candidate.can_publish ? '可发布' :
            report.status === 'ready' ? '候选态' :
            report.status === 'no_failures' ? '无失败' : '等待数据';
    }

    panel.innerHTML = `
        <div class="failure-learning-outcome ${releasePlain.state}">
            <span>结论</span>
            <strong>${escapeHtml(releasePlain.title)}</strong>
            <small>${escapeHtml(releasePlain.desc)}</small>
        </div>
        ${renderFailurePublishNotice(report, !!options.publishAttempted)}
        <div class="failure-learning-summary">
            <div>
                <span>当前诊断策略版本</span>
                <strong>v${escapeHtml(agentState.policy_version ?? '-')}</strong>
                <small>发布成功后会写入 SRE/data/evolution/agent_state.json，并被下一次 RCA 读取。</small>
            </div>
            <div>
                <span>回放验证结果</span>
                <strong>${pct(replay.current_top1_rate)} → ${pct(replay.projected_top1_rate)}</strong>
                <small>用 ${replay.replay_set_size || 0} 条历史诊断回放，预计修正 ${replay.improved_failures || 0} 个失败。</small>
            </div>
            <div>
                <span>准备写入的改进</span>
                <strong>${patches.length}</strong>
                <small>${escapeHtml(candidate.reason || '')}</small>
            </div>
        </div>
        <div class="failure-learning-flow failure-learning-operator">
            ${(report.stages || []).map(item => `
                <div class="failure-stage ${stageClass(item)}">
                    <span>${escapeHtml(failureStagePlain(item).title)}</span>
                    <small>${escapeHtml(failureStagePlain(item).desc)}</small>
                    <em>${escapeHtml(item.detail || '')}</em>
                </div>
            `).join('')}
        </div>
        <div class="failure-learning-grid">
            <div class="failure-learning-section">
                <h4>系统找到的失败诊断</h4>
                ${cases.length ? cases.map(renderFailureLearningCase).join('') : '<div class="failure-learning-empty">暂无失败 case。完成未命中 RCA 后会自动进入这里。</div>'}
            </div>
            <div class="failure-learning-section">
                <h4>下一次 RCA 会用到的改进动作</h4>
                ${patches.length ? patches.slice(0, 8).map(renderHarnessPatch).join('') : '<div class="failure-learning-empty">暂无可生成补丁。</div>'}
            </div>
        </div>
        <div class="failure-replay-card ${candidate.can_publish ? 'pass' : 'hold'}">
            <strong>安全守门：${escapeHtml((replay.regression_guard || {}).status === 'pass' ? '通过' : ((replay.regression_guard || {}).status || '等待'))}</strong>
            <span>当前历史回放成功 ${replay.current_successes || 0} 条；发布候选后预计成功 ${replay.projected_successes || 0} 条。</span>
            <small>${escapeHtml((replay.regression_guard || {}).reason || '')}</small>
        </div>
        ${report.last_release?.release_id ? `
            <div class="failure-release-note">
                最近发布：${escapeHtml(report.last_release.release_id)} · Harness v${escapeHtml(report.last_release.policy_version)} · ${escapeHtml(report.last_release.published_at || '')}
            </div>` : ''}
    `;

    if (suggestionBox) {
        const suggestions = patches.slice(0, 5).map(p => `${failureModuleLabel(p.module)}：${p.title}`).concat(fallbackSuggestions || []);
        suggestionBox.innerHTML = suggestions.length
            ? `<strong>下一轮会真实生效的改进：</strong>${suggestions.slice(0, 8).map(s => `<div>${escapeHtml(s)}</div>`).join('')}`
            : '<span class="text-muted">失败 case 出现后会生成 Prompt、Memory、Tool Router、Context Builder、SOP 和 Evaluator 补丁。</span>';
    }
}

function renderFailureLearningCase(item) {
    const tags = (item.failure_taxonomy || []).map(t => `<span class="failure-taxonomy ${escapeHtml(t.severity || 'medium')}">${escapeHtml(t.name || t.code || '')}</span>`).join('');
    return `
        <div class="failure-case-card">
            <div class="failure-case-title">
                <strong>${escapeHtml(item.case_id || '?')}</strong>
                <span>${escapeHtml(item.source_id || '')}</span>
            </div>
            <p class="failure-learning-plain-note">
                当时系统把 <b>${escapeHtml(item.wrong_top || '?')}</b> 当成最可能根因，但真实根因是 <b>${escapeHtml(item.ground_truth || '?')}</b>。
                改进候选发布后，回放预计把真实根因提升到第 <b>${escapeHtml(String(item.projected_rank || 'missing'))}</b> 位。
            </p>
            <div class="failure-case-compare">
                <div><small>原先误判</small><b>${escapeHtml(item.wrong_top || '?')}</b></div>
                <div><small>应该命中</small><b>${escapeHtml(item.ground_truth || '?')}</b></div>
                <div><small>回放排名</small><b>${escapeHtml(String(item.candidate_rank || 'missing'))} → ${escapeHtml(String(item.projected_rank || 'missing'))}</b></div>
            </div>
            <div class="failure-taxonomies">${tags}</div>
            <small class="failure-case-note">${escapeHtml(item.capture_contract?.next_use || '')}</small>
        </div>
    `;
}

function renderHarnessPatch(patch) {
    return `
        <div class="harness-patch-card">
            <div>
                <strong>${escapeHtml(patch.title || patch.module || '')}</strong>
                <span>${escapeHtml(failureModuleLabel(patch.module))}</span>
            </div>
            <p>${escapeHtml(patch.patch || '')}</p>
            <div class="harness-patch-impact">
                <small>下次怎么用：满足触发条件时自动加入诊断上下文或工具策略。</small>
                <small>触发条件：${escapeHtml(patch.activation_condition || '')}</small>
            </div>
            <em>影响 case：${escapeHtml((patch.affected_cases || []).join(' / ') || patch.case_id || '-')}</em>
        </div>
    `;
}

function renderAgentProfile(profile) {
    const el = document.getElementById('evo-agent-profile');
    if (!el) return;
    if (!profile || profile.error) {
        el.innerHTML = '<span class="text-muted">Agent 能力画像暂不可用</span>';
        return;
    }

    const loop = profile.loop || [];
    const skills = profile.skill_memory || [];
    const promptRules = profile.prompt_rules || [];
    const reflections = profile.failure_reflections || [];
    const toolStats = profile.tool_policy?.stats || {};

    el.innerHTML = `
        <div class="agent-profile-header">
            <div>
                <h4>${escapeHtml(profile.agent_name || 'Ops Factory Multi-Agent RCA')}</h4>
                <p>${escapeHtml(profile.base_model_role || '')}</p>
            </div>
            <span class="badge">迭代 ${profile.agent_state?.iterations || 0} · Policy v${profile.agent_state?.policy_version || 1}</span>
        </div>
        <div class="agent-state-note">最近一次自进化更新：${escapeHtml(profile.agent_state?.last_update_reason || 'unknown')}</div>
        <div class="agent-loop">
            ${loop.map(item => `
                <div class="agent-loop-step">
                    <strong>${escapeHtml(item.label || item.stage)}</strong>
                    <span>${escapeHtml(item.description || '')}</span>
                </div>
            `).join('')}
        </div>
        <div class="agent-profile-grid">
            <div class="agent-panel">
                <h4>工具策略</h4>
                ${Object.entries(toolStats).map(([tool, stat]) => `
                    <div class="tool-policy-row">
                        <span>${escapeHtml(tool)}</span>
                        <strong>${((stat.hit_rate || 0) * 100).toFixed(0)}%</strong>
                        <small>${stat.runs || 0} 次 / MRR ${(stat.avg_mrr || 0).toFixed(3)}</small>
                    </div>
                `).join('') || '<span class="text-muted">暂无工具统计</span>'}
            </div>
            <div class="agent-panel">
                <h4>技能记忆</h4>
                ${skills.slice(0, 6).map(skill => `
                    <div class="skill-memory-item">
                        <strong>${escapeHtml(skill.name)}</strong>
                        <span>${escapeHtml(skill.policy)}</span>
                        <small>${escapeHtml(skill.evidence || '')}</small>
                    </div>
                `).join('') || '<span class="text-muted">成功样本积累后会生成技能</span>'}
            </div>
            <div class="agent-panel">
                <h4>提示词补丁</h4>
                ${promptRules.map(rule => `<div class="prompt-rule">${escapeHtml(rule)}</div>`).join('')}
            </div>
            <div class="agent-panel">
                <h4>失败反思</h4>
                ${reflections.map(item => `
                    <div class="failure-reflection">
                        <strong>${escapeHtml(item.case_id || '?')}</strong>
                        <span>${escapeHtml(item.miss || '')}</span>
                        <small>${escapeHtml(item.repair || '')}</small>
                    </div>
                `).join('') || '<span class="text-muted">暂无失败反思</span>'}
            </div>
        </div>
    `;
}
