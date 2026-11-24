/**
 * static/mine_profile.js
 * Owns Mine Profile UI panel: identity & status, company & classification,
 * headcount lookahead, plan vs actual output with inline-SVG charts, and contacts hierarchy.
 * Must NOT bypass server-side role enforcement, make unauthorized calls, or leak sensitive fields to workers.
 */

let currentMineProfileId = null;
let profileHeadcountInterval = null;
let cachedSuperiorContacts = [];

function escapeHtmlProf(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function sanitizeTelLink(phone) {
  if (!phone) return '';
  return String(phone).replace(/[^0-9+]/g, '');
}

function renderEvacuationNotes(notes) {
  if (!notes || !notes.trim()) {
    return '<span style="color:var(--ink-3); font-style:italic;">None recorded</span>';
  }
  return escapeHtmlProf(notes).replace(/\n/g, '<br>');
}

// =============================================================================
// INITIALIZATION
// =============================================================================

function initMineProfile() {
  const roleEl = document.getElementById('currentUserRole');
  const role = roleEl ? roleEl.textContent.trim().toLowerCase() : null;
  const btn = document.getElementById('btnMineProfile');
  if (btn) {
    if (role && role !== 'role') {
      btn.style.display = 'inline-flex';
    } else {
      btn.style.display = 'none';
    }
  }
}

// Listen for DOM load and periodic auth check
window.addEventListener('DOMContentLoaded', () => {
  initMineProfile();
  setInterval(initMineProfile, 2000);
});

// =============================================================================
// MODAL CONTROLS & MINE PICKER
// =============================================================================

async function openMineProfileModal() {
  const modal = document.getElementById('mineProfileModal');
  if (!modal) return;
  modal.style.display = 'flex';

  // Load accessible mines
  await loadProfileMines();
  if (currentMineProfileId) {
    await loadMineProfile(currentMineProfileId);
  }
}

function closeMineProfileModal() {
  const modal = document.getElementById('mineProfileModal');
  if (modal) modal.style.display = 'none';
  if (profileHeadcountInterval) {
    clearInterval(profileHeadcountInterval);
    profileHeadcountInterval = null;
  }
}

async function loadProfileMines() {
  const picker = document.getElementById('mineProfileSelect');
  if (!picker) return;

  try {
    const res = await fetch('/api/mines');
    if (!res.ok) return;
    const data = await res.json();
    const mines = data.mines || [];

    picker.innerHTML = '';
    if (mines.length === 0) {
      picker.innerHTML = '<option value="">No mines accessible</option>';
      currentMineProfileId = null;
      return;
    }

    mines.forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m.id;
      opt.textContent = `${m.name} (${m.status})`;
      picker.appendChild(opt);
    });

    if (!currentMineProfileId || !mines.some((m) => m.id === currentMineProfileId)) {
      currentMineProfileId = mines[0].id;
    }
    picker.value = currentMineProfileId;
  } catch (err) {
    console.error('Failed to load profile mines', err);
  }
}

async function onProfileMineChange() {
  const picker = document.getElementById('mineProfileSelect');
  if (!picker) return;
  currentMineProfileId = parseInt(picker.value, 10);
  if (currentMineProfileId) {
    await loadMineProfile(currentMineProfileId);
  }
}

// =============================================================================
// LOAD PROFILE (SUPERIOR OR WORKER)
// =============================================================================

async function loadMineProfile(mineId) {
  const contentEl = document.getElementById('mineProfileContent');
  if (!contentEl) return;
  contentEl.innerHTML = '<div style="padding:20px; color:var(--ink-3);">Loading mine profile...</div>';

  if (profileHeadcountInterval) {
    clearInterval(profileHeadcountInterval);
    profileHeadcountInterval = null;
  }

  try {
    const res = await fetch(`/api/mines/${mineId}/profile`);
    if (!res.ok) {
      const err = await res.json();
      contentEl.innerHTML = `<div class="neo-alert neo-alert-danger" style="margin:16px;">Failed to load profile: ${escapeHtmlProf(err.detail || res.statusText)}</div>`;
      return;
    }

    const data = await res.json();

    // Check whether model is superior or worker view
    if (data.identity_status && data.company_classification) {
      renderSuperiorProfile(data);
      // Start 15s headcount refresh
      profileHeadcountInterval = setInterval(() => refreshProfileHeadcount(mineId), 15000);
    } else {
      renderWorkerProfile(data);
    }
  } catch (err) {
    contentEl.innerHTML = `<div class="neo-alert neo-alert-danger" style="margin:16px;">Network error loading mine profile: ${escapeHtmlProf(err.message)}</div>`;
  }
}

// =============================================================================
// WORKER VIEW (TRIMMED, WORKER-SAFE)
// =============================================================================

