// static/incidents.js
// Step 7: Incidents, Emergency Roll Call, Downlink Commands, and Buzzer Controls

let activeIncidents = [];
let currentRollCallIncidentId = null;
let rollCallPollInterval = null;
let incidentPollInterval = null;

// Escaping helper
function escapeHtmlInc(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// 1. Initializer & Polling
function initIncidents() {
  const role = window.currentUserRole || (document.getElementById('currentUserRole') ? document.getElementById('currentUserRole').textContent.trim() : null);
  const btnIncidents = document.getElementById('btnIncidents');
  
  if (role && (role === 'manager' || role === 'safety_officer' || role === 'engineer' || role === 'foreman')) {
    if (btnIncidents) btnIncidents.style.display = 'inline-block';
    
    // Superior only UI checks
    const isSuperior = (role === 'manager' || role === 'safety_officer' || role === 'engineer');
    const bcastBtn = document.getElementById('btnOpenBroadcastForm');
    if (bcastBtn) bcastBtn.style.display = isSuperior ? 'inline-block' : 'none';

    // Node drawer buzzer controls
    const buzzerSec = document.getElementById('drawerBuzzerSection');
    if (buzzerSec) buzzerSec.style.display = isSuperior ? 'block' : 'none';

    refreshIncidents();
    if (!incidentPollInterval) {
      incidentPollInterval = setInterval(refreshIncidents, 10000);
    }
  } else {
    if (btnIncidents) btnIncidents.style.display = 'none';
    const banner = document.getElementById('incidentAlertBanner');
    if (banner) banner.style.display = 'none';
  }
}

// 2. Refresh Incidents
async function refreshIncidents() {
  try {
    const res = await fetch('/api/incidents?status=open');
    if (!res.ok) return;
    const data = await res.json();
    activeIncidents = data.incidents || [];
    updateEmergencyBanner();
    if (document.getElementById('incidentsModal') && document.getElementById('incidentsModal').classList.contains('active')) {
      renderIncidentsList();
    }
  } catch (err) {
    console.error('Error refreshing incidents:', err);
  }
}

// 3. Emergency Banner
function updateEmergencyBanner() {
  const banner = document.getElementById('incidentAlertBanner');
  if (!banner) return;

  if (activeIncidents.length > 0) {
    const inc = activeIncidents[0]; // Primary active incident
    const bannerText = document.getElementById('incidentBannerText');
    const unaccountedSpan = document.getElementById('incidentBannerUnaccounted');
    
    if (bannerText) {
      bannerText.textContent = `LEVEL ${inc.level} INCIDENT: ${inc.title} (${inc.tunnel_name || inc.mine_name || 'Active'})`;
    }
    if (unaccountedSpan) {
      unaccountedSpan.textContent = inc.unaccounted_count;
    }
    banner.style.display = 'block';
  } else {
    banner.style.display = 'none';
  }
}

function openActiveIncidentRollCall() {
  if (activeIncidents.length > 0) {
    openRollCallModal(activeIncidents[0].id);
  }
}

// 4. Incidents Modal
async function openIncidentsModal() {
  const modal = document.getElementById('incidentsModal');
  if (!modal) return;
  modal.classList.add('active');

  // Load mines and tunnels for broadcast alert dropdowns
  populateBroadcastSelects();
  loadAllIncidentsList();
}

function closeIncidentsModal() {
  const modal = document.getElementById('incidentsModal');
  if (modal) modal.classList.remove('active');
}

async function populateBroadcastSelects() {
  const mineSelect = document.getElementById('broadcastMineSelect');
  const tunnelSelect = document.getElementById('broadcastTunnelSelect');
  if (!mineSelect || !tunnelSelect) return;

  mineSelect.innerHTML = '<option value="">Select Mine...</option>';
  tunnelSelect.innerHTML = '<option value="">All Tunnels (Mine-wide)</option>';

  try {
    const res = await fetch('/api/mines');
    if (res.ok) {
      const mines = await res.json();
      mines.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name;
        mineSelect.appendChild(opt);
      });
    }
  } catch (err) {
    console.error('Failed to populate mines:', err);
  }

  mineSelect.onchange = async () => {
    tunnelSelect.innerHTML = '<option value="">All Tunnels (Mine-wide)</option>';
    if (!mineSelect.value) return;
    try {
      const res = await fetch(`/api/mines/${mineSelect.value}/tunnels`);
      if (res.ok) {
        const tunnels = await res.json();
        tunnels.forEach(t => {
          const opt = document.createElement('option');
          opt.value = t.id;
          opt.textContent = t.name;
          tunnelSelect.appendChild(opt);
        });
      }
    } catch (err) {
      console.error('Failed to populate tunnels:', err);
    }
  };
}

