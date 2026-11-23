import os
import base64
import subprocess

# Read logos as base64
with open('docs/synopsis_assets/logo_saef.jpg', 'rb') as f:
    logo_saef_b64 = base64.b64encode(f.read()).decode('utf-8')

with open('docs/synopsis_assets/logo_aitm.jpg', 'rb') as f:
    logo_aitm_b64 = base64.b64encode(f.read()).decode('utf-8')

html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AITM ECE Mini-Project Synopsis - Coal Mine Subsidence Monitoring</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 0;
  }}
  * {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{
    margin: 0;
    padding: 0;
    font-family: "Times New Roman", Times, serif;
    color: #000000;
    background-color: #ffffff;
    font-size: 10pt;
    line-height: 1.25;
  }}
  .page {{
    width: 210mm;
    height: 297mm;
    padding: 9mm 14mm 11mm 14mm;
    position: relative;
    page-break-after: always;
    break-after: page;
    background: #ffffff;
    overflow: hidden;
  }}
  .page:last-child {{
    page-break-after: auto;
    break-after: auto;
  }}
  
  /* Header Styles */
  .header-container {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    margin-bottom: 3px;
  }}
  .header-logo-left {{
    width: 66px;
    height: 66px;
    object-fit: contain;
    flex-shrink: 0;
  }}
  .header-logo-right {{
    width: 64px;
    height: 64px;
    object-fit: contain;
    flex-shrink: 0;
  }}
  .header-text {{
    text-align: center;
    flex-grow: 1;
    padding: 0 6px;
  }}
  .inst-trust {{
    font-size: 9.5pt;
    font-style: italic;
    margin-bottom: 1px;
    color: #111;
  }}
  .inst-name {{
    font-size: 12.5pt;
    font-weight: bold;
    letter-spacing: 0.2px;
    margin-bottom: 1px;
    color: #000;
    font-family: "Bookman Old Style", "Times New Roman", serif;
  }}
  .inst-affil {{
    font-size: 8pt;
    margin-bottom: 1px;
    color: #222;
  }}
  .inst-addr {{
    font-size: 8.5pt;
    margin-bottom: 1px;
  }}
  .inst-accred {{
    font-size: 8.8pt;
    font-weight: bold;
    font-style: italic;
  }}
  .header-divider {{
    border: none;
    border-top: 1.5px solid #000;
    margin: 3px 0 5px 0;
  }}
  .dept-title {{
    text-align: center;
    font-size: 11.5pt;
    font-weight: bold;
    text-decoration: underline;
    margin-bottom: 2px;
  }}
  .doc-title {{
    text-align: center;
    font-size: 10.5pt;
    font-weight: bold;
    text-decoration: underline;
    margin-bottom: 6px;
  }}

  /* Table Styles */
  table.synopsis-table {{
    width: 100%;
    border-collapse: collapse;
    border: 1.2px solid #000000;
    margin-bottom: 0;
  }}
  table.synopsis-table td {{
    border: 1px solid #000000;
    vertical-align: top;
    padding: 3.5px 6.5px;
  }}
  td.item-num {{
    width: 30px;
    text-align: center;
    font-weight: bold;
    font-size: 10pt;
    padding: 3.5px 2px !important;
  }}
  td.item-content {{
    font-size: 9.5pt;
    line-height: 1.22;
  }}
  .label-red {{
    color: #b00000;
    font-weight: bold;
  }}
  .val-text {{
    color: #000000;
  }}
  
  /* Fill-in Fields */
  .field-row {{
    margin: 1.5px 0;
    font-size: 9pt;
    display: flex;
    align-items: center;
  }}
  .field-label {{
    width: 82px;
    font-weight: 600;
    color: #222;
    flex-shrink: 0;
  }}
  .field-line {{
    flex-grow: 1;
    border-bottom: 1px solid #666;
    height: 12px;
    display: inline-block;
    margin-left: 4px;
  }}
  .member-container {{
    margin-top: 2px;
  }}
  .member-item {{
    margin-bottom: 4px;
    padding-bottom: 2px;
    border-bottom: 1px dotted #ccc;
  }}
  .member-item:last-child {{
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
  }}
  .member-title {{
    font-weight: bold;
    font-size: 9pt;
    color: #111;
    margin-bottom: 1px;
  }}

  /* Section Paragraphs & Lists */
  p.section-para {{
    margin: 0 0 2px 0;
    text-align: justify;
    line-height: 1.20;
  }}
  ul.custom-bullets {{
    margin: 1px 0 2px 0;
    padding-left: 15px;
  }}
  ul.custom-bullets li {{
    margin-bottom: 1.5px;
    text-align: justify;
    line-height: 1.18;
  }}
  ol.custom-nums {{
    margin: 1px 0 2px 0;
    padding-left: 16px;
  }}
  ol.custom-nums li {{
    margin-bottom: 1.5px;
    text-align: justify;
    line-height: 1.18;
  }}

  /* Sub-tables */
  table.inner-table {{
    width: 100%;
    border-collapse: collapse;
    margin: 2px 0;
    font-size: 8.8pt;
  }}
  table.inner-table th, table.inner-table td {{
    border: 1px solid #000;
    padding: 2.5px 5px;
  }}
  table.inner-table th {{
    background-color: #f2f2f2;
    font-weight: bold;
    text-align: left;
  }}
  
  /* Footer */
  .page-footer {{
    position: absolute;
    bottom: 7mm;
    left: 14mm;
    right: 14mm;
    display: flex;
    justify-content: space-between;
    font-size: 8.8pt;
    font-weight: bold;
    border-top: 1px solid #000;
    padding-top: 2.5px;
  }}

  /* Approval Block */
  .approval-container {{
    border: 1px solid #000;
    padding: 12px 14px;
    margin-top: 6px;
  }}
  .approval-header {{
    font-size: 10.5pt;
    font-weight: bold;
    margin-bottom: 28px;
    text-decoration: underline;
  }}
  .sig-row-guide-coord {{
    display: flex;
    justify-content: space-between;
    margin-bottom: 34px;
    padding: 0 15px;
  }}
  .sig-block {{
    text-align: center;
    width: 210px;
  }}
  .sig-line {{
    border-top: 1px solid #000;
    padding-top: 4px;
    font-weight: bold;
    font-size: 9.5pt;
  }}
  .sig-hod {{
    display: flex;
    justify-content: center;
    margin-bottom: 6px;
  }}

  /* Rubric Section */
  .rubric-box {{
    margin-top: 10px;
    border: 1px solid #000;
    padding: 8px 12px;
  }}
  .rubric-title {{
    font-weight: bold;
    font-size: 9.5pt;
    margin-bottom: 4px;
    text-align: center;
    background: #eee;
    padding: 2.5px;
    border: 1px solid #ccc;
  }}