function renderWorkerProfile(data) {
  const contentEl = document.getElementById('mineProfileContent');
  if (!contentEl) return;

  let tunnelHtml = '<span style="color:var(--ink-3); font-style:italic;">No active tunnel assignment</span>';
  if (data.current_tunnel) {
    tunnelHtml = `<strong>${escapeHtmlProf(data.current_tunnel.name)}</strong> <span class="role-tag" style="background:#e0f2fe; color:#0369a1;">${escapeHtmlProf(data.current_tunnel.status)}</span>`;
  }

  let contactsHtml = '';
  if (!data.contacts || data.contacts.length === 0) {
    contactsHtml = '<div style="color:var(--ink-3); font-size:12px; padding:8px 0;">No emergency contacts listed.</div>';
  } else {
    contactsHtml = data.contacts.map((c) => {
      const tel = sanitizeTelLink(c.phone);
      return `
        <div style="display:flex; justify-content:space-between; align-items:center; padding:10px 14px; border:2px solid #000; background:#fff; margin-bottom:8px; box-shadow:2px 2px 0 #000;">
          <div>
            <div style="font-weight:700; font-size:14px;">${escapeHtmlProf(c.name)} <span style="font-size:11px; font-weight:600; color:var(--ink-3);">[${escapeHtmlProf(c.role.replace('_', ' ').toUpperCase())}]</span></div>
            <div style="font-size:12px; font-family:var(--mono); color:var(--ink-2);">${escapeHtmlProf(c.phone)}</div>
          </div>
          <a href="tel:${tel}" class="neo-btn btn-small btn-gold" style="text-decoration:none; display:inline-flex; align-items:center; gap:4px;">
            📞 Call
          </a>
        </div>
      `;
    }).join('');
  }

  contentEl.innerHTML = `
    <div style="display:flex; flex-direction:column; gap:16px; padding:10px 0;">
      <!-- Mine Info Card -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:12px;">
          <div>
            <span style="font-size:11px; font-family:var(--mono); text-transform:uppercase; color:var(--ink-3);">Mine Overview</span>
            <h2 style="margin:0; font-family:var(--condensed); font-size:22px; text-transform:uppercase;">${escapeHtmlProf(data.name)}</h2>
          </div>
          <span class="role-tag" style="background:#dcfce7; color:#166534; font-size:13px; font-weight:800; border:2px solid #000;">
            ${escapeHtmlProf(data.status.toUpperCase())}
          </span>
        </div>

        <div style="margin-bottom:14px;">
          <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--ink-3); margin-bottom:4px;">Your Assigned Tunnel</div>
          <div style="font-size:14px;">${tunnelHtml}</div>
        </div>

        <div style="margin-bottom:16px;">
          <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--ink-3); margin-bottom:4px;">Evacuation Notes</div>
          <div style="background:#f8fafc; border:2px solid #000; padding:12px; font-size:13px; line-height:1.5;">
            ${renderEvacuationNotes(data.evacuation_notes)}
          </div>
        </div>

        <div>
          <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--ink-3); margin-bottom:8px;">Emergency Contacts (Call Hierarchy)</div>
          ${contactsHtml}
        </div>
      </div>
    </div>
  `;
}

// =============================================================================
// SUPERIOR VIEW (ALL 5 GROUPS IN ORDER)
// =============================================================================