function toggleBroadcastForm() {
  const formBox = document.getElementById('broadcastFormBox');
  if (!formBox) return;
  formBox.style.display = (formBox.style.display === 'none' || !formBox.style.display) ? 'block' : 'none';
}

async function handleBroadcastSubmit(event) {
  event.preventDefault();
  const mineId = document.getElementById('broadcastMineSelect').value;
  const tunnelId = document.getElementById('broadcastTunnelSelect').value || null;
  const title = document.getElementById('broadcastTitle').value.trim();
  const level = parseInt(document.getElementById('broadcastLevel').value, 10);
  const siren = document.getElementById('broadcastSiren').checked;

  if (!mineId || !title || !level) {
    alert('Please fill all required fields');
    return;
  }

  try {
    const res = await fetch('/api/incidents', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        mine_id: parseInt(mineId, 10),
        tunnel_id: tunnelId ? parseInt(tunnelId, 10) : null,
        title: title,
        level: level,
        siren: siren
      })
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`Failed to broadcast: ${err.detail || 'Error'}`);
      return;
    }

    document.getElementById('broadcastTitle').value = '';
    toggleBroadcastForm();
    await refreshIncidents();
    loadAllIncidentsList();
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

async function loadAllIncidentsList() {
  const container = document.getElementById('incidentsListContainer');
  if (!container) return;
  container.innerHTML = '<div style="padding:10px; color:var(--ink-3);">Loading incidents...</div>';

  try {
    const res = await fetch('/api/incidents?status=all');
    if (!res.ok) {
      container.innerHTML = '<div style="color:red; padding:10px;">Failed to load incidents</div>';
      return;
    }
    const data = await res.json();
    const list = data.incidents || [];
    if (list.length === 0) {
      container.innerHTML = '<div style="padding:14px; color:var(--ink-3); text-align:center;">No recorded incidents.</div>';
      return;
    }

    const role = window.currentUserRole || (document.getElementById('currentUserRole') ? document.getElementById('currentUserRole').textContent.trim() : null);
    const isSuperior = (role === 'manager' || role === 'safety_officer' || role === 'engineer');

    container.innerHTML = list.map(inc => {
      const isOpen = inc.status === 'open';
      const statusBadge = isOpen
        ? `<span class="badge badge-danger">OPEN (LEVEL ${inc.level})</span>`
        : `<span class="badge" style="background:#888; color:#fff;">CLOSED</span>`;

      const sirenBadge = inc.siren_active
        ? `<span class="badge badge-warning" style="animation:blinker 1s infinite;">🔊 SIREN ON</span>`
        : `<span class="badge" style="background:#eee; color:#666;">SIREN OFF</span>`;

      return `
        <div class="zine-box" style="margin-bottom:10px; padding:12px; border-left: 6px solid ${isOpen ? '#ff2d55' : '#888'};">
          <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:6px;">
            <div>
              <strong style="font-size:14px;">${escapeHtmlInc(inc.title)}</strong>
              <div style="font-size:11px; color:var(--ink-2); margin-top:2px;">
                Mine: <strong>${escapeHtmlInc(inc.mine_name || 'N/A')}</strong> | 
                Tunnel: <strong>${escapeHtmlInc(inc.tunnel_name || 'Mine-wide')}</strong> | 
                Source: <code>${inc.source}</code>
              </div>
            </div>
            <div style="display:flex; gap:6px; align-items:center;">
              ${statusBadge}
              ${sirenBadge}
            </div>
          </div>
          
          <div style="display:flex; justify-content:space-between; align-items:center; font-size:12px; margin-top:8px; padding-top:8px; border-top:1px dashed #ddd;">
            <div>
              Roll Call: 
              <strong style="color:${inc.unaccounted_count > 0 ? '#ff2d55' : '#00875a'};">
                ${inc.unaccounted_count} Unaccounted
              </strong> / ${inc.total_count} Total
            </div>
            <div style="display:flex; gap:6px;">
              <button class="neo-btn btn-small btn-primary" onclick="openRollCallModal(${inc.id})">📋 Roll Call</button>
              ${isSuperior && isOpen ? `
                <button class="neo-btn btn-small" onclick="toggleIncidentSiren(${inc.id}, ${!inc.siren_active})">
                  ${inc.siren_active ? 'Mute Siren' : 'Activate Siren'}
                </button>
                <button class="neo-btn btn-small btn-danger" onclick="openCloseIncidentDialog(${inc.id}, ${inc.unaccounted_count})">
                  Close Incident
                </button>
              ` : ''}
            </div>
          </div>
        </div>
      `;
    }).join('');
  } catch (err) {
    container.innerHTML = `<div style="color:red; padding:10px;">Network error: ${err.message}</div>`;
  }
}

