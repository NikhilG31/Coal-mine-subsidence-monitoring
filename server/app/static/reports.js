/**
 * static/reports.js
 * Owns worker safety problem reporting, superior reports inbox, and persistent SOS banners.
 * Must NOT handle hardware command dispatching, badge issuance, or bypass role scoping.
 */

let reportsSosPollInterval = null;
let currentReportsUser = null;

function escapeHtmlRep(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function formatElapsed(isoTimestamp) {
  if (!isoTimestamp) return '0s ago';
  const diffSec = Math.max(0, Math.floor((Date.now() - new Date(isoTimestamp).getTime()) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ${diffSec % 60}s ago`;
  const diffHr = Math.floor(diffMin / 60);
  return `${diffHr}h ${diffMin % 60}m ago`;
}

// ==========================================
// INITIALIZER & PERSISTENT SOS BANNER POLLING
// ==========================================

function initReports() {
  const roleEl = document.getElementById('currentUserRole');
  const role = roleEl ? roleEl.textContent.trim().toLowerCase() : null;
  const btnReports = document.getElementById('btnReports');

  if (role) {
    if (btnReports) btnReports.style.display = 'inline-flex';
    pollSosReports();
    if (!reportsSosPollInterval) {
      reportsSosPollInterval = setInterval(pollSosReports, 3000);
    }
  } else {
    if (btnReports) btnReports.style.display = 'none';
    const banner = document.getElementById('sosAlertBanner');
    if (banner) banner.style.display = 'none';
  }
}

async function pollSosReports() {
  try {
    const res = await fetch('/api/reports?status=open&type=sos');
    if (!res.ok) return;
    const data = await res.json();
    const sosReports = Array.isArray(data) ? data : (data.reports || []);
    updateSosBanner(sosReports);
  } catch (err) {
    // Network or server error; do not disrupt UI
  }
}

function updateSosBanner(sosReports) {
  const banner = document.getElementById('sosAlertBanner');
  if (!banner) return;

  if (sosReports.length === 0) {
    banner.style.display = 'none';
    return;
  }

  // Display details of the latest active SOS report
  const topReport = sosReports[0];
  const bannerText = document.getElementById('sosBannerText');
  let subject = 'DISTRESS SIGNAL';
  if (topReport.node_number) {
    subject = `Device Node #${escapeHtmlRep(topReport.node_number)}`;
  } else if (topReport.reporter_name) {
    subject = `Worker ${escapeHtmlRep(topReport.reporter_name)}`;
  } else if (topReport.source === 'device') {
    subject = 'Device Node (Unplaced)';
  }

  const tunnelInfo = topReport.tunnel_name ? `Tunnel: ${escapeHtmlRep(topReport.tunnel_name)}` : 'Location Unassigned';
  const repeats = topReport.repeat_count > 1 ? ` • ${topReport.repeat_count} signals` : '';
  const elapsed = formatElapsed(topReport.last_signal_at || topReport.created_at);

  if (bannerText) {
    bannerText.textContent = `${subject} • ${tunnelInfo}${repeats} • ${elapsed} (${sosReports.length} Active SOS)`;
  }
  banner.style.display = 'block';
}

// ==========================================
// MODAL CONTROLS & ROUTING
// ==========================================

async function openReportsModal() {
  const modal = document.getElementById('reportsModal');
  if (!modal) return;
  modal.classList.add('open');
  modal.classList.add('active');

  const roleEl = document.getElementById('currentUserRole');
  const role = roleEl ? roleEl.textContent.trim().toLowerCase() : '';
  const isSuperior = ['manager', 'safety_officer', 'engineer'].includes(role);

  const workerSection = document.getElementById('workerReportsSection');
  const superiorSection = document.getElementById('superiorReportsSection');

  if (isSuperior) {
    if (workerSection) workerSection.style.display = 'none';
    if (superiorSection) superiorSection.style.display = 'block';
    await loadSuperiorReports();
  } else {
    if (workerSection) workerSection.style.display = 'block';
    if (superiorSection) superiorSection.style.display = 'none';
    await populateWorkerTunnels();
    await loadMyReports();
  }
}

function closeReportsModal() {
  const modal = document.getElementById('reportsModal');
  if (!modal) return;
  modal.classList.remove('open');
  modal.classList.remove('active');
}

// ==========================================
// WORKER VIEW LOGIC
// ==========================================

async function populateWorkerTunnels() {
  const tunnelSelect = document.getElementById('workerReportTunnel');
  if (!tunnelSelect) return;
  tunnelSelect.innerHTML = '<option value="">Loading locations...</option>';

  try {
    const res = await fetch('/api/mines');
    if (!res.ok) throw new Error('Failed to load mines');
    const data = await res.json();
    const mines = data.mines || [];

    let optionsHtml = '<option value="">(No specific tunnel)</option>';
    for (const m of mines) {
      const tRes = await fetch(`/api/mines/${m.id}/tunnels`);
      if (tRes.ok) {
        const tData = await tRes.json();
        for (const t of (tData.tunnels || [])) {
          optionsHtml += `<option value="${t.id}">${escapeHtmlRep(m.name)} - ${escapeHtmlRep(t.name)} (${escapeHtmlRep(t.code)})</option>`;
        }
      }
    }
    tunnelSelect.innerHTML = optionsHtml;
  } catch (err) {
    tunnelSelect.innerHTML = '<option value="">(No specific tunnel)</option>';
  }
}

async function handleWorkerReportSubmit(e) {
  e.preventDefault();
  const type = document.getElementById('workerReportType').value;
  const severity = document.getElementById('workerReportSeverity').value;
  const tunnelVal = document.getElementById('workerReportTunnel').value;
  const description = document.getElementById('workerReportDescription').value.trim();

  const payload = {
    type: type,
    severity: severity,
    description: description || null,
  };
  if (tunnelVal) {
    payload.tunnel_id = parseInt(tunnelVal, 10);
  }

  try {
    const res = await fetch('/api/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (res.status === 429) {
      const retryAfter = res.headers.get('Retry-After');
      alert(`Rate limit reached: Maximum 20 reports per hour. Please wait ${retryAfter || 60} seconds.`);
      return;
    }
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      alert(`Submission failed: ${errData.detail || 'Server error'}`);
      return;
    }

    document.getElementById('workerReportDescription').value = '';
    alert('Report submitted successfully.');
    await loadMyReports();
  } catch (err) {
    alert(`Submission error: ${err.message}`);
  }
}

async function handleWorkerSosClick() {
  const confirmed = confirm('Send an SOS from this device?\n\nThis will instantly dispatch a critical distress alert to all safety superiors.');
  if (!confirmed) return;

  const tunnelVal = document.getElementById('workerReportTunnel') ? document.getElementById('workerReportTunnel').value : null;
  const payload = {
    type: 'sos',
    severity: 'critical',
    description: 'Worker manual distress SOS alert',
  };
  if (tunnelVal) {
    payload.tunnel_id = parseInt(tunnelVal, 10);
  }

  try {
    const res = await fetch('/api/reports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (res.status === 429) {
      alert('Rate limit reached: Please notify personnel in person or via telephone.');
      return;
    }
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      alert(`SOS Alert failed: ${errData.detail || 'Server error'}`);
      return;
    }

    alert('CRITICAL SOS ALERT SENT. Superiors have been notified.');
    await loadMyReports();
    pollSosReports();
  } catch (err) {
    alert(`SOS failed to transmit: ${err.message}`);
  }
}