function renderSuperiorProfile(data) {
  const contentEl = document.getElementById('mineProfileContent');
  if (!contentEl) return;

  const ident = data.identity_status;
  const comp = data.company_classification;
  const hc = data.headcount;
  const prod = data.production;
  cachedSuperiorContacts = JSON.parse(JSON.stringify(data.contacts || []));

  contentEl.innerHTML = `
    <div style="display:flex; flex-direction:column; gap:20px; padding:10px 0;">
      
      <!-- CARD 1: Identity & Status -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:14px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:20px;">1️⃣</span>
            <div>
              <span style="font-size:11px; font-family:var(--mono); color:var(--ink-3); text-transform:uppercase;">Group 1</span>
              <h3 style="margin:0; font-family:var(--condensed); font-size:18px; text-transform:uppercase;">Identity &amp; Status</h3>
            </div>
          </div>
          <button id="btnEditProfileToggle" class="neo-btn btn-small btn-primary" onclick="toggleEditProfileForm()">✏️ Edit Profile</button>
        </div>

        <div id="identityViewBlock">
          <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px; margin-bottom:14px;">
            <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">SITE NAME:</span> <div style="font-weight:700; font-size:15px;">${escapeHtmlProf(ident.name)} (ID: ${ident.id})</div></div>
            <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">OPERATIONAL STATUS:</span> <div><span class="role-tag" style="background:#dcfce7; color:#166534; font-weight:800; border:2px solid #000;">${escapeHtmlProf(ident.status.toUpperCase())}</span></div></div>
            <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">SURFACE COORDINATES:</span> <div style="font-family:var(--mono); font-size:13px;">${ident.lat !== null && ident.lon !== null ? `${ident.lat.toFixed(6)}, ${ident.lon.toFixed(6)}` : '<span style="color:var(--ink-3);">Unset</span>'}</div></div>
          </div>
          <div>
            <span style="font-size:11px; color:var(--ink-3); font-weight:700; text-transform:uppercase;">Evacuation Notes:</span>
            <div style="background:#f8fafc; border:2px solid #000; padding:10px 14px; font-size:13px; line-height:1.5; margin-top:4px;">
              ${renderEvacuationNotes(ident.evacuation_notes)}
            </div>
          </div>
        </div>

        <!-- Inline PATCH Form (Hidden by default) -->
        <div id="identityEditBlock" style="display:none; background:#fffbeb; border:2px dashed #000; padding:16px; margin-top:10px;">
          <h4 style="margin:0 0 12px 0; font-family:var(--condensed); font-size:15px; text-transform:uppercase;">Edit Mine Profile (PATCH)</h4>
          <div id="profileEditError" style="display:none; margin-bottom:10px;" class="neo-alert neo-alert-danger"></div>
          
          <div style="display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:10px;">
            <div>
              <label style="font-size:11px; font-weight:700;">Mine Name *</label>
              <input type="text" id="editProfName" class="neo-input" value="${escapeHtmlProf(ident.name)}">
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Subsidiary</label>
              <input type="text" id="editProfSubsidiary" class="neo-input" value="${escapeHtmlProf(comp.subsidiary || '')}">
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Status *</label>
              <select id="editProfStatus" class="neo-input">
                <option value="active" ${ident.status === 'active' ? 'selected' : ''}>Active</option>
                <option value="maintenance" ${ident.status === 'maintenance' ? 'selected' : ''}>Maintenance</option>
                <option value="suspended" ${ident.status === 'suspended' ? 'selected' : ''}>Suspended</option>
                <option value="closed" ${ident.status === 'closed' ? 'selected' : ''}>Closed</option>
              </select>
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Method *</label>
              <select id="editProfMethod" class="neo-input">
                <option value="underground" ${comp.method === 'underground' ? 'selected' : ''}>Underground</option>
                <option value="opencast" ${comp.method === 'opencast' ? 'selected' : ''}>Opencast</option>
                <option value="mixed" ${comp.method === 'mixed' ? 'selected' : ''}>Mixed</option>
              </select>
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Area (ha)</label>
              <input type="number" step="0.01" min="0" id="editProfArea" class="neo-input" value="${comp.area_ha !== null ? comp.area_ha : ''}">
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Max Depth (m)</label>
              <input type="number" step="0.1" min="0" id="editProfDepth" class="neo-input" value="${comp.max_depth_m !== null ? comp.max_depth_m : ''}">
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Latitude (-90..90)</label>
              <input type="number" step="0.000001" min="-90" max="90" id="editProfLat" class="neo-input" value="${ident.lat !== null ? ident.lat : ''}">
            </div>
            <div>
              <label style="font-size:11px; font-weight:700;">Longitude (-180..180)</label>
              <input type="number" step="0.000001" min="-180" max="180" id="editProfLon" class="neo-input" value="${ident.lon !== null ? ident.lon : ''}">
            </div>
          </div>
          <div style="margin-bottom:12px;">
            <label style="font-size:11px; font-weight:700;">Evacuation Notes (max 2000 chars)</label>
            <textarea id="editProfNotes" class="neo-input" rows="3" maxlength="2000">${escapeHtmlProf(ident.evacuation_notes || '')}</textarea>
          </div>
          <div style="display:flex; justify-content:flex-end; gap:8px;">
            <button class="neo-btn btn-small" onclick="toggleEditProfileForm()">Cancel</button>
            <button class="neo-btn btn-small btn-gold" onclick="submitProfilePatch()">💾 Save Changes</button>
          </div>
        </div>
      </div>

      <!-- CARD 2: Company & Classification -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; align-items:center; gap:10px; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:14px;">
          <span style="font-size:20px;">2️⃣</span>
          <div>
            <span style="font-size:11px; font-family:var(--mono); color:var(--ink-3); text-transform:uppercase;">Group 2</span>
            <h3 style="margin:0; font-family:var(--condensed); font-size:18px; text-transform:uppercase;">Company &amp; Classification</h3>
          </div>
        </div>

        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:14px;">
          <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">OPERATING COMPANY:</span> <div style="font-weight:700; font-size:15px;">${escapeHtmlProf(comp.company_name)}</div></div>
          <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">SUBSIDIARY / DIVISION:</span> <div style="font-size:14px;">${escapeHtmlProf(comp.subsidiary || 'Direct')}</div></div>
          <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">MINING METHOD:</span> <div style="font-size:14px; text-transform:capitalize; font-weight:600;">${escapeHtmlProf(comp.method)}</div></div>
          <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">CONCESSION AREA:</span> <div style="font-size:14px; font-family:var(--mono);">${comp.area_ha !== null ? `${comp.area_ha.toFixed(2)} ha` : 'Unspecified'}</div></div>
          <div><span style="font-size:11px; color:var(--ink-3); font-weight:700;">MAX WORKING DEPTH:</span> <div style="font-size:14px; font-family:var(--mono);">${comp.max_depth_m !== null ? `${comp.max_depth_m.toFixed(1)} m` : 'Unspecified'}</div></div>
        </div>
      </div>

      <!-- CARD 3: Headcount by Shift and Tunnel -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:14px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:20px;">3️⃣</span>
            <div>
              <span style="font-size:11px; font-family:var(--mono); color:var(--ink-3); text-transform:uppercase;">Group 3 (Auto-refresh 15s)</span>
              <h3 style="margin:0; font-family:var(--condensed); font-size:18px; text-transform:uppercase;">Headcount by Shift &amp; Tunnel</h3>
            </div>
          </div>
          <button class="neo-btn btn-small" onclick="refreshProfileHeadcount(currentMineProfileId)">🔄 Refresh</button>
        </div>

        <div id="profileHeadcountBlock">
          ${renderHeadcountTables(hc)}
        </div>
      </div>

      <!-- CARD 4: Plan vs Actual Output -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:14px; flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:20px;">4️⃣</span>
            <div>
              <span style="font-size:11px; font-family:var(--mono); color:var(--ink-3); text-transform:uppercase;">Group 4</span>
              <h3 style="margin:0; font-family:var(--condensed); font-size:18px; text-transform:uppercase;">Plan vs Actual Output (Tonnes)</h3>
            </div>
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <label style="font-size:11px; font-weight:700;">MONTH:</label>
            <input type="month" id="prodMonthInput" class="neo-input" style="padding:4px 8px; font-size:12px;" value="${prod.month}" onchange="onProdMonthChange()">
            <button class="neo-btn btn-small btn-primary" onclick="toggleProdEntryForm()">+ Log Output</button>
          </div>
        </div>

        <div id="prodDataBlock">
          ${renderProductionContent(prod)}
        </div>
      </div>

      <!-- CARD 5: Key Contacts (Call Hierarchy Editor) -->
      <div class="neo-card" style="background:#fff; border:3px solid #000; padding:18px; box-shadow:4px 4px 0 #000;">
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:2px solid #000; padding-bottom:10px; margin-bottom:14px; flex-wrap:wrap; gap:8px;">
          <div style="display:flex; align-items:center; gap:10px;">
            <span style="font-size:20px;">5️⃣</span>
            <div>
              <span style="font-size:11px; font-family:var(--mono); color:var(--ink-3); text-transform:uppercase;">Group 5</span>
              <h3 style="margin:0; font-family:var(--condensed); font-size:18px; text-transform:uppercase;">Key Contacts &amp; Call Hierarchy</h3>
            </div>
          </div>
          <div style="display:flex; gap:8px;">
            <button class="neo-btn btn-small btn-gold" onclick="addNewContactRow()">+ Add Contact</button>
            <button class="neo-btn btn-small btn-success" onclick="saveContactsHierarchy()">💾 Save Contacts</button>
          </div>
        </div>

        <div id="contactsEditorContainer">
          ${renderContactsEditor(cachedSuperiorContacts)}
        </div>
      </div>

    </div>
  `;
}