</style>
</head>
<body>

<!-- ==================== PAGE 1 ==================== -->
<div class="page">
  <div class="header-container">
    <img src="data:image/jpeg;base64,{logo_saef_b64}" class="header-logo-left" alt="SAEF Logo">
    <div class="header-text">
      <div class="inst-trust">Suresh Angadi Education Foundation's</div>
      <div class="inst-name">ANGADI INSTITUTE OF TECHNOLOGY & MANAGEMENT</div>
      <div class="inst-affil">(Approved by AICTE, New Delhi, Affiliated to Visvesvaraya Technological University, Belagavi)</div>
      <div class="inst-addr">Savagaon Road Belagavi-590009</div>
      <div class="inst-accred">Accredited By NAAC & NBA</div>
    </div>
    <img src="data:image/jpeg;base64,{logo_aitm_b64}" class="header-logo-right" alt="AITM Logo">
  </div>
  
  <hr class="header-divider">
  <div class="dept-title">Department of Electronics and Communication Engineering</div>
  <div class="doc-title">Mini Project Synopsis</div>

  <table class="synopsis-table">
    <tr>
      <td class="item-num">1.</td>
      <td class="item-content">
        <span class="label-red">Name of the College:</span> <span class="val-text">Angadi Institute of Technology & Management, Belagavi</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">2.</td>
      <td class="item-content">
        <span class="label-red">Mini-Project Title:</span> <span class="val-text" style="font-weight: bold;">IoT-Based Underground Coal Mine Subsidence and Geotechnical Hazard Monitoring System</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">3.</td>
      <td class="item-content">
        <span class="label-red">Branch:</span> <span class="val-text">Department of Electronics and Communication Engineering</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">4.</td>
      <td class="item-content">
        <span class="label-red">Broad Theme / Category:</span> <span class="val-text">IoT, Embedded Systems, Wireless Sensor Networks (WSN), Geotechnical Safety</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">5.</td>
      <td class="item-content">
        <span class="label-red">Name of project guide:</span>
        <div style="margin-top: 2px; padding-left: 6px;">
          <div class="field-row">
            <span class="field-label">1. Name :</span>
            <span class="val-text" style="font-weight: 500;">Prof. / Dr. / Mr. / Mrs. </span>
            <span class="field-line"></span>
          </div>
          <div class="field-row">
            <span class="field-label" style="padding-left: 12px;">Email id :</span>
            <span class="field-line"></span>
          </div>
          <div class="field-row">
            <span class="field-label" style="padding-left: 12px;">Contact No.:</span>
            <span class="field-line"></span>
          </div>
        </div>
      </td>
    </tr>
    <tr>
      <td class="item-num">6.</td>
      <td class="item-content">
        <span class="label-red">Name of Team Members (Strictly not more than four students in a batch):</span>
        <div class="member-container">
          <!-- Member 1 -->
          <div class="member-item">
            <div class="member-title">1. Name : <span class="field-line" style="width: 320px; display: inline-block;"></span></div>
            <div style="display: flex; gap: 15px; margin-top: 1.5px;">
              <div class="field-row" style="flex: 1;"><span class="field-label">USN No. :</span><span class="field-line"></span></div>
              <div class="field-row" style="flex: 1;"><span class="field-label">Mobile No. :</span><span class="field-line"></span></div>
            </div>
            <div class="field-row" style="margin-top: 1.5px;"><span class="field-label">Email id :</span><span class="field-line"></span></div>
          </div>
          <!-- Member 2 -->
          <div class="member-item">
            <div class="member-title">2. Name : <span class="field-line" style="width: 320px; display: inline-block;"></span></div>
            <div style="display: flex; gap: 15px; margin-top: 1.5px;">
              <div class="field-row" style="flex: 1;"><span class="field-label">USN No. :</span><span class="field-line"></span></div>
              <div class="field-row" style="flex: 1;"><span class="field-label">Mobile No. :</span><span class="field-line"></span></div>
            </div>
            <div class="field-row" style="margin-top: 1.5px;"><span class="field-label">Email id :</span><span class="field-line"></span></div>
          </div>
          <!-- Member 3 -->
          <div class="member-item">
            <div class="member-title">3. Name : <span class="field-line" style="width: 320px; display: inline-block;"></span></div>
            <div style="display: flex; gap: 15px; margin-top: 1.5px;">
              <div class="field-row" style="flex: 1;"><span class="field-label">USN No. :</span><span class="field-line"></span></div>
              <div class="field-row" style="flex: 1;"><span class="field-label">Mobile No. :</span><span class="field-line"></span></div>
            </div>
            <div class="field-row" style="margin-top: 1.5px;"><span class="field-label">Email id :</span><span class="field-line"></span></div>
          </div>
          <!-- Member 4 -->
          <div class="member-item">
            <div class="member-title">4. Name : <span class="field-line" style="width: 320px; display: inline-block;"></span></div>
            <div style="display: flex; gap: 15px; margin-top: 1.5px;">
              <div class="field-row" style="flex: 1;"><span class="field-label">USN No. :</span><span class="field-line"></span></div>
              <div class="field-row" style="flex: 1;"><span class="field-label">Mobile No. :</span><span class="field-line"></span></div>
            </div>
            <div class="field-row" style="margin-top: 1.5px;"><span class="field-label">Email id :</span><span class="field-line"></span></div>
          </div>
        </div>
      </td>
    </tr>
  </table>

  <div class="page-footer">
    <span>Department of Electronics and Communication Engineering –AITM-Belagavi-09</span>
    <span>1</span>
  </div>