// 5. Siren Toggle
async function toggleIncidentSiren(incidentId, turnOn) {
  try {
    const res = await fetch(`/api/incidents/${incidentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ siren: turnOn })
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Failed to update siren: ${err.detail || 'Error'}`);
      return;
    }
    await refreshIncidents();
    loadAllIncidentsList();
  } catch (err) {
    alert(`Error: ${err.message}`);
  }
}

// 6. Close Incident
let pendingCloseIncidentId = null;
let pendingCloseUnaccounted = 0;

function openCloseIncidentDialog(incidentId, unaccountedCount) {
  pendingCloseIncidentId = incidentId;
  pendingCloseUnaccounted = unaccountedCount;
  const modal = document.getElementById('closeIncidentModal');
  if (!modal) return;

  const warnBox = document.getElementById('closeIncidentWarnBox');
  const confirmBox = document.getElementById('closeIncidentConfirmRow');
  const confirmCheckbox = document.getElementById('closeIncidentConfirmCheck');

  if (unaccountedCount > 0) {
    warnBox.style.display = 'block';
    warnBox.innerHTML = `⚠️ <strong>WARNING:</strong> ${unaccountedCount} personnel remain unaccounted for in the underground roll call!`;
    confirmBox.style.display = 'block';
    confirmCheckbox.checked = false;
  } else {
    warnBox.style.display = 'none';
    confirmBox.style.display = 'none';
    confirmCheckbox.checked = true;
  }

  document.getElementById('closeIncidentNote').value = '';
  modal.classList.add('active');
}

function closeCloseIncidentModal() {
  const modal = document.getElementById('closeIncidentModal');
  if (modal) modal.classList.remove('active');
}