async function loadMyReports() {
  const container = document.getElementById('myReportsContainer');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--ink-3); font-size:12px;">Loading reports...</div>';

  try {
    const res = await fetch('/api/reports');
    if (!res.ok) throw new Error('Failed to fetch reports');
    const data = await res.json();
    const reports = Array.isArray(data) ? data : (data.reports || []);

    if (reports.length === 0) {
      container.innerHTML = '<div style="color:var(--ink-3); font-size:12px; padding:8px 0;">No reports submitted yet.</div>';
      return;
    }

    let html = `<table class="neo-table" style="font-size:12px; width:100%;">
      <thead>
        <tr>
          <th>Time</th>
          <th>Type</th>
          <th>Severity</th>
          <th>Tunnel</th>
          <th>Status</th>
          <th>Resolution</th>
        </tr>
      </thead>
      <tbody>`;

    for (const r of reports) {
      const statusBadge = getStatusBadge(r.status);
      const sevBadge = getSeverityBadge(r.severity);
      html += `<tr>
        <td>${formatElapsed(r.created_at)}</td>
        <td><strong>${escapeHtmlRep(r.type.toUpperCase())}</strong></td>
        <td>${sevBadge}</td>
        <td>${r.tunnel_name ? escapeHtmlRep(r.tunnel_name) : '-'}</td>
        <td>${statusBadge}</td>
        <td style="font-size:11px; color:#555;">${r.resolution_note ? escapeHtmlRep(r.resolution_note) : (r.status === 'resolved' ? 'Resolved' : '-')}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div style="color:#d50000; font-size:12px;">Failed to load reports: ${escapeHtmlRep(err.message)}</div>`;
  }
}