// =============================================================================
// HEADCOUNT RENDERING
// =============================================================================

function renderHeadcountTables(hc) {
  const totals = hc.totals || { inside: 0, scheduled_now: 0, upcoming: 0 };
  const tunnels = hc.tunnels || [];

  return `
    <div style="display:grid; grid-template-columns:repeat(3, 1fr); gap:12px; margin-bottom:14px;">
      <div style="background:#f0fdf4; border:2px solid #000; padding:12px; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:#166534; text-transform:uppercase;">Total Inside Now</div>
        <div style="font-size:24px; font-weight:800; font-family:var(--mono); color:#15803d;">${totals.inside}</div>
      </div>
      <div style="background:#eff6ff; border:2px solid #000; padding:12px; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:#1e40af; text-transform:uppercase;">Active Scheduled Now</div>
        <div style="font-size:24px; font-weight:800; font-family:var(--mono); color:#1d4ed8;">${totals.scheduled_now}</div>
      </div>
      <div style="background:#faf5ff; border:2px solid #000; padding:12px; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:#6b21a8; text-transform:uppercase;">Upcoming (Next 12h)</div>
        <div style="font-size:24px; font-weight:800; font-family:var(--mono); color:#7e22ce;">${totals.upcoming}</div>
      </div>
    </div>

    <table class="neo-table" style="width:100%; border:2px solid #000; font-size:13px;">
      <thead style="background:#000; color:#fff;">
        <tr>
          <th style="padding:8px 12px; text-align:left;">Tunnel Name</th>
          <th style="padding:8px 12px; text-align:center;">Workers Inside</th>
          <th style="padding:8px 12px; text-align:center;">Scheduled Now</th>
          <th style="padding:8px 12px; text-align:center;">Upcoming Shift</th>
        </tr>
      </thead>
      <tbody>
        ${tunnels.length === 0 ? '<tr><td colspan="4" style="text-align:center; padding:12px; color:var(--ink-3);">No underground tunnels registered for this mine.</td></tr>' : tunnels.map(t => `
          <tr style="border-bottom:1px solid #ddd;">
            <td style="padding:8px 12px; font-weight:700;">${escapeHtmlProf(t.tunnel_name)}</td>
            <td style="padding:8px 12px; text-align:center; font-family:var(--mono); font-weight:700; color:${t.inside > 0 ? '#15803d' : 'inherit'};">${t.inside}</td>
            <td style="padding:8px 12px; text-align:center; font-family:var(--mono);">${t.scheduled_now}</td>
            <td style="padding:8px 12px; text-align:center; font-family:var(--mono);">${t.upcoming}</td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

async function refreshProfileHeadcount(mineId) {
  const block = document.getElementById('profileHeadcountBlock');
  if (!block || !mineId) return;

  try {
    const res = await fetch(`/api/mines/${mineId}/headcount`);
    if (!res.ok) return;
    const hc = await res.json();
    block.innerHTML = renderHeadcountTables(hc);
  } catch (err) {
    console.error('Failed to refresh headcount', err);
  }
}

// =============================================================================
// PRODUCTION RENDERING & SVG CHART
// =============================================================================

function renderProductionContent(prodSummary, daysList) {
  const summary = prodSummary;
  const days = daysList || [];

  const varianceColor = summary.variance_t >= 0 ? '#15803d' : '#b91c1c';
  const varianceSign = summary.variance_t > 0 ? '+' : '';

  return `
    <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(140px, 1fr)); gap:12px; margin-bottom:16px;">
      <div style="border:2px solid #000; padding:10px; background:#f8fafc; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:var(--ink-3);">PLANNED TOTAL</div>
        <div style="font-size:20px; font-weight:800; font-family:var(--mono);">${summary.planned_total_t.toLocaleString()} t</div>
      </div>
      <div style="border:2px solid #000; padding:10px; background:#f8fafc; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:var(--ink-3);">ACTUAL TOTAL</div>
        <div style="font-size:20px; font-weight:800; font-family:var(--mono);">${summary.actual_total_t.toLocaleString()} t</div>
      </div>
      <div style="border:2px solid #000; padding:10px; background:#f8fafc; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:var(--ink-3);">VARIANCE</div>
        <div style="font-size:20px; font-weight:800; font-family:var(--mono); color:${varianceColor};">${varianceSign}${summary.variance_t.toLocaleString()} t</div>
      </div>
      <div style="border:2px solid #000; padding:10px; background:#f8fafc; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:var(--ink-3);">ACHIEVEMENT %</div>
        <div style="font-size:20px; font-weight:800; font-family:var(--mono);">${summary.achievement_pct !== null ? `${summary.achievement_pct}%` : 'N/A'}</div>
      </div>
      <div style="border:2px solid #000; padding:10px; background:#f8fafc; text-align:center;">
        <div style="font-size:11px; font-weight:700; color:var(--ink-3);">REPORTED DAYS</div>
        <div style="font-size:20px; font-weight:800; font-family:var(--mono);">${summary.reported_days}</div>
      </div>
    </div>

    <!-- Inline SVG Bar Chart -->
    <div style="margin-bottom:16px; border:2px solid #000; background:#fdfdfd; padding:12px;">
      <div style="font-size:11px; font-weight:700; text-transform:uppercase; color:var(--ink-3); margin-bottom:8px; display:flex; justify-content:space-between;">
        <span>Daily Extraction (Planned vs Actual)</span>
        <span><span style="color:#0284c7;">■</span> Planned &nbsp; <span style="color:#16a34a;">■</span> Actual</span>
      </div>
      <div style="overflow-x:auto;">
        ${renderSvgProductionChart(days)}
      </div>
    </div>

    <!-- Production Log / Entry Form (Hidden by default) -->
    <div id="prodEntryFormBlock" style="display:none; background:#fefce8; border:2px dashed #000; padding:14px; margin-bottom:16px;">
      <h4 style="margin:0 0 10px 0; font-family:var(--condensed); font-size:14px; text-transform:uppercase;">Record Daily Coal Extraction</h4>
      <div id="prodEntryError" style="display:none; margin-bottom:10px;" class="neo-alert neo-alert-danger"></div>
      <div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:10px; margin-bottom:10px;">
        <div>
          <label style="font-size:11px; font-weight:700;">Date (YYYY-MM-DD) *</label>
          <input type="date" id="entryDay" class="neo-input" value="${new Date().toISOString().slice(0, 10)}">
        </div>
        <div>
          <label style="font-size:11px; font-weight:700;">Planned Tonnes (t)</label>
          <input type="number" step="0.01" min="0" id="entryPlanned" class="neo-input" placeholder="e.g. 500.00">
        </div>
        <div>
          <label style="font-size:11px; font-weight:700;">Actual Tonnes (t)</label>
          <input type="number" step="0.01" min="0" id="entryActual" class="neo-input" placeholder="e.g. 485.50">
        </div>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:8px;">
        <button class="neo-btn btn-small" onclick="toggleProdEntryForm()">Cancel</button>
        <button class="neo-btn btn-small btn-gold" onclick="submitSingleProductionEntry()">💾 Save Entry</button>
      </div>
    </div>

    <!-- Table of Days -->
    <table class="neo-table" style="width:100%; border:2px solid #000; font-size:12px;">
      <thead style="background:#000; color:#fff;">
        <tr>
          <th style="padding:6px 10px; text-align:left;">Date</th>
          <th style="padding:6px 10px; text-align:right;">Planned (t)</th>
          <th style="padding:6px 10px; text-align:right;">Actual (t)</th>
          <th style="padding:6px 10px; text-align:right;">Variance</th>
          <th style="padding:6px 10px; text-align:right;">Achievement</th>
        </tr>
      </thead>
      <tbody>
        ${days.length === 0 ? '<tr><td colspan="5" style="text-align:center; padding:10px; color:var(--ink-3);">No daily records logged for this month.</td></tr>' : days.map(d => {
          const p = d.planned_t;
          const a = d.actual_t;
          const diff = (a !== null && p !== null) ? (a - p) : null;
          const ach = (p && a !== null) ? ((a / p) * 100).toFixed(1) + '%' : (a !== null ? '100%' : 'N/A');
          return `
            <tr style="border-bottom:1px solid #ddd;">
              <td style="padding:6px 10px; font-family:var(--mono);">${d.day}</td>
              <td style="padding:6px 10px; text-align:right; font-family:var(--mono);">${p !== null ? p.toFixed(2) : '-'}</td>
              <td style="padding:6px 10px; text-align:right; font-family:var(--mono); font-weight:700;">${a !== null ? a.toFixed(2) : '-'}</td>
              <td style="padding:6px 10px; text-align:right; font-family:var(--mono); color:${diff !== null ? (diff >= 0 ? '#15803d' : '#b91c1c') : 'inherit'};">${diff !== null ? (diff > 0 ? '+' : '') + diff.toFixed(2) : '-'}</td>
              <td style="padding:6px 10px; text-align:right; font-family:var(--mono);">${ach}</td>
            </tr>
          `;
        }).join('')}
      </tbody>
    </table>
  `;
}

function renderSvgProductionChart(days) {
  if (!days || days.length === 0) {
    return '<div style="text-align:center; padding:20px; color:var(--ink-3); font-size:12px;">No daily data to visualize for this month.</div>';
  }

  const height = 160;
  const paddingBottom = 25;
  const paddingTop = 15;
  const chartHeight = height - paddingBottom - paddingTop;
  const barWidth = 14;
  const groupSpacing = 16;
  const totalWidth = Math.max(500, days.length * (barWidth * 2 + groupSpacing) + 40);

  let maxVal = 10;
  days.forEach(d => {
    if (d.planned_t && d.planned_t > maxVal) maxVal = d.planned_t;
    if (d.actual_t && d.actual_t > maxVal) maxVal = d.actual_t;
  });
  maxVal *= 1.15; // 15% headroom

  let svgBars = '';
  days.forEach((d, i) => {
    const x = 30 + i * (barWidth * 2 + groupSpacing);
    const dayLabel = d.day.slice(8); // DD

    // Planned bar (blue)
    if (d.planned_t !== null && d.planned_t > 0) {
      const pHeight = (d.planned_t / maxVal) * chartHeight;
      const py = paddingTop + (chartHeight - pHeight);
      svgBars += `<rect x="${x}" y="${py}" width="${barWidth}" height="${pHeight}" fill="#0284c7" stroke="#000" stroke-width="1"><title>Planned: ${d.planned_t} t (${d.day})</title></rect>`;
    }

    // Actual bar (green)
    if (d.actual_t !== null && d.actual_t > 0) {
      const aHeight = (d.actual_t / maxVal) * chartHeight;
      const ay = paddingTop + (chartHeight - aHeight);
      svgBars += `<rect x="${x + barWidth}" y="${ay}" width="${barWidth}" height="${aHeight}" fill="#16a34a" stroke="#000" stroke-width="1"><title>Actual: ${d.actual_t} t (${d.day})</title></rect>`;
    }

    // Day label
    svgBars += `<text x="${x + barWidth}" y="${height - 8}" font-size="10" font-family="monospace" text-anchor="middle" fill="#000">${dayLabel}</text>`;
  });

  return `
    <svg width="${totalWidth}" height="${height}" style="display:block;">
      <line x1="20" y1="${height - paddingBottom}" x2="${totalWidth - 10}" y2="${height - paddingBottom}" stroke="#000" stroke-width="2" />
      ${svgBars}
    </svg>
  `;
}

async function onProdMonthChange() {
  const monthInput = document.getElementById('prodMonthInput');
  const dataBlock = document.getElementById('prodDataBlock');
  if (!monthInput || !dataBlock || !currentMineProfileId) return;

  const month = monthInput.value;
  if (!month) return;

  dataBlock.innerHTML = '<div style="padding:15px; color:var(--ink-3);">Loading month data...</div>';

  try {
    const res = await fetch(`/api/mines/${currentMineProfileId}/production?month=${encodeURIComponent(month)}`);
    if (!res.ok) {
      const err = await res.json();
      dataBlock.innerHTML = `<div class="neo-alert neo-alert-danger">${escapeHtmlProf(err.detail || 'Error loading month')}</div>`;
      return;
    }
    const data = await res.json();
    dataBlock.innerHTML = renderProductionContent(data.summary, data.days);
  } catch (err) {
    dataBlock.innerHTML = `<div class="neo-alert neo-alert-danger">${escapeHtmlProf(err.message)}</div>`;
  }
}

// =============================================================================
// CONTACTS HIERARCHY EDITOR
// =============================================================================

function renderContactsEditor(contacts) {
  if (!contacts || contacts.length === 0) {
    return `
      <div style="padding:15px; text-align:center; color:var(--ink-3); border:2px dashed #000; margin-bottom:10px;">
        No emergency contacts configured. Click "+ Add Contact" to define the emergency call hierarchy.
      </div>
    `;
  }

  return `
    <div style="display:flex; flex-direction:column; gap:8px; margin-bottom:12px;">
      ${contacts.map((c, idx) => `
        <div style="display:grid; grid-template-columns:50px 140px 1fr 140px 90px; gap:8px; align-items:center; background:#f8fafc; border:2px solid #000; padding:8px 12px; box-shadow:2px 2px 0 #000;">
          <div style="font-family:var(--mono); font-weight:800; font-size:14px; text-align:center;">#${c.rank}</div>
          <div>
            <select class="neo-input" style="padding:4px;" onchange="updateContactField(${idx}, 'role', this.value)">
              <option value="shift_supervisor" ${c.role === 'shift_supervisor' ? 'selected' : ''}>Shift Supervisor</option>
              <option value="safety_officer" ${c.role === 'safety_officer' ? 'selected' : ''}>Safety Officer</option>
              <option value="rescue_station" ${c.role === 'rescue_station' ? 'selected' : ''}>Rescue Station</option>
              <option value="other" ${c.role === 'other' ? 'selected' : ''}>Other</option>
            </select>
          </div>
          <div>
            <input type="text" class="neo-input" style="padding:4px;" placeholder="Full Name" value="${escapeHtmlProf(c.name)}" onchange="updateContactField(${idx}, 'name', this.value)">
          </div>
          <div>
            <input type="text" class="neo-input" style="padding:4px;" placeholder="Phone Number" value="${escapeHtmlProf(c.phone)}" onchange="updateContactField(${idx}, 'phone', this.value)">
          </div>
          <div style="display:flex; gap:4px; justify-content:flex-end;">
            <button class="neo-btn btn-small" style="padding:2px 6px;" title="Move Up" ${idx === 0 ? 'disabled' : ''} onclick="moveContactUp(${idx})">⬆️</button>
            <button class="neo-btn btn-small" style="padding:2px 6px;" title="Move Down" ${idx === contacts.length - 1 ? 'disabled' : ''} onclick="moveContactDown(${idx})">⬇️</button>
            <button class="neo-btn btn-small btn-danger" style="padding:2px 6px;" title="Delete" onclick="deleteContactRow(${idx})">❌</button>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function updateContactField(idx, field, value) {
  if (cachedSuperiorContacts[idx]) {
    cachedSuperiorContacts[idx][field] = value;
  }
}

function moveContactUp(idx) {
  if (idx <= 0 || idx >= cachedSuperiorContacts.length) return;
  const temp = cachedSuperiorContacts[idx - 1];
  cachedSuperiorContacts[idx - 1] = cachedSuperiorContacts[idx];
  cachedSuperiorContacts[idx] = temp;
  // Re-rank 1..N
  cachedSuperiorContacts.forEach((c, i) => { c.rank = i + 1; });
  document.getElementById('contactsEditorContainer').innerHTML = renderContactsEditor(cachedSuperiorContacts);
}

function moveContactDown(idx) {
  if (idx < 0 || idx >= cachedSuperiorContacts.length - 1) return;
  const temp = cachedSuperiorContacts[idx + 1];
  cachedSuperiorContacts[idx + 1] = cachedSuperiorContacts[idx];
  cachedSuperiorContacts[idx] = temp;
  // Re-rank 1..N
  cachedSuperiorContacts.forEach((c, i) => { c.rank = i + 1; });
  document.getElementById('contactsEditorContainer').innerHTML = renderContactsEditor(cachedSuperiorContacts);
}

function deleteContactRow(idx) {
  cachedSuperiorContacts.splice(idx, 1);
  cachedSuperiorContacts.forEach((c, i) => { c.rank = i + 1; });
  document.getElementById('contactsEditorContainer').innerHTML = renderContactsEditor(cachedSuperiorContacts);
}

function addNewContactRow() {
  if (cachedSuperiorContacts.length >= 20) {
    alert('Maximum 20 emergency contacts allowed per mine.');
    return;
  }
  cachedSuperiorContacts.push({
    role: 'shift_supervisor',
    name: '',
    phone: '',
    rank: cachedSuperiorContacts.length + 1,
  });
  document.getElementById('contactsEditorContainer').innerHTML = renderContactsEditor(cachedSuperiorContacts);
}

async function saveContactsHierarchy() {
  if (!currentMineProfileId) return;

  if (cachedSuperiorContacts.length === 0) {
    alert('At least 1 emergency contact is required.');
    return;
  }

  // Validate
  for (let i = 0; i < cachedSuperiorContacts.length; i++) {
    const c = cachedSuperiorContacts[i];
    if (!c.name || !c.name.trim()) {
      alert(`Contact #${c.rank} must have a name.`);
      return;
    }
    if (!c.phone || !/^[0-9+() -]{5,32}$/.test(c.phone.trim())) {
      alert(`Contact #${c.rank} (${c.name}) has an invalid phone number. Must match digits, spaces, hyphens, plus.`);
      return;
    }
  }

  const payload = {
    contacts: cachedSuperiorContacts.map((c, i) => ({
      role: c.role,
      name: c.name.trim(),
      phone: c.phone.trim(),
      rank: i + 1,
    })),
  };

  try {
    const res = await fetch(`/api/mines/${currentMineProfileId}/contacts`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      alert('Error updating contacts: ' + (err.detail || res.statusText));
      return;
    }

    alert('Key contacts call hierarchy updated successfully!');
    await loadMineProfile(currentMineProfileId);
  } catch (err) {
    alert('Network error saving contacts: ' + err.message);
  }
}

// =============================================================================
// PATCH MINE METADATA
// =============================================================================

function toggleEditProfileForm() {
  const block = document.getElementById('identityEditBlock');
  if (!block) return;
  block.style.display = block.style.display === 'none' ? 'block' : 'none';
}

async function submitProfilePatch() {
  if (!currentMineProfileId) return;
  const errBox = document.getElementById('profileEditError');
  if (errBox) errBox.style.display = 'none';

  const name = document.getElementById('editProfName').value.trim();
  const subsidiary = document.getElementById('editProfSubsidiary').value.trim() || null;
  const status = document.getElementById('editProfStatus').value;
  const method = document.getElementById('editProfMethod').value;
  const areaVal = document.getElementById('editProfArea').value;
  const depthVal = document.getElementById('editProfDepth').value;
  const latVal = document.getElementById('editProfLat').value;
  const lonVal = document.getElementById('editProfLon').value;
  const notes = document.getElementById('editProfNotes').value;

  const payload = {};
  if (name) payload.name = name;
  payload.subsidiary = subsidiary;
  payload.status = status;
  payload.method = method;
  payload.area_ha = areaVal !== '' ? parseFloat(areaVal) : null;
  payload.max_depth_m = depthVal !== '' ? parseFloat(depthVal) : null;

  if (latVal !== '' || lonVal !== '') {
    if (latVal === '' || lonVal === '') {
      if (errBox) {
        errBox.textContent = 'Latitude and Longitude must both be provided together.';
        errBox.style.display = 'block';
      }
      return;
    }
    payload.lat = parseFloat(latVal);
    payload.lon = parseFloat(lonVal);
  } else {
    payload.lat = null;
    payload.lon = null;
  }

  payload.evacuation_notes = notes || null;

  try {
    const res = await fetch(`/api/mines/${currentMineProfileId}/profile`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      if (errBox) {
        errBox.textContent = err.detail || 'Failed to update profile.';
        errBox.style.display = 'block';
      }
      return;
    }

    alert('Mine profile updated successfully!');
    await loadMineProfile(currentMineProfileId);
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message;
      errBox.style.display = 'block';
    }
  }
}

// =============================================================================
// PRODUCTION ENTRY SUBMIT
// =============================================================================

function toggleProdEntryForm() {
  const block = document.getElementById('prodEntryFormBlock');
  if (!block) return;
  block.style.display = block.style.display === 'none' ? 'block' : 'none';
}

async function submitSingleProductionEntry() {
  if (!currentMineProfileId) return;
  const errBox = document.getElementById('prodEntryError');
  if (errBox) errBox.style.display = 'none';

  const day = document.getElementById('entryDay').value;
  const pVal = document.getElementById('entryPlanned').value;
  const aVal = document.getElementById('entryActual').value;

  if (!day) {
    if (errBox) { errBox.textContent = 'Date is required.'; errBox.style.display = 'block'; }
    return;
  }
  if (pVal === '' && aVal === '') {
    if (errBox) { errBox.textContent = 'At least one of planned or actual tonnes must be provided.'; errBox.style.display = 'block'; }
    return;
  }

  const entry = { day };
  if (pVal !== '') entry.planned_t = parseFloat(pVal);
  if (aVal !== '') entry.actual_t = parseFloat(aVal);

  try {
    const res = await fetch(`/api/mines/${currentMineProfileId}/production`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries: [entry] }),
    });

    if (!res.ok) {
      const err = await res.json();
      if (errBox) {
        errBox.textContent = err.detail || 'Failed to save production entry.';
        errBox.style.display = 'block';
      }
      return;
    }

    alert('Daily extraction saved successfully!');
    toggleProdEntryForm();
    onProdMonthChange();
  } catch (err) {
    if (errBox) {
      errBox.textContent = err.message;
      errBox.style.display = 'block';
    }
  }
}