async function handleCloseIncidentSubmit(event) {
  event.preventDefault();
  if (!pendingCloseIncidentId) return;

  const note = document.getElementById('closeIncidentNote').value.trim();
  const confirmCheck = document.getElementById('closeIncidentConfirmCheck').checked;

  if (pendingCloseUnaccounted > 0 && !confirmCheck) {
    alert('You must confirm all personnel are accounted for externally before closing this incident.');
    return;
  }

  try {
    const res = await fetch(`/api/incidents/${pendingCloseIncidentId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        close: true,
        close_note: note || null,
        confirm_unaccounted: confirmCheck
      })
    });

    if (!res.ok) {
      const err = await res.json();
      const msg = typeof err.detail === 'string' ? err.detail : (err.detail && err.detail.message ? err.detail.message : (err.code ? `${err.code}: ${JSON.stringify(err)}` : JSON.stringify(err)));
      alert(`Cannot close incident: ${msg}`);
      return;
    }

    closeCloseIncidentModal();
    await refreshIncidents();
    loadAllIncidentsList();
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

// 7. Roll Call Modal
async function openRollCallModal(incidentId) {
  currentRollCallIncidentId = incidentId;
  const modal = document.getElementById('rollCallModal');
  if (!modal) return;
  modal.classList.add('active');

  loadRollCallRoster();
  if (rollCallPollInterval) clearInterval(rollCallPollInterval);
  rollCallPollInterval = setInterval(loadRollCallRoster, 5000);
}

function closeRollCallModal() {
  const modal = document.getElementById('rollCallModal');
  if (modal) modal.classList.remove('active');
  if (rollCallPollInterval) {
    clearInterval(rollCallPollInterval);
    rollCallPollInterval = null;
  }
  currentRollCallIncidentId = null;
}

async function loadRollCallRoster() {
  if (!currentRollCallIncidentId) return;
  const container = document.getElementById('rollCallContainer');
  if (!container) return;

  try {
    // 1. Fetch incident detail
    const resInc = await fetch(`/api/incidents/${currentRollCallIncidentId}`);
    if (resInc.ok) {
      const inc = await resInc.json();
      document.getElementById('rollCallTitle').textContent = `ROLL CALL: ${inc.title}`;
      document.getElementById('rollCallMetrics').innerHTML = `
        <span class="badge ${inc.unaccounted_count > 0 ? 'badge-danger' : 'badge-success'}">
          ${inc.unaccounted_count} Unaccounted
        </span>
        <span class="badge badge-success">${inc.accounted_count} Accounted</span>
        <span style="font-size:12px; color:var(--ink-2); font-weight:700;">Total: ${inc.total_count}</span>
      `;
    }

    // 2. Fetch roster
    const resRoster = await fetch(`/api/incidents/${currentRollCallIncidentId}/roster`);
    if (!resRoster.ok) return;
    const roster = await resRoster.json();

    const unaccounted = roster.filter(w => !w.accounted_at);
    const accounted = roster.filter(w => !!w.accounted_at);

    let html = '';

    // Unaccounted section
    html += `
      <div style="margin-bottom:14px;">
        <h4 style="font-family:var(--condensed); color:#ff2d55; margin-bottom:6px;">
          ⚠️ UNACCOUNTED PERSONNEL (${unaccounted.length})
        </h4>
    `;
    if (unaccounted.length === 0) {
      html += '<div style="padding:8px; font-size:12px; color:#00875a; font-weight:700; background:#e3fcef; border:1px solid #abf5d1;">All personnel are accounted for!</div>';
    } else {
      html += unaccounted.map(u => `
        <div class="zine-box" style="display:flex; justify-content:space-between; align-items:center; padding:10px; margin-bottom:6px; background:#ffebe6; border-left:4px solid #ff2d55;">
          <div>
            <strong>${escapeHtmlInc(u.full_name)}</strong>
            <span style="font-family:var(--mono); font-size:11px; color:#666; margin-left:6px;">Emp #${escapeHtmlInc(u.employee_no || 'N/A')}</span>
            <div style="font-size:11px; color:#888;">Added: ${new Date(u.added_at).toLocaleTimeString()}</div>
          </div>
          <button class="neo-btn btn-small btn-success" onclick="markWorkerAccounted(${u.user_id})">
            ✓ Mark Accounted
          </button>
        </div>
      `).join('');
    }
    html += '</div>';

    // Accounted section
    html += `
      <div>
        <h4 style="font-family:var(--condensed); color:#00875a; margin-bottom:6px;">
          ✓ ACCOUNTED PERSONNEL (${accounted.length})
        </h4>
    `;
    if (accounted.length === 0) {
      html += '<div style="padding:8px; font-size:12px; color:var(--ink-3);">No personnel accounted yet.</div>';
    } else {
      html += accounted.map(a => `
        <div class="zine-box" style="display:flex; justify-content:space-between; align-items:center; padding:8px; margin-bottom:4px; background:#f4f5f7; border-left:4px solid #00875a;">
          <div>
            <strong>${escapeHtmlInc(a.full_name)}</strong>
            <span style="font-family:var(--mono); font-size:11px; color:#666; margin-left:6px;">Emp #${escapeHtmlInc(a.employee_no || 'N/A')}</span>
          </div>
          <div style="font-size:11px; text-align:right; color:#555;">
            <span>${new Date(a.accounted_at).toLocaleTimeString()}</span>
            <div style="font-size:10px; color:#888;">by ${escapeHtmlInc(a.accounted_by_name || 'System')}</div>
          </div>
        </div>
      `).join('');
    }
    html += '</div>';

    container.innerHTML = html;
  } catch (err) {
    console.error('Roll call render error:', err);
  }
}

async function markWorkerAccounted(userId) {
  if (!currentRollCallIncidentId) return;
  try {
    const res = await fetch(`/api/incidents/${currentRollCallIncidentId}/roster/${userId}/account`, {
      method: 'POST'
    });
    if (!res.ok) {
      const err = await res.json();
      alert(`Failed to account worker: ${err.detail || 'Error'}`);
      return;
    }
    loadRollCallRoster();
    refreshIncidents();
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

// 8. Node Drawer Buzzer Controls
async function sendNodeBuzzerCommand(cmd) {
  const nodeId = window.currentNodeId || (document.getElementById('drawerNodeTitle') ? parseInt(document.getElementById('drawerNodeTitle').getAttribute('data-node-id') || document.getElementById('drawerNodeTitle').textContent.replace(/\D/g, ''), 10) : null);
  if (!nodeId) {
    alert('No node selected');
    return;
  }

  const statusLabel = document.getElementById('drawerBuzzerStatus');
  if (statusLabel) statusLabel.textContent = `Dispatching ${cmd}...`;

  try {
    const res = await fetch(`/api/nodes/${nodeId}/commands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cmd: cmd })
    });

    if (!res.ok) {
      const err = await res.json();
      alert(`Command error: ${err.detail || 'Failed'}`);
      if (statusLabel) statusLabel.textContent = 'Command failed';
      return;
    }

    const data = await res.json();
    if (statusLabel) {
      statusLabel.textContent = `Pending: ${data.cmd.toUpperCase()} (seq #${data.seq})`;
    }
  } catch (err) {
    alert(`Network error: ${err.message}`);
  }
}

// Poll commands status when inspector drawer is open
async function refreshCurrentNodeCommands(nodeId) {
  const statusLabel = document.getElementById('drawerBuzzerStatus');
  if (!statusLabel || !nodeId) return;

  try {
    const res = await fetch(`/api/nodes/${nodeId}/commands?limit=1`);
    if (res.ok) {
      const data = await res.json();
      if (data.commands && data.commands.length > 0) {
        const last = data.commands[0];
        statusLabel.textContent = `Last: ${last.cmd.toUpperCase()} (seq #${last.seq}) · ${last.status.toUpperCase()}`;
      } else {
        statusLabel.textContent = 'Last command: None';
      }
    }
  } catch (err) {
    // Silent
  }
}

// Hook into DOMContentLoaded
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(initIncidents, 1000);
  setInterval(() => {
    const drawer = document.getElementById('inspectorDrawer');
    if (drawer && drawer.classList.contains('active')) {
      const titleEl = document.getElementById('drawerNodeTitle');
      if (titleEl) {
        const match = titleEl.textContent.match(/\d+/);
        if (match) {
          refreshCurrentNodeCommands(parseInt(match[0], 10));
        }
      }
    }
  }, 3000);
});