// ==========================================
// SUPERIOR INBOX LOGIC
// ==========================================

async function loadSuperiorReports() {
  const container = document.getElementById('superiorReportsContainer');
  if (!container) return;
  container.innerHTML = '<div style="color:var(--ink-3); font-size:12px;">Loading reports inbox...</div>';

  const statusFilter = document.getElementById('superiorFilterStatus') ? document.getElementById('superiorFilterStatus').value : '';
  const typeFilter = document.getElementById('superiorFilterType') ? document.getElementById('superiorFilterType').value : '';

  let url = '/api/reports?';
  const params = [];
  if (statusFilter) params.push(`status=${encodeURIComponent(statusFilter)}`);
  if (typeFilter) params.push(`type=${encodeURIComponent(typeFilter)}`);
  url += params.join('&');

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to load reports inbox');
    const data = await res.json();
    const reports = Array.isArray(data) ? data : (data.reports || []);

    if (reports.length === 0) {
      container.innerHTML = '<div style="color:var(--ink-3); font-size:12px; padding:12px 0;">No reports matching criteria.</div>';
      return;
    }

    let html = `<table class="neo-table" style="font-size:12px; width:100%;">
      <thead>
        <tr>
          <th>Time / Signal</th>
          <th>Source</th>
          <th>Type &amp; Severity</th>
          <th>Location / Node</th>
          <th>Reporter</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>`;

    for (const r of reports) {
      const isDevice = r.source === 'device';
      const statusBadge = getStatusBadge(r.status);
      const sevBadge = getSeverityBadge(r.severity);

      let locInfo = r.tunnel_name ? escapeHtmlRep(r.tunnel_name) : 'Unassigned';
      if (isDevice) {
        locInfo += `<br><span style="font-size:11px; font-weight:700; color:#d50000;">Node #${r.node_number || 'Unplaced'}</span>`;
        if (r.gateway_name) locInfo += ` (${escapeHtmlRep(r.gateway_name)})`;
        if (r.repeat_count > 1) locInfo += ` • <span style="color:#b71c1c;">${r.repeat_count}x</span>`;
      }

      let rollCallBtn = '';
      if (r.open_incident_id) {
        rollCallBtn = `<button class="neo-btn btn-small btn-gold" style="font-size:10px; margin-top:4px;" onclick="viewIncidentRollCall(${r.open_incident_id})">📋 Roll Call</button>`;
      }

      let actionsHtml = '-';
      if (r.status === 'open') {
        actionsHtml = `
          <button class="neo-btn btn-small" style="font-size:11px; padding:2px 6px;" onclick="transitionReport(${r.id}, 'acknowledged')">Acknowledge</button>
          <button class="neo-btn btn-small btn-success" style="font-size:11px; padding:2px 6px;" onclick="promptResolveReport(${r.id})">Resolve</button>
        `;
      } else if (r.status === 'acknowledged') {
        actionsHtml = `
          <button class="neo-btn btn-small btn-success" style="font-size:11px; padding:2px 6px;" onclick="promptResolveReport(${r.id})">Resolve</button>
        `;
      }

      html += `<tr>
        <td>
          ${formatElapsed(r.last_signal_at || r.created_at)}
          ${r.description ? `<br><span style="font-size:11px; color:#444;">${escapeHtmlRep(r.description)}</span>` : ''}
        </td>
        <td><span style="font-family:var(--mono); font-size:11px; font-weight:700;">${escapeHtmlRep(r.source.toUpperCase())}</span></td>
        <td><strong>${escapeHtmlRep(r.type.toUpperCase())}</strong><br>${sevBadge}</td>
        <td>${locInfo}${rollCallBtn}</td>
        <td>${r.reporter_name ? escapeHtmlRep(r.reporter_name) : (isDevice ? 'Hardware Node' : '-')}</td>
        <td>${statusBadge}</td>
        <td style="white-space:nowrap;">${actionsHtml}</td>
      </tr>`;
    }

    html += `</tbody></table>`;
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div style="color:#d50000; font-size:12px;">Failed to load inbox: ${escapeHtmlRep(err.message)}</div>`;
  }
}

async function transitionReport(reportId, nextStatus, note = null) {
  const payload = { status: nextStatus };
  if (note !== null) payload.resolution_note = note;

  try {
    const res = await fetch(`/api/reports/${reportId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(`Action failed: ${err.detail || 'Server error'}`);
      return;
    }

    await loadSuperiorReports();
    pollSosReports();
  } catch (err) {
    alert(`Request error: ${err.message}`);
  }
}

