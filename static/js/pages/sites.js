import { api, chip, escapeHtml, quotaGateChip, quotaStateChip } from '../api.js';

export async function renderSites(el) {
  const [data, counts, protocols, quotaSnap] = await Promise.all([
    api('/api/sites'),
    api('/api/site-counts'),
    api('/api/protocols'),
    api('/api/quota_gate/snap'),
  ]);
  const items = data.items || [];
  const activeProto = (protocols.items || []).find((p) => p.status === '生效');
  const gateAligned = !!quotaSnap.aligned;

  el.innerHTML = `
    <div class="card">
      <h2>${quotaGateChip(quotaSnap)} 试验中心目录</h2>
      <p class="muted">登记临床试验中心、启停状态与访视抓取配额。停用前须先结束采集中批次。配额徽标与已用配额对不上时，超配额抓取会被配额闸挡下。</p>
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div class="detail-box" style="flex:1;min-width:260px">
          <strong>配额闸命运（甲路：切流账册 + 物化包）</strong><br/>
          <span class="muted">读轨：</span>${escapeHtml(quotaSnap.active_read || 'old_track')}
          ｜ <span class="muted">切流指纹：</span>${escapeHtml(quotaSnap.cutover_fp || '空')}
          ｜ <span class="muted">拒回刷：</span>${quotaSnap.reject_refresh ? '开' : '关'}
          ｜ <span class="muted">归档：</span>${quotaSnap.archive_count || 0}
          ｜ <span class="muted">物化中心：</span>${quotaSnap.pack_count || 0}
          <div class="muted" style="margin-top:6px">
            徽标由物化包实况计算：抓取使真实已用配额漂移后徽标转红，重新落台收口恢复真绿。
          </div>
        </div>
        <div class="row" style="margin:0">
          <button id="quotaCommit" type="button" ${gateAligned ? 'class="ghost"' : ''}>
            ${gateAligned ? '重新落台收口' : '落台收口配额闸'}
          </button>
        </div>
      </div>
      <div id="quotaMsg" class="muted" style="margin-top:8px"></div>
      <div class="grid">
        <div class="stat"><div class="n">${counts.total || 0}</div><div class="l">中心总数</div></div>
        <div class="stat"><div class="n">${counts['启用'] || 0}</div><div class="l">启用</div></div>
        <div class="stat"><div class="n">${counts['观察'] || 0}</div><div class="l">观察</div></div>
        <div class="stat"><div class="n">${counts['停用'] || 0}</div><div class="l">停用</div></div>
      </div>
      <div class="row">
        <input id="sname" placeholder="中心名称" />
        <input id="sregion" placeholder="区域" />
        <input id="sprincipal" placeholder="负责人" />
        <input id="squota" type="number" value="50" style="width:100px" title="配额" />
        <input id="snote" placeholder="备注" />
        <button id="create">登记中心</button>
      </div>
      <p class="muted">当前生效方案：${activeProto ? escapeHtml(activeProto.title + ' @ ' + activeProto.version) : '无'}</p>
    </div>
    <div class="card">
      <div class="row">
        <select id="filterStatus">
          <option value="">全部状态</option>
          <option>启用</option>
          <option>观察</option>
          <option>停用</option>
        </select>
        <button id="refresh" class="ghost">刷新</button>
      </div>
      <table>
        <thead>
          <tr>
            <th>编码</th><th>名称</th><th>状态</th><th>配额</th>
            <th>已用配额(真实)</th><th>已用配额(物化包)</th><th>配额徽标</th>
            <th>批次</th><th>缺口合计</th><th>区域/负责人</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          ${items.map((s) => `
            <tr data-id="${s.id}">
              <td>${escapeHtml(s.site_code)}</td>
              <td>
                <div>${escapeHtml(s.name)}</div>
                <div class="muted">${escapeHtml(s.note || '')}</div>
              </td>
              <td>${chip(s.status)}</td>
              <td>${s.quota}</td>
              <td><strong>${s.used_live ?? 0}</strong></td>
              <td>${s.used_pack ?? 0}</td>
              <td>${quotaStateChip(s.quota_state)}</td>
              <td>${s.batch_count}</td>
              <td>${s.gap_total}</td>
              <td>${escapeHtml(s.region || '-')} / ${escapeHtml(s.principal || '-')}</td>
              <td class="row">
                <button data-st="启用" data-id="${s.id}">启用</button>
                <button data-st="观察" data-id="${s.id}" class="warn">观察</button>
                <button data-st="停用" data-id="${s.id}" class="danger">停用</button>
                <button data-q="${s.id}">配额+10</button>
                <button data-detail="${s.id}" class="ghost">详情</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
      <div id="siteDetail" class="detail-box" style="margin-top:12px">选择一行查看批次明细。</div>
    </div>
    <div class="card">
      <h2>方案版本</h2>
      <p class="muted">生效方案决定抓取时的必填字段与访视编码清单。同时只能有一个生效方案。</p>
      <div class="row">
        <input id="ptitle" placeholder="方案标题" />
        <input id="pver" placeholder="版本" value="1.1" style="width:90px" />
        <input id="pfields" placeholder="必填字段逗号分隔" style="min-width:260px"
          value="subject_key,visit_code,visit_date,investigator,weight_kg" />
        <input id="pvisits" placeholder="访视编码" value="V1,V2,V3,EOT" />
        <button id="pcreate">登记方案</button>
      </div>
      <table>
        <thead><tr><th>编码</th><th>标题</th><th>版本</th><th>状态</th><th>必填字段</th><th>操作</th></tr></thead>
        <tbody>
          ${(protocols.items || []).map((p) => `
            <tr>
              <td>${escapeHtml(p.protocol_code)}</td>
              <td>${escapeHtml(p.title)}</td>
              <td>${escapeHtml(p.version)}</td>
              <td>${chip(p.status)}</td>
              <td class="muted">${escapeHtml((p.required_fields_list || []).join(', '))}</td>
              <td class="row">
                <button data-pstatus="生效" data-pid="${p.id}">生效</button>
                <button data-pstatus="草稿" data-pid="${p.id}" class="ghost">草稿</button>
                <button data-pstatus="废止" data-pid="${p.id}" class="danger">废止</button>
              </td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;

  const errBox = (msg) => {
    const box = el.querySelector('#siteDetail');
    box.textContent = '错误：' + msg;
  };

  const siteRow = (s) => `
    <tr data-id="${s.id}">
      <td>${escapeHtml(s.site_code)}</td>
      <td>
        <div>${escapeHtml(s.name)}</div>
        <div class="muted">${escapeHtml(s.note || '')}</div>
      </td>
      <td>${chip(s.status)}</td>
      <td>${s.quota}</td>
      <td><strong>${s.used_live ?? 0}</strong></td>
      <td>${s.used_pack ?? 0}</td>
      <td>${quotaStateChip(s.quota_state)}</td>
      <td>${s.batch_count}</td>
      <td>${s.gap_total}</td>
      <td>${escapeHtml(s.region || '-')} / ${escapeHtml(s.principal || '-')}</td>
      <td class="row">
        <button data-st="启用" data-id="${s.id}">启用</button>
        <button data-st="观察" data-id="${s.id}" class="warn">观察</button>
        <button data-st="停用" data-id="${s.id}" class="danger">停用</button>
        <button data-q="${s.id}">配额+10</button>
        <button data-detail="${s.id}" class="ghost">详情</button>
      </td>
    </tr>`;

  const quotaMsg = el.querySelector('#quotaMsg');
  el.querySelector('#quotaCommit').onclick = async () => {
    quotaMsg.className = 'muted';
    quotaMsg.textContent = '落台中…';
    try {
      const r = await api('/api/quota_gate/commit', {
        method: 'POST',
        body: JSON.stringify({}),
      });
      quotaMsg.textContent = `已落台：读轨 ${r.active_read} · 指纹 ${r.cutover_fp} · 对齐 ${r.aligned ? '是' : '否'}`;
      await renderSites(el);
    } catch (e) {
      quotaMsg.className = 'err';
      quotaMsg.textContent = '落台失败：' + e.message;
    }
  };

  el.querySelector('#create').onclick = async () => {
    try {
      await api('/api/sites', {
        method: 'POST',
        body: JSON.stringify({
          name: el.querySelector('#sname').value,
          region: el.querySelector('#sregion').value,
          principal: el.querySelector('#sprincipal').value,
          quota: Number(el.querySelector('#squota').value || 50),
          note: el.querySelector('#snote').value,
        }),
      });
      await renderSites(el);
    } catch (e) { errBox(e.message); }
  };

  el.querySelector('#refresh').onclick = () => renderSites(el);
  el.querySelector('#filterStatus').onchange = async (ev) => {
    const status = ev.target.value;
    const url = status ? `/api/sites?status=${encodeURIComponent(status)}` : '/api/sites';
    const filtered = await api(url);
    const tbody = el.querySelector('tbody');
    tbody.innerHTML = (filtered.items || []).map(siteRow).join('');
  };

  el.querySelectorAll('[data-st]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/api/sites/${btn.dataset.id}/status`, {
          method: 'POST',
          body: JSON.stringify({ status: btn.dataset.st }),
        });
        await renderSites(el);
      } catch (e) { errBox(e.message); }
    };
  });

  el.querySelectorAll('[data-q]').forEach((btn) => {
    btn.onclick = async () => {
      const site = items.find((x) => String(x.id) === btn.dataset.q);
      try {
        await api(`/api/sites/${btn.dataset.q}/quota`, {
          method: 'POST',
          body: JSON.stringify({ quota: (site?.quota || 50) + 10 }),
        });
        await renderSites(el);
      } catch (e) { errBox(e.message); }
    };
  });

  el.querySelectorAll('[data-detail]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        const detail = await api(`/api/sites/${btn.dataset.detail}`);
        el.querySelector('#siteDetail').textContent = JSON.stringify({
          中心: detail.name,
          状态: detail.status,
          开放缺口: detail.open_gaps,
          批次: (detail.batches || []).map((b) => ({
            编码: b.batch_code,
            状态: b.status,
            行数: b.item_count,
            缺口: b.gap_count,
          })),
        }, null, 2);
      } catch (e) { errBox(e.message); }
    };
  });

  el.querySelector('#pcreate').onclick = async () => {
    const fields = el.querySelector('#pfields').value.split(',').map((x) => x.trim()).filter(Boolean);
    const visits = el.querySelector('#pvisits').value.split(',').map((x) => x.trim()).filter(Boolean);
    try {
      await api('/api/protocols', {
        method: 'POST',
        body: JSON.stringify({
          title: el.querySelector('#ptitle').value,
          version: el.querySelector('#pver').value,
          required_fields: fields,
          visit_codes: visits,
        }),
      });
      await renderSites(el);
    } catch (e) { errBox(e.message); }
  };

  el.querySelectorAll('[data-pstatus]').forEach((btn) => {
    btn.onclick = async () => {
      try {
        await api(`/api/protocols/${btn.dataset.pid}/status`, {
          method: 'POST',
          body: JSON.stringify({ status: btn.dataset.pstatus }),
        });
        await renderSites(el);
      } catch (e) { errBox(e.message); }
    };
  });
}