</div>

<!-- ==================== PAGE 2 ==================== -->
<div class="page">
  <table class="synopsis-table" style="margin-top: 1px;">
    <tr>
      <td class="item-num">7.</td>
      <td class="item-content">
        <span class="label-red">Team Leader of the Project :</span>
        <div style="margin-top: 2px; padding-left: 6px;">
          <div class="field-row"><span class="field-label">Name :</span><span class="field-line" style="max-width: 380px;"></span></div>
          <div style="display: flex; gap: 15px; margin-top: 1px; max-width: 500px;">
            <div class="field-row" style="flex: 1;"><span class="field-label">USN No. :</span><span class="field-line"></span></div>
            <div class="field-row" style="flex: 1;"><span class="field-label">Mobile No. :</span><span class="field-line"></span></div>
          </div>
          <div class="field-row" style="margin-top: 1px;"><span class="field-label">Email id :</span><span class="field-line" style="max-width: 380px;"></span></div>
        </div>
      </td>
    </tr>
    <tr>
      <td class="item-num">8.</td>
      <td class="item-content">
        <span class="label-red">Date of commencement of the Project:</span> <span class="val-text">15/09/2026</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">9.</td>
      <td class="item-content">
        <span class="label-red">Probable date of completion of the project:</span> <span class="val-text">15/12/2026</span>
      </td>
    </tr>
    <tr>
      <td class="item-num">10.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Scope / Objectives of the project:(not more than 500 words)</div>
        <p class="section-para">
          Underground coal mining operations are high-risk environments prone to sudden strata displacement, roof collapse, pillar failure, and surface subsidence resulting from excavation voids. Traditional monitoring relies on manual borehole extensometers, optical surveys, and periodic visual inspections by mining sirdars, which suffer from severe reporting latency and fail to provide early warnings. Furthermore, underground electromagnetic attenuation obstructs standard cellular connectivity. To prevent catastrophic cave-ins, protect personnel, and satisfy Directorate General of Mines Safety (DGMS) regulatory standards, this project develops an automated, low-power wireless geotechnical telemetry and subsidence monitoring platform using low-latency ESP-NOW mesh telemetry, multi-sensor kinematic fusion, and a centralized GIS-enabled hazard dispatch dashboard.
        </p>
        <div style="font-weight: bold; margin: 1.5px 0 1px 0; font-size: 9pt;">The key objectives are:</div>
        <ul class="custom-bullets" style="font-size: 8.8pt;">
          <li>To design and deploy wireless sensor nodes with 6-axis IMUs (MPU-6050) and shock sensors (SW-160) for real-time tracking of subsurface tilt deviations, vibrations, and seismic shocks.</li>
          <li>To establish a low-overhead peer-to-peer ESP-NOW radio telemetry link (2.4 GHz) enabling reliable data forwarding between underground nodes and surface gateway stations.</li>
          <li>To engineer an intelligent ESP32 bridge gateway aggregating telemetry into compact binary packets dispatched via HTTP POST to a centralized application server.</li>
          <li>To build an asynchronous FastAPI backend and PostgreSQL database featuring a 4-tier hazard engine (Normal, Watch, Warning, Critical) with hysteresis to eliminate alert flapping.</li>
          <li>To integrate a physical emergency SOS button and downlink-commanded acoustic buzzer for bidirectional worker distress signaling and localized evacuation alerts.</li>
          <li>To construct an interactive Leaflet GIS dashboard providing 2D/3D tunnel mapping, spatial sensor health visualization, offline node watchdog alarms, and DGMS audit logging.</li>
        </ul>
      </td>
    </tr>
    <tr>
      <td class="item-num">11.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Methodology: (not more than 500 words)</div>
        <ul class="custom-bullets" style="margin-bottom: 1.5px; font-size: 8.8pt;">
          <li><strong>Hardware Components:</strong> ESP32-WROOM-32 microcontrollers (nodes & gateway), MPU-6050 6-axis IMU, SW-160 vibration/shock sensor, active piezo buzzer, IP67 sealed tactile SOS button, 3.7V 2600 mAh 18650 Li-ion battery with TP4056 BMS circuit, AMS1117-3.3V LDO regulator, and IP65 dust/waterproof enclosure.</li>
          <li><strong>Software Tools:</strong> ESP-IDF / Arduino IDE (C/C++ firmware), FreeRTOS (multitasking & timer interrupts), Python 3.11 with FastAPI (asynchronous REST backend), PostgreSQL 16 & SQLAlchemy (relational database), Alembic (migrations), Leaflet.js, HTML5/CSS3/JavaScript (GIS dashboard), and Postman (API testing).</li>
        </ul>
        <div style="font-weight: bold; margin: 1.5px 0 1px 0; font-size: 9pt;">Implementation Steps:</div>
        <ol class="custom-nums" style="font-size: 8.8pt;">
          <li><strong>Sensor Interfacing & Calibration:</strong> Connect MPU-6050 via I2C and SW-160 via GPIO interrupt to ESP32. Implement 100 Hz moving-window calibration (100 samples) to establish baseline reference gravity and compute pitch, roll, and vibration RMS.</li>
          <li><strong>Wireless Telemetry & Protocol Pipeline:</strong> Program sensor nodes to transmit compact 37-byte packed binary telemetry structs via ESP-NOW to the gateway. Configure gateway to bridge frames into 38-byte binary payloads forwarded via HTTP POST to the central server.</li>
          <li><strong>Server Architecture & Hazard Engine:</strong> Build FastAPI ingest endpoints with database placement verification (<code>ck_nodes_placement_complete</code>). Implement a 4-tier hazard engine classifying telemetry against tilt and vibration thresholds with hysteresis (&plusmn;0.1&deg; tilt, &plusmn;0.01g vibration).</li>
          <li><strong>Bidirectional Emergency Control:</strong> Program ISR routines for the SOS button to initiate high-priority burst transmissions. Integrate server downlink command queues (0xA5 magic frame) for remote acoustic buzzer activation and dynamic rate throttling (5s normal / 2s fast mode).</li>
          <li><strong>Dashboard Integration & System Testing:</strong> Construct a Leaflet GIS dashboard displaying node positions, live hazard statuses, time-series graphs, and worker headcount verification. Validate system accuracy through simulated tilt and shock tests.</li>
        </ol>
      </td>
    </tr>
    <tr>
      <td class="item-num">12.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Expected Outcome of the project:</div>
        <ul class="custom-bullets" style="font-size: 8.8pt;">
          <li>A functional multi-node wireless IoT prototype delivering continuous subsurface tilt (&plusmn;0.1&deg; precision) and vibration RMS telemetry for underground mine galleries.</li>
          <li>High-reliability, low-latency telemetry transmission using ESP-NOW mesh networking resilient to underground electromagnetic and rock attenuation.</li>
          <li>Automated 4-tier hazard classification engine with hysteresis eliminating spurious alarms while ensuring sub-second detection of ground instability.</li>
          <li>Bidirectional safety signaling featuring instantaneous worker SOS alerts and downlink-commanded localized acoustic buzzer alarms.</li>
          <li>Centralized Leaflet GIS web dashboard featuring real-time tunnel heatmaps, automated node offline watchdog detection, and DGMS-compliant audit trails.</li>
          <li>A cost-effective, scalable, and modular geotechnical safety monitoring prototype adaptable to diverse mining and civil excavation environments.</li>
        </ul>
      </td>
    </tr>
  </table>

  <div class="page-footer">
    <span>Department of Electronics and Communication Engineering –AITM-Belagavi-09</span>
    <span>2</span>
  </div>