function promptResolveReport(reportId) {
  const note = prompt('Enter resolution note (optional):', 'Issue resolved and verified');
  if (note === null) return;
  transitionReport(reportId, 'resolved', note);
}

function viewIncidentRollCall(incidentId) {
  closeReportsModal();
  if (typeof openRollCallModal === 'function') {
    openRollCallModal(incidentId);
  }
}

// ==========================================
// BADGE FORMATTERS
// ==========================================

function getStatusBadge(status) {
  if (status === 'open') {
    return '<span style="background:#ffcdd2; color:#b71c1c; font-size:11px; font-weight:700; padding:2px 6px; border:1px solid #e57373;">OPEN</span>';
  } else if (status === 'acknowledged') {
    return '<span style="background:#bbdefb; color:#0d47a1; font-size:11px; font-weight:700; padding:2px 6px; border:1px solid #64b5f6;">ACKNOWLEDGED</span>';
  } else if (status === 'resolved') {
    return '<span style="background:#c8e6c9; color:#1b5e20; font-size:11px; font-weight:700; padding:2px 6px; border:1px solid #81c784;">RESOLVED</span>';
  }
  return `<span style="font-size:11px;">${escapeHtmlRep(status)}</span>`;
}

function getSeverityBadge(severity) {
  if (severity === 'critical') {
    return '<span style="background:#ffebee; color:#b71c1c; font-size:10px; font-weight:800; padding:1px 5px; border:1px solid #ff1744;">CRITICAL</span>';
  } else if (severity === 'high') {
    return '<span style="background:#fff3e0; color:#e65100; font-size:10px; font-weight:700; padding:1px 5px; border:1px solid #ff9800;">HIGH</span>';
  } else if (severity === 'medium') {
    return '<span style="background:#fffde7; color:#f57f17; font-size:10px; font-weight:700; padding:1px 5px; border:1px solid #fbc02d;">MEDIUM</span>';
  }
  return '<span style="background:#f1f8e9; color:#33691e; font-size:10px; font-weight:600; padding:1px 5px; border:1px solid #8bc34a;">LOW</span>';
}

// Hook into DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initReports, 1200);
  setInterval(initReports, 3000);
});
