(function () {
    const ticketPrintContextNode = document.getElementById('conductorTicketPrintContext');
    const conductorLiveEndpoint = document.body.dataset.conductorLiveEndpoint || '';
    const conductorPanelsEndpoint = document.body.dataset.conductorPanelsEndpoint || '';
    const conductorLocationEndpoint = document.body.dataset.conductorLocationEndpoint || '';
    const csrfHeaders = document.body.dataset.csrfToken ? { 'X-CSRFToken': document.body.dataset.csrfToken } : {};
    const ticketPrintContext = ticketPrintContextNode ? JSON.parse(ticketPrintContextNode.textContent || '{}') : {};
    const trackingStatus = document.getElementById('trackingStatus');
    const currentStop = document.getElementById('currentStop');
    const currentCoords = document.getElementById('currentCoords');
    const lastUpdate = document.getElementById('lastUpdate');
    const summaryOrigin = document.getElementById('summaryOrigin');
    const originStopInput = document.getElementById('originStopInput');
    const destinationStopInput = document.getElementById('destinationStopInput');
    const passengerTypeInput = document.getElementById('passengerTypeInput');
    const summaryDestination = document.getElementById('summaryDestination');
    const summaryPassengerType = document.getElementById('summaryPassengerType');
    const summaryFare = document.getElementById('summaryFare');
    const ticketMockForm = document.getElementById('ticketMockForm');
    const offboardForm = document.querySelector('.offboard-form');
    const saveMockButton = document.getElementById('saveMockButton');
    const previewMockButton = document.getElementById('previewMockButton');
    const currentOccupancy = document.getElementById('currentOccupancy');
    const todayBoarded = document.getElementById('todayBoarded');
    const todayTransactions = document.getElementById('todayTransactions');
    const destinationManifestList = document.getElementById('destinationManifestList');
    const recentTicketList = document.getElementById('recentTicketList');
    const destinationButtons = Array.from(document.querySelectorAll('.destination-chip'));
    const passengerButtons = Array.from(document.querySelectorAll('.passenger-chip'));
    const LOCATION_REFRESH_MS = 3000;
    const LIVE_STATUS_REFRESH_MS = 3000;
    const PANEL_REFRESH_MS = 1000;
    const MIN_LOCATION_SEND_MS = 2500;
    const GEO_OPTIONS = {
      enableHighAccuracy: true,
      maximumAge: 1000,
      timeout: 5000
    };
    let selectedDestinationButton = null;
    let selectedPassengerButton = null;
    let conductorWatchId = null;
    let conductorPollId = null;
    let conductorLocationInFlight = false;
    let lastConductorLocationSentAt = 0;

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>"']/g, (char) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[char]));
    }

    function currentFareValue() {
      const passengerType = passengerTypeInput ? passengerTypeInput.value : '';
      if (!selectedDestinationButton || !passengerType) {
        return 0;
      }
      return Number(selectedDestinationButton.dataset[`fare${passengerType.charAt(0).toUpperCase()}${passengerType.slice(1)}`] || 0);
    }

    function updateTicketSummary() {
      const passengerType = passengerTypeInput ? passengerTypeInput.value : '';
      const destination = destinationStopInput ? destinationStopInput.value : '';
      const fareValue = currentFareValue();
      const isReady = Boolean(destination && passengerType);

      if (summaryDestination) summaryDestination.textContent = destination || 'Select destination';
      if (summaryPassengerType) summaryPassengerType.textContent = passengerType ? passengerType.charAt(0).toUpperCase() + passengerType.slice(1) : 'Select passenger type';
      if (summaryFare) summaryFare.textContent = `PHP ${Math.round(fareValue || 0)}`;
      if (saveMockButton) saveMockButton.disabled = !isReady;
      if (previewMockButton) previewMockButton.disabled = !isReady;
    }

    function passengerTypeLabel(value) {
      const normalized = String(value || '').trim();
      return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : 'Regular';
    }

    function renderSidebar(payload) {
      if (!payload || typeof payload !== 'object') {
        return;
      }
      const sidebar = payload && typeof payload === 'object' ? payload : {};
      const todaySummary = sidebar.today_summary || {};

      if (todayBoarded && todaySummary.boarded !== undefined) {
        todayBoarded.textContent = todaySummary.boarded;
      }
      if (todayTransactions && todaySummary.transactions !== undefined) {
        todayTransactions.textContent = todaySummary.transactions;
      }

      if (destinationManifestList) {
        const manifest = Array.isArray(sidebar.destination_manifest) ? sidebar.destination_manifest : [];
        destinationManifestList.innerHTML = manifest.length
          ? manifest.map((item) => `
              <article>
                <strong>${escapeHtml(item.name)}</strong>
                <span>${escapeHtml(item.count)} onboard</span>
              </article>
            `).join('')
          : '<p class="muted">No saved passengers are pending for the downstream stops yet.</p>';
      }

      if (recentTicketList) {
        const transactions = Array.isArray(sidebar.recent_transactions) ? sidebar.recent_transactions : [];
        recentTicketList.innerHTML = transactions.length
          ? transactions.map((row) => `
              <article>
                <strong>${escapeHtml(row.origin_stop || row.stop_name || 'Origin')} to ${escapeHtml(row.destination_stop || 'Open destination')}</strong>
                <span>${escapeHtml(row.recorded_at || '')}</span>
                <p>${escapeHtml(passengerTypeLabel(row.passenger_type))} passenger &middot; PHP ${Math.round(Number(row.fare_amount || 0))}</p>
              </article>
            `).join('')
          : '<p class="muted">No saved tickets yet.</p>';
      }
    }

    function formatTicketDate(value) {
      const date = value ? new Date(String(value).replace(' ', 'T')) : new Date();
      return date.toLocaleDateString('en-PH', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
      });
    }

    function formatTicketTime(value) {
      const date = value ? new Date(String(value).replace(' ', 'T')) : new Date();
      return date.toLocaleTimeString('en-PH', {
        hour: '2-digit',
        minute: '2-digit'
      });
    }

    function ticketNumber(value) {
      return String(value || Date.now()).replace(/\D+/g, '').slice(-5).padStart(5, '0');
    }

    function selectedTicketPayload() {
      const passengerType = passengerTypeInput ? passengerTypeInput.value : '';
      const destination = destinationStopInput ? destinationStopInput.value : '';
      const origin = summaryOrigin ? summaryOrigin.textContent.trim() : '';
      const fareValue = Math.round(currentFareValue() || 0);

      if (!passengerType || !destination) {
        return null;
      }

      return {
        id: Date.now(),
        recordedAt: new Date().toISOString(),
        passengerType,
        originStop: origin,
        destinationStop: destination,
        fareAmount: fareValue,
        plateNumber: ticketPrintContext.plateNumber,
        busNumber: ticketPrintContext.busNumber,
        routeName: ticketPrintContext.routeName,
        routeStart: ticketPrintContext.routeStart,
        routeEnd: ticketPrintContext.routeEnd
      };
    }

    function openTicketPrintWindow(message) {
      const ticketWindow = window.open('', 'gajoda_ticket_print', 'width=420,height=760');

      if (!ticketWindow) {
        return false;
      }

      ticketWindow.document.open();
      ticketWindow.document.write(`
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>Ticket Printer</title>
          <style>
            body {
              margin: 0;
              display: grid;
              min-height: 100vh;
              place-items: center;
              background: #f2f2f2;
              color: #111;
              font-family: Arial, sans-serif;
              text-align: center;
            }
            p { margin: 0; padding: 18px; }
          </style>
        </head>
        <body><p>${escapeHtml(message || 'Preparing ticket...')}</p></body>
        </html>
      `);
      ticketWindow.document.close();
      return ticketWindow;
    }

    function writeTicketPrintDocument(ticketWindow, ticket, options = {}) {
      if (!ticketWindow || !ticket) {
        return false;
      }

      const shouldAutoPrint = options.autoPrint !== false;
      const passengerType = ticket.passengerType || '';
      const routeStart = ticket.routeStart || ticketPrintContext.routeStart || '';
      const routeEnd = ticket.routeEnd || ticketPrintContext.routeEnd || '';
      const routeLabel = `${routeStart} - ${routeEnd}`;
      const busNumber = ticket.busNumber || ticketPrintContext.busNumber || ticket.plateNumber || ticketPrintContext.plateNumber || '';

      ticketWindow.document.open();
      ticketWindow.document.write(`
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <meta name="viewport" content="width=device-width, initial-scale=1.0">
          <title>Ticket Print Mock</title>
          <style>
            @page { size: 58mm auto; margin: 0; }
            * { box-sizing: border-box; }
            body {
              margin: 0;
              background: #f2f2f2;
              color: #111;
              font-family: "Courier New", Consolas, monospace;
            }
            .print-toolbar {
              display: flex;
              flex-wrap: wrap;
              gap: 8px;
              justify-content: center;
              align-items: center;
              padding: 12px;
              font-family: Arial, sans-serif;
            }
            .print-toolbar span {
              width: 100%;
              color: #555;
              font-size: 12px;
              text-align: center;
            }
            .print-toolbar button {
              border: 0;
              border-radius: 8px;
              padding: 9px 12px;
              background: #d60000;
              color: #fff;
              font-weight: 700;
              cursor: pointer;
            }
            .ticket-paper {
              width: 58mm;
              min-height: 92mm;
              margin: 0 auto 18px;
              padding: 5mm 4mm 8mm;
              background: #fff;
              box-shadow: 0 18px 38px rgba(0, 0, 0, 0.18);
              overflow: hidden;
            }
            .receipt-rule {
              margin: 2mm 0;
              border: 0;
              border-top: 1px dashed #111;
            }
            .ticket-title {
              text-align: center;
              font-weight: 700;
              letter-spacing: 0;
              margin-bottom: 1mm;
              font-size: 12px;
              text-transform: uppercase;
            }
            .ticket-subtitle {
              text-align: center;
              font-size: 8px;
              letter-spacing: 0;
              margin: 0;
            }
            .ticket-row {
              display: grid;
              grid-template-columns: 20mm 1fr;
              gap: 2mm;
              font-size: 9px;
              line-height: 1.25;
              margin: 1.2mm 0;
              word-break: break-word;
            }
            .ticket-label {
              color: #111;
              font-weight: 700;
            }
            .route-line {
              margin: 2mm 0 1mm;
              font-size: 9px;
              line-height: 1.15;
              text-align: left;
              word-break: break-word;
            }
            .route-line strong { display: block; }
            .fare-total {
              display: flex;
              justify-content: space-between;
              align-items: flex-end;
              gap: 3mm;
              margin-top: 3mm;
              font-size: 10px;
              font-weight: 700;
            }
            .fare-total strong {
              display: inline-block;
              font-size: 20px;
              line-height: 1;
              letter-spacing: 0;
            }
            .mock-note {
              margin: 5mm 0 0;
              text-align: center;
              font-size: 6px;
              color: #111;
            }
            .paper-feed {
              height: 8mm;
            }
            @media print {
              body { background: #fff; }
              .print-toolbar { display: none; }
              .ticket-paper {
                width: 58mm;
                min-height: 0;
                margin: 0;
                box-shadow: none;
              }
            }
          </style>
        </head>
        <body>
          <div class="print-toolbar">
            <span>${shouldAutoPrint ? '58mm thermal ticket. Select the paired printer in the print dialog.' : 'Receipt preview only. Use Print when you want to test the browser print dialog.'}</span>
            <button onclick="window.print()">Print</button>
            <button onclick="window.close()">Close</button>
          </div>
          <main class="ticket-paper">
            <div class="ticket-title">${escapeHtml(ticketPrintContext.operator)}</div>
            <p class="ticket-subtitle">58MM THERMAL RECEIPT</p>
            <hr class="receipt-rule">
            <div class="ticket-row"><span class="ticket-label">Plate No:</span><span>${escapeHtml(ticket.plateNumber || ticketPrintContext.plateNumber)}</span></div>
            <div class="ticket-row"><span class="ticket-label">Bus #:</span><span>${escapeHtml(String(busNumber).replace(/\D+/g, '') || busNumber)}</span></div>
            <div class="ticket-row"><span class="ticket-label">Ticket ID:</span><span>${escapeHtml(ticketNumber(ticket.id))}</span></div>
            <div class="ticket-row"><span class="ticket-label">Date:</span><span>${escapeHtml(formatTicketDate(ticket.recordedAt))}</span></div>
            <div class="ticket-row"><span class="ticket-label">Time:</span><span>${escapeHtml(formatTicketTime(ticket.recordedAt))}</span></div>
            <hr class="receipt-rule">
            <div class="route-line">
              <strong>Route: ${escapeHtml(routeLabel)}</strong>
            </div>
            <div class="ticket-row"><span class="ticket-label">Origin:</span><span>${escapeHtml(ticket.originStop)}</span></div>
            <div class="ticket-row"><span class="ticket-label">Destination:</span><span>${escapeHtml(ticket.destinationStop)}</span></div>
            <div class="ticket-row"><span class="ticket-label">Fare Type:</span><span>${escapeHtml(passengerType.charAt(0).toUpperCase() + passengerType.slice(1))}</span></div>
            <hr class="receipt-rule">
            <div class="fare-total"><span>TOTAL PHP</span><strong>${escapeHtml(Number(ticket.fareAmount || 0).toFixed(2))}</strong></div>
            <p class="mock-note">VALID BOARDING TICKET</p>
            <div class="paper-feed"></div>
          </main>
          <script>window.focus();<\/script>
        </body>
        </html>
      `);
      ticketWindow.document.close();
      if (shouldAutoPrint) {
        setTimeout(() => {
          ticketWindow.focus();
          ticketWindow.print();
        }, 600);
      }
      return true;
    }

    function openTicketPrintMock(options = {}) {
      const ticket = selectedTicketPayload();
      const ticketWindow = openTicketPrintWindow('Preparing ticket preview...');

      if (!ticket || !ticketWindow) {
        return false;
      }

      return writeTicketPrintDocument(ticketWindow, ticket, options);
    }

    function showTicketPrintError(ticketWindow, message) {
      if (!ticketWindow) {
        alert(message);
        return;
      }
      ticketWindow.document.open();
      ticketWindow.document.write(`
        <!DOCTYPE html>
        <html lang="en">
        <head>
          <meta charset="UTF-8">
          <title>Ticket Not Printed</title>
          <style>
            body {
              margin: 0;
              display: grid;
              min-height: 100vh;
              place-items: center;
              background: #fff;
              color: #111;
              font-family: Arial, sans-serif;
              text-align: center;
            }
            main { max-width: 320px; padding: 20px; }
            button {
              border: 0;
              border-radius: 8px;
              padding: 9px 12px;
              background: #d60000;
              color: #fff;
              font-weight: 700;
              cursor: pointer;
            }
          </style>
        </head>
        <body>
          <main>
            <h1>Ticket not printed</h1>
            <p>${escapeHtml(message)}</p>
            <button onclick="window.close()">Close</button>
          </main>
        </body>
        </html>
      `);
      ticketWindow.document.close();
    }

    destinationButtons.forEach((button) => {
      button.addEventListener('click', () => {
        destinationButtons.forEach((item) => item.classList.remove('is-active'));
        button.classList.add('is-active');
        selectedDestinationButton = button;
        if (destinationStopInput) destinationStopInput.value = button.dataset.destination || '';
        updateTicketSummary();
      });
    });

    passengerButtons.forEach((button) => {
      button.addEventListener('click', () => {
        passengerButtons.forEach((item) => item.classList.remove('is-active'));
        button.classList.add('is-active');
        selectedPassengerButton = button;
        if (passengerTypeInput) passengerTypeInput.value = button.dataset.passengerType || '';
        updateTicketSummary();
      });
    });

    if (ticketMockForm) {
      ticketMockForm.addEventListener('submit', async (event) => {
        event.preventDefault();

        const ticketWindow = openTicketPrintWindow('Saving ticket before printing...');
        const originalButtonText = saveMockButton ? saveMockButton.textContent : '';

        if (saveMockButton) {
          saveMockButton.disabled = true;
          saveMockButton.textContent = 'Saving...';
        }

        try {
          const response = await fetch(window.location.href, {
            method: 'POST',
            headers: {
              'Accept': 'application/json',
              'X-Requested-With': 'XMLHttpRequest'
            },
            body: new FormData(ticketMockForm)
          });
          const payload = await response.json().catch(() => ({}));

          if (!response.ok || !payload.success || !payload.ticket) {
            throw new Error(payload.error || 'Ticket was not saved.');
          }

          writeTicketPrintDocument(ticketWindow, payload.ticket, { autoPrint: true });
          if (currentOccupancy && payload.ticket.occupancyAfter !== undefined) {
            const capacity = payload.ticket.capacity || currentOccupancy.textContent.split('/')[1] || '';
            currentOccupancy.textContent = `${payload.ticket.occupancyAfter}/${capacity}`;
          }
          renderSidebar(payload.ticket.sidebar);
          refreshTripLocation().catch(() => {});
        } catch (error) {
          showTicketPrintError(ticketWindow, error.message || 'Unable to print ticket.');
        } finally {
          if (saveMockButton) {
            saveMockButton.textContent = originalButtonText || 'Print Ticket';
          }
          updateTicketSummary();
        }
      });
    }

    if (previewMockButton) {
      previewMockButton.addEventListener('click', () => {
        openTicketPrintMock({ autoPrint: false });
      });
    }

    if (offboardForm) {
      offboardForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const submitButton = offboardForm.querySelector('button[type="submit"]');
        const originalButtonText = submitButton ? submitButton.textContent : '';

        if (submitButton) {
          submitButton.disabled = true;
          submitButton.textContent = 'Updating...';
        }

        try {
          const response = await fetch(window.location.href, {
            method: 'POST',
            headers: {
              'Accept': 'application/json',
              'X-Requested-With': 'XMLHttpRequest'
            },
            body: new FormData(offboardForm)
          });
          const payload = await response.json().catch(() => ({}));
          if (!response.ok || !payload.success) {
            throw new Error(payload.error || 'Unable to offboard passengers.');
          }
          if (payload.live) {
            applyLivePayload(payload.live);
          }
          refreshTripLocation().catch(() => {});
        } catch (error) {
          if (trackingStatus) trackingStatus.textContent = error.message || 'Offboard update failed';
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
            submitButton.textContent = originalButtonText || 'Off Board Current Stop';
          }
        }
      });
    }

    function applyLivePayload(payload, options = {}) {
      const shouldUpdateTrackingStatus = options.updateTrackingStatus !== false;
      const shouldUpdateGpsFields = options.updateGpsFields !== false;
      if (!payload.active) {
        if (trackingStatus && shouldUpdateTrackingStatus) trackingStatus.textContent = 'No active trip';
        return;
      }
      if (trackingStatus && shouldUpdateTrackingStatus) {
        trackingStatus.textContent = payload.tracking ? 'Live GPS active' : 'Waiting for GPS';
      }
      const stopLabel = payload.stop_name || 'On route';
      if (currentStop) currentStop.textContent = stopLabel;
      if (currentCoords) currentCoords.textContent = stopLabel;
      if (summaryOrigin) summaryOrigin.textContent = stopLabel;
      if (originStopInput) originStopInput.value = stopLabel;
      if (lastUpdate && shouldUpdateGpsFields) {
        lastUpdate.textContent = payload.recorded_at || 'No live update yet';
      }
      if (currentOccupancy && payload.occupancy !== undefined && payload.capacity !== undefined) {
        currentOccupancy.textContent = `${payload.occupancy}/${payload.capacity}`;
      }
      renderSidebar(payload.sidebar);
    }

    async function refreshTripLocation() {
      const response = await fetch(conductorLiveEndpoint, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('Failed to load trip location');
      }
      const payload = await response.json();
      applyLivePayload(payload, { updateTrackingStatus: true, updateGpsFields: true });
    }

    async function refreshConductorPanels() {
      if (!conductorPanelsEndpoint) {
        return;
      }
      const response = await fetch(conductorPanelsEndpoint, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error('Failed to load conductor panels');
      }
      const payload = await response.json();
      applyLivePayload(payload, { updateTrackingStatus: false, updateGpsFields: false });
    }

    async function pushConductorLocation(latitude, longitude) {
      if (!conductorLocationEndpoint) {
        return { success: false };
      }
      const response = await fetch(conductorLocationEndpoint, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders
        },
        body: JSON.stringify({ latitude, longitude })
      });
      return response.json();
    }

    function startConductorGpsFallback() {
      if (!navigator.geolocation || !conductorLocationEndpoint || conductorWatchId !== null) {
        return;
      }

      const handleConductorPosition = async (position) => {
        const now = Date.now();
        if (conductorLocationInFlight || now - lastConductorLocationSentAt < MIN_LOCATION_SEND_MS) {
          return;
        }

        conductorLocationInFlight = true;
        try {
          const result = await pushConductorLocation(position.coords.latitude, position.coords.longitude);
          if (result.success && trackingStatus) {
            lastConductorLocationSentAt = Date.now();
            trackingStatus.textContent = 'Live GPS active';
          }
        } catch (error) {
          if (trackingStatus) trackingStatus.textContent = 'Conductor GPS standby';
        } finally {
          conductorLocationInFlight = false;
        }
      };

      const handleConductorLocationError = () => {
        if (trackingStatus) trackingStatus.textContent = 'Waiting for GPS permission';
      };

      const requestConductorPosition = () => {
        navigator.geolocation.getCurrentPosition(handleConductorPosition, handleConductorLocationError, GEO_OPTIONS);
      };

      conductorWatchId = navigator.geolocation.watchPosition(handleConductorPosition, handleConductorLocationError, GEO_OPTIONS);
      requestConductorPosition();
      conductorPollId = setInterval(requestConductorPosition, LOCATION_REFRESH_MS);
    }

    updateTicketSummary();
    startConductorGpsFallback();
    refreshTripLocation().catch(() => {
      if (trackingStatus) trackingStatus.textContent = 'Waiting for GPS';
    });
    refreshConductorPanels().catch(() => {});
    setInterval(() => refreshTripLocation().catch(() => {
      if (trackingStatus) trackingStatus.textContent = 'GPS refresh failed';
    }), LIVE_STATUS_REFRESH_MS);
    setInterval(() => refreshConductorPanels().catch(() => {}), PANEL_REFRESH_MS);
})();