</div>

<!-- ==================== PAGE 3 ==================== -->
<div class="page">
  <table class="synopsis-table" style="margin-top: 1px;">
    <tr>
      <td class="item-num">13.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Application of the project:</div>
        <ul class="custom-bullets" style="margin: 1px 0; font-size: 8.6pt;">
          <li><strong>1. Industry:</strong> Real-time strata movement, roof sag, and pillar convergence monitoring in underground coal/metal mines, metro rail tunnels, highway excavations, and hydro-electric adits.</li>
          <li><strong>2. Health:</strong> Prevention of fatal crush accidents, entrapments, and severe occupational injuries caused by unpredicted roof cave-ins and sidewall spalling in hazardous underground shafts.</li>
          <li><strong>3. Agriculture:</strong> Early detection of agricultural surface subsidence, soil cracking, and sinkhole formation over abandoned coal seams and depleted underground aquifers.</li>
          <li><strong>4. Disaster Management:</strong> Early warning platform for geological subsidence, rockbursts, hillside landslides, open-pit slope instability, and post-seismic structural safety assessment.</li>
          <li><strong>5. Societal:</strong> Safeguarding surface civilian settlements, public road networks, railway corridors, and civil water bodies located directly above underground active mining leases.</li>
          <li><strong>6. Education / Academic:</strong> Practical pedagogical testbed for research in embedded FreeRTOS architectures, low-power Wireless Sensor Networks (WSN), and IoT geotechnical safety engineering.</li>
        </ul>
      </td>
    </tr>
    <tr>
      <td class="item-num">14.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">In case of fabrication work in the project, an engineering drawing with dimensions / detailed design calculations (approximately) of the machine / device should be attached to the proposal.</div>
        <p class="section-para" style="margin-bottom: 1.5px; font-size: 8.6pt;">
          <strong>• Physical Prototype Enclosure Specifications:</strong> The sensor node is packaged within a ruggedized, dust-tight, and water-resistant IP65 polycarbonate enclosure measuring approximately <strong>115 mm (L) &times; 90 mm (W) &times; 55 mm (H)</strong> equipped with internal silicone rubber sealing gasket and external stainless-steel bracket ears for rigid fastening to mine tunnel roof bolts or rock gallery sidewalls.
        </p>
        <p class="section-para" style="margin-bottom: 1.5px; font-size: 8.6pt;">
          <strong>• Internal Subassembly & Dimensions:</strong> (a) Base FR4 mounting plate: 100 mm &times; 75 mm securing ESP32 MCU, MPU-6050, SW-160, and regulator circuit. (b) Battery cradle: Dual 18650 Li-ion battery compartment (75 mm &times; 40 mm &times; 20 mm) mechanically isolated. (c) Exterior ports: 16 mm IP67 sealed red tactile momentary SOS button, 12 mm acoustic buzzer horn with dust membrane, and 2.4 GHz external 3 dBi omnidirectional whip antenna connector.
        </p>
        <p class="section-para" style="margin-bottom: 0; font-size: 8.6pt;">
          <strong>• Power Budget & Battery Autonomy Calculations:</strong> Normal operating current: <em>I<sub>sleep</sub></em> = 15 mA (CPU idle), <em>I<sub>tx</sub></em> = 120 mA (100 ms burst every 5 s). Average current: <em>I<sub>avg</sub></em> = [(120 &times; 0.1) + (15 &times; 4.9)] / 5.0 = <strong>17.1 mA</strong>. Total battery capacity: Dual 3.7V 2600 mAh Li-ion cells in parallel = <strong>5200 mAh</strong>. Operating lifespan: <em>T</em> = 5200 mAh / 17.1 mA &approx; <strong>304 hours (&approx; 12.6 days)</strong> continuous autonomous monitoring per charge cycle. (<em>Electronic/firmware prototype; no heavy mechanical machine fabrication required</em>).
        </p>
      </td>
    </tr>
    <tr>
      <td class="item-num">15.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Budget details (break-up details should be given) :</div>
        <table class="inner-table" style="font-size: 8.6pt; margin: 1.5px 0;">
          <tr>
            <th style="width: 72%;">Budget Item</th>
            <th style="width: 28%; text-align: right;">Amount (&#8377;)</th>
          </tr>
          <tr>
            <td>a) Materials / Consumables (Microcontrollers, Sensors, Batteries, Enclosures)</td>
            <td style="text-align: right;">3,850.00</td>
          </tr>
          <tr>
            <td>b) Labor</td>
            <td style="text-align: right;">0.00</td>
          </tr>
          <tr>
            <td>c) Travel</td>
            <td style="text-align: right;">0.00</td>
          </tr>
          <tr>
            <td>d) Report (Documentation, Synopsis & Report Printing, Spiral Binding)</td>
            <td style="text-align: right;">250.00</td>
          </tr>
          <tr>
            <td>e) Miscellaneous (Wiring, Connectors, Soldering Supplies & Hardware Mounting)</td>
            <td style="text-align: right;">400.00</td>
          </tr>
          <tr style="font-weight: bold; background: #e8e8e8;">
            <td style="text-align: right;">Total</td>
            <td style="text-align: right;">&#8377; 4,500.00</td>
          </tr>
        </table>
        <div style="font-size: 8pt; color: #333; margin-top: 1px;">
          <em>Consumables breakdown:</em> 2x ESP32 boards (&#8377;1,100), 1x MPU-6050 IMU (&#8377;220), 1x SW-160 sensor (&#8377;90), 2x 18650 cells + TP4056 (&#8377;520), Buzzer + SOS switch (&#8377;140), IP65 Enclosure (&#8377;680), Custom PCB & components (&#8377;650), Gateway 5V 2A supply (&#8377;450).
        </div>
      </td>
    </tr>
    <tr>
      <td class="item-num">16.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Any other technical details (Please specify):</div>
        <ul class="custom-bullets" style="margin: 1px 0; font-size: 8.3pt;">
          <li><strong>Wireless Protocols:</strong> ESP-NOW peer-to-peer 2.4 GHz IEEE 802.11 (1 Mbps PHY, channel 7, 37-byte payload, &lt;10 ms latency) and Wi-Fi HTTP client (38-byte binary payload, 10-byte downlink command struct with 0xA5 magic byte).</li>
          <li><strong>Kinematic Sensing:</strong> 100 Hz sampling (100 samples / 10 ms window), multi-window baseline calibration, pitch (&theta;) and roll (&phi;) angular computation, and running vibration RMS acceleration calculation.</li>
          <li><strong>Hazard Engine:</strong> 4-tier classification (Levels 0–3) with dual-parameter hysteresis (&Delta;&theta;<sub>hyst</sub> = 0.1&deg;, &Delta;V<sub>hyst</sub> = 0.01g) to eliminate alert flapping. Dynamic fast reporting mode (2 s) upon threshold trigger.</li>
          <li><strong>Database & Security:</strong> PostgreSQL 16 with composite partial unique indexes, placement completeness integrity constraint (<code>ck_nodes_placement_complete</code>), immutable audit logging, and background node heartbeat watchdog daemon.</li>
        </ul>
      </td>
    </tr>
    <tr>
      <td class="item-num">17.</td>
      <td class="item-content">
        <div class="label-red" style="margin-bottom: 1.5px;">Reference:</div>
        <ol class="custom-nums" style="margin: 1px 0; font-size: 8pt;">
          <li>P. S. Roy, A. Banerjee, and S. Chattopadhyay, "IoT-Based Real-Time Monitoring and Early Warning System for Underground Coal Mines," <em>IEEE Trans. Ind. Appl.</em>, vol. 57, no. 4, pp. 3412–3421, 2021.</li>
          <li>M. Verma, K. Sharma, and R. Mukherjee, "Geotechnical Strata Movement and Surface Subsidence Detection Using Low-Power WSN," <em>Springer J. Rock Mech. Geotech. Eng.</em>, vol. 12, no. 3, pp. 589–598, 2020.</li>
          <li>D. Bhattacharya, S. Mishra, and A. Ray, "Development of an ESP-NOW Mesh Telemetry Architecture for Underground Mine Safety Applications," <em>Elsevier Ad Hoc Networks</em>, vol. 114, p. 102431, 2021.</li>
          <li>R. K. Sinha and A. K. Patra, "Evaluation of InSAR and In-Situ Tiltmeter Sensor Telemetry for Ground Subsidence Monitoring in Jharia Coalfield," <em>Mining Technology</em>, vol. 129, no. 2, pp. 98–109, 2020.</li>
          <li>Directorate General of Mines Safety (DGMS), "Standard Guidelines for Continuous Strata Monitoring in Underground Coal Mines," <em>DGMS Tech. Circular</em>, no. 04 of 2019, Dhanbad, India.</li>
          <li>A. Kumar and S. Sengupta, "Design and Implementation of Low-Latency Bi-Directional IoT Emergency Communication Systems," <em>IEEE Internet of Things J.</em>, vol. 9, no. 14, pp. 12105–12115, 2022.</li>
        </ol>
      </td>
    </tr>
  </table>

  <div class="page-footer">
    <span>Department of Electronics and Communication Engineering –AITM-Belagavi-09</span>
    <span>3</span>
  </div>
</div>

<!-- ==================== PAGE 4 ==================== -->
<div class="page">
  <div style="text-align: center; margin-bottom: 10px;">
    <div style="font-size: 12pt; font-weight: bold; font-family: 'Bookman Old Style', serif;">ANGADI INSTITUTE OF TECHNOLOGY & MANAGEMENT, BELAGAVI</div>
    <div style="font-size: 10.5pt; font-weight: bold; text-decoration: underline; margin-top: 2px;">Department of Electronics and Communication Engineering</div>
    <div style="font-size: 9.5pt; font-weight: bold; margin-top: 2px; color: #444;">MINI-PROJECT SYNOPSIS EVALUATION & APPROVAL RECORD</div>
  </div>

  <div class="approval-container">
    <div class="approval-header">Approved By:</div>
    
    <div class="sig-row-guide-coord">
      <div class="sig-block">
        <div class="sig-line">Signature of Project Guide</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 3px;">Name: _______________________</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 1.5px;">Date: ____ / ____ / 2026</div>
      </div>
      <div class="sig-block">
        <div class="sig-line">Signature of Project Coordinator</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 3px;">Name: _______________________</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 1.5px;">Date: ____ / ____ / 2026</div>
      </div>
    </div>
    
    <div class="sig-hod">
      <div class="sig-block" style="width: 250px;">
        <div class="sig-line">Signature of HOD</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 3px;">Department of ECE, AITM Belagavi</div>
        <div style="font-size: 8.8pt; color: #555; margin-top: 1.5px;">Official Seal / Stamp</div>
      </div>
    </div>
  </div>

  <div class="rubric-box">
    <div class="rubric-title">DEPARTMENTAL REVIEW COMMITTEE EVALUATION RUBRIC</div>
    <table class="inner-table" style="margin-top: 5px; font-size: 8.8pt;">
      <tr>
        <th style="width: 8%; text-align: center;">Sl. No.</th>
        <th style="width: 54%;">Assessment Parameter</th>
        <th style="width: 18%; text-align: center;">Max. Marks</th>
        <th style="width: 20%; text-align: center;">Marks Awarded</th>
      </tr>
      <tr>
        <td style="text-align: center;">1.</td>
        <td>Problem Identification, Relevance & Scope Definition</td>
        <td style="text-align: center;">20</td>
        <td style="text-align: center;"></td>
      </tr>
      <tr>
        <td style="text-align: center;">2.</td>
        <td>Methodology, System Architecture & Implementation Plan</td>
        <td style="text-align: center;">30</td>
        <td style="text-align: center;"></td>
      </tr>
      <tr>
        <td style="text-align: center;">3.</td>
        <td>Technical Depth, Hardware/Software Feasibility & Safety Compliance</td>
        <td style="text-align: center;">30</td>
        <td style="text-align: center;"></td>
      </tr>
      <tr>
        <td style="text-align: center;">4.</td>
        <td>Presentation, Synopsis Formatting Quality & Q&amp;A Viva-Voce</td>
        <td style="text-align: center;">20</td>
        <td style="text-align: center;"></td>
      </tr>
      <tr style="font-weight: bold; background: #f2f2f2;">
        <td colspan="2" style="text-align: right;">Total Marks Awarded:</td>
        <td style="text-align: center;">100</td>
        <td style="text-align: center;"></td>
      </tr>
    </table>

    <div style="margin-top: 10px; font-size: 9pt;">
      <div style="font-weight: bold; margin-bottom: 3px;">Committee Recommendations / Remarks:</div>
      <div style="border-bottom: 1px dotted #555; height: 16px;"></div>
      <div style="border-bottom: 1px dotted #555; height: 16px;"></div>
    </div>

    <div style="display: flex; justify-content: space-between; margin-top: 24px; padding: 0 15px;">
      <div style="text-align: center;">
        <div style="border-top: 1px solid #333; width: 180px; padding-top: 3px; font-weight: bold; font-size: 8.8pt;">Review Member 1 (Signature)</div>
      </div>
      <div style="text-align: center;">
        <div style="border-top: 1px solid #333; width: 180px; padding-top: 3px; font-weight: bold; font-size: 8.8pt;">Review Member 2 (Signature)</div>
      </div>
    </div>
  </div>

  <div class="page-footer">
    <span>Department of Electronics and Communication Engineering –AITM-Belagavi-09</span>
    <span>4</span>
  </div>
</div>

</body>
</html>
"""

os.makedirs('docs', exist_ok=True)
html_path = os.path.abspath('docs/AITM_ECE_Mini_Project_Synopsis.html')
with open(html_path, 'w', encoding='utf-8') as f:
    f.write(html_content)

print('Updated HTML written to:', html_path)
